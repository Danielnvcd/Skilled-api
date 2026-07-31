"""API del panel de sistemas (TI/soporte) para el SPA.

Rol `sistemas` — y `super_admin` como cuenta de recuperación. Este eje de
permisos es INDEPENDIENTE del de `admin`: administra el sistema (cuentas,
sesiones, servidor, eventos de seguridad), no los datos de nómina.

Todos los endpoints exigen además 2FA activo: el rol puede crear cuentas y
revocar sesiones, así que una contraseña filtrada no debe alcanzar para entrar.
"""
from ._core import bp
from . import endpoints  # noqa: F401

__all__ = ['bp']
