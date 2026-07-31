"""Reportes Excel del módulo de Inventario (Pausa 6).

  _excel.py       estilos, streaming del .xlsx y parseo de fechas (compartido)
  existencias.py  inventario actual con valor por producto
  movimientos.py  bitácora del periodo y kardex de un producto
  consumo.py      material entregado por proyecto y solicitudes

Importar el paquete registra las rutas de todos los submódulos en `bp`.
"""
from . import existencias  # noqa: F401  /reportes/inventario-actual.xlsx
from . import movimientos  # noqa: F401  /reportes/{movimientos,kardex}.xlsx
from . import consumo      # noqa: F401  /reportes/{consumo-proyecto,solicitudes}.xlsx
