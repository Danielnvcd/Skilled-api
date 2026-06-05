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
from app.models import RefreshToken, TwoFactorBackupCode, User
from app.routes.auth import (
    _DUMMY_PW_HASH,
    _check_lockout,
    _clear_login_failures,
    _format_ttl,
    _hash_token,
    _LOGIN_FAILS_WINDOW,
    _LOGIN_FAILS_THRESHOLD,
    _LOCKOUT_DURATIONS,
    _LOCKOUT_LEVEL_TTL,
    _register_login_failure,
)
from app.utils import log_action

bp = Blueprint('api_auth', __name__, url_prefix='/api/auth')


@bp.after_request
def _no_store_on_auth_responses(response):
    """Marca todas las respuestas del blueprint como no-cacheables.

    Cualquier respuesta contiene tokens, datos de usuario u otra info sensible
    que NO debe quedar en cache de browser, proxies intermedios o service
    workers. Sin esto, un volver-atrás del browser podría re-mostrar tokens.
    """
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

# ── Constantes JWT ──────────────────────────────────────────────────────────
ACCESS_TOKEN_LIFETIME_MINUTES = 20      # mismo TTL que la sesión Flask clásica
PRE_2FA_LIFETIME_SECONDS = 300          # ventana para completar 2FA tras password
_RT_COOKIE = 'rt_api'                   # cookie del refresh token para el SPA

# ── Tope de longitud de inputs sensibles ────────────────────────────────────
# Evita DoS por hash bcrypt de payloads gigantes (cada check_password_hash es
# CPU-intensivo; un atacante con cuerpo de varios MB satura workers). También
# bloquea ataques de truncamiento donde el usuario aprovecha que werkzeug
# trunca silenciosamente passwords muy largos antes de hashear.
_MAX_USERNAME_LEN = 80
_MAX_PASSWORD_LEN = 256
_MAX_TOTP_CODE_LEN = 8  # códigos TOTP siempre son 6 dígitos, dejamos margen
_MAX_BACKUP_CODE_LEN = 32  # códigos de respaldo: 14 chars con guiones, margen amplio
_BACKUP_CODES_COUNT = 10   # cantidad por generación
_BACKUP_CODES_LOW_THRESHOLD = 3  # avisar al usuario cuando le quedan estos o menos

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
        # `jti` único permite revocar este JWT específico al instante via
        # blacklist en Redis (ver _is_jti_revoked / _revoke_jti). Sin esto,
        # un JWT robado o un logout no se podía invalidar hasta su exp.
        'jti': secrets.token_urlsafe(16),
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


# ── Blacklist de JWT por jti ────────────────────────────────────────────────
# Permite revocar un access token al instante sin esperar a que expire (≤20m).
# Casos de uso:
#   - Logout: revocar el jti del JWT que viene en el header Authorization.
#   - Cuenta comprometida: admin revoca jti específicos.
#
# Implementación: SET con TTL == segundos restantes al `exp` del token. Después
# Redis lo limpia solo, sin acumulación.
#
# Si no hay Redis, no podemos blacklisstear, así que /logout solo limpia
# refresh tokens y deja el JWT vivo hasta su exp (comportamiento legacy).

def _revoke_jti(jti: str, exp_ts: int) -> None:
    """Marca un jti como revocado hasta su exp."""
    from app.extensions import get_redis
    r = get_redis()
    if not r:
        return
    ttl = max(1, int(exp_ts - datetime.now(timezone.utc).timestamp()))
    r.setex(f'jwt_revoked:{jti}', ttl, '1')


# ── Backup codes para 2FA ───────────────────────────────────────────────────
# Permiten recuperar acceso cuando el usuario pierde el dispositivo TOTP
# (teléfono robado, app desinstalada). Cada código es one-shot; el usuario
# guarda los 10 en lugar seguro (impresos / password manager).
#
# Formato: 14 chars "XXXX-XXXX-XXXX" en alfabeto base32 limpio (sin 0/O/1/I).
# Equivalente a ~60 bits de entropía — más que suficiente, no brute-forceable
# en la ventana de TTL del stepToken (5 min).
#
# Storage: SHA-256 hex del código (no bcrypt — los códigos ya son aleatorios
# largos, bcrypt no aporta y duplica costo CPU). Plain text se muestra UNA SOLA
# VEZ al generarlos.

# Alfabeto sin caracteres ambiguos para que el usuario los lea bien al copiar
# desde papel impreso.
_BACKUP_CODE_ALPHABET = '23456789ABCDEFGHJKLMNPQRSTUVWXYZ'  # sin 0/O/1/I


def _hash_backup_code(code: str) -> str:
    """SHA-256 hex del código (normalizado a uppercase, sin guiones/espacios)."""
    import hashlib as _h
    normalized = code.upper().replace('-', '').replace(' ', '')
    return _h.sha256(normalized.encode()).hexdigest()


def _format_backup_code(raw_chars: str) -> str:
    """Inserta guiones cada 4 chars para legibilidad: ABCD-EFGH-JKLM."""
    return f'{raw_chars[:4]}-{raw_chars[4:8]}-{raw_chars[8:12]}'


def _generate_backup_codes(user_id: int) -> list[str]:
    """Genera _BACKUP_CODES_COUNT códigos, los persiste hasheados, devuelve plaintext.

    Invalida cualquier código anterior (consumido o no) del mismo usuario:
    el usuario ve una lista nueva y debe descartar la vieja.
    """
    # Borrar todos los existentes (consumidos o no): regenerar siempre
    # invalida los anteriores. Patrón estándar (Google, GitHub).
    TwoFactorBackupCode.query.filter_by(user_id=user_id).delete()
    plaintexts = []
    for _ in range(_BACKUP_CODES_COUNT):
        raw = ''.join(secrets.choice(_BACKUP_CODE_ALPHABET) for _ in range(12))
        formatted = _format_backup_code(raw)
        plaintexts.append(formatted)
        db.session.add(TwoFactorBackupCode(
            user_id=user_id,
            code_hash=_hash_backup_code(formatted),
        ))
    db.session.commit()
    return plaintexts


def _count_active_backup_codes(user_id: int) -> int:
    return TwoFactorBackupCode.query.filter_by(
        user_id=user_id, consumed_at=None,
    ).count()


def _try_consume_backup_code(user_id: int, code: str) -> bool:
    """Intenta consumir un código de respaldo. True si era válido (y queda marcado)."""
    if not code:
        return False
    h = _hash_backup_code(code)
    tok = TwoFactorBackupCode.query.filter_by(
        user_id=user_id, code_hash=h, consumed_at=None,
    ).first()
    if not tok:
        return False
    tok.consumed_at = datetime.now(timezone.utc)
    db.session.commit()
    return True


# ── Notificación de login desde device nuevo ────────────────────────────────
# Cuando un usuario inicia sesión con éxito desde una combinación (IP + UA) que
# nunca habíamos visto, le mandamos una notificación in-app. Permite que detecte
# logins ajenos rápido y dispare /sessions/all si no los reconoce.
#
# Tracking: hash de IP+UA por usuario en Redis con TTL largo (90 días). Si el
# hash existe, ya conocemos el device. Si no, es nuevo → notif + registrar.
#
# Sin Redis no podemos rastrear → no mandamos notifs (falla benigno: el
# /sessions sigue mostrando la sesión activa con su UA/IP).


def _device_fingerprint(ua: str, ip: str) -> str:
    """Hash corto para identificar combos UA+IP. No es PII reversible."""
    import hashlib as _h
    src = f'{ua or "_"}|{ip or "_"}'
    return _h.sha256(src.encode()).hexdigest()[:24]


def _is_known_device(user_id: int, fp: str) -> bool:
    from app.extensions import get_redis
    r = get_redis()
    if not r:
        # Sin Redis no podemos saber. Asumir "conocido" para NO spammear con
        # notifs de "nuevo device" en cada login.
        return True
    key = f'known_device:{user_id}:{fp}'
    # SET NX: si no existía, marcamos como conocido y devolvemos False (=era nuevo).
    # Si existía, devolvemos True (=ya conocido).
    was_new = r.set(key, '1', nx=True, ex=90 * 86400)
    return not was_new


def _notify_new_device_login(user_id: int, ip: str, ua: str) -> None:
    """Si el (user_id, IP+UA) no estaba registrado, manda notif al usuario."""
    fp = _device_fingerprint(ua, ip)
    if _is_known_device(user_id, fp):
        return
    try:
        from app.models import Notificacion
        from app.realtime import emit_to_user
        # UA suele tener formato verboso "Mozilla/5.0 (Windows NT...)". Capeamos.
        ua_short = (ua or 'desconocido')[:120]
        ip_short = (ip or 'desconocida')[:45]
        notif = Notificacion(
            usuario_id=user_id,
            tipo='LOGIN_NUEVO_DEVICE',
            titulo='Nuevo inicio de sesión',
            mensaje=(
                f'Hubo un inicio de sesión nuevo desde {ip_short}. '
                f'Si no fuiste tú, ve a Mi Perfil → Sesiones y revoca todas.'
            ),
            url='/perfil',
        )
        db.session.add(notif)
        db.session.commit()
        try:
            emit_to_user(user_id, 'notif:new', {'id': notif.id, 'tipo': notif.tipo})
        except Exception:
            pass
    except Exception as e:
        try:
            db.session.rollback()
        except Exception:
            pass
        current_app.logger.warning('No se pudo crear notif LOGIN_NUEVO_DEVICE: %s', e)


# ── Lockout escalado para 2FA ───────────────────────────────────────────────
# El blueprint auth.py protege el password con lockout escalado (10m → 24h),
# pero el 2FA no lo tenía. Un atacante con la password real podría brute-forcear
# el código TOTP (6 dígitos = 1M combinaciones) en ventanas de 30s antes de
# que la pestaña expire.
#
# Aplicamos la misma infraestructura: mismo window/threshold/durations/level TTL,
# pero con keys distintas para que el contador no se cruce con el de password.
# Key dimension: user_id (sacado del stepToken pre_2fa). Si no podemos extraer
# el sub, no contamos — falla open para no DOSearse a sí mismo si Redis está down
# (el rate limit per-IP del endpoint sigue activo como red de seguridad).

def _twofa_lockout_key(user_id: int) -> str:        return f'twofa_lockout:{user_id}'
def _twofa_level_key(user_id: int) -> str:          return f'twofa_lockout_level:{user_id}'
def _twofa_fails_key(user_id: int) -> str:          return f'twofa_fails:{user_id}'


def _check_twofa_lockout(user_id: int):
    from app.extensions import get_redis
    r = get_redis()
    if not r:
        return None
    ttl = r.ttl(_twofa_lockout_key(user_id))
    return ttl if (ttl is not None and ttl > 0) else None


def _register_twofa_failure(user_id: int) -> None:
    from app.extensions import get_redis
    r = get_redis()
    if not r:
        return
    fkey = _twofa_fails_key(user_id)
    fails = r.incr(fkey)
    if fails == 1:
        r.expire(fkey, _LOGIN_FAILS_WINDOW)
    if fails >= _LOGIN_FAILS_THRESHOLD:
        lkey = _twofa_level_key(user_id)
        try:
            level = int(r.get(lkey) or 0)
        except (TypeError, ValueError):
            level = 0
        duration = _LOCKOUT_DURATIONS[min(level, len(_LOCKOUT_DURATIONS) - 1)]
        r.setex(_twofa_lockout_key(user_id), duration, '1')
        r.setex(lkey, _LOCKOUT_LEVEL_TTL, level + 1)
        r.delete(fkey)


def _clear_twofa_failures(user_id: int) -> None:
    from app.extensions import get_redis
    r = get_redis()
    if not r:
        return
    r.delete(_twofa_fails_key(user_id), _twofa_lockout_key(user_id), _twofa_level_key(user_id))


def _is_jti_revoked(jti: str) -> bool:
    from app.extensions import get_redis
    r = get_redis()
    if not r:
        return False
    return bool(r.get(f'jwt_revoked:{jti}'))


# ── Pinning del secret de setup-2fa ─────────────────────────────────────────
# El flujo de activación de 2FA en dos pasos (/setup-2fa devuelve secret →
# /confirm-2fa valida con código) era vulnerable: el cliente podía mandar un
# secret arbitrario en confirm-2fa y el backend lo persistía sin verificar
# que viniera de un setup-2fa previo. Atacante con sesión + current_password
# podía instalar un TOTP bajo su control.
#
# Fix: al hacer /setup-2fa, guardar el secret en Redis con TTL 10min por
# usuario. En /confirm-2fa, el secret que envíe el cliente DEBE coincidir
# con el pineado.
#
# Si no hay Redis, degradamos: no pineamos y no podemos validar. En ese caso
# /confirm-2fa rechaza el flujo y obliga a configurar Redis (más seguro que
# degradar silenciosamente al comportamiento vulnerable).

_SETUP_2FA_TTL = 600  # 10 min — mismo que el TTL del HTML clásico


def _setup_2fa_key(user_id: int) -> str:
    return f'totp_setup_secret:{user_id}'


def _pin_setup_2fa_secret(user_id: int, secret: str) -> bool:
    """Persiste el secret candidato. Devuelve True si se pudo pinear."""
    from app.extensions import get_redis
    r = get_redis()
    if not r:
        return False
    r.setex(_setup_2fa_key(user_id), _SETUP_2FA_TTL, secret)
    return True


def _peek_setup_2fa_secret(user_id: int) -> str | None:
    """Lee el secret pineado sin borrarlo. Para validar en confirm-2fa."""
    from app.extensions import get_redis
    r = get_redis()
    if not r:
        return None
    return r.get(_setup_2fa_key(user_id))


def _delete_setup_2fa_secret(user_id: int) -> None:
    """Borra el secret pineado. Se llama después de un confirm-2fa exitoso."""
    from app.extensions import get_redis
    r = get_redis()
    if not r:
        return
    r.delete(_setup_2fa_key(user_id))


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
    """Decodifica y valida un JWT emitido por este servicio.

    `audience` y `issuer` se validan estrictamente: bloquea replay de tokens
    emitidos por otra app que comparta el SECRET_KEY (escenarios de copy-paste
    de config entre servicios).
    """
    try:
        payload = jwt.decode(
            token,
            _jwt_secret(),
            algorithms=['HS256'],
            audience=_JWT_AUD,
            issuer=_JWT_ISS,
            options={'require': ['exp', 'iat', 'sub', 'type']},
        )
    except jwt.PyJWTError:
        return None
    if payload.get('type') != expected_type:
        return None
    return payload


# ── Metadata de session (UA + IP) por RT, en Redis ──────────────────────────
# Para que el usuario reconozca login extraño desde /api/auth/sessions, asociamos
# user-agent + IP a cada RT emitido. Lo guardamos en Redis con TTL == duración
# del RT para evitar migración de schema. Si Redis no está disponible, /sessions
# muestra los RTs sin metadata (degradación benigna).

import json as _json


def _store_rt_meta(rt_id: int) -> None:
    """Persiste UA + IP del request actual asociados al RT recién creado."""
    from app.extensions import get_redis, get_real_client_ip_flask
    r = get_redis()
    if not r:
        return
    ua = (request.headers.get('User-Agent') or '')[:200] if request else ''
    ip = get_real_client_ip_flask() if request else ''
    payload = _json.dumps({'ua': ua, 'ip': ip})
    # TTL = duración del RT + 1 día de margen
    ttl = REFRESH_TOKEN_LIFETIME_DAYS * 86400 + 86400
    r.setex(f'rt_meta:{rt_id}', ttl, payload)


def _load_rt_meta(rt_id: int) -> dict:
    from app.extensions import get_redis
    r = get_redis()
    if not r:
        return {}
    raw = r.get(f'rt_meta:{rt_id}')
    if not raw:
        return {}
    try:
        return _json.loads(raw)
    except Exception:
        return {}


# Tope de refresh tokens activos por usuario. Si supera el cap, revocamos
# los más antiguos (FIFO) antes de emitir uno nuevo. Esto evita acumulación
# si el usuario nunca cierra sesión y solo cierra el browser — los RTs
# olvidados son ventanas de ataque si se filtran via XSS / robo de cookie.
_MAX_ACTIVE_RT_PER_USER = 8

# Ventana de gracia para distinguir race (legítimo) de replay (ataque) en
# refresh. Si un RT fue revocado HACE MENOS de este tiempo y llega otro request
# con el mismo, asumimos que es una pestaña vecina que hizo refresh casi al
# mismo tiempo. Más allá de la ventana, asumimos compromiso → revocar familia.
_RT_ROTATION_GRACE_SECONDS = 10


def _mark_rt_just_rotated(rt_id: int) -> None:
    """Marca un RT como recién rotado para distinguir race de replay."""
    from app.extensions import get_redis
    r = get_redis()
    if not r:
        return
    r.setex(f'rt_just_rotated:{rt_id}', _RT_ROTATION_GRACE_SECONDS, '1')


def _is_rt_just_rotated(rt_id: int) -> bool:
    from app.extensions import get_redis
    r = get_redis()
    if not r:
        # Sin Redis no podemos distinguir. Fallar inseguro (asumir race) es
        # malo en términos de seguridad; fallar seguro (asumir replay) rompe
        # multi-pestaña. Optamos por SEGURO: asumimos replay → revocar familia.
        # Quien necesite multi-pestaña debe tener Redis.
        return False
    return bool(r.get(f'rt_just_rotated:{rt_id}'))


def _enforce_rt_cap(user_id: int) -> None:
    """Revoca los RTs activos más viejos si el usuario tendría más de _MAX_ACTIVE_RT_PER_USER
    tras emitir uno nuevo. Llamar ANTES de _issue_refresh_token."""
    activos = (
        RefreshToken.query
        .filter_by(user_id=user_id, revoked=False)
        .order_by(RefreshToken.created_at.asc())
        .all()
    )
    sobrante = len(activos) - (_MAX_ACTIVE_RT_PER_USER - 1)
    if sobrante <= 0:
        return
    ids_a_revocar = [t.id for t in activos[:sobrante]]
    (
        db.session.query(RefreshToken)
        .filter(RefreshToken.id.in_(ids_a_revocar))
        .update({RefreshToken.revoked: True}, synchronize_session=False)
    )
    # Commit en el caller junto con la emisión del nuevo


def _issue_refresh_token(user_id: int) -> str:
    _enforce_rt_cap(user_id)
    raw = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_LIFETIME_DAYS)
    tok = RefreshToken(token_hash=_hash_token(raw), user_id=user_id, expires_at=expires)
    db.session.add(tok)
    db.session.commit()
    # Asocia UA + IP del request actual al RT recién creado (Redis, sin
    # schema change). Usado por /api/auth/sessions para que el usuario
    # reconozca cada sesión activa.
    _store_rt_meta(tok.id)
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

        # Blacklist check: si el jti fue revocado (logout, admin force-out),
        # rechazamos el token aunque su firma y exp sean válidos.
        jti = payload.get('jti')
        if jti and _is_jti_revoked(jti):
            return jsonify({'error': 'Sesión cerrada'}), 401

        # Validación robusta del sub: el JWT puede llevar cualquier string.
        # Si no es convertible a int (porque viene corrupto o de otro service),
        # debe ser 401 — no 500 — para no leakear stacks.
        try:
            uid = int(payload['sub'])
        except (TypeError, ValueError, KeyError):
            return jsonify({'error': 'Token inválido'}), 401

        user = User.query.get(uid)
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

    # Cap de longitud: defensa contra DoS por hash de payloads gigantes y
    # contra truncamiento silencioso. Si el usuario manda > MAX, rechazamos
    # antes de tocar BD o hashear.
    if len(username) > _MAX_USERNAME_LEN or len(password) > _MAX_PASSWORD_LEN:
        return jsonify({'error': 'Credenciales incorrectas'}), 401

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
        # User-Agent capeado a 200 chars para evitar logs gigantes. La IP ya
        # viene validada por get_real_client_ip_flask (rechaza spoof del
        # CF-Connecting-IP si la conexión directa no es de Cloudflare).
        ua = (request.headers.get('User-Agent') or '')[:200]
        log_action(
            f"API login fallido para '{username[:80]}' desde IP {get_real_client_ip_flask()} "
            f"UA={ua!r}"
        )
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
    from app.extensions import get_real_client_ip_flask
    ip = get_real_client_ip_flask()
    ua = (request.headers.get('User-Agent') or '')[:200]
    log_action(f"API login exitoso desde IP {ip} UA={ua!r}")
    # Si es device nuevo, avisar al usuario (no aplica para login con 2FA —
    # aquel se notifica más abajo, en api_verify_2fa).
    _notify_new_device_login(u.id, ip, ua)

    resp = jsonify({'token': token, 'user': _user_to_dict(u)})
    # Emitimos refresh token siempre (no depende de `remember`) para que el
    # SPA pueda rotar el JWT corto cada ~20 min sin forzar relogin al usuario.
    # `remember` se mantiene en el payload por compatibilidad, pero ya no
    # condiciona la emisión. El logout limpia la cookie igual.
    _set_rt_cookie(resp, _issue_refresh_token(u.id))
    return resp


def _api_verify_2fa_user_key() -> str:
    """Key per-user para el rate limit de verify-2fa.

    Decodifica el stepToken sin verificación de firma (solo extrae `sub`) —
    es OK porque solo usamos el resultado para bucketear el rate limit; la
    autenticación real ocurre dentro del endpoint con _decode_token().

    Falla seguro: si no podemos extraer sub, devolvemos un bucket "anon" que
    comparte rate limit con todos los anónimos.
    """
    try:
        data = request.get_json(silent=True) or {}
        step = data.get('stepToken') or ''
        if not step:
            return 'api_v2fa_user:anon'
        # decode sin verificación — solo lectura de claims públicos
        payload = jwt.decode(step, options={'verify_signature': False, 'verify_aud': False, 'verify_iss': False})
        sub = str(payload.get('sub') or 'anon')
        return f'api_v2fa_user:{sub}'
    except Exception:
        return 'api_v2fa_user:anon'


@bp.route('/verify-2fa', methods=['POST'])
@limiter.limit("4 per minute")
@limiter.limit("8 per minute", key_func=_api_verify_2fa_user_key)
def api_verify_2fa():
    data = request.get_json(silent=True) or {}
    step_token = data.get('stepToken') or ''
    code = (data.get('code') or '').strip()
    # Cap único contra DoS: backup codes (14 chars) y TOTP (6) caben en
    # _MAX_BACKUP_CODE_LEN. No necesitamos flag is_backup_code — el endpoint
    # intenta TOTP primero y cae a backup si falla.
    if len(code) > _MAX_BACKUP_CODE_LEN:
        return jsonify({'error': 'Código inválido'}), 401

    payload = _decode_token(step_token, 'pre_2fa')
    if not payload:
        return jsonify({'error': 'Sesión 2FA expirada. Inicia sesión de nuevo.'}), 401

    try:
        uid = int(payload['sub'])
    except (TypeError, ValueError, KeyError):
        return jsonify({'error': 'Sesión inválida. Inicia sesión de nuevo.'}), 401

    # Lockout escalado: si esta cuenta acumuló N fallos de 2FA en la ventana,
    # rechazar antes de evaluar el código. Bloquea brute-force del TOTP en
    # casos donde el atacante ya tiene la password (phishing, credential stuffing).
    lock_ttl = _check_twofa_lockout(uid)
    if lock_ttl:
        return jsonify({
            'error': f'Demasiados intentos 2FA fallidos. Intenta de nuevo en {_format_ttl(lock_ttl)}.',
        }), 423

    user = User.query.get(uid)
    if not user or not user.totp_secret:
        return jsonify({'error': 'Sesión inválida. Inicia sesión de nuevo.'}), 401

    if (user.password_version or 1) != payload.get('pv', 1):
        return jsonify({'error': 'Tu contraseña cambió. Inicia sesión de nuevo.'}), 401

    g._jwt_user = user

    # Validar código: primero TOTP. Si falla, probar como backup code.
    # El usuario que perdió el teléfono pega el backup code directo en el
    # mismo campo, sin UI extra. Para TOTP exigimos formato corto (≤8 chars)
    # para no malgastar CPU verificando códigos de respaldo contra el secret.
    totp_ok = len(code) <= _MAX_TOTP_CODE_LEN and pyotp.TOTP(user.totp_secret).verify(code, valid_window=1)
    backup_used = False
    if not totp_ok:
        backup_used = _try_consume_backup_code(uid, code)

    if not totp_ok and not backup_used:
        _register_twofa_failure(uid)
        post_fail_ttl = _check_twofa_lockout(uid)
        if post_fail_ttl:
            return jsonify({
                'error': f'Cuenta bloqueada por 2FA fallido. Intenta en {_format_ttl(post_fail_ttl)}.',
            }), 423
        from app.extensions import get_real_client_ip_flask
        ua = (request.headers.get('User-Agent') or '')[:200]
        log_action(
            f"API 2FA fallido para {user.username} desde IP {get_real_client_ip_flask()} "
            f"UA={ua!r}"
        )
        return jsonify({'error': 'Código incorrecto'}), 401

    # Anti-replay del TOTP: si pasó por TOTP y este código se usó hace <90s,
    # rechazar. Para backup codes no aplica (ya están marked consumed en BD).
    if totp_ok and _totp_code_already_used(user.id, code):
        log_action(f"API 2FA replay bloqueado para {user.username}")
        return jsonify({'error': 'Este código ya fue usado. Espera al siguiente.'}), 401

    # Éxito: limpiar el contador de fallos 2FA para esta cuenta.
    _clear_twofa_failures(user.id)

    # Si fue backup code, notificar al usuario por bitácora + notif in-app.
    # Es un evento sensible: o el dispositivo se perdió, o alguien con la
    # password está usando los backup codes filtrados.
    if backup_used:
        remaining = _count_active_backup_codes(user.id)
        log_action(
            f"Login 2FA con BACKUP CODE para {user.username} "
            f"(restantes: {remaining})"
        )
        try:
            from app.models import Notificacion
            from app.realtime import emit_to_user
            notif = Notificacion(
                usuario_id=user.id,
                tipo='LOGIN_BACKUP_CODE',
                titulo='Login con código de respaldo',
                mensaje=(
                    f'Se usó un código de respaldo para iniciar sesión. Quedan '
                    f'{remaining} de {_BACKUP_CODES_COUNT}. Si no fuiste tú, '
                    f'cambia la contraseña y regenera los códigos.'
                ),
                url='/perfil',
            )
            db.session.add(notif)
            db.session.commit()
            try:
                emit_to_user(user.id, 'notif:new', {'id': notif.id, 'tipo': notif.tipo})
            except Exception:
                pass
        except Exception as e:
            try:
                db.session.rollback()
            except Exception:
                pass
            current_app.logger.warning('No se pudo crear notif LOGIN_BACKUP_CODE: %s', e)

    token = _encode_access_token(user)
    user.last_seen = datetime.now()
    db.session.commit()
    from app.extensions import get_real_client_ip_flask
    ip = get_real_client_ip_flask()
    ua = (request.headers.get('User-Agent') or '')[:200]
    log_action(f"API login 2FA exitoso para {user.username} desde IP {ip} UA={ua!r}")
    _notify_new_device_login(user.id, ip, ua)

    resp = jsonify({'token': token, 'user': _user_to_dict(user)})
    # En el flujo 2FA emitimos refresh token siempre para no obligar a reautenticar
    # cada 20 min cuando hay TOTP activo (UX). El SPA puede limpiar la cookie con logout.
    _set_rt_cookie(resp, _issue_refresh_token(user.id))
    return resp


def _api_refresh_user_key() -> str:
    """Key per-user para el rate limit de refresh.

    Hash del raw_rt cookie como bucket: requests con la misma cookie comparten
    rate limit, independiente de la IP. Cubre el caso de una cookie filtrada
    siendo replayed desde múltiples IPs (proxy chain) — el rate limit per-IP
    no detiene eso.

    Si no hay cookie, devolvemos un bucket compartido — el endpoint igual
    rechazará el request por falta de RT, pero al menos no compartimos rate
    limit con el resto del mundo.
    """
    raw_rt = request.cookies.get(_RT_COOKIE)
    if not raw_rt:
        return 'api_refresh:no_cookie'
    # Hash truncado de la cookie cruda (sha256 hexadecimal). 16 chars son
    # suficientes para el bucket; no exponemos la cookie en logs del limiter.
    return f'api_refresh:{_hash_token(raw_rt)[:16]}'


@bp.route('/refresh', methods=['POST'])
@limiter.limit("30 per minute")
@limiter.limit("15 per minute", key_func=_api_refresh_user_key)
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
    tok = RefreshToken.query.filter_by(token_hash=h).first()
    if not tok:
        resp = jsonify({'error': 'Refresh token inválido'})
        _clear_rt_cookie(resp)
        return resp, 401

    exp = tok.expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)

    # ── Detección de replay vs race (token reuse) ───────────────────────────
    # Si el cliente presenta un RT ya revocado, hay dos escenarios:
    #
    # (a) Race legítimo: dos pestañas del mismo navegador hicieron refresh
    #     casi simultáneamente. Una ganó (rotó el RT viejo); la otra llega
    #     unos ms después con el mismo RT, ya marcado revoked. NO es ataque.
    #
    # (b) Replay malicioso: un atacante robó la cookie (XSS, malware) y la
    #     usa después de que el usuario legítimo ya rotó. Es señal clara
    #     de compromiso → revocar la familia entera (todos los RTs del
    #     usuario) y forzar re-login. Patrón OAuth (RFC 6749 §10.4).
    #
    # Para distinguir, marcamos cada RT recién rotado en Redis con TTL 10s.
    # Replay dentro de esa ventana = race; fuera = ataque.
    if tok.revoked:
        if _is_rt_just_rotated(tok.id):
            # Race legítimo: pestaña vecina llegó tarde. Devolvemos 401 sin
            # revocar la familia para no botar al usuario de TODAS sus pestañas.
            resp = jsonify({'error': 'Refresh token ya rotado, reintenta'})
            _clear_rt_cookie(resp)
            return resp, 401

        # Replay fuera de la ventana de gracia: asumir compromiso.
        try:
            RefreshToken.query.filter_by(user_id=tok.user_id, revoked=False).update(
                {'revoked': True}, synchronize_session=False,
            )
            db.session.commit()
            log_action(
                f"Posible robo de refresh token detectado (replay). Todas las sesiones "
                f"del user_id={tok.user_id} fueron revocadas."
            )
            current_app.logger.warning(
                "Refresh token replay detected for user_id=%s; revoked entire RT family",
                tok.user_id,
            )
        except Exception as e:
            db.session.rollback()
            current_app.logger.error("Error revocando familia tras replay: %s", e)
        resp = jsonify({'error': 'Sesión comprometida. Vuelve a iniciar sesión.'})
        _clear_rt_cookie(resp)
        return resp, 401

    if exp <= now:
        tok.revoked = True
        db.session.commit()
        resp = jsonify({'error': 'Refresh token expirado'})
        _clear_rt_cookie(resp)
        return resp, 401

    user_id = tok.user_id

    # Revocación atómica: si dos requests intentan rotar la misma cookie en
    # paralelo (dos pestañas, refresh proactivo + reactivo), solo el primero
    # logrará el UPDATE con revoked=False; el segundo recibirá rowcount=0 y
    # debe rechazarse en lugar de emitir un RT nuevo (que dejaría dos válidos
    # en BD, con el riesgo de que el "perdedor" haga logout cuando una
    # pestaña intente usarlo).
    updated = (
        db.session.query(RefreshToken)
        .filter(RefreshToken.id == tok.id, RefreshToken.revoked == False)  # noqa: E712
        .update({RefreshToken.revoked: True}, synchronize_session=False)
    )
    db.session.commit()
    if updated == 0:
        # Race perdido: otro request ya rotó este token.
        resp = jsonify({'error': 'Refresh token ya rotado'})
        _clear_rt_cookie(resp)
        return resp, 401

    # Marca el RT recién revocado como "rotado en ventana de gracia". Si llega
    # otro request en los próximos _RT_ROTATION_GRACE_SECONDS con este mismo
    # RT, se trata como race (pestaña vecina) y NO como replay malicioso.
    _mark_rt_just_rotated(tok.id)

    user = User.query.get(user_id)
    if user is None:
        resp = jsonify({'error': 'Usuario no encontrado'})
        _clear_rt_cookie(resp)
        return resp, 401

    new_raw = secrets.token_urlsafe(32)
    new_tok = RefreshToken(
        token_hash=_hash_token(new_raw),
        user_id=user.id,
        expires_at=now + timedelta(days=REFRESH_TOKEN_LIFETIME_DAYS),
    )
    db.session.add(new_tok)
    db.session.flush()  # asegura new_tok.id antes del DELETE
    # Housekeeping: limpiar tokens viejos del usuario.
    RefreshToken.query.filter(
        RefreshToken.user_id == user.id,
        (RefreshToken.revoked == True) | (RefreshToken.expires_at <= now),  # noqa: E712
        RefreshToken.id != new_tok.id,
    ).delete(synchronize_session=False)
    db.session.commit()
    # Persistir metadata (UA + IP) del request que rotó. Si la sesión cambia
    # de IP repentinamente, /sessions lo refleja al siguiente refresh.
    _store_rt_meta(new_tok.id)

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

    # Revocar el JWT actual (vía jti) si el cliente lo mandó en Authorization.
    # Esto cierra el access token AL INSTANTE, sin esperar a su exp (≤20 min).
    # Sin Redis no podemos blacklistear — degradamos: el JWT queda vivo hasta
    # su exp, pero los refresh tokens igual se revocan abajo.
    auth_h = request.headers.get('Authorization', '')
    if auth_h.startswith('Bearer '):
        access_tok = auth_h.split(' ', 1)[1].strip()
        payload = _decode_token(access_tok, 'access')
        if payload and payload.get('jti') and payload.get('exp'):
            _revoke_jti(payload['jti'], int(payload['exp']))

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


@bp.route('/me/activity', methods=['GET'])
@jwt_required
@limiter.limit("30 per minute")
def api_my_activity():
    """Devuelve las últimas acciones del PROPIO usuario en el audit log.

    Útil en el perfil para que el usuario vea qué hizo recientemente:
    logins, cambios de password, configuración de 2FA, generación de
    backup codes, etc. Solo lectura, sin PII de otros usuarios.

    `?limit=N` (default 20, máx 50).
    """
    from app.models import AuditLog
    user = g._jwt_user
    try:
        limit = int(request.args.get('limit') or 20)
    except (TypeError, ValueError):
        limit = 20
    limit = max(1, min(limit, 50))

    rows = (
        AuditLog.query
        .filter(AuditLog.user == user.username)
        .order_by(AuditLog.created_at.desc())
        .limit(limit)
        .all()
    )
    return jsonify([
        {
            'id': r.id,
            'action': r.action,
            'ip': r.ip,
            'created_at': r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ])


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
        meta = _load_rt_meta(t.id)
        out.append({
            'id': t.id,
            'created_at': t.created_at.isoformat() if t.created_at else None,
            'expires_at': exp.isoformat(),
            # UA + IP del último login/rotación. Permite al usuario reconocer
            # sesiones extrañas y revocarlas. None si Redis estaba caído al
            # momento de emitir.
            'user_agent': meta.get('ua') or None,
            'ip': meta.get('ip') or None,
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

    Hace DOS cosas:
      1) Revoca todos los refresh tokens activos (corta el ciclo de refresh).
      2) Incrementa password_version → invalida cualquier JWT vivo del usuario
         (cualquier dispositivo presentando un access token viejo recibe 401
         al instante, sin esperar 20 min).

    Es la única forma sin lista negra masiva de invalidar TODOS los JWT del
    usuario al instante. Como efecto secundario, esta misma sesión también
    queda invalidada — el cliente debe re-loguear inmediatamente.
    """
    user = g._jwt_user
    RefreshToken.query.filter_by(user_id=user.id, revoked=False).update({'revoked': True})
    user.password_version = (user.password_version or 1) + 1
    db.session.commit()
    log_action(f'Revocó todas las sesiones ({user.username}) — JWT vivos invalidados')
    # Cierra cualquier WebSocket activo del usuario. Sin esto, el SPA seguía
    # recibiendo pushes (notif:new, bitacora:new) por unos segundos hasta que
    # el siguiente request HTTP recibiera 401 por pv mismatch.
    try:
        from app.realtime import force_logout_user
        force_logout_user(user.id)
    except Exception as e:
        current_app.logger.warning('force_logout_user falló en /sessions/all: %s', e)
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
        from app.utils import image_to_webp
        unique_filename = f"profile_{user.id}_{uuid.uuid4().hex[:8]}.webp"
        upload_path = os.path.join(current_app.config['UPLOAD_FOLDER'], unique_filename)
        webp_buf = image_to_webp(foto)
        with open(upload_path, 'wb') as f:
            f.write(webp_buf.getvalue())
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


@bp.route('/profile/foto', methods=['DELETE'])
@jwt_required
@limiter.limit('10 per minute')
def api_delete_profile_foto():
    """Elimina la foto de perfil del propio usuario.

    Borra el archivo del disco (si no es la default) y limpia el campo
    profile_pic a None. La UI mostrará las iniciales del usuario después.
    """
    import os
    user = g._jwt_user
    old = user.profile_pic
    user.profile_pic = None

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        current_app.logger.error('Error eliminando foto de perfil: %s', e)
        return jsonify({'error': 'Error al eliminar la foto'}), 500

    # Limpieza de disco best-effort — no falla la op si el archivo no existe.
    if old and old != 'default.png':
        path = os.path.join(current_app.config['UPLOAD_FOLDER'], old)
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception as e:
                current_app.logger.warning('No se pudo eliminar archivo de foto: %s', e)

    log_action(f"Eliminó su foto de perfil ({user.username})")
    return jsonify(_user_to_dict(user))


@bp.route('/change-password/<int:user_id>', methods=['POST'])
@jwt_required
@limiter.limit('10 per minute')
def api_change_own_password(user_id: int):
    """Cambia la contraseña propia. Requiere current_password y, si 2FA está
    activo, también un código TOTP actual válido (anti-replay).

    Refuerzo de seguridad: sin la exigencia de TOTP, un atacante con sesión
    activa + contraseña filtrada (phishing en la propia app) podía cambiar la
    contraseña sin tocar el 2FA. Aunque el 2FA seguiría protegiendo el login,
    el atacante podría bloquear al usuario legítimo y mantener su acceso vía
    la sesión que sigue viva (los RTs se revocan acá, pero el JWT actual no).
    Exigir TOTP en el mismo flujo cierra ese hueco.
    """
    from werkzeug.security import generate_password_hash
    from app.utils import is_strong_password
    user = g._jwt_user
    if user.id != user_id:
        return jsonify({'error': 'Solo puedes cambiar tu propia contraseña'}), 403

    data = request.get_json(silent=True) or {}
    current_password = data.get('current_password') or data.get('currentPassword') or ''
    new_password = data.get('new_password') or data.get('newPassword') or ''
    totp_code = (data.get('code') or data.get('totp_code') or '').strip()

    if not current_password or not new_password:
        return jsonify({'error': 'Contraseña actual y nueva son obligatorias'}), 400
    if len(current_password) > _MAX_PASSWORD_LEN or len(new_password) > _MAX_PASSWORD_LEN:
        return jsonify({'error': 'Contraseña excede longitud permitida'}), 400
    if not check_password_hash(user.password_hash, current_password):
        return jsonify({'error': 'La contraseña actual es incorrecta'}), 401

    # Exigir TOTP cuando el usuario lo tiene activo. Devolvemos un flag
    # `requires_totp` para que el cliente sepa mostrar el campo y no presente
    # el error como "contraseña incorrecta" (lo cual confundiría al usuario).
    if user.totp_secret:
        if not totp_code:
            return jsonify({
                'error': 'Se requiere código 2FA para cambiar la contraseña',
                'requires_totp': True,
            }), 401
        if len(totp_code) > _MAX_TOTP_CODE_LEN:
            return jsonify({'error': 'Código 2FA inválido'}), 401
        if not pyotp.TOTP(user.totp_secret).verify(totp_code, valid_window=1):
            return jsonify({'error': 'Código 2FA incorrecto'}), 401
        if _totp_code_already_used(user.id, totp_code):
            return jsonify({'error': 'Ese código ya fue usado. Espera al siguiente.'}), 401

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
    if len(current_password) > _MAX_PASSWORD_LEN:
        return jsonify({'error': 'La contraseña actual es incorrecta'}), 401
    if not check_password_hash(user.password_hash, current_password):
        return jsonify({'error': 'La contraseña actual es incorrecta'}), 401

    secret = pyotp.random_base32()
    # Pin server-side: el secret QUE EL CLIENTE DEVOLVERÁ en /confirm-2fa debe
    # coincidir con éste. Bloquea el ataque donde un cliente malicioso envía
    # un secret arbitrario en confirm-2fa para tomar control del TOTP.
    if not _pin_setup_2fa_secret(user.id, secret):
        current_app.logger.error(
            'setup-2fa: Redis no disponible para pinear el secret. Rechazando.'
        )
        return jsonify({
            'error': 'No se puede iniciar la configuración de 2FA en este momento. Intenta más tarde.',
        }), 503
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
    if len(current_password) > _MAX_PASSWORD_LEN:
        return jsonify({'error': 'La contraseña actual es incorrecta'}), 401
    if len(code) > _MAX_TOTP_CODE_LEN or len(current_2fa_code) > _MAX_TOTP_CODE_LEN:
        return jsonify({'error': 'Código inválido'}), 400
    # El secret TOTP es base32 de 32 chars. Capeamos a 64 con margen.
    if len(secret) > 64:
        return jsonify({'error': 'Secret inválido'}), 400
    if not check_password_hash(user.password_hash, current_password):
        return jsonify({'error': 'La contraseña actual es incorrecta'}), 401

    # Validar que el secret venga del /setup-2fa más reciente de este usuario.
    # Hacemos PEEK (sin borrar): si el código TOTP es incorrecto, el usuario
    # debe poder reintentar sin re-escanear el QR. El delete ocurre solo al
    # final cuando todo se valida exitosamente.
    pinned = _peek_setup_2fa_secret(user.id)
    if pinned is None:
        return jsonify({
            'error': 'La configuración de 2FA expiró. Vuelve a escanear el código QR.',
        }), 400
    if not secrets.compare_digest(pinned, secret):
        log_action(f"2FA confirm API: secret no coincide con el pineado para {user.username}")
        return jsonify({
            'error': 'El secret no coincide con el de la configuración. Vuelve a escanear el código QR.',
        }), 400

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
        # Activar/cambiar 2FA es un evento de seguridad equivalente a cambiar
        # contraseña: invalida sesiones de otros dispositivos. El access token
        # actual (JWT corto, ≤20 min) sigue vivo hasta que expire, pero los
        # refresh tokens activos quedan revocados — la próxima vez que cualquier
        # otro dispositivo intente refrescar, será forzado a re-login.
        RefreshToken.query.filter_by(user_id=user.id, revoked=False).update({'revoked': True})
        db.session.commit()
        # Solo borrar el pin de Redis tras un commit exitoso. Así, si la BD
        # falla y hace rollback, el usuario puede reintentar sin re-escanear.
        _delete_setup_2fa_secret(user.id)
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
    if len(current_password) > _MAX_PASSWORD_LEN or len(code) > _MAX_TOTP_CODE_LEN:
        return jsonify({'error': 'Credenciales incorrectas'}), 401
    if not check_password_hash(user.password_hash, current_password):
        return jsonify({'error': 'La contraseña actual es incorrecta'}), 401
    if not pyotp.TOTP(user.totp_secret).verify(code, valid_window=1):
        log_action(f"2FA disable: código incorrecto para {user.username}")
        return jsonify({'error': 'Código 2FA incorrecto'}), 401
    if _totp_code_already_used(user.id, code):
        return jsonify({'error': 'Ese código ya fue usado. Espera al siguiente.'}), 401

    try:
        user.totp_secret = None
        # Desactivar 2FA es un evento de seguridad: revocar todas las sesiones
        # vivas del usuario (otros dispositivos). Consistente con confirm-2fa y
        # con change-password.
        RefreshToken.query.filter_by(user_id=user.id, revoked=False).update({'revoked': True})
        # Y limpiar backup codes: sin 2FA no aplican.
        TwoFactorBackupCode.query.filter_by(user_id=user.id).delete()
        db.session.commit()
        log_action(f"2FA desactivado vía API para {user.username}")
        return jsonify({'ok': True})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error('Error desactivando 2FA: %s', e)
        return jsonify({'error': 'Error al desactivar 2FA'}), 500


# ── Backup codes para 2FA ───────────────────────────────────────────────────

@bp.route('/backup-codes', methods=['GET'])
@jwt_required
def api_backup_codes_status():
    """Devuelve cuántos códigos quedan activos. No revela los códigos."""
    user = g._jwt_user
    if not user.totp_secret:
        return jsonify({'enabled': False, 'remaining': 0})
    remaining = _count_active_backup_codes(user.id)
    return jsonify({
        'enabled': True,
        'remaining': remaining,
        'low': remaining <= _BACKUP_CODES_LOW_THRESHOLD,
    })


@bp.route('/backup-codes', methods=['POST'])
@jwt_required
@limiter.limit('4 per minute')
def api_generate_backup_codes():
    """Genera 10 códigos de respaldo y los devuelve UNA SOLA VEZ.

    Requisitos:
      - 2FA debe estar activo (no tiene sentido generarlos sin TOTP).
      - Reauth con current_password + código TOTP actual válido (no replay).

    Genera SIEMPRE invalida los anteriores (consumidos o no). El cliente debe
    advertir al usuario antes de llamar este endpoint.
    """
    user = g._jwt_user
    data = request.get_json(silent=True) or {}
    current_password = data.get('current_password') or data.get('currentPassword') or ''
    code = (data.get('code') or '').strip()

    if not user.totp_secret:
        return jsonify({'error': '2FA no está activo. Actívalo primero.'}), 400
    if not current_password or not code:
        return jsonify({'error': 'Contraseña actual y código 2FA son obligatorios'}), 400
    if len(current_password) > _MAX_PASSWORD_LEN or len(code) > _MAX_TOTP_CODE_LEN:
        return jsonify({'error': 'Credenciales incorrectas'}), 401
    if not check_password_hash(user.password_hash, current_password):
        return jsonify({'error': 'La contraseña actual es incorrecta'}), 401
    if not pyotp.TOTP(user.totp_secret).verify(code, valid_window=1):
        log_action(f"backup-codes: código TOTP incorrecto para {user.username}")
        return jsonify({'error': 'Código 2FA incorrecto'}), 401
    if _totp_code_already_used(user.id, code):
        return jsonify({'error': 'Ese código ya fue usado. Espera al siguiente.'}), 401

    try:
        codes = _generate_backup_codes(user.id)
        log_action(f"Backup codes 2FA regenerados ({len(codes)}) para {user.username}")
        return jsonify({
            'codes': codes,
            'count': len(codes),
            'warning': (
                'Estos códigos se muestran UNA SOLA VEZ. Guárdalos en lugar '
                'seguro. Cada código solo se puede usar una vez para acceder '
                'cuando no tengas el dispositivo TOTP. Regenerar invalida los '
                'anteriores.'
            ),
        })
    except Exception as e:
        db.session.rollback()
        current_app.logger.error('Error generando backup codes: %s', e)
        return jsonify({'error': 'Error al generar códigos de respaldo'}), 500


@bp.route('/backup-codes', methods=['DELETE'])
@jwt_required
@limiter.limit('4 per minute')
def api_revoke_backup_codes():
    """Revoca TODOS los códigos de respaldo del usuario.

    Útil si el usuario sospecha que la hoja impresa se perdió/robó. Requiere
    reauth con password + código TOTP actual.
    """
    user = g._jwt_user
    data = request.get_json(silent=True) or {}
    current_password = data.get('current_password') or data.get('currentPassword') or ''
    code = (data.get('code') or '').strip()

    if not user.totp_secret:
        return jsonify({'error': '2FA no está activo'}), 400
    if not current_password or not code:
        return jsonify({'error': 'Contraseña actual y código 2FA son obligatorios'}), 400
    if len(current_password) > _MAX_PASSWORD_LEN or len(code) > _MAX_TOTP_CODE_LEN:
        return jsonify({'error': 'Credenciales incorrectas'}), 401
    if not check_password_hash(user.password_hash, current_password):
        return jsonify({'error': 'La contraseña actual es incorrecta'}), 401
    if not pyotp.TOTP(user.totp_secret).verify(code, valid_window=1):
        return jsonify({'error': 'Código 2FA incorrecto'}), 401
    if _totp_code_already_used(user.id, code):
        return jsonify({'error': 'Ese código ya fue usado. Espera al siguiente.'}), 401

    n = TwoFactorBackupCode.query.filter_by(user_id=user.id).delete()
    db.session.commit()
    log_action(f"Backup codes 2FA revocados ({n}) para {user.username}")
    return jsonify({'ok': True, 'revocados': n})
