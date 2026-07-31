"""Movimientos de inventario: ENTRADA, SALIDA, AJUSTE, TRASPASO, REASIGNACION.

  registro.py  listado, alta (`_perform_movimiento`) y movimiento rápido de PWA
  vale.py      vale PDF de un movimiento ya registrado

`tomas` importa `_perform_movimiento` desde aquí para generar los AJUSTES del
cierre dentro de su propia transacción.
"""
from . import registro  # noqa: F401  /movimientos/ (GET, POST), /movimientos/rapido
from . import vale      # noqa: F401  /movimientos/<id>/pdf
from .registro import _perform_movimiento  # noqa: F401  lo usa tomas.cerrar_toma
