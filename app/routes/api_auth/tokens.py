"""Helpers de tokens: encoding/decoding JWT, cookies del RT, anti-replay TOTP,
revocación por jti, metadata de sesión y cap de RTs por usuario.

Solo importa de `_core` — nunca de los módulos de endpoints, para mantener
limpio el grafo de imports del paquete.
"""
import json as _json
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from flask import request

from app.constants import REFRESH_TOKEN_LIFETIME_DAYS
from app.extensions import db
from app.models import RefreshToken, User

from ._core import (
    ACCESS_TOKEN_LIFETIME_MINUTES,
    PRE_2FA_LIFETIME_SECONDS,
    _JWT_AUD,
    _JWT_ISS,
    _MAX_ACTIVE_RT_PER_USER,
    _RT_COOKIE,
    _RT_COOKIE_PATH,
    _RT_ROTATION_GRACE_SECONDS,
    _cookie_samesite,
    _cookie_secure,
    _hash_token,
    _jwt_secret,
)


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
        # `jti` para consumo de un solo uso: el stepToken acredita "esta persona
        # ya probó la contraseña". Sin jti seguía siendo canjeable durante los
        # 5 min de su TTL aunque ya se hubiera usado para completar el 2FA, y no
        # había forma de invalidarlo. Ver `_burn_pre_2fa_jti`.
        'jti': secrets.token_urlsafe(16),
        'type': 'pre_2fa',
        'iss': _JWT_ISS,
        'aud': _JWT_AUD,
    }
    return jwt.encode(payload, _jwt_secret(), algorithm='HS256')


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
    from app.extensions import redis_call
    ttl = max(1, int(exp_ts - datetime.now(timezone.utc).timestamp()))
    redis_call(lambda r: r.setex(f'jwt_revoked:{jti}', ttl, '1'))


def _is_jti_revoked(jti: str) -> bool:
    """¿Este access token fue revocado (logout, revocación de sesión)?

    Se llama en CADA request autenticada desde `jwt_required`, así que es el
    punto donde una caída de Redis dolía más: antes propagaba la excepción y
    tumbaba la API completa. Ahora degrada a False — el token sigue siendo
    válido hasta su exp (≤20 min), que es el comportamiento legacy de cuando
    no había Redis.
    """
    from app.extensions import redis_call
    return bool(redis_call(lambda r: r.get(f'jwt_revoked:{jti}'), default=False))


def _burn_pre_2fa_jti(jti: str, exp_ts: int) -> bool:
    """Consume el stepToken pre_2fa. Devuelve False si YA se había consumido.

    El stepToken es la prueba de "esta persona ya pasó el factor contraseña".
    Debe canjearse una sola vez: en cuanto se completa el 2FA y se emite un
    access token, presentar el mismo stepToken otra vez tiene que fallar.

    Sin esto, un stepToken filtrado (queda en `sessionStorage` del SPA, en el
    `state` de navegación y en el cuerpo de la request) seguía siendo canjeable
    durante el resto de su TTL de 5 min: quien lo tuviera junto con un código
    TOTP válido podía acuñar sesiones adicionales sin conocer la contraseña.

    Implementación: SET NX EX en Redis con TTL == lo que le quede al `exp`, así
    la key caduca sola. Sin Redis degradamos a permitir (misma política que el
    resto del módulo: el lockout escalado de 2FA sigue activo y no rompemos el
    login de entornos sin Redis).

    `jti` vacío → True: cubre los stepToken en vuelo emitidos por la versión
    anterior durante un deploy, que todavía no traen el claim.
    """
    if not jti:
        return True
    from app.extensions import redis_call
    ttl = max(1, int(exp_ts - datetime.now(timezone.utc).timestamp()))
    return bool(redis_call(
        lambda r: r.set(f'pre2fa_used:{jti}', '1', nx=True, ex=ttl),
        default=True,  # Redis caído → permitir, nunca bloquear un login legítimo
    ))


# Anti-replay de códigos TOTP: pyotp.verify(valid_window=1) acepta el mismo
# código durante ~60s. Si un atacante hace shoulder-surfing o intercepta el
# código en ese ventana, puede usarlo otra vez. Esta función marca el código
# como "ya usado" en Redis con TTL de 90s (cubre la ventana +1 con margen).
def _totp_code_already_used(user_id: int, code: str) -> bool:
    from app.extensions import redis_call
    import hashlib
    # Hasheamos para no guardar el código en claro en Redis
    h = hashlib.sha256(f'{user_id}:{code}'.encode()).hexdigest()
    key = f'totp_used:{h}'
    # SET NX EX: solo crea la key si no existe, con TTL 90s. Devuelve None si
    # ya existía → código ya usado.
    # Sin Redis (o con Redis caído) no podemos detectar replay — degradamos a
    # `default=True` (= "se pudo escribir" = no usado antes), que preserva el
    # comportamiento original. El lockout escalado sigue frenando brute-force.
    was_set = redis_call(lambda r: r.set(key, '1', nx=True, ex=90), default=True)
    return not was_set


# ── Metadata de session (UA + IP) por RT, en Redis ──────────────────────────
# Para que el usuario reconozca login extraño desde /api/auth/sessions, asociamos
# user-agent + IP a cada RT emitido. Lo guardamos en Redis con TTL == duración
# del RT para evitar migración de schema. Si Redis no está disponible, /sessions
# muestra los RTs sin metadata (degradación benigna).

def _store_rt_meta(rt_id: int) -> None:
    """Persiste UA + IP del request actual asociados al RT recién creado."""
    from app.extensions import redis_call, get_real_client_ip_flask
    ua = (request.headers.get('User-Agent') or '')[:200] if request else ''
    ip = get_real_client_ip_flask() if request else ''
    payload = _json.dumps({'ua': ua, 'ip': ip})
    # TTL = duración del RT + 1 día de margen
    ttl = REFRESH_TOKEN_LIFETIME_DAYS * 86400 + 86400
    redis_call(lambda r: r.setex(f'rt_meta:{rt_id}', ttl, payload))


def _load_rt_meta(rt_id: int) -> dict:
    from app.extensions import redis_call
    raw = redis_call(lambda r: r.get(f'rt_meta:{rt_id}'))
    if not raw:
        return {}
    try:
        return _json.loads(raw)
    except Exception:
        return {}


def _mark_rt_just_rotated(rt_id: int) -> None:
    """Marca un RT como recién rotado para distinguir race de replay."""
    from app.extensions import redis_call
    redis_call(lambda r: r.setex(f'rt_just_rotated:{rt_id}', _RT_ROTATION_GRACE_SECONDS, '1'))


def _is_rt_just_rotated(rt_id: int) -> bool:
    from app.extensions import redis_call
    # Sin Redis no podemos distinguir. Fallar inseguro (asumir race) es
    # malo en términos de seguridad; fallar seguro (asumir replay) rompe
    # multi-pestaña. Optamos por SEGURO: asumimos replay → revocar familia.
    # Quien necesite multi-pestaña debe tener Redis.
    return bool(redis_call(lambda r: r.get(f'rt_just_rotated:{rt_id}'), default=False))


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


def _set_rt_cookie(response, raw: str) -> None:
    """Emite la cookie del refresh token, acotada a `/api/auth`.

    Antes iba con `path='/'`, así que el navegador la adjuntaba a TODAS las
    requests de la API — cientos al día — cuando en realidad solo la leen
    `/api/auth/refresh`, `/api/auth/logout` y `/api/auth/sessions/<id>`.
    Acotarla reduce la exposición del secreto de larga vida (7 días): deja de
    aparecer en los headers de cada llamada, y por tanto en cualquier lugar
    donde esos headers se registren o inspeccionen.

    Migración: la cookie vieja en `path='/'` sigue existiendo en los navegadores
    ya usados y TAMBIÉN hace match con `/api/auth/*`, así que el cliente mandaría
    DOS cookies `rt_api` y `request.cookies.get()` elegiría una de forma no
    determinista — el usuario podría quedar deslogueado al azar. Por eso cada
    vez que emitimos la nueva, borramos explícitamente la de `path='/'`.
    La primera respuesta post-deploy deja el estado limpio y no se repite.
    """
    _delete_legacy_root_cookie(response)
    response.set_cookie(
        _RT_COOKIE,
        raw,
        max_age=REFRESH_TOKEN_LIFETIME_DAYS * 86400,
        httponly=True,
        secure=_cookie_secure(),
        samesite=_cookie_samesite(),
        path=_RT_COOKIE_PATH,
    )


def _delete_legacy_root_cookie(response) -> None:
    """Borra la cookie `rt_api` que quedó en `path='/'` de la versión anterior."""
    response.delete_cookie(
        _RT_COOKIE, path='/', secure=_cookie_secure(), samesite=_cookie_samesite(),
    )


def _clear_rt_cookie(response) -> None:
    """Limpia la cookie en AMBOS paths.

    Un logout tiene que cerrar la sesión sí o sí: si solo borráramos el path
    nuevo, un navegador que todavía tuviera la cookie vieja en `/` seguiría
    presentándola en el siguiente `/refresh`.
    """
    response.delete_cookie(
        _RT_COOKIE, path=_RT_COOKIE_PATH, secure=_cookie_secure(), samesite=_cookie_samesite(),
    )
    _delete_legacy_root_cookie(response)
