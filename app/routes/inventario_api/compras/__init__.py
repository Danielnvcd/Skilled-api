"""Solicitudes de compra (procura): del pedido a la entrada al almacén.

  _core.py      guard de rol propio del módulo, schemas y serializers
  crud.py       alta, listado, cambio de estado y cancelación
  recepcion.py  recepción total/parcial → ENTRADA al stock
  consultas.py  productos con compra activa (indicadores del catálogo)
  pdf.py        orden de compra en PDF + link de WhatsApp

Importar el paquete registra las rutas de todos los submódulos en `bp`.
"""
from . import crud       # noqa: F401  /solicitudes-compra/ (POST, GET, PATCH, DELETE)
from . import recepcion  # noqa: F401  /solicitudes-compra/<id>/recibir
from . import consultas  # noqa: F401  /solicitudes-compra/productos-activos
from . import pdf        # noqa: F401  /solicitudes-compra/<id>/pdf
