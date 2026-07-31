"""Etiquetas imprimibles y órdenes de compra express.

  impresion.py   PDF de etiquetas Avery 5160/5163 con Code128 o QR
  oc_express.py  sugerencia de compra por proveedor + PDF y link de WhatsApp

`compras/pdf.py` reutiliza `_render_oc_express_pdf` y `_whatsapp_link` de
`oc_express`, así que ambos se re-exportan aquí.
"""
from . import impresion    # noqa: F401  /etiquetas/pdf
from . import oc_express   # noqa: F401  /ordenes-compra/express/*
from .oc_express import _render_oc_express_pdf, _whatsapp_link  # noqa: F401
