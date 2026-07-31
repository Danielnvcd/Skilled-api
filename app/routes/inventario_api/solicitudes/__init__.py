"""Solicitudes de material y herramienta — ciclo completo.

  crud.py             alta, listado y ubicaciones de una solicitud
  estado.py           transiciones de estatus + edición de cantidad aprobada
  entrega.py          entrega total/parcial de una solicitud APROBADA
  entrega_directa.py  surtido de mostrador (sin aprobación previa)
  pdf.py              PDF de la solicitud guardada y preview del carrito
  _comun.py           piezas compartidas por las dos formas de entregar

Importar el paquete registra las rutas de todos los submódulos en `bp`.
"""
from . import crud            # noqa: F401  /solicitudes/ (POST, GET), ubicaciones
from . import estado          # noqa: F401  /solicitudes/<id>/estado, detalles
from . import entrega         # noqa: F401  /solicitudes/<id>/entregar
from . import entrega_directa  # noqa: F401  /solicitudes/entrega-directa
from . import pdf             # noqa: F401  /solicitudes/<id>/pdf, preview-pdf
