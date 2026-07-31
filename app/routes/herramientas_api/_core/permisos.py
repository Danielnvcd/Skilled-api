"""Autorización del módulo de Herramientas.

**Único punto de acoplamiento con `inventario_api`.** Herramientas comparte con
Inventario los mismos roles y la misma bitácora, así que reusa sus guards en vez
de duplicarlos. Antes cada módulo de rutas hacía su propio
`from app.routes.inventario_api import ...` (8 imports repartidos); ahora entran
por aquí y el resto del paquete solo conoce `._core`.

Si algún día Herramientas necesita su propio recorte de roles, se cambia aquí y
no en siete archivos.
"""
from app.models import HerramientaUnidad, User
from app.routes.inventario_api import (
    CODIGO_REGEX,
    _IMAGEN_URL_REGEX,
    _audit,
    _int_arg,
    _parse_or_422,
    _require_inventario,
    _require_inventario_admin,
    _require_login,
)

__all__ = [
    '_require_login', '_require_inventario', '_require_inventario_admin',
    '_parse_or_422', '_int_arg', '_audit',
    'CODIGO_REGEX', '_IMAGEN_URL_REGEX',
    '_puede_ver_unidad', '_redactar_para_rol',
]


def _puede_ver_unidad(user: User, unidad: HerramientaUnidad) -> bool:
    """Inventario y admin ven todo. Solicitante/coordinador solo ven unidades
    asignadas a su trabajador."""
    if user.role in ('inventario', 'admin', 'super_admin'):
        return True
    if user.role in ('solicitante_material', 'coordinador') and user.trabajador_id:
        return unidad.asignado_trabajador_id == user.trabajador_id
    return False


def _redactar_para_rol(user) -> bool:
    """True si al usuario hay que ocultarle los campos sensibles de la unidad.

    Los campos administrativos/financieros/logísticos NO deben salir hacia roles
    "solicitantes" (coordinador): solo necesitan identificar la herramienta y
    pedir baja. Se redacta en el backend para que no se obtengan ni llamando la
    API directo — ver `_CAMPOS_SENSIBLES_UNIDAD` en `serializers.py`.
    """
    return bool(user) and getattr(user, 'role', None) == 'coordinador'
