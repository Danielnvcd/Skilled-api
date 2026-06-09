"""Paquete `api_ajustes` — API JSON para Ajustes (periodos Inbursa) — SPA React.

Espejo de `ajustes.py`. Maneja periodos mensuales que agrupan descuentos
individuales por trabajador, con bloqueo de solapamiento de fechas y de
eliminación de descuentos ya cobrados por prenómina.

Originalmente un archivo de ~510 líneas; dividido por dominios:

  _core.py      bp + helpers (_num, _periodo_row)
  periodos.py   listar, crear, detalle, cerrar, excel
  descuentos.py picker de trabajadores + agregar/eliminar/bulk-delete

Importar este paquete registra TODOS los endpoints en `bp` (efecto colateral
de los `@bp.route(...)` en cada submódulo). El blueprint se registra desde
`app/__init__.py` igual que antes — el contrato externo no cambió.
"""
from ._core import bp

from . import periodos    # noqa: F401  /periodos (listar/crear/detalle/cerrar/excel)
from . import descuentos  # noqa: F401  /trabajadores-disponibles, /descuentos/*

__all__ = ['bp']
