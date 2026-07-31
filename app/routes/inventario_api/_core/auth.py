"""Guards de autorización del módulo de Inventario y escritura de auditoría.

Cada guard envuelve `jwt_required`, deja el usuario en `request.current_user` y
registra el intento fallido antes de devolver 403.
"""
from functools import wraps

from flask import g, jsonify, request

from app.extensions import db, get_real_client_ip_flask
from app.models import AuditLog, User
from app.routes.api_auth import jwt_required
from app.utils import log_action, _safe_log_value

# Roles con permiso de LECTURA sobre el catálogo (ver productos, armar pedidos).
_ROLES_LECTURA = ('inventario', 'solicitante_material', 'coordinador', 'admin', 'super_admin')
# Roles con permiso de ESCRITURA/borrado sobre el inventario.
_ROLES_ESCRITURA = ('inventario', 'admin', 'super_admin')
# Roles que planean materiales por proyecto (el coordinador es dueño del suyo).
_ROLES_PLAN_MATERIALES = ('inventario', 'coordinador', 'admin', 'super_admin')


def _require_login(view):
    @wraps(view)
    @jwt_required
    def wrapper(*args, **kwargs):
        request.current_user = g._jwt_user
        return view(*args, **kwargs)
    return wrapper


def _guard_por_roles(roles, etiqueta: str, mensaje: str):
    """Fabrica un decorador que exige que `request.current_user.role` esté en
    `roles`. `etiqueta` identifica el intento en la bitácora y `mensaje` es el
    texto del 403 que ve el SPA."""
    def decorador(view):
        @wraps(view)
        @_require_login
        def wrapper(*args, **kwargs):
            if request.current_user.role not in roles:
                log_action(f"API 403 {etiqueta} '{request.path}' (rol: {request.current_user.role})")
                return jsonify({'detail': mensaje}), 403
            return view(*args, **kwargs)
        return wrapper
    return decorador


# Lectura: solicitantes, coordinadores, inventario, admin y super_admin.
# Coordinador incluido para que pueda ver catálogo y armar pedidos.
_require_inventario = _guard_por_roles(
    _ROLES_LECTURA, 'lectura', 'Forbidden: Required permissions missing',
)

# Escritura/borrado: inventario, admin y super_admin.
_require_inventario_admin = _guard_por_roles(
    _ROLES_ESCRITURA, 'escritura', 'Se requiere rol de inventario o administrador',
)

# Plan de materiales por proyecto: el coordinador es dueño de sus proyectos y
# planea sus materiales. Guard aparte de `_require_inventario_admin` a
# propósito: le damos al coordinador SOLO el plan de materiales por proyecto, NO
# el resto de la escritura de inventario (movimientos, catálogo, tomas,
# entregas, compras…).
_require_plan_materiales = _guard_por_roles(
    _ROLES_PLAN_MATERIALES, 'plan-materiales',
    'Se requiere rol de inventario, coordinador o administrador',
)


def _audit(user: User, action: str):
    """Escribe AuditLog usando la IP real (anti-spoofing en get_real_client_ip_flask).
    MED-03: pasa por _safe_log_value para evitar log forging vía CRLF."""
    entry = AuditLog(
        user=_safe_log_value(user.username, 80),
        action=_safe_log_value(action, 200),
        ip=get_real_client_ip_flask(),
    )
    db.session.add(entry)
