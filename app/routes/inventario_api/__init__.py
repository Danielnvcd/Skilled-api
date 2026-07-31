"""Paquete `inventario_api` — API JSON del módulo de Inventario.

Originalmente un solo archivo de ~4300 líneas; ahora dividido por dominios:

  _core.py        bp + decoradores + schemas + serializers + helpers de stock
  productos.py    catálogo de productos (CRUD, stocks, kardex)
  almacenes.py    almacenes + estantes + QR
  movimientos.py  ENTRADA / SALIDA / AJUSTE / TRASPASO (incluye _perform_movimiento)
  solicitudes.py  ciclo completo de solicitudes + PDF
  catalogo.py     proyectos + categorías + importación masiva desde Excel
  reportes.py     reportes Excel (Pausa 6)
  etiquetas.py    etiquetas Avery + órdenes de compra express (Pausas 8a y 9)
  tomas.py        tomas físicas de inventario (Pausa 10)

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
# Orden: tomas depende de movimientos (_perform_movimiento), así que movimientos
# debe importarse primero. El resto no tiene dependencias entre sí.
from . import productos        # noqa: F401  registra /productos/*
from . import imagenes         # noqa: F401  registra /productos/imagenes/* (pipeline R2)
from . import almacenes        # noqa: F401  registra /almacenes/* y /estantes/*
from . import movimientos      # noqa: F401  registra /movimientos/*
from . import solicitudes      # noqa: F401  registra /solicitudes/* y PDFs
from . import compras          # noqa: F401  registra /solicitudes-compra/* (procura)
from . import catalogo         # noqa: F401  registra /proyectos/, /categorias/*, importar
from . import proyectos_materiales  # noqa: F401  registra /proyectos-materiales/*
from . import asignacion       # noqa: F401  registra asignar/devolver por proyecto
from . import reportes         # noqa: F401  registra /reportes/*
from . import etiquetas        # noqa: F401  registra /etiquetas/* y /ordenes-compra/*
from . import tomas            # noqa: F401  registra /tomas/*

__all__ = [
    'bp',
    '_require_login', '_require_inventario', '_require_inventario_admin',
    '_parse_or_422', '_int_arg', '_audit',
    'CODIGO_REGEX', '_IMAGEN_URL_REGEX',
]
