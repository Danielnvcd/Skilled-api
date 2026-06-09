"""API JSON para administración de usuarios (SPA React).

Solo accesible para admin/super_admin. Mantiene las protecciones del flujo
clásico: no se puede eliminar la propia cuenta ni al usuario `admin`; al
cambiar la contraseña se revocan todos los refresh tokens del usuario.
"""
from ._core import bp
from . import crud, foto, seguridad  # noqa: F401

__all__ = ['bp']
