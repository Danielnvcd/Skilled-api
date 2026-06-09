"""API JWT para el frontend React.

Convive con el blueprint clásico `auth.py` (sesión + plantillas Jinja). Aquí no se
usa Flask-Session ni cookies de sesión: el access token es un JWT firmado y el
refresh token se entrega en una cookie HttpOnly (`rt_api`) — separada de la cookie
`rt` del flujo HTML para evitar colisiones de rotación entre ambos clientes.

Estructura interna del paquete:

    _core.py        bp + after_request + constantes + serializers + helpers de cookie/CSRF
    tokens.py       encode/decode JWT, revocación por jti, RT meta/cap/issue/rotation
    jwt_required.py decorador `jwt_required` (lo importan TODOS los `api_*`)
    login.py        /login, /verify-2fa, /refresh, /logout
    twofa.py        /setup-2fa, /confirm-2fa, /disable-2fa, /backup-codes (×3)
    sessions.py     /sessions (×3) + helpers de notificación de device nuevo
    perfil.py       /me, /me/activity, /users (×3), /profile (×2), /change-password

El contrato externo no cambia: el blueprint `bp` se sigue registrando como antes
en `app/__init__.py`, y las URLs/métodos/payloads quedan idénticos.
"""
from ._core import bp
from .jwt_required import jwt_required
from .tokens import _decode_token, _encode_access_token, _is_jti_revoked

# Importar los módulos de endpoints para que sus `@bp.route(...)` se registren
# cuando se importe el paquete. El orden es flexible — no hay side effects
# cruzados entre ellos más allá de los imports de helpers ya hechos.
from . import login, perfil, sessions, twofa  # noqa: F401,E402

__all__ = ['bp', 'jwt_required', '_decode_token', '_encode_access_token', '_is_jti_revoked']
