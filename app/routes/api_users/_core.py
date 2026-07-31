"""Núcleo del paquete `api_users`.

Define el blueprint para administración de usuarios y el serializer común.
"""
from flask import Blueprint

from app.models import User


bp = Blueprint('api_users', __name__, url_prefix='/api/users')


_ROLE_ORDER = {
    'super_admin': 0,
    'sistemas': 1,
    'admin': 2,
    'finanzas': 3,
    'coordinador': 4,
    'inventario': 5,
    'solicitante_material': 6,
}

# super_admin nunca se puede crear/asignar desde este endpoint (no está en
# _VALID_NEW_ROLES); esa cuenta solo se crea por seeding/manual. Es la última
# línea de recuperación si la cuenta de `sistemas` queda inaccesible.
#
# `sistemas` SÍ está en la lista: es TI/soporte y necesita poder dar de alta a
# sus pares. Pero solo un `sistemas` o un `super_admin` puede llegar a este
# endpoint (ver `require_gestion_usuarios`), así que un admin de RH no puede
# fabricarse un compañero con control total.
_VALID_NEW_ROLES = {
    'sistemas', 'admin', 'finanzas', 'coordinador', 'inventario', 'solicitante_material',
}


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
        'activo': bool(u.activo),
        'last_seen': u.last_seen.isoformat() if u.last_seen else None,
        # Liga opcional a Trabajador (RRHH) — habilita asignaciones de
        # herramienta y filtros "lo mío" basados en empleado.
        'trabajador_id': u.trabajador_id,
        'trabajador_no_empleado': t.no_empleado if t else None,
        'trabajador_nombre': t.nombre_completo if t else None,
    }
