"""Asignación de material a proyectos: previsualizar, aplicar, devolver.

  _comun.py       conversión de números y consumo de bucket con aborto
  validacion.py   arma el plan de líneas (ok / ajustada / error) sin escribir
  movimientos.py  aplica la asignación y la devolución (escribe stock + kardex)
  existencias.py  resúmenes por proyecto y stock libre (General)
  excel.py        plantilla e importación masiva

── Principio que NO se rompe ────────────────────────────────────────────────
Todo pasa por los mismos helpers de stock que usan los movimientos y genera un
`MovimientoInventario` por línea. Si algo modificara existencias sin quedar en
el kardex, el inventario dejaría de ser auditable.
"""
from . import movimientos  # noqa: F401  asignar/previsualizar, asignar, devolver
from . import existencias  # noqa: F401  resumen-asignacion, general/existencias
from . import excel        # noqa: F401  plantilla-asignacion, asignar/importar
