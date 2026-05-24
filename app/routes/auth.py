from flask import Blueprint, render_template, redirect, url_for, flash, request, session, current_app, g
from werkzeug.security import generate_password_hash, check_password_hash
from urllib.parse import urlparse, urljoin
from app.extensions import db, limiter, get_redis
from app.models import User, RefreshToken
from app.utils import log_action, login_required, is_strong_password, _get_session_user
from app.constants import REFRESH_TOKEN_LIFETIME_DAYS
import pyotp
import qrcode
import io
import base64
import secrets
import hashlib
from datetime import datetime, timedelta, timezone

# ── Constantes del refresh token ─────────────────────────────────────────────
_RT_COOKIE = 'rt'          # nombre de la cookie HttpOnly del refresh token

# ── Anti enumeración por timing ──────────────────────────────────────────────
# Hash dummy precomputado al cargar el módulo. En la rama "usuario no existe"
# se compara la contraseña contra este hash para igualar el costo de un
# check_password_hash real e impedir distinguir usernames válidos por tiempo.
_DUMMY_PW_HASH = generate_password_hash('not-a-real-password-timing-dummy')

# ── Expiración del estado pre-2FA ────────────────────────────────────────────
# Tras introducir credenciales y antes de completar el 2FA, el usuario tiene
# este tiempo para escribir el código. Evita que una pestaña abierta varias
# horas siga siendo válida para completar el 2FA.
_PRE_2FA_LIFETIME_SECONDS = 300  # 5 minutos

# ── Lockout escalado por username ────────────────────────────────────────────
# A los _LOGIN_FAILS_THRESHOLD fallos consecutivos dentro de _LOGIN_FAILS_WINDOW,
# bloquea la cuenta por una duración que escala según cuántas veces se haya
# disparado el lockout en las últimas 24 h. El nivel decae solo: si no hay
# nuevos lockouts en 24 h, vuelve a empezar desde 10 min.
# Requiere Redis. Sin Redis el sistema degrada al rate-limit normal sin romper.
_LOGIN_FAILS_WINDOW = 15 * 60          # ventana para contar fallos
_LOGIN_FAILS_THRESHOLD = 5             # fallos para disparar lockout
_LOCKOUT_LEVEL_TTL = 24 * 3600         # ventana de memoria del nivel de escalación
_LOCKOUT_DURATIONS = [                  # escalado: 10m → 30m → 1h → 3h → 12h → 24h
    10 * 60,
    30 * 60,
    60 * 60,
    3 * 60 * 60,
    12 * 60 * 60,
    24 * 60 * 60,
]


def _norm_user(username: str) -> str:
    """Normaliza el username para usarlo como key de Redis (case-insensitive)."""
    return (username or '').lower().strip()


def _lockout_key(username):       return f"login_lockout:{_norm_user(username)}"
def _level_key(username):         return f"login_lockout_level:{_norm_user(username)}"
def _fails_key(username):         return f"login_fails:{_norm_user(username)}"


def _check_lockout(username):
    """Devuelve los segundos restantes de lockout, o None si la cuenta no está bloqueada."""
    if not _norm_user(username):
        return None
    r = get_redis()
    if not r:
        return None
    ttl = r.ttl(_lockout_key(username))
    return ttl if (ttl is not None and ttl > 0) else None


def _register_login_failure(username):
    """Incrementa el contador de fallos. Al pasar el umbral, dispara lockout escalado."""
    if not _norm_user(username):
        return
    r = get_redis()
    if not r:
        return
    fkey = _fails_key(username)
    fails = r.incr(fkey)
    if fails == 1:
        r.expire(fkey, _LOGIN_FAILS_WINDOW)
    if fails >= _LOGIN_FAILS_THRESHOLD:
        lkey = _level_key(username)
        try:
            level = int(r.get(lkey) or 0)
        except (TypeError, ValueError):
            level = 0
        duration = _LOCKOUT_DURATIONS[min(level, len(_LOCKOUT_DURATIONS) - 1)]
        r.setex(_lockout_key(username), duration, '1')
        r.setex(lkey, _LOCKOUT_LEVEL_TTL, level + 1)  # decae si pasan 24 h sin nuevos lockouts
        r.delete(fkey)


def _clear_login_failures(username):
    """Resetea contador, lockout y nivel tras un login exitoso."""
    if not _norm_user(username):
        return
    r = get_redis()
    if not r:
        return
    r.delete(_fails_key(username), _lockout_key(username), _level_key(username))


def _format_ttl(seconds: int) -> str:
    """Convierte segundos a string humano para mostrar al usuario."""
    if seconds <= 60:
        return "1 minuto"
    if seconds < 3600:
        m = (seconds + 59) // 60
        return f"{m} minutos"
    if seconds < 86400:
        h = (seconds + 3599) // 3600
        return f"{h} {'hora' if h == 1 else 'horas'}"
    d = (seconds + 86399) // 86400
    return f"{d} {'día' if d == 1 else 'días'}"


def _is_safe_url(target):
    """Verifica que el URL de redirección sea del mismo dominio (anti open-redirect)."""
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ('http', 'https') and ref_url.netloc == test_url.netloc


# ── Helpers de refresh token ──────────────────────────────────────────────────

def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _issue_refresh_token(user_id: int) -> str:
    """Genera un refresh token, persiste su hash en BD y retorna el valor crudo."""
    raw = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_LIFETIME_DAYS)
    tok = RefreshToken(token_hash=_hash_token(raw), user_id=user_id, expires_at=expires)
    db.session.add(tok)
    db.session.commit()
    return raw


def _revoke_refresh_token(raw: str) -> None:
    """Marca un refresh token como revocado (idempotente)."""
    h = _hash_token(raw)
    tok = RefreshToken.query.filter_by(token_hash=h).first()
    if tok and not tok.revoked:
        tok.revoked = True
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()


def _set_rt_cookie(response, raw: str) -> None:
    """Escribe la cookie de refresh token con las flags de seguridad correctas."""
    is_prod = current_app.config.get('SESSION_COOKIE_SECURE', False)
    response.set_cookie(
        _RT_COOKIE,
        raw,
        max_age=REFRESH_TOKEN_LIFETIME_DAYS * 86400,
        httponly=True,
        secure=is_prod,
        samesite='Strict',
        path='/',
    )


def _clear_rt_cookie(response) -> None:
    is_prod = current_app.config.get('SESSION_COOKIE_SECURE', False)
    response.delete_cookie(_RT_COOKIE, path='/', secure=is_prod, samesite='Strict')


def _build_session(user: User, permanent: bool = True) -> None:
    """Rellena la sesión Flask con los datos del usuario."""
    session['user_id'] = user.id
    session['user'] = user.username
    session['role'] = user.role
    session['password_version'] = user.password_version or 1
    session.permanent = permanent


bp = Blueprint('auth', __name__)

@bp.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    user = g._current_user  # ya cargado por login_required, sin query adicional

    if not user:
        session.clear()
        flash('Tu sesión es inválida. Por favor inicia sesión de nuevo.', 'danger')
        return redirect(url_for('auth.login'))
    
    if request.method == 'POST':
        new_password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')

        if new_password:
            if new_password != confirm_password:
                flash('Las contraseñas no coinciden.', 'danger')
            elif not is_strong_password(new_password):
                flash('La nueva contraseña es demasiado débil (usa mayúsculas, minúsculas, números y símbolos).', 'danger')
            else:
                user.password_hash = generate_password_hash(new_password)
                # Incrementar versión invalida todas las sesiones abiertas en otros dispositivos
                user.password_version = (user.password_version or 1) + 1
                # SEGURIDAD: NO borrar `totp_secret` al rotar contraseña. El segundo factor
                # sobrevive: el usuario sigue protegido por TOTP aunque alguien (un admin
                # malicioso, por ejemplo) le resetee la contraseña. El TOTP solo se quita
                # cuando el dueño lo desactiva explícitamente.
                # Revocar todos los refresh tokens del usuario (invalida "recordarme" en todos los dispositivos)
                RefreshToken.query.filter_by(user_id=user.id, revoked=False).update({'revoked': True})
                try:
                    db.session.commit()
                    # Actualizar la versión en la sesión activa para no desloguear al propio usuario
                    session['password_version'] = user.password_version
                    flash('Contraseña actualizada correctamente.', 'success')
                    log_action(f"Cambio de contraseña propio")
                except Exception as e:
                    db.session.rollback()
                    current_app.logger.error(f'Error al actualizar contraseña: {e}')
                    flash('Ocurrió un error al actualizar la contraseña. Intenta de nuevo.', 'danger')

        profile_pic = request.files.get('profile_pic')
        if profile_pic and profile_pic.filename != '':
            from werkzeug.utils import secure_filename
            from app.utils import allowed_image_file
            import os, uuid
            if allowed_image_file(profile_pic):
                ext = profile_pic.filename.rsplit('.', 1)[-1].lower() if '.' in profile_pic.filename else ''
                filename = secure_filename(profile_pic.filename)
                unique_filename = f"profile_{user.id}_{uuid.uuid4().hex[:8]}.{ext}"
                upload_path = os.path.join(current_app.config['UPLOAD_FOLDER'], unique_filename)
                profile_pic.save(upload_path)

                if user.profile_pic and user.profile_pic != 'default.png':
                    old_pic_path = os.path.join(current_app.config['UPLOAD_FOLDER'], user.profile_pic)
                    if os.path.exists(old_pic_path):
                        try:
                            os.remove(old_pic_path)
                        except Exception as e:
                            current_app.logger.warning(f"No se pudo eliminar foto antigua: {e}")

                user.profile_pic = unique_filename
                db.session.commit()
                flash('Foto de perfil actualizada correctamente.', 'success')
                log_action(f"Foto de perfil actualizada para {user.username}")
            else:
                flash('Foto rechazada: solo se permiten imágenes JPG o PNG reales.', 'warning')
            
    return render_template('profile.html', user=user)

@bp.before_app_request
def _try_refresh_session():
    """
    Si el access token (sesión) expiró pero existe una cookie 'rt' válida,
    renueva la sesión automáticamente y rota el refresh token (detección de replay).
    """
    if 'user_id' in session:
        return  # Sesión activa, nada que hacer
    if request.endpoint and 'static' in request.endpoint:
        return

    raw_rt = request.cookies.get(_RT_COOKIE)
    if not raw_rt:
        return

    now = datetime.now(timezone.utc)
    h = _hash_token(raw_rt)

    try:
        tok = RefreshToken.query.filter_by(token_hash=h, revoked=False).first()
        if not tok:
            g._clear_rt = True
            return

        # Normalizar timezone del campo expires_at para la comparación
        exp = tok.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp <= now:
            tok.revoked = True
            db.session.commit()
            g._clear_rt = True
            return

        user = tok.user
        if user is None:
            tok.revoked = True
            db.session.commit()
            g._clear_rt = True
            return

        # Rotar: revocar token actual y emitir uno nuevo
        tok.revoked = True
        new_raw = secrets.token_urlsafe(32)
        new_tok = RefreshToken(
            token_hash=_hash_token(new_raw),
            user_id=user.id,
            expires_at=now + timedelta(days=REFRESH_TOKEN_LIFETIME_DAYS),
        )
        db.session.add(new_tok)
        # Limpiar tokens expirados/revocados de este usuario (housekeeping)
        RefreshToken.query.filter(
            RefreshToken.user_id == user.id,
            (RefreshToken.revoked == True) | (RefreshToken.expires_at <= now),
            RefreshToken.id != new_tok.id,
        ).delete(synchronize_session=False)
        db.session.commit()

        _build_session(user, permanent=True)
        g._new_rt = new_raw
        current_app.logger.info("Sesión renovada vía refresh token para user_id=%s", user.id)

    except Exception as e:
        current_app.logger.warning("Error en _try_refresh_session: %s", e)
        db.session.rollback()


@bp.after_app_request
def _rotate_rt_cookie(response):
    """Adjunta o elimina la cookie de refresh token según lo que decidió _try_refresh_session."""
    if getattr(g, '_new_rt', None):
        _set_rt_cookie(response, g._new_rt)
    elif getattr(g, '_clear_rt', False):
        _clear_rt_cookie(response)
    return response


@bp.before_app_request
def update_last_seen():
    if 'user_id' not in session:
        return
    if request.endpoint and 'static' in request.endpoint:
        return

    user_id = session['user_id']
    r = get_redis()

    if r:
        # Si la key existe en Redis, significa que ya actualizamos hace menos de 5 min
        cache_key = f"last_seen:{user_id}"
        if r.get(cache_key):
            return  # Aún no toca actualizar

        # Key expiró o no existe → actualizar BD y renovar cache
        try:
            user = _get_session_user()  # reutiliza g._current_user si ya está cargado
            if user:
                user.last_seen = datetime.now()
                db.session.commit()
                r.setex(cache_key, 300, "1")  # TTL de 5 minutos
        except Exception as e:
            current_app.logger.warning(f"Error updating last_seen: {e}")
            db.session.rollback()
    else:
        # Fallback sin Redis: throttle via session (cada 5 min)
        import time
        now = time.time()
        last_update = session.get('_last_seen_ts', 0)
        if now - last_update < 300:  # 5 minutos
            return
        try:
            user = _get_session_user()  # reutiliza g._current_user si ya está cargado
            if user:
                user.last_seen = datetime.now()
                db.session.commit()
                session['_last_seen_ts'] = now
        except Exception as e:
            current_app.logger.warning(f"Error updating last_seen: {e}")
            db.session.rollback()

@bp.route('/login', methods=['GET', 'POST'])
@limiter.limit("4 per minute", methods=['POST'])
@limiter.limit(
    "8 per minute",
    methods=['POST'],
    key_func=lambda: f"login_user:{(request.form.get('username') or '').lower().strip()}"
)
def login():
    # Si ya tiene sesión activa, redirigir según rol
    if 'user_id' in session:
        next_url = request.args.get('next') or request.referrer
        if next_url and _is_safe_url(next_url) and '/login' not in next_url:
            return redirect(next_url)
        role = session.get('role', '')
        is_mobile = request.user_agent.platform in ['android', 'iphone', 'ipad'] or 'mobi' in request.user_agent.string.lower()
        if role == 'inventario':
            dest = url_for('inventario_ui.movil') if is_mobile else url_for('inventario_ui.web')
        elif role == 'solicitante_material':
            dest = url_for('inventario_ui.solicitar')
        elif role == 'coordinador':
            dest = url_for('horas.movil') if is_mobile else url_for('horas.index')
        else:
            dest = url_for('main.home')
        return redirect(dest)

    if request.method == 'POST':
        attempted_username = request.form.get('username') or ''
        password = request.form.get('password') or ''

        # Lockout escalado: si la cuenta (real o inexistente) está bloqueada, no procesar.
        # Se chequea ANTES de hacer el hash para no malgastar CPU ni dar pistas por timing.
        # El mensaje es el mismo para usernames reales y falsos → no leakea existencia.
        lockout_ttl = _check_lockout(attempted_username)
        if lockout_ttl:
            flash(
                f'Cuenta bloqueada por demasiados intentos fallidos. '
                f'Intenta de nuevo en {_format_ttl(lockout_ttl)}.',
                'danger'
            )
            return render_template('login.html')

        u = User.query.filter_by(username=attempted_username).first()

        if u:
            password_ok = check_password_hash(u.password_hash, password)
        else:
            # Igualar tiempos cuando el usuario no existe: ejecuta un check_password_hash
            # contra un hash dummy precomputado para no exponer la existencia del username
            # mediante diferencias de tiempo de respuesta.
            check_password_hash(_DUMMY_PW_HASH, password)
            password_ok = False

        if password_ok:
            # Login bueno → resetear contador/lockout/nivel para esta cuenta.
            _clear_login_failures(attempted_username)
            remember = request.form.get('remember') == 'on'
            session.clear()

            if u.totp_secret:
                session['pre_2fa_user_id'] = u.id
                session['pre_2fa_pw_version'] = u.password_version or 1
                session['pre_2fa_ts'] = datetime.now(timezone.utc).timestamp()
                session['remember'] = remember
                return redirect(url_for('auth.verify_2fa'))

            _build_session(u, permanent=remember)
            u.last_seen = datetime.now()
            db.session.commit()
            log_action("Login exitoso")

            is_mobile = request.user_agent.platform in ['android', 'iphone', 'ipad'] or 'mobi' in request.user_agent.string.lower()
            if u.role == 'inventario':
                dest = url_for('inventario_ui.movil') if is_mobile else url_for('inventario_ui.web')
            elif u.role == 'solicitante_material':
                dest = url_for('inventario_ui.solicitar')
            elif u.role == 'coordinador':
                dest = url_for('horas.movil') if is_mobile else url_for('horas.index')
            else:
                dest = url_for('main.home')

            response = redirect(dest)
            if remember:
                _set_rt_cookie(response, _issue_refresh_token(u.id))
            return response
        
        # Registrar fallo en el contador de lockout escalado (Redis). En el 5to fallo dentro
        # de _LOGIN_FAILS_WINDOW dispara un bloqueo cuya duración crece por nivel:
        # 10m → 30m → 1h → 3h → 12h → 24h.
        _register_login_failure(attempted_username)

        # Si el fallo dejó la cuenta bloqueada, informar el tiempo restante para que el
        # usuario sepa cuándo volver. Si no, mensaje genérico "Credenciales incorrectas".
        post_fail_ttl = _check_lockout(attempted_username)
        if post_fail_ttl:
            flash(
                f'Cuenta bloqueada por demasiados intentos fallidos. '
                f'Intenta de nuevo en {_format_ttl(post_fail_ttl)}.',
                'danger'
            )
        else:
            flash('Credenciales incorrectas', 'danger')

        # Registrar el fallo en bitácora con la IP real validada: get_real_client_ip_flask
        # sólo confía en CF-Connecting-IP cuando la conexión directa proviene de un rango
        # oficial de Cloudflare, evitando que un atacante spoofee el header.
        from app.extensions import get_real_client_ip_flask
        _attempted_user = attempted_username[:80]  # truncar, nunca loguear contraseña
        _real_ip = get_real_client_ip_flask()
        log_action(f"Login fallido para usuario '{_attempted_user}' desde IP {_real_ip}")
        
    return render_template('login.html')

@bp.route('/verify-2fa', methods=['GET', 'POST'])
@limiter.limit("4 per minute", methods=['POST'])
@limiter.limit(
    "8 per minute",
    methods=['POST'],
    key_func=lambda: f"v2fa_user:{session.get('pre_2fa_user_id', 'anon')}"
)
def verify_2fa():
    if 'pre_2fa_user_id' not in session:
        return redirect(url_for('auth.login'))

    # Expiración del estado pre-2FA: tras _PRE_2FA_LIFETIME_SECONDS se debe re-autenticar.
    # Cubre el caso de una pestaña olvidada y limita la ventana de aprovechamiento si alguien
    # obtiene acceso físico al navegador después de que el usuario legítimo ingresó credenciales.
    pre_ts = session.get('pre_2fa_ts')
    now_ts = datetime.now(timezone.utc).timestamp()
    if not pre_ts or (now_ts - pre_ts) > _PRE_2FA_LIFETIME_SECONDS:
        session.pop('pre_2fa_user_id', None)
        session.pop('pre_2fa_pw_version', None)
        session.pop('pre_2fa_ts', None)
        session.pop('remember', None)
        flash('La verificación 2FA expiró. Inicia sesión de nuevo.', 'warning')
        return redirect(url_for('auth.login'))

    if request.method == 'POST':
        user = User.query.get(session['pre_2fa_user_id'])
        code = request.form.get('code')
        
        if not user or not user.totp_secret:
            session.pop('pre_2fa_user_id', None)
            flash('Sesión inválida. Inicia sesión de nuevo.', 'danger')
            return redirect(url_for('auth.login'))
        
        totp = pyotp.TOTP(user.totp_secret)
        if totp.verify(code, valid_window=1):
            if session.get('pre_2fa_pw_version', 1) != (user.password_version or 1):
                session.clear()
                flash('Tu contraseña fue cambiada. Inicia sesión de nuevo.', 'warning')
                return redirect(url_for('auth.login'))
            remember = session.get('remember', False)
            session.clear()
            _build_session(user, permanent=remember)
            log_action(f"Login 2FA exitoso para {user.username}")

            is_mobile = request.user_agent.platform in ['android', 'iphone', 'ipad'] or 'mobi' in request.user_agent.string.lower()
            if user.role == 'inventario':
                dest = url_for('inventario_ui.movil') if is_mobile else url_for('inventario_ui.web')
            elif user.role == 'solicitante_material':
                dest = url_for('inventario_ui.solicitar')
            elif user.role == 'coordinador':
                dest = url_for('horas.movil') if is_mobile else url_for('horas.index')
            else:
                dest = url_for('main.home')

            response = redirect(dest)
            if remember:
                _set_rt_cookie(response, _issue_refresh_token(user.id))
            return response
        else:
            log_action(f"2FA fallido para {user.username}")
            flash('Código incorrecto.', 'danger')
            
    return render_template('verify_2fa.html')

@bp.route('/setup-2fa', methods=['GET', 'POST'])
@login_required
@limiter.limit("4 per minute", methods=['POST'])
def setup_2fa():
    user = g._current_user  # ya cargado por login_required, sin query adicional
    
    # Vida útil del secreto pre-confirmación de 2FA. Si caduca, se regenera otro:
    # evita que un atacante con la sesión secuestrada complete un setup 2FA abandonado
    # por la víctima y obtenga control persistente del segundo factor.
    _SETUP_TTL = 600  # 10 minutos

    now_ts = datetime.now(timezone.utc).timestamp()
    setup_ts = session.get('totp_secret_setup_ts')
    setup_expired = (not setup_ts) or (now_ts - setup_ts > _SETUP_TTL)

    if request.method == 'POST':
        secret = session.get('totp_secret_setup')
        code = request.form.get('code')

        if not secret or setup_expired:
            session.pop('totp_secret_setup', None)
            session.pop('totp_secret_setup_ts', None)
            flash('La configuración de 2FA expiró. Escanea el nuevo código QR.', 'warning')
            return redirect(url_for('auth.setup_2fa'))

        totp = pyotp.TOTP(secret)
        if totp.verify(code, valid_window=1):
            user.totp_secret = secret
            db.session.commit()
            session.pop('totp_secret_setup', None)
            session.pop('totp_secret_setup_ts', None)
            flash('Doble factor de autenticación activado correctamente.', 'success')
            log_action(f"2FA activado para {user.username}")
            return redirect(url_for('main.home'))
        else:
            log_action(f"2FA setup: código incorrecto para {user.username}")
            flash('Código incorrecto.', 'danger')

    if 'totp_secret_setup' not in session or setup_expired:
        session['totp_secret_setup'] = pyotp.random_base32()
        session['totp_secret_setup_ts'] = now_ts

    secret = session['totp_secret_setup']
    
    totp_uri = pyotp.totp.TOTP(secret).provisioning_uri(
        name=user.username,
        issuer_name="SistemaNominas"
    )
    
    img = qrcode.make(totp_uri)
    buffered = io.BytesIO()
    img.save(buffered, format="PNG")
    qr_base64 = base64.b64encode(buffered.getvalue()).decode('utf-8')
    
    return render_template('setup_2fa.html', qr_code=qr_base64, secret=secret)

@bp.route('/logout', methods=['POST'])
@login_required
def logout():
    # Registrar actividad ANTES de limpiar sesión
    user = g._current_user  # ya cargado por login_required, sin query adicional
    if user:
        user.last_seen = datetime.now()
        db.session.commit()
    log_action("Logout")

    # Revocar refresh token si existe (invalida la cookie aunque persista en el navegador)
    raw_rt = request.cookies.get(_RT_COOKIE)
    if raw_rt:
        _revoke_refresh_token(raw_rt)

    session.clear()
    flash('Sesión cerrada correctamente.', 'info')

    response = redirect(url_for('auth.login'))
    _clear_rt_cookie(response)
    return response
