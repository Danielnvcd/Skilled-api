"""Paquete `api_hikvision` — API de la integración con lectores biométricos.

Solo rol admin/super_admin (eje RRHH). Importar este paquete registra todas las
rutas en `bp` como efecto de los `@bp.route(...)` de cada submódulo.

  _core.py           blueprint, helpers de listado y permisos
  dispositivos.py    CRUD de lectores + probar conexión
  puerta.py          parámetros, estado en vivo y apertura remota
  sincronizacion.py  candidatos, sincronizar, quitar del lector, cambiar foto
  equipo.py          estado del equipo, reloj, auditoría ERP vs. equipo
  actividad.py       accesos permitidos/rechazados y sus capturas

La lógica ISAPI NO vive aquí: está en `app/services/hikvision/`.
"""
from ._core import bp

from . import dispositivos      # noqa: F401  /dispositivos*
from . import puerta            # noqa: F401  /dispositivos/<id>/puerta*
from . import sincronizacion    # noqa: F401  /dispositivos/<id>/empleados*
from . import equipo            # noqa: F401  /dispositivos/<id>/estado|hora|auditoria
from . import actividad         # noqa: F401  /dispositivos/<id>/eventos*

__all__ = ['bp']
