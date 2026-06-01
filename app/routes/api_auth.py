"""API JWT para el frontend React.

Convive con el blueprint clásico `auth.py` (sesión + plantillas Jinja). Aquí no se
usa Flask-Session ni cookies de sesión: el access token es un JWT firmado y el
refresh token se entrega en una cookie HttpOnly (`rt_api`) — separada de la cookie
`rt` del flujo HTML para evitar colisiones de rotación entre ambos clientes.
"""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import jwt
import pyotp
from flask import Blueprint, current_app, g, jsonify, request
from werkzeug.security import check_password_hash

from app.constants import REFRESH_TOKEN_LIFETIME_DAYS
from app.extensions import db, limiter
from app.models import RefreshToken, User
from app.routes.auth import (
    _DUMMY_PW_HASH,
    _check_lockout,
    _clear_login_failures,
    _format_ttl,
    _hash_token,
    _register_login_failure,
)
from app.utils import log_action

bp = Blueprint('api_auth', __name__, url_prefix='/api/auth')

# ── Constantes JWT ──────────────────────────────────────────────────────────
ACCESS_TOKEN_LIFETIME_MINUTES = 20      # mismo TTL que la sesión Flask clásica
PRE_2FA_LIFETIME_SECONDS = 300          # ventana para completar 2FA tras password
_RT_COOKIE = 'rt_api'                   # cookie del refresh token para el SPA

# JWT issuer/audience: defensa en profundidad — si por error reusamos el mismo
# SECRET_KEY en otro servicio (común al hacer copy-paste de config), los tokens
# emitidos por ESTA app no son válidos en aquella y viceversa. También bloquea
# attacks de cross-service token replay.
_JWT_ISS = 'skilled-erp-api'
_JWT_AUD = 'skilled-erp-spa'


def _jwt_secret() -> str:
    return current_app.config['SECRET_KEY']


# ── Helpers de tokens ───────────────────────────────────────────────────────

def _encode_access_token(user: User) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        'sub': str(user.id),
        'username': user.username,
        'role': user.role,
        'pv': user.password_version or 1,
        'iat': int(now.timestamp()),
        'exp': int((now + timedelta(minutes=ACCESS_TOKEN_LIFETIME_MINUTES)).timestamp()),
        'type': 'access',
        'iss': _JWT_ISS,
        'aud': _JWT_AUD,
    }
    return jwt.encode(payload, _jwt_secret(), algorithm='HS256')


def _encode_pre_2fa_token(user: User) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        'sub': str(user.id),
        'pv': user.password_version or 1,
        'iat': int(now.timestamp()),
        'exp': int((now + timedelta(seconds=PRE_2FA_LIFETIME_SECONDS)).timestamp()),
        'type': 'pre_2fa',
        'iss': _JWT_ISS,
        'aud': _JWT_AUD,
    }
    return jwt.encode(payload, _jwt_secret(), algorithm='HS256')


# Anti-replay de códigos TOTP: pyotp.verify(valid_window=1) acepta el mismo
# código durante ~60s. Si un atacante hace shoulder-surfing o intercepta el
# código en ese ventana, puede usarlo otra vez. Esta función marca el código
# como "ya usado" en Redis con TTL de 90s (cubre la ventana +1 con margen).
def _totp_code_already_used(user_id: int, code: str) -> bool:
    from app.extensions import get_redis
    import hashlib
    r = get_redis()
    if not r:
        # Sin Redis no podemos detectar replay — degradamos a comportamiento
        # original. El lockout escalado sigue protegiendo contra brute-force.
        return False
    # Hasheamos para no guardar el código en claro en Redis
    h = hashlib.sha256(f'{user_id}:{code}'.encode()).hexdigest()
    key = f'totp_used:{h}'
    # SET NX EX: solo crea la key si no existe, con TTL 90s. Devuelve None si
    # ya existía → código ya usado.
    was_set = r.set(key, '1', nx=True, ex=90)
    return not was_set


def _decode_token(token: str, expected_type: str) -> dict | None:
    try:
        # `audience` y `issuer` validados aquí. PyJWT rechaza el token si no
        # coinciden — bloquea replay de tokens emitidos por otra app que
        # comparta el SECRET_KEY (escenarios de copy-paste de config entre
        # servicios). Tokens viejos sin `iss`/`aud` siguen siendo aceptados
        # mientras dura el grace period (los rechaza naturalmente al expirar
        # a los 20 min / 5 min para el pre_2fa).
        payload = jwt.decode(
            token,
            _jwt_secret(),
            algorithms=['HS256'],
            audience=_JWT_AUD,
            issuer=_JWT_ISS,
            options={'require': ['exp', 'iat', 'sub', 'type']},
        )
    except jwt.PyJWTError:
        # Fallback: aceptar tokens viejos sin iss/aud durante la transición.
        # Quitar este bloque después de que el TTL de access token (20 min)
        # haya pasado tras el deploy del fix.
        try:
            payload = jwt.decode(
                token,
                _jwt_secret(),
                algorithms=['HS256'],
                options={'require': ['exp', 'iat', 'sub', 'type']},
            )
        except jwt.PyJWTError:
            return None
    if payload.get('type') != expected_type:
        return None
    return payload


def _issue_refresh_token(user_id: int) -> str:
    raw = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_LIFETIME_DAYS)
    tok = RefreshToken(token_hash=_hash_token(raw), user_id=user_id, expires_at=expires)
    db.session.add(tok)
    db.session.commit()
    return raw


def _cookie_secure() -> bool:
    # Marca Secure solo cuando la conexión es HTTPS. Con ProxyFix activo,
    # request.is_secure respeta X-Forwarded-Proto (Cloudflare Tunnel) en prod
    # y devuelve False en el dev server HTTP local — evita que la cookie se
    # marque como Secure y desaparezca para el SPA en localhost.
    # SameSite=None EXIGE Secure=True; si se configuró None, forzamos Secure.
    if _cookie_samesite().lower() == 'none':
        return True
    return request.is_secure


def _cookie_samesite() -> str:
    """SameSite del refresh cookie.

    - 'Lax' (default): suficiente cuando frontend y API comparten dominio
      (same-site) — p.ej. ambos detrás de Cloudflare bajo *.skilled.com.mx.
    - 'None': obligatorio cuando el SPA vive en otro origen (Vercel) y manda
      la cookie cross-site al backend. Requiere Secure=True (HTTPS).
    """
    import os
    return os.environ.get('RT_COOKIE_SAMESITE', 'Lax')


def _set_rt_cookie(response, raw: str) -> None:
    response.set_cookie(
        _RT_COOKIE,
        raw,
        max_age=REFRESH_TOKEN_LIFETIME_DAYS * 86400,
        httponly=True,
        secure=_cookie_secure(),
        samesite=_cookie_samesite(),
        path='/',
    )


def _clear_rt_cookie(response) -> None:
    response.delete_cookie(_RT_COOKIE, path='/', secure=_cookie_secure(), samesite=_cookie_samesite())


# ── HIGH-02 fix: protección CSRF para endpoints que dependen SOLO de cookie ──
# /refresh y /logout leen el refresh token de la cookie (HttpOnly). En
# despliegue cross-site (Vercel + RT_COOKIE_SAMESITE=None) el cookie viaja en
# requests cross-origin, así que cualquier sitio podría hacer un form-POST y
# desloguear/refresh-rotar al usuario.
#
# Mitigación: exigir un header custom (`X-Requested-With: XMLHttpRequest`).
# Los browsers tratan ese header como "no-trivial" y disparan preflight CORS,
# que el backend rechaza si el Origin no está en la whitelist. Un <form> HTML
# vanilla no puede setear headers custom, así que el ataque CSRF cross-site
# queda bloqueado.
def _csrf_protected_cookie_endpoint():
    """Devuelve un response 403 si la request no incluye el header anti-CSRF.
    Llamar al inicio de endpoints que autentican por cookie (no Authorization)."""
    xrw = request.headers.get('X-Requested-With', '').strip().lower()
    if xrw != 'xmlhttprequest':
        return jsonify({'error': 'Header X-Requested-With requerido'}), 403
    return None


def _user_to_dict(user: User) -> dict:
    """Vista completa del usuario — incluye role, totp_enabled, last_seen.
    Solo se debe devolver al PROPIO usuario (en /me) o a un admin."""
    t = getattr(user, 'trabajador', None)
    return {
        'id': user.id,
        'username': user.username,
        'role': user.role,
        'full_name': user.full_name,
        'area': user.area,
        'position': user.position,
        'factory': user.factory,
        'contact_info': user.contact_info,
        'profile_pic': user.profile_pic,
        'totp_enabled': bool(user.totp_secret),
        'last_seen': user.last_seen.isoformat() if user.last_seen else None,
        'trabajador_id': user.trabajador_id,
        'trabajador_no_empleado': t.no_empleado if t else None,
    }


def _user_to_dict_public(user: User) -> dict:
    """Vista pública del usuario — para el directorio interno.
    HIGH-01 fix: NO devuelve `role`, `totp_enabled` ni `last_seen` (reconocimiento
    para atacantes que buscan admins sin 2FA o ventanas de actividad).
    """
    t = getattr(user, 'trabajador', None)
    return {
        'id': user.id,
        'username': user.username,
        'full_name': user.full_name,
        'area': user.area,
        'position': user.position,
        'factory': user.factory,
        'contact_info': user.contact_info,
        'profile_pic': user.profile_pic,
        'trabajador_id': user.trabajador_id,
        'trabajador_no_empleado': t.no_empleado if t else None,
    }


def _is_admin_user(user: User) -> bool:
    return user.role in ('admin', 'super_admin')


# ── Decorador para endpoints protegidos por JWT ─────────────────────────────

def jwt_required(fn):
    from functools import wraps

    @wraps(fn)
    def wrapper(*args, **kwargs):
        auth = request.headers.get('Authorization', '')
        if not auth.startswith('Bearer '):
            return jsonify({'error': 'Token requerido'}), 401
        token = auth.split(' ', 1)[1].strip()
        payload = _decode_token(token, 'access')
        if not payload:
            return jsonify({'error': 'Token inválido o expirado'}), 401

        user = User.query.get(int(payload['sub']))
        if not user:
            return jsonify({'error': 'Usuario no encontrado'}), 401
        # Invalidar JWT si la contraseña cambió desde que se emitió.
        if (user.password_version or 1) != payload.get('pv', 1):
            return jsonify({'error': 'Sesión inválida, vuelve a iniciar sesión'}), 401
        # Mantener `last_seen` fresco para el indicador "en línea". Throttled
        # a 60s para no commitear en cada request. La conexión Socket.IO y su
        # handler 'heartbeat' cubren el caso de lectura pura (sin requests).
        try:
            now = datetime.now()
            if not user.last_seen or (now - user.last_seen).total_seconds() > 60:
                user.last_seen = now
                db.session.commit()
        except Exception:
            try:
                db.session.rollback()
            except Exception:
                pass
        g._jwt_user = user
        return fn(*args, **kwargs)

    return wrapper


# ── Endpoints ───────────────────────────────────────────────────────────────

@bp.route('/login', methods=['POST'])
@limiter.limit("4 per minute")
@limiter.limit(
    "8 per minute",
    key_func=lambda: f"api_login_user:{((request.get_json(silent=True) or {}).get('username') or '').lower().strip()}",
)
def api_login():
    data = request.get_json(silent=True) or {}
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''
    remember = bool(data.get('remember'))

    if not username or not password:
        return jsonify({'error': 'Usuario y contraseña son obligatorios'}), 400

    lockout_ttl = _check_lockout(username)
    if lockout_ttl:
        return jsonify({
            'error': f'Cuenta bloqueada por demasiados intentos. Intenta en {_format_ttl(lockout_ttl)}.',
        }), 423

    u = User.query.filter_by(username=username).first()
    if u:
        password_ok = check_password_hash(u.password_hash, password)
    else:
        check_password_hash(_DUMMY_PW_HASH, password)
        password_ok = False

    if not password_ok:
        _register_login_failure(username)
        post_fail_ttl = _check_lockout(username)
        if post_fail_ttl:
            return jsonify({
                'error': f'Cuenta bloqueada por demasiados intentos. Intenta en {_format_ttl(post_fail_ttl)}.',
            }), 423
        from app.extensions import get_real_client_ip_flask
        if u:
            g._jwt_user = u
        log_action(f"API login fallido para '{username[:80]}' desde IP {get_real_client_ip_flask()}")
        return jsonify({'error': 'Credenciales incorrectas'}), 401

    _clear_login_failures(username)

    if u.totp_secret:
        return jsonify({
            'requires2fa': True,
            'stepToken': _encode_pre_2fa_token(u),
        })

    token = _encode_access_token(u)
    u.last_seen = datetime.now()
    db.session.commit()
    g._jwt_user = u
    log_action("API login exitoso")

    resp = jsonify({'token': token, 'user': _user_to_dict(u)})
    if remember:
        _set_rt_cookie(resp, _issue_refresh_token(u.id))
    return resp


@bp.route('/verify-2fa', methods=['POST'])
@limiter.limit("4 per minute")
def api_verify_2fa():
    data = request.get_json(silent=True) or {}
    step_token = data.get('stepToken') or ''
    code = (data.get('code') or '').strip()

    payload = _decode_token(step_token, 'pre_2fa')
    if not payload:
        return jsonify({'error': 'Sesión 2FA expirada. Inicia sesión de nuevo.'}), 401

    user = User.query.get(int(payload['sub']))
    if not user or not user.totp_secret:
        return jsonify({'error': 'Sesión inválida. Inicia sesión de nuevo.'}), 401

    if (user.password_version or 1) != payload.get('pv', 1):
        return jsonify({'error': 'Tu contraseña cambió. Inicia sesión de nuevo.'}), 401

    g._jwt_user = user

    if not pyotp.TOTP(user.totp_secret).verify(code, valid_window=1):
        log_action(f"API 2FA fallido para {user.username}")
        return jsonify({'error': 'Código incorrecto'}), 401

    # Anti-replay: si este mismo código fue usado en los últimos 90s, rechazar.
    # Cubre escenarios de shoulder-surfing donde alguien ve el código del SMS
    # /authenticator y trata de reusarlo dentro de la misma ventana TOTP.
    if _totp_code_already_used(user.id, code):
        log_action(f"API 2FA replay bloqueado para {user.username}")
        return jsonify({'error': 'Este código ya fue usado. Espera al siguiente.'}), 401

    token = _encode_access_token(user)
    user.last_seen = datetime.now()
    db.session.commit()
    log_action(f"API login 2FA exitoso para {user.username}")

    resp = jsonify({'token': token, 'user': _user_to_dict(user)})
    # En el flujo 2FA emitimos refresh token siempre para no obligar a reautenticar
    # cada 20 min cuando hay TOTP activo (UX). El SPA puede limpiar la cookie con logout.
    _set_rt_cookie(resp, _issue_refresh_token(user.id))
    return resp


@bp.route('/refresh', methods=['POST'])
@limiter.limit("30 per minute")
def api_refresh():
    # HIGH-02: rechazar requests que no vengan de XHR/fetch del SPA.
    csrf_err = _csrf_protected_cookie_endpoint()
    if csrf_err:
        return csrf_err
    raw_rt = request.cookies.get(_RT_COOKIE)
    if not raw_rt:
        return jsonify({'error': 'Refresh token no presente'}), 401

    now = datetime.now(timezone.utc)
    h = _hash_token(raw_rt)
    tok = RefreshToken.query.filter_by(token_hash=h, revoked=False).first()
    if not tok:
        resp = jsonify({'error': 'Refresh token inválido'})
        _clear_rt_cookie(resp)
        return resp, 401

    exp = tok.expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if exp <= now:
        tok.revoked = True
        db.session.commit()
        resp = jsonify({'error': 'Refresh token expirado'})
        _clear_rt_cookie(resp)
        return resp, 401

    user = tok.user
    if user is None:
        tok.revoked = True
        db.session.commit()
        resp = jsonify({'error': 'Usuario no encontrado'})
        _clear_rt_cookie(resp)
        return resp, 401

    # Rotar: revocar el actual y emitir uno nuevo (detección de replay).
    tok.revoked = True
    new_raw = secrets.token_urlsafe(32)
    new_tok = RefreshToken(
        token_hash=_hash_token(new_raw),
        user_id=user.id,
        expires_at=now + timedelta(days=REFRESH_TOKEN_LIFETIME_DAYS),
    )
    db.session.add(new_tok)
    # Housekeeping: limpiar tokens viejos del usuario.
    RefreshToken.query.filter(
        RefreshToken.user_id == user.id,
        (RefreshToken.revoked == True) | (RefreshToken.expires_at <= now),  # noqa: E712
        RefreshToken.id != new_tok.id,
    ).delete(synchronize_session=False)
    db.session.commit()

    access = _encode_access_token(user)
    resp = jsonify({'token': access, 'user': _user_to_dict(user)})
    _set_rt_cookie(resp, new_raw)
    return resp


@bp.route('/logout', methods=['POST'])
def api_logout():
    # HIGH-02: bloquear logout CSRF cross-site (especialmente con SameSite=None).
    csrf_err = _csrf_protected_cookie_endpoint()
    if csrf_err:
        return csrf_err
    raw_rt = request.cookies.get(_RT_COOKIE)
    if raw_rt:
        h = _hash_token(raw_rt)
        tok = RefreshToken.query.filter_by(token_hash=h).first()
        if tok and not tok.revoked:
            tok.revoked = True
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()
        if tok:
            user = User.query.get(tok.user_id)
            if user:
                g._jwt_user = user

    log_action("API logout")
    resp = jsonify({'ok': True})
    _clear_rt_cookie(resp)
    return resp


@bp.route('/me', methods=['GET'])
@jwt_required
@limiter.limit("60 per minute")
def api_me():
    return jsonify(_user_to_dict(g._jwt_user))


# ── Gestión de sesiones propias (refresh tokens activos) ────────────────────

@bp.route('/sessions', methods=['GET'])
@jwt_required
def api_list_sessions():
    """Lista las sesiones activas del propio usuario (cookies de refresh).

    Útil para que el usuario detecte logins ajenos y los revoque desde su
    pantalla de Perfil. Solo devuelve metadatos — el hash del token nunca
    sale del servidor.
    """
    user = g._jwt_user
    now = datetime.now(timezone.utc)
    tokens = (
        RefreshToken.query
        .filter_by(user_id=user.id, revoked=False)
        .order_by(RefreshToken.created_at.desc())
        .all()
    )
    out = []
    for t in tokens:
        exp = t.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp <= now:
            continue
        out.append({
            'id': t.id,
            'created_at': t.created_at.isoformat() if t.created_at else None,
            'expires_at': exp.isoformat(),
        })
    return jsonify(out)


@bp.route('/sessions/<int:session_id>', methods=['DELETE'])
@jwt_required
@limiter.limit("20 per minute")
def api_revoke_session(session_id: int):
    """Revoca una sesión específica del propio usuario."""
    user = g._jwt_user
    tok = RefreshToken.query.filter_by(id=session_id, user_id=user.id).first()
    if not tok:
        return jsonify({'error': 'Sesión no encontrada'}), 404
    if not tok.revoked:
        tok.revoked = True
        db.session.commit()
        log_action(f'Revocó sesión #{session_id} ({user.username})')
    return jsonify({'ok': True})


@bp.route('/sessions/all', methods=['DELETE'])
@jwt_required
@limiter.limit("10 per minute")
def api_revoke_all_sessions():
    """Revoca TODAS las sesiones del propio usuario (pánico).

    No invalida el access token actual (corto, ≤20 min), pero el usuario no
    podrá refrescar la sesión cuando expire — fuerza re-login en todos los
    dispositivos al cabo de unos minutos. Para invalidar también el JWT
    actual usa /change-password (incrementa password_version).
    """
    user = g._jwt_user
    RefreshToken.query.filter_by(user_id=user.id, revoked=False).update({'revoked': True})
    db.session.commit()
    log_action(f'Revocó todas las sesiones ({user.username})')
    return jsonify({'ok': True})


# ── Directorio / perfiles ajenos ────────────────────────────────────────────

@bp.route('/users', methods=['GET'])
@jwt_required
def api_list_users():
    """Lista de usuarios para el directorio.

    HIGH-01 fix: admin/super_admin reciben datos completos (role, 2FA, last_seen);
    el resto recibe vista pública (sin role / 2FA / last_seen) — esto evita que
    coordinadores, inventario o solicitantes_material enumeren admins sin 2FA
    para targetear con brute-force o ataquen ventanas de actividad.
    """
    from app.models import User
    serializer = _user_to_dict if _is_admin_user(g._jwt_user) else _user_to_dict_public
    users = User.query.all()
    return jsonify([serializer(u) for u in users])


@bp.route('/users/<int:user_id>', methods=['GET'])
@jwt_required
def api_get_user(user_id: int):
    """Detalle de un usuario para ver su perfil desde el directorio.

    HIGH-01 fix: si el solicitante es admin o es el propio usuario, ve todo.
    Otros usuarios reciben la vista pública (sin role/2FA/last_seen).
    """
    user = User.query.get(user_id)
    if not user:
        return jsonify({'error': 'Usuario no encontrado'}), 404
    is_self = (user.id == g._jwt_user.id)
    serializer = _user_to_dict if (is_self or _is_admin_user(g._jwt_user)) else _user_to_dict_public
    return jsonify(serializer(user))


@bp.route('/users/<int:user_id>/foto', methods=['GET'])
@jwt_required
def api_get_user_foto(user_id: int):
    """Sirve la foto de perfil con autenticación JWT (Bearer en Authorization)."""
    import os
    from flask import send_from_directory
    user = User.query.get(user_id)
    if not user or not user.profile_pic or user.profile_pic == 'default.png':
        return jsonify({'error': 'Sin foto'}), 404
    filename = user.profile_pic
    # Anti path-traversal: profile_pic se genera siempre internamente con secure_filename
    if '/' in filename or '\\' in filename or '..' in filename:
        return jsonify({'error': 'Ruta inválida'}), 400
    folder = current_app.config['UPLOAD_FOLDER']
    full = os.path.join(folder, filename)
    if not os.path.exists(full):
        return jsonify({'error': 'Foto no encontrada en disco'}), 404
    return send_from_directory(folder, filename)


# ── Perfil propio ───────────────────────────────────────────────────────────

@bp.route('/profile', methods=['POST'])
@jwt_required
@limiter.limit('15 per minute')
def api_update_profile():
    """Actualiza datos del perfil propio (multipart para soportar la foto)."""
    import os
    import uuid
    from werkzeug.utils import secure_filename
    from app.utils import allowed_image_file

    user = g._jwt_user
    data = request.form if request.form else (request.get_json(silent=True) or {})

    # Aceptar tanto snake_case como camelCase (compatibilidad con frontends mixtos)
    def field(name_snake, name_camel=None):
        return data.get(name_snake) or (data.get(name_camel) if name_camel else None)

    full_name = field('full_name', 'fullName')
    area = field('area')
    position = field('position')
    factory = field('factory')
    contact_info = field('contact_info', 'contactInfo')

    if full_name is not None: user.full_name = full_name
    if area is not None: user.area = area
    if position is not None: user.position = position
    if factory is not None: user.factory = factory
    if contact_info is not None: user.contact_info = contact_info

    foto = request.files.get('profile_pic') or request.files.get('profilePic')
    if foto and foto.filename:
        if not allowed_image_file(foto):
            return jsonify({'error': 'Foto rechazada: solo se permiten imágenes JPG o PNG reales.'}), 400
        ext = foto.filename.rsplit('.', 1)[-1].lower() if '.' in foto.filename else 'png'
        unique_filename = f"profile_{user.id}_{uuid.uuid4().hex[:8]}.{ext}"
        upload_path = os.path.join(current_app.config['UPLOAD_FOLDER'], unique_filename)
        foto.save(upload_path)
        # Borrar foto vieja (si no es default) para no llenar disco
        if user.profile_pic and user.profile_pic != 'default.png':
            old = os.path.join(current_app.config['UPLOAD_FOLDER'], user.profile_pic)
            if os.path.exists(old):
                try:
                    os.remove(old)
                except Exception as e:
                    current_app.logger.warning('No se pudo eliminar foto vieja: %s', e)
        user.profile_pic = unique_filename

    try:
        db.session.commit()
        log_action(f"Actualizó su perfil ({user.username})")
        return jsonify(_user_to_dict(user))
    except Exception as e:
        db.session.rollback()
        current_app.logger.error('Error actualizando perfil API: %s', e)
        return jsonify({'error': 'Error al actualizar el perfil'}), 500


@bp.route('/change-password/<int:user_id>', methods=['POST'])
@jwt_required
@limiter.limit('10 per minute')
def api_change_own_password(user_id: int):
    """Cambia la contraseña propia. Requiere current_password."""
    from werkzeug.security import generate_password_hash
    from app.utils import is_strong_password
    user = g._jwt_user
    if user.id != user_id:
        return jsonify({'error': 'Solo puedes cambiar tu propia contraseña'}), 403

    data = request.get_json(silent=True) or {}
    current_password = data.get('current_password') or data.get('currentPassword') or ''
    new_password = data.get('new_password') or data.get('newPassword') or ''

    if not current_password or not new_password:
        return jsonify({'error': 'Contraseña actual y nueva son obligatorias'}), 400
    if not check_password_hash(user.password_hash, current_password):
        return jsonify({'error': 'La contraseña actual es incorrecta'}), 401
    if not is_strong_password(new_password):
        return jsonify({
            'error': 'La contraseña nueva es débil. Mínimo 8 caracteres con mayúsculas, minúsculas, números y símbolos.',
        }), 400
    if current_password == new_password:
        return jsonify({'error': 'La nueva contraseña debe ser diferente a la actual'}), 400

    try:
        user.password_hash = generate_password_hash(new_password)
        user.password_version = (user.password_version or 1) + 1
        # SEGURIDAD: NO desactivar 2FA al cambiar la contraseña propia. El TOTP
        # es un factor independiente — el usuario que rota su password no debe
        # perder el segundo factor (UX y postura defensiva). Para quitar el
        # TOTP el usuario debe usar el flujo dedicado de "Desactivar 2FA"
        # (requiere current_password).
        RefreshToken.query.filter_by(user_id=user.id, revoked=False).update({'revoked': True})
        db.session.commit()
        log_action('Cambio de contraseña propio (API)')
        return jsonify({'ok': True})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error('Error cambiando contraseña API: %s', e)
        return jsonify({'error': 'Error al actualizar la contraseña'}), 500


# ── 2FA setup (flujo en dos pasos: pedir secret → confirmar código) ────────

@bp.route('/setup-2fa', methods=['POST'])
@jwt_required
@limiter.limit('4 per minute')
def api_setup_2fa():
    """Paso 1 del setup 2FA. Requiere current_password (reauth).
    Devuelve un secret y el QR en base64 para que el usuario lo escanee."""
    import base64
    import io as _io
    import qrcode

    user = g._jwt_user
    data = request.get_json(silent=True) or {}
    current_password = data.get('current_password') or data.get('currentPassword') or ''
    if not current_password:
        return jsonify({'error': 'Contraseña actual requerida'}), 400
    if not check_password_hash(user.password_hash, current_password):
        return jsonify({'error': 'La contraseña actual es incorrecta'}), 401

    secret = pyotp.random_base32()
    totp_uri = pyotp.totp.TOTP(secret).provisioning_uri(
        name=user.username,
        issuer_name='SistemaNominas',
    )
    img = qrcode.make(totp_uri)
    buffered = _io.BytesIO()
    img.save(buffered, format='PNG')
    qr_b64 = base64.b64encode(buffered.getvalue()).decode('utf-8')
    return jsonify({'secret': secret, 'qr': qr_b64})


@bp.route('/confirm-2fa', methods=['POST'])
@jwt_required
@limiter.limit('6 per minute')
def api_confirm_2fa():
    """Paso 2: valida el código TOTP contra el secret y lo persiste.

    Si el usuario YA tiene 2FA activo, además exige `current_2fa_code` válido
    contra el secret existente. Esto bloquea el ataque donde un atacante con
    sesión activa + contraseña cambia el dispositivo TOTP sin tener el actual.
    """
    user = g._jwt_user
    data = request.get_json(silent=True) or {}
    current_password = data.get('current_password') or data.get('currentPassword') or ''
    secret = data.get('secret') or ''
    code = (data.get('code') or '').strip()
    current_2fa_code = (data.get('current_2fa_code') or data.get('currentTwoFaCode') or '').strip()

    if not current_password or not secret or not code:
        return jsonify({'error': 'Faltan datos (contraseña, secret o código)'}), 400
    if not check_password_hash(user.password_hash, current_password):
        return jsonify({'error': 'La contraseña actual es incorrecta'}), 401

    # Re-keying: si ya hay 2FA activo, exigir el código actual del dispositivo
    # registrado antes de aceptar el nuevo secret.
    if user.totp_secret:
        if not current_2fa_code:
            return jsonify({
                'error': 'Ya tienes 2FA activo. Para cambiar de dispositivo necesitas el código actual.',
                'requires_current_2fa_code': True,
            }), 401
        if not pyotp.TOTP(user.totp_secret).verify(current_2fa_code, valid_window=1):
            log_action(f"2FA re-key API: código actual incorrecto para {user.username}")
            return jsonify({'error': 'Código 2FA actual incorrecto'}), 401
        if _totp_code_already_used(user.id, current_2fa_code):
            return jsonify({'error': 'Ese código ya fue usado. Espera al siguiente.'}), 401

    if not pyotp.TOTP(secret).verify(code, valid_window=1):
        log_action(f"2FA setup API: código incorrecto para {user.username}")
        return jsonify({'error': 'Código incorrecto'}), 400

    try:
        user.totp_secret = secret
        db.session.commit()
        log_action(f"2FA activado vía API para {user.username}")
        return jsonify({'ok': True})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error('Error guardando totp_secret: %s', e)
        return jsonify({'error': 'Error al activar 2FA'}), 500


@bp.route('/disable-2fa', methods=['POST'])
@jwt_required
@limiter.limit('4 per minute')
def api_disable_2fa():
    """Desactiva 2FA del propio usuario. Exige:
      - current_password (re-auth contra hijack de sesión).
      - code (TOTP actual válido — prueba que el usuario tiene el dispositivo).
    Doble requisito a propósito: si solo pidiéramos contraseña, un phisher con
    creds podría apagar el 2FA y luego loguear sin segundo factor."""
    user = g._jwt_user
    data = request.get_json(silent=True) or {}
    current_password = data.get('current_password') or data.get('currentPassword') or ''
    code = (data.get('code') or '').strip()

    if not user.totp_secret:
        return jsonify({'error': '2FA no está activo en esta cuenta'}), 400
    if not current_password or not code:
        return jsonify({'error': 'Contraseña actual y código 2FA son obligatorios'}), 400
    if not check_password_hash(user.password_hash, current_password):
        return jsonify({'error': 'La contraseña actual es incorrecta'}), 401
    if not pyotp.TOTP(user.totp_secret).verify(code, valid_window=1):
        log_action(f"2FA disable: código incorrecto para {user.username}")
        return jsonify({'error': 'Código 2FA incorrecto'}), 401
    if _totp_code_already_used(user.id, code):
        return jsonify({'error': 'Ese código ya fue usado. Espera al siguiente.'}), 401

    try:
        user.totp_secret = None
        db.session.commit()
        log_action(f"2FA desactivado vía API para {user.username}")
        return jsonify({'ok': True})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error('Error desactivando 2FA: %s', e)
        return jsonify({'error': 'Error al desactivar 2FA'}), 500
