"""Almacenes (bodegas) y estantes.

  bodegas.py   CRUD de bodegas, validación por QR y existencias por bodega
  estantes.py  CRUD de estantes, rejilla de celdas y QR imprimible

Importar el paquete registra las rutas de ambos submódulos en `bp`.
"""
from . import bodegas   # noqa: F401  /almacenes/*
from . import estantes  # noqa: F401  /estantes/*
