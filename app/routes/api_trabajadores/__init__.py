"""Paquete `api_trabajadores` — API JSON del módulo de Empleados (SPA React).

Reusa el modelo `Trabajador` y los helpers internos del blueprint clásico
`trabajadores.py` (parser de fechas, guardado de foto, validaciones). La
autorización viene del JWT, no de la sesión: la función `_authorized` replica
`is_authorized_for_worker` leyendo el rol/usuario desde el JWT.

Originalmente un archivo de ~2060 líneas; ahora dividido por dominios:

  _core.py        bp + _authorized + serializers + _apply_payload (whitelist por rol)
  crud.py         listar, ficha-tecnica, obtener, crear, actualizar, dar_baja,
                  reactivar, bulk_accion
  timeline.py     /<id>/timeline (horas + ausencias + ajustes + préstamos)
  credenciales.py credenciales bulk + edición individual
  multimedia.py   foto (subir/get/thumb) + documentos (subir/descargar/eliminar)
  importar.py     plantilla-importar, procesar_excel_trabajadores, importar
  exportar.py     exportar_uno, bulk_exportar, exportar_todos

Importar este paquete registra TODOS los endpoints en `bp` (efecto colateral
de los `@bp.route(...)` en cada submódulo). El blueprint se registra desde
`app/__init__.py` igual que antes — el contrato externo no cambió.

Re-exports: `procesar_excel_trabajadores` lo usa la ruta legacy
`trabajadores.procesar_importacion` para reusar la misma lógica de validación.
"""
from ._core import bp
from .importar import procesar_excel_trabajadores

# Importar los submódulos provoca que sus @bp.route(...) registren las rutas
# en el blueprint compartido. Sin estos imports, el paquete no expondría
# ningún endpoint y la app arrancaría con un blueprint vacío.
from . import crud           # noqa: F401  listar, ficha-tecnica, obtener, crear, ...
from . import timeline       # noqa: F401  /<id>/timeline
from . import credenciales   # noqa: F401  /credenciales-lista, /<id>/credenciales
from . import multimedia     # noqa: F401  /<id>/foto*, /<id>/documentos*
from . import importar       # noqa: F401  /plantilla-importar, /importar
from . import exportar       # noqa: F401  /<id>/exportar, /bulk-exportar, /exportar-todos

__all__ = ['bp', 'procesar_excel_trabajadores']
