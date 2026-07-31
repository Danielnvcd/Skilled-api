"""Etiquetas Avery imprimibles con código de barras o QR (Pausa 8a)."""
import datetime
import io

import qrcode
from flask import jsonify, request, send_file
from marshmallow import fields, validate

from app.extensions import db, limiter, get_real_client_ip_flask
from app.models import Producto

from .._core import (
    bp,
    _require_inventario,
    _parse_or_422,
    _BaseSchema,
    _audit,
)


# ─── Etiquetas imprimibles (Pausa 8a) ────────────────────────────────────────

class _EtiquetaItemSchema(_BaseSchema):
    producto_id = fields.Int(required=True)
    cantidad = fields.Int(required=True, validate=validate.Range(min=1, max=500))


class EtiquetasPdfSchema(_BaseSchema):
    formato = fields.Str(load_default='avery_5160',
                          validate=validate.OneOf(['avery_5160', 'avery_5163']))
    tipo = fields.Str(load_default='barcode',
                       validate=validate.OneOf(['barcode', 'qr']))
    # max=500 alineado con ETIQUETAS_MAX_TOTAL: el tope real de seguridad es el
    # total de etiquetas (suma de cantidades), no el número de líneas. Con
    # cantidad 1 por línea, "seleccionar todos" puede mandar hasta 500 productos.
    items = fields.List(
        fields.Nested(_EtiquetaItemSchema),
        required=True,
        validate=validate.Length(min=1, max=500),
    )


# Especificaciones físicas de las hojas Avery más comunes (página Letter 8.5"×11").
# Medidas en pulgadas; convertimos a puntos con `inch` al usar reportlab.
ETIQUETA_FORMATOS = {
    # 30 etiquetas/hoja (3 columnas × 10 filas), 2.625" × 1".
    'avery_5160': {
        'cols': 3, 'rows': 10,
        'label_w': 2.625, 'label_h': 1.0,
        'col_gap': 0.125, 'row_gap': 0.0,
        'top_margin': 0.5, 'left_margin': 0.1875,
        'descripcion': 'Avery 5160 — 30 etiquetas/hoja',
    },
    # 10 etiquetas/hoja (2 columnas × 5 filas), 4" × 2".
    'avery_5163': {
        'cols': 2, 'rows': 5,
        'label_w': 4.0, 'label_h': 2.0,
        'col_gap': 0.125, 'row_gap': 0.0,
        'top_margin': 0.5, 'left_margin': 0.15625,
        'descripcion': 'Avery 5163 — 10 etiquetas/hoja',
    },
}

# Tope de seguridad: PDFs con > 500 etiquetas se rechazan (evita DoS por
# generación masiva accidental — un usuario puede pedir 200 productos × 500
# cantidad sin este check). 500 cabe en ~17 hojas Avery 5160.
ETIQUETAS_MAX_TOTAL = 500


def _truncate_text(s: str, max_chars: int) -> str:
    s = (s or '').strip()
    if len(s) <= max_chars:
        return s
    return s[:max_chars - 1].rstrip() + '…'


def _draw_etiqueta(c, x, y, w, h, prod, tipo):
    """Dibuja una etiqueta en (x, y) (esquina inferior-izquierda en coord. reportlab).

    Layout:
      - barcode: descripción arriba (2 líneas), código de barras al centro,
        texto del código abajo.
      - qr: QR cuadrado a la izquierda, descripción y código a la derecha.
    """
    from reportlab.lib.units import inch
    from reportlab.graphics.barcode.code128 import Code128
    from reportlab.lib.utils import ImageReader

    pad = 0.06 * inch
    codigo = prod.codigo or ''
    descripcion = prod.descripcion or ''
    categoria = prod.categoria or ''
    unidad = prod.unidad or ''

    if tipo == 'qr':
        # QR ocupa ~ alto de la etiqueta menos padding. Cuadrado.
        qr_size = h - 2 * pad
        # Generamos PIL Image y la metemos al canvas como ImageReader.
        img = qrcode.make(codigo)
        img_buf = io.BytesIO()
        img.save(img_buf, format='PNG')
        img_buf.seek(0)
        c.drawImage(ImageReader(img_buf), x + pad, y + pad,
                    width=qr_size, height=qr_size, preserveAspectRatio=True)

        # Texto a la derecha del QR.
        text_x = x + pad + qr_size + 0.05 * inch
        avail_w = w - (text_x - x) - pad
        max_chars = max(8, int(avail_w / (0.06 * inch)))  # heurística
        c.setFont('Helvetica-Bold', 9)
        c.drawString(text_x, y + h - 0.2 * inch, _truncate_text(descripcion, max_chars))
        c.setFont('Helvetica', 7)
        c.drawString(text_x, y + h - 0.32 * inch, _truncate_text(categoria, max_chars))
        c.setFont('Helvetica-Bold', 10)
        c.drawString(text_x, y + pad + 0.08 * inch, _truncate_text(codigo, max_chars))
        c.setFont('Helvetica', 6)
        c.drawString(text_x, y + pad, f'Unidad: {_truncate_text(unidad, 12)}')
    else:
        # Barcode Code128 + texto.
        # Descripción arriba.
        max_desc = 32 if w < 3.5 * 72 else 50
        c.setFont('Helvetica-Bold', 8)
        c.drawString(x + pad, y + h - 0.16 * inch, _truncate_text(descripcion, max_desc))
        c.setFont('Helvetica', 6)
        c.drawString(x + pad, y + h - 0.27 * inch, _truncate_text(categoria, max_desc))

        # Code128 centrado horizontalmente.
        # barWidth ajustado para que quepa "razonablemente" en 2.4" (5160) o 3.8" (5163).
        bar_h = 0.30 * inch if h < 1.5 * inch else 0.50 * inch
        bar_w = 0.011 * inch if w < 3.5 * inch else 0.014 * inch
        bc = Code128(codigo, barHeight=bar_h, barWidth=bar_w, humanReadable=False)
        # Centrar
        bc_w = bc.width
        bc_x = x + max(pad, (w - bc_w) / 2)
        bc_y = y + (0.20 * inch if h < 1.5 * inch else 0.40 * inch)
        bc.drawOn(c, bc_x, bc_y)

        # Texto del código abajo (centrado).
        c.setFont('Helvetica-Bold', 8)
        c.drawCentredString(x + w / 2, y + 0.08 * inch, _truncate_text(codigo, 24))


def _generar_etiquetas_pdf(productos_expandidos, formato: str, tipo: str) -> io.BytesIO:
    """productos_expandidos: lista plana de Producto, uno por etiqueta a imprimir.
    El orden determina la posición (izq→der, arriba→abajo, hoja por hoja).
    """
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.pdfgen import canvas

    fmt = ETIQUETA_FORMATOS[formato]
    page_w, page_h = letter

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.setTitle(f'Etiquetas {fmt["descripcion"]}')

    label_w = fmt['label_w'] * inch
    label_h = fmt['label_h'] * inch
    col_gap = fmt['col_gap'] * inch
    row_gap = fmt['row_gap'] * inch
    top_margin = fmt['top_margin'] * inch
    left_margin = fmt['left_margin'] * inch
    cols = fmt['cols']
    rows = fmt['rows']
    per_page = cols * rows

    for idx, prod in enumerate(productos_expandidos):
        pos = idx % per_page
        if pos == 0 and idx > 0:
            c.showPage()
        row = pos // cols
        col = pos % cols
        x = left_margin + col * (label_w + col_gap)
        # En reportlab origen = bottom-left, así que para fila r desde arriba:
        y = page_h - top_margin - (row + 1) * label_h - row * row_gap
        _draw_etiqueta(c, x, y, label_w, label_h, prod, tipo)

    c.save()
    buf.seek(0)
    return buf


@bp.route('/etiquetas/pdf', methods=['POST'])
@limiter.limit(
    "10/minute",
    key_func=lambda: f"ip:{get_real_client_ip_flask()}",
)
@_require_inventario
def generar_etiquetas_pdf():
    """Genera un PDF de etiquetas Avery (5160 o 5163) con código de barras o QR.

    Body:
      - formato: 'avery_5160' (default, 30/hoja) | 'avery_5163' (10/hoja).
      - tipo: 'barcode' (Code128, default) | 'qr'.
      - items: [{producto_id, cantidad}], ≥1 línea, ≤500 líneas.

    Reglas:
      - Tope global: 500 etiquetas por PDF.
      - Producto debe existir y estar activo.
    """
    data, err = _parse_or_422(EtiquetasPdfSchema(), request.get_json(silent=True))
    if err: return err

    items = data['items']
    total = sum(int(it['cantidad']) for it in items)
    if total > ETIQUETAS_MAX_TOTAL:
        return jsonify({
            'detail': f'Total de etiquetas ({total}) excede el tope de {ETIQUETAS_MAX_TOTAL}'
        }), 422

    # Cargamos todos los productos en una sola query.
    ids = [it['producto_id'] for it in items]
    productos = {
        p.id: p for p in Producto.query.filter(Producto.id.in_(ids), Producto.activo == True).all()  # noqa: E712
    }
    faltantes = [i for i in ids if i not in productos]
    if faltantes:
        return jsonify({
            'detail': f'Productos no encontrados o inactivos: {faltantes}',
        }), 404

    # Expandimos a una lista plana (una entrada por etiqueta) respetando el
    # orden del payload — el front controla en qué orden salen las hojas.
    expandidos = []
    for it in items:
        prod = productos[it['producto_id']]
        expandidos.extend([prod] * int(it['cantidad']))

    pdf = _generar_etiquetas_pdf(expandidos, data['formato'], data['tipo'])

    _audit(
        request.current_user,
        f"Etiquetas PDF ({data['formato']}, {data['tipo']}, {total} etiquetas)",
    )
    db.session.commit()

    ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f'etiquetas_{data["formato"]}_{ts}.pdf'
    return send_file(
        pdf,
        mimetype='application/pdf',
        as_attachment=False,
        download_name=filename,
    )
