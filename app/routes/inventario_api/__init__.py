"""Paquete `inventario_api` — API JSON del módulo de Inventario.

Cada dominio es un paquete con sus rutas repartidas por responsabilidad, y el
núcleo compartido vive en `_core/`:

  _core/                bp, guards, schemas, serializers, stock, reservas, PDF
  productos/            catálogo: consultas, escritura, existencias, kardex
  almacenes/            bodegas y estantes (rejilla de celdas, QR)
  movimientos/          registro de movimientos (`_perform_movimiento`) y vale PDF
  solicitudes/          ciclo completo: alta, estado, entrega, entrega directa, PDF
  compras/              procura: alta, recepción → ENTRADA, PDF de la orden
  catalogo/             proyectos, categorías e importación/exportación Excel
  proyectos_materiales/ plan de materiales por proyecto (consulta y escritura)
  asignacion/           asignar/devolver material de un proyecto (+ carga Excel)
  etiquetas/            etiquetas Avery y órdenes de compra express
  reportes.py           reportes Excel (Pausa 6)
  tomas.py              tomas físicas de inventario (Pausa 10)
  imagenes.py           pipeline de imágenes de producto → WebP + R2

Importar este paquete registra TODOS los endpoints en `bp` (efecto colateral
de los `@bp.route(...)` en cada submódulo). El blueprint se registra desde
`app/__init__.py` igual que antes — el contrato externo no cambió.

Re-exports: los símbolos que `app/routes/herramientas_api.py` importa por
nombre (`from app.routes.inventario_api import ...`) deben quedar visibles
aquí. No quitar de la lista `__all__` sin actualizar también herramientas_api.
"""
from ._core import (
    bp,
    _require_login, _require_inventario, _require_inventario_admin,
    _parse_or_422, _int_arg, _audit,
    CODIGO_REGEX, _IMAGEN_URL_REGEX,
)

# Importar los submódulos provoca que sus @bp.route(...) registren las rutas
# en el blueprint compartido. Sin estos imports, el paquete no expondría
# ningún endpoint y la app arrancaría con un blueprint vacío.
# Orden: `tomas` depende de `movimientos` (_perform_movimiento) y `compras` de
# `etiquetas` (PDF de la orden), así que esos dos van antes que sus
# dependientes. El resto no tiene dependencias entre sí.
from . import productos        # noqa: F401  registra /productos/*
from . import imagenes         # noqa: F401  registra /productos/imagenes/* (pipeline R2)
from . import almacenes        # noqa: F401  registra /almacenes/* y /estantes/*
from . import movimientos      # noqa: F401  registra /movimientos/*
from . import solicitudes      # noqa: F401  registra /solicitudes/* y PDFs
from . import etiquetas        # noqa: F401  registra /etiquetas/* y /ordenes-compra/*
from . import compras          # noqa: F401  registra /solicitudes-compra/* (procura)
from . import catalogo         # noqa: F401  registra /proyectos/, /categorias/*, importar
from . import proyectos_materiales  # noqa: F401  registra /proyectos-materiales/*
from . import asignacion       # noqa: F401  registra asignar/devolver por proyecto
from . import reportes         # noqa: F401  registra /reportes/*
from . import tomas            # noqa: F401  registra /tomas/*

__all__ = [
    'bp',
    '_require_login', '_require_inventario', '_require_inventario_admin',
    '_parse_or_422', '_int_arg', '_audit',
    'CODIGO_REGEX', '_IMAGEN_URL_REGEX',
]
