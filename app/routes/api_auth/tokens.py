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
    from app.extensions import get_redis
    r = get_redis()
    if not r:
        return
    ttl = max(1, int(exp_ts - datetime.now(timezone.utc).timestamp()))
    r.setex(f'jwt_revoked:{jti}', ttl, '1')


def _is_jti_revoked(jti: str) -> bool:
    from app.extensions import get_redis
    r = get_redis()
    if not r:
        return False
    return bool(r.get(f'jwt_revoked:{jti}'))


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


# ── Metadata de session (UA + IP) por RT, en Redis ──────────────────────────
# Para que el usuario reconozca login extraño desde /api/auth/sessions, asociamos
# user-agent + IP a cada RT emitido. Lo guardamos en Redis con TTL == duración
# del RT para evitar migración de schema. Si Redis no está disponible, /sessions
# muestra los RTs sin metadata (degradación benigna).

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
