"""Núcleo del paquete `api_auth`.

Aquí viven el blueprint, sus constantes, los serializers de `User` y los
helpers que no dependen del resto del paquete. Ningún sub-módulo del paquete
importa endpoints — solo de `_core` y `tokens`. Eso rompe ciclos.
"""
import hashlib
from flask import Blueprint, current_app, jsonify, request
from werkzeug.security import generate_password_hash

from app.extensions import get_redis
from app.models import User


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

# TTL del setup-2fa pin en Redis (10 min — mismo que el HTML clásico).
_SETUP_2FA_TTL = 600

# Tope de refresh tokens activos por usuario. Si supera el cap, se revocan
# los más antiguos (FIFO) antes de emitir uno nuevo. Limita la acumulación
# de RTs olvidados que serían ventanas de ataque si se filtran.
_MAX_ACTIVE_RT_PER_USER = 8

# Ventana de gracia para distinguir race (legítimo) de replay (ataque) en
# refresh. Si un RT fue revocado HACE MENOS de este tiempo y llega otro request
# con el mismo, asumimos pestaña vecina; más allá, compromiso → revocar familia.
_RT_ROTATION_GRACE_SECONDS = 10


def _jwt_secret() -> str:
    return current_app.config['SECRET_KEY']


# ── Serializers ─────────────────────────────────────────────────────────────

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


# ── Cookie del refresh token ────────────────────────────────────────────────

def _cookie_samesite() -> str:
    """SameSite del refresh cookie.

    - 'Lax' (default): suficiente cuando frontend y API comparten dominio
      (same-site) — p.ej. ambos detrás de Cloudflare bajo *.skilled.com.mx.
    - 'None': obligatorio cuando el SPA vive en otro origen (Vercel) y manda
      la cookie cross-site al backend. Requiere Secure=True (HTTPS).
    """
    import os
    return os.environ.get('RT_COOKIE_SAMESITE', 'Lax')


def _cookie_secure() -> bool:
    # Marca Secure solo cuando la conexión es HTTPS. Con ProxyFix activo,
    # request.is_secure respeta X-Forwarded-Proto (Cloudflare Tunnel) en prod
    # y devuelve False en el dev server HTTP local — evita que la cookie se
    # marque como Secure y desaparezca para el SPA en localhost.
    # SameSite=None EXIGE Secure=True; si se configuró None, forzamos Secure.
    if _cookie_samesite().lower() == 'none':
        return True
    return request.is_secure


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


# ── Anti enumeración por timing ──────────────────────────────────────────────
# Hash dummy precomputado al cargar el módulo. En la rama "usuario no existe"
# se compara la contraseña contra este hash para igualar el costo de un
# check_password_hash real e impedir distinguir usernames válidos por tiempo.
_DUMMY_PW_HASH = generate_password_hash('not-a-real-password-timing-dummy')


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


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()
