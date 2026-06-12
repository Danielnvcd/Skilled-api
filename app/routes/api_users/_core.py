"""Núcleo del paquete `api_users`.

Define el blueprint para administración de usuarios y el serializer común.
"""
from flask import Blueprint

from app.models import User


bp = Blueprint('api_users', __name__, url_prefix='/api/users')


_ROLE_ORDER = {
    'super_admin': 0,
    'admin': 1,
    'finanzas': 2,
    'coordinador': 3,
    'inventario': 4,
    'solicitante_material': 5,
}

# super_admin nunca se puede crear/asignar desde este endpoint (no está en
# _VALID_NEW_ROLES); esa cuenta solo se crea por seeding/manual.
# Los admins pueden crear y eliminar otros admins por decisión operativa.
_VALID_NEW_ROLES = {'admin', 'finanzas', 'coordinador', 'inventario', 'solicitante_material'}


def _user_to_dict(u: User) -> dict:
    t = u.trabajador  # FK opcional; relationship lazy='select'
    return {
        'id': u.id,
        'username': u.username,
        'role': u.role,
        'full_name': u.full_name,
        'area': u.area,
        'position': u.position,
        'factory': u.factory,
        'contact_info': u.contact_info,
        'profile_pic': u.profile_pic,
        'totp_enabled': bool(u.totp_secret),
        'last_seen': u.last_seen.isoformat() if u.last_seen else None,
        # Liga opcional a Trabajador (RRHH) — habilita asignaciones de
        # herramienta y filtros "lo mío" basados en empleado.
        'trabajador_id': u.trabajador_id,
        'trabajador_no_empleado': t.no_empleado if t else None,
        'trabajador_nombre': t.nombre_completo if t else None,
    }
