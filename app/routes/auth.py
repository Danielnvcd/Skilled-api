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
                # Revocar todos los refresh tokens del usuario (invalida "recordarme" en todos los dispositivos)
                RefreshToken.query.filter_by(user_id=user.id, revoked=False).update({'revoked': True})
                db.session.commit()
                # Actualizar la versión en la sesión activa para no desloguear al propio usuario
                session['password_version'] = user.password_version
                flash('Contraseña actualizada correctamente.', 'success')
                log_action(f"Contraseña actualizada para {user.username}")

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
def login():
    # Si ya tiene sesión activa, redirigir según rol
    if 'user_id' in session:
        next_url = request.args.get('next') or request.referrer
        if next_url and _is_safe_url(next_url) and '/login' not in next_url:
            return redirect(next_url)
        role = session.get('role', '')
        if role == 'inventario':
            is_mobile = request.user_agent.platform in ['android', 'iphone', 'ipad'] or 'mobi' in request.user_agent.string.lower()
            dest = url_for('inventario_ui.movil') if is_mobile else url_for('inventario_ui.web')
        elif role == 'solicitante_material':
            dest = url_for('inventario_ui.solicitar')
        elif role == 'coordinador':
            dest = url_for('horas.index')
        else:
            dest = url_for('main.home')
        return redirect(dest)
        
    if request.method == 'POST':
        u = User.query.filter_by(username=request.form.get('username')).first()
        
        if u and check_password_hash(u.password_hash, request.form.get('password')):
            remember = request.form.get('remember') == 'on'
            session.clear()

            if u.totp_secret:
                session['pre_2fa_user_id'] = u.id
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
                dest = url_for('horas.index')
            else:
                dest = url_for('main.home')

            response = redirect(dest)
            if remember:
                _set_rt_cookie(response, _issue_refresh_token(u.id))
            return response
        
        flash('Credenciales incorrectas', 'danger')
        # Registrar fallo con IP real (Cloudflare Tunnel envía CF-Connecting-IP)
        _attempted_user = (request.form.get('username') or '')[:80]  # truncar, nunca loguear contraseña
        _real_ip = request.headers.get('CF-Connecting-IP', request.remote_addr)
        log_action(f"Login fallido para usuario '{_attempted_user}' desde IP {_real_ip}")
        
    return render_template('login.html')

@bp.route('/verify-2fa', methods=['GET', 'POST'])
@limiter.limit("4 per minute", methods=['POST'])
def verify_2fa():
    if 'pre_2fa_user_id' not in session:
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
                dest = url_for('horas.index')
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
def setup_2fa():
    user = g._current_user  # ya cargado por login_required, sin query adicional
    
    if request.method == 'POST':
        secret = session.get('totp_secret_setup')
        code = request.form.get('code')
        
        if not secret:
            flash('Error en la sesión. Intenta de nuevo.', 'danger')
            return redirect(url_for('auth.setup_2fa'))
            
        totp = pyotp.TOTP(secret)
        if totp.verify(code, valid_window=1):
            user.totp_secret = secret
            db.session.commit()
            session.pop('totp_secret_setup', None)
            flash('Doble factor de autenticación activado correctamente.', 'success')
            log_action(f"2FA activado para {user.username}")
            return redirect(url_for('main.home'))
        else:
            log_action(f"2FA setup: código incorrecto para {user.username}")
            flash('Código incorrecto.', 'danger')

    if 'totp_secret_setup' not in session:
        session['totp_secret_setup'] = pyotp.random_base32()
    
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
