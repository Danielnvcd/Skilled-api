"""Etiquetas imprimibles (Pausa 8a) + Órdenes de compra express (Pausa 9).

Etiquetas:
  - PDF de etiquetas Avery 5160 (30/hoja) o 5163 (10/hoja), con Code128 o QR.

OC express:
  - Sugiere cantidades a comprar agrupadas por proveedor default.
  - Genera PDF "throw-away" listo para enviar al proveedor + link de WhatsApp.
"""
import io
import os
import datetime
import re as _re
from decimal import Decimal
from urllib.parse import quote

import qrcode
from flask import jsonify, request, send_file, render_template, current_app
from marshmallow import fields, validate

from app.extensions import db, limiter, get_real_client_ip_flask
from app.models import Producto, MovimientoInventario

from ._core import (
    bp,
    _require_inventario, _require_inventario_admin,
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
    items = fields.List(
        fields.Nested(_EtiquetaItemSchema),
        required=True,
        validate=validate.Length(min=1, max=200),
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
      - items: [{producto_id, cantidad}], ≥1 línea, ≤200 líneas.

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


# ─── Compras express (Pausa 9) ───────────────────────────────────────────────
#
# Cierra el ciclo Bajo mínimo → Orden de Compra sin construir aún el módulo
# completo de proveedores. La sugerencia de cantidad usa la misma fórmula de
# consumo que Bajo mínimo (Pausa 5) — la diferencia es que aquí se agrupa por
# `Producto.proveedor_default_nombre` para que un PDF cubra a un solo
# proveedor por vez (más práctico para enviar por WhatsApp).
#
# Endpoints:
#   POST /ordenes-compra/express/sugerencia  → JSON con grupos por proveedor
#   POST /ordenes-compra/express/pdf         → PDF (binario) + WhatsApp link
#                                              en header X-Whatsapp-Link
#
# Sin migración nueva más allá de las columnas `proveedor_default_*` ya
# agregadas. El número de OC es un folio efímero (`OCE-YYYYMMDDHHMMSS`); no se
# persiste la orden porque la consideramos un PDF "throw-away" — el inventario
# real se actualiza cuando llega la entrada al almacén.

# Topes generosos para soportar inventarios grandes (2000+ productos bajo
# mínimo en un solo proveedor o en "Sin proveedor"). El PDF con miles de
# ítems funciona, pero xhtml2pdf no es rápido con tablas enormes — la
# generación puede tardar varios segundos. Si se vuelve un problema,
# considerar migrar a reportlab nativo o paginar en cliente.
OC_EXPRESS_MAX_PDF_ITEMS = 10_000
OC_EXPRESS_MAX_SUGERENCIA_ITEMS = 10_000


class _OCExpressItemSchema(_BaseSchema):
    """Línea de la orden, ya editada por el usuario en el modal de preview."""
    producto_id = fields.Int(required=True)
    cantidad = fields.Float(required=True, validate=validate.Range(min=0.01, max=1_000_000))


class OCExpressPdfSchema(_BaseSchema):
    proveedor = fields.Str(required=True, validate=validate.Length(min=1, max=150))
    contacto = fields.Str(load_default='', allow_none=True, validate=validate.Length(max=150))
    notas = fields.Str(load_default='', allow_none=True, validate=validate.Length(max=2000))
    items = fields.List(
        fields.Nested(_OCExpressItemSchema),
        required=True,
        validate=validate.Length(min=1, max=OC_EXPRESS_MAX_PDF_ITEMS),
    )


class OCExpressSugerenciaSchema(_BaseSchema):
    producto_ids = fields.List(
        fields.Int(),
        required=True,
        validate=validate.Length(min=1, max=OC_EXPRESS_MAX_SUGERENCIA_ITEMS),
    )


def _cantidad_sugerida_para_30d(stock_actual: float, stock_minimo: float,
                                  consumo_diario: float) -> float:
    """Cantidad a comprar para cubrir 30 días + reponer el mínimo.

    Formula:   (consumo_diario * 30) - stock_actual + stock_minimo
    Si el resultado da negativo (stock ya cubre el mes), devolvemos
    `max(0, stock_minimo - stock_actual)` para al menos volver al mínimo.
    Redondeo: hacia arriba a 2 decimales.
    """
    necesidad_30d = (consumo_diario * 30.0) - stock_actual + stock_minimo
    if necesidad_30d <= 0:
        # Aún hay buffer para el mes, pero podemos estar bajo el mínimo.
        necesidad_30d = max(0.0, stock_minimo - stock_actual)
    # Redondeo a 2 decimales hacia arriba.
    import math
    return math.ceil(necesidad_30d * 100) / 100.0


@bp.route('/ordenes-compra/express/sugerencia', methods=['POST'])
@limiter.limit('20/minute', key_func=lambda: f"ip:{get_real_client_ip_flask()}")
@_require_inventario_admin
def oc_express_sugerencia():
    """Calcula sugerencia de compra para un set de productos y los agrupa
    por proveedor default. El frontend usa esta respuesta para armar el modal
    de preview antes de generar el PDF.

    Body: `{ producto_ids: [int, ...] }` (1..100).

    Response 200:
    ```
    {
      "grupos": [
        {
          "proveedor": "Cementos del Norte" | "Sin proveedor",
          "contacto": "55 1234 5678" | "",
          "items": [
            {
              "producto_id": 1, "codigo": "CEM-50", "descripcion": "...",
              "unidad": "saco", "stock_actual": 5.0, "stock_minimo": 20.0,
              "consumo_promedio_30d": 1.2, "cantidad_sugerida": 27.0
            }
          ]
        }
      ]
    }
    ```
    """
    data, err = _parse_or_422(OCExpressSugerenciaSchema(), request.get_json(silent=True))
    if err: return err

    ids = list(dict.fromkeys(data['producto_ids']))  # dedupe preservando orden
    productos = {
        p.id: p for p in Producto.query.filter(
            Producto.id.in_(ids),
            Producto.activo == True,  # noqa: E712
        ).all()
    }
    faltantes = [i for i in ids if i not in productos]
    if faltantes:
        return jsonify({
            'detail': f'Productos no encontrados o inactivos: {faltantes}',
        }), 404

    # Consumo en una sola query (anti N+1), igual que el endpoint bajo-mínimo.
    hace_30 = datetime.datetime.now() - datetime.timedelta(days=30)
    consumos = dict(
        db.session.query(
            MovimientoInventario.producto_id,
            db.func.coalesce(db.func.sum(MovimientoInventario.cantidad), 0),
        )
        .filter(
            MovimientoInventario.producto_id.in_(ids),
            MovimientoInventario.tipo == 'SALIDA',
            MovimientoInventario.fecha >= hace_30,
        )
        .group_by(MovimientoInventario.producto_id)
        .all()
    )

    # Construimos los items y los agrupamos por proveedor default.
    grupos: dict[str, dict] = {}
    for pid in ids:
        p = productos[pid]
        consumo_total = float(consumos.get(p.id, 0) or 0)
        consumo_diario = round(consumo_total / 30.0, 2)
        stock = float(p.stock_actual or 0)
        minimo = float(p.stock_minimo or 0)
        sugerida = _cantidad_sugerida_para_30d(stock, minimo, consumo_diario)

        # Normalizamos el nombre del proveedor: vacío/None → "Sin proveedor"
        # para que igual aparezca en el preview (el usuario completa después).
        prov = (p.proveedor_default_nombre or '').strip() or 'Sin proveedor'
        contacto = (p.proveedor_default_contacto or '').strip()

        if prov not in grupos:
            grupos[prov] = {'proveedor': prov, 'contacto': contacto, 'items': []}
        else:
            # Si dos productos del grupo tienen contactos distintos, conservamos
            # el primero — el usuario puede sobreescribir en el modal.
            if not grupos[prov]['contacto'] and contacto:
                grupos[prov]['contacto'] = contacto

        grupos[prov]['items'].append({
            'producto_id': p.id,
            'codigo': p.codigo,
            'descripcion': p.descripcion,
            'unidad': p.unidad,
            'stock_actual': stock,
            'stock_minimo': minimo,
            'consumo_promedio_30d': consumo_diario,
            'cantidad_sugerida': sugerida,
        })

    return jsonify({'grupos': list(grupos.values())})


def _whatsapp_link(proveedor: str, contacto: str, folio: str,
                    items: list[dict]) -> str:
    """Construye un enlace `https://wa.me/<num>?text=...` con un resumen
    listo para enviar al proveedor.

    - Si el contacto incluye dígitos, los extrae y los usa como número.
      Caso contrario, `wa.me/?text=` (el usuario elige el chat).
    - Solo soporta números MX +52 cuando el contacto trae 10 dígitos sin
      prefijo (`5512345678` → `525512345678`).
    """
    digitos = _re.sub(r'\D', '', contacto or '')
    if digitos and len(digitos) == 10:
        digitos = '52' + digitos  # asumimos MX
    elif len(digitos) > 15:
        digitos = digitos[:15]  # E.164 máximo

    lineas = [f'Orden de compra {folio}', f'Proveedor: {proveedor}', '']
    for it in items[:40]:  # WhatsApp truncará si pasa de ~2k chars
        lineas.append(f"• {it['codigo']} — {it['descripcion']} · {it['cantidad']} {it['unidad']}")
    if len(items) > 40:
        lineas.append(f'... y {len(items) - 40} ítems más (ver PDF adjunto)')
    texto = '\n'.join(lineas)

    base = f'https://wa.me/{digitos}' if digitos else 'https://wa.me/'
    return f'{base}?text={quote(texto)}'


def _render_oc_express_pdf(*, folio: str, fecha_str: str, proveedor: str,
                            contacto: str, notas: str, solicitante: str,
                            items: list[dict]) -> io.BytesIO | None:
    """Genera el PDF con xhtml2pdf reutilizando el estilo de
    `solicitud_pedido_pdf.html` (mismo header azul, mismas tablas).
    Devuelve None si xhtml2pdf no está disponible.
    """
    try:
        from xhtml2pdf import pisa
    except ImportError:
        return None
    # API-only: la app se construye con `static_folder=None`, así que cualquier
    # asset (logo) tiene que resolverse contra BASE_DIR. Si el archivo no existe
    # el template se renderiza sin logo (degradación benigna).
    base_dir = current_app.config.get('BASE_DIR') or os.path.dirname(current_app.root_path)
    logo_path = os.path.join(base_dir, 'static', 'imagenes', 'skilled (1).png')
    html_salida = render_template(
        'orden_compra_express_pdf.html',
        folio=folio,
        fecha=fecha_str,
        proveedor=proveedor,
        contacto=contacto,
        notas=notas,
        solicitante=solicitante,
        items=items,
        logo_path=logo_path if os.path.exists(logo_path) else None,
    )
    buf = io.BytesIO()
    status = pisa.CreatePDF(io.BytesIO(html_salida.encode('utf-8')), dest=buf)
    if status.err:
        return None
    buf.seek(0)
    return buf


@bp.route('/ordenes-compra/express/pdf', methods=['POST'])
@limiter.limit('10/minute', key_func=lambda: f"ip:{get_real_client_ip_flask()}")
@_require_inventario_admin
def oc_express_pdf():
    """Genera el PDF de la orden de compra express y devuelve binario PDF +
    el link de WhatsApp en el header `X-Whatsapp-Link` (URL-encoded).

    Body:
    ```
    {
      "proveedor": "Cementos del Norte",
      "contacto": "55 1234 5678",
      "notas": "Entregar en planta 2",
      "items": [{"producto_id": 1, "cantidad": 27.0}, ...]
    }
    ```

    Reglas:
      - Productos deben existir y estar activos.
      - Sin items → 422.
      - Tope `OC_EXPRESS_MAX_PDF_ITEMS` (anti-DoS).
      - NO persiste la orden — es un PDF "throw-away" para enviar al proveedor.
      - El descuento/entrada al stock se hace por separado vía /movimientos.
    """
    data, err = _parse_or_422(OCExpressPdfSchema(), request.get_json(silent=True))
    if err: return err

    # Dedupe + carga
    ids = list({it['producto_id'] for it in data['items']})
    if len(ids) != len(data['items']):
        return jsonify({'detail': 'Productos duplicados en items'}), 422

    productos = {
        p.id: p for p in Producto.query.filter(
            Producto.id.in_(ids),
            Producto.activo == True,  # noqa: E712
        ).all()
    }
    faltantes = [i for i in ids if i not in productos]
    if faltantes:
        return jsonify({
            'detail': f'Productos no encontrados o inactivos: {faltantes}',
        }), 404

    user = request.current_user
    folio = 'OCE-' + datetime.datetime.now().strftime('%Y%m%d%H%M%S')
    fecha_str = datetime.datetime.now().strftime('%d/%m/%Y %H:%M')
    proveedor = data['proveedor'].strip()
    contacto = (data.get('contacto') or '').strip()
    notas = (data.get('notas') or '').strip()
    solicitante = user.full_name or user.username

    # Items decorados (con código/descripción/unidad para el PDF).
    items_view = []
    for it in data['items']:
        p = productos[it['producto_id']]
        cant = float(it['cantidad'])
        cant_int_or_float = int(cant) if cant % 1 == 0 else round(cant, 2)
        items_view.append({
            'codigo': p.codigo,
            'descripcion': p.descripcion,
            'unidad': p.unidad,
            'cantidad': cant_int_or_float,
        })

    pdf = _render_oc_express_pdf(
        folio=folio, fecha_str=fecha_str,
        proveedor=proveedor, contacto=contacto, notas=notas,
        solicitante=solicitante, items=items_view,
    )
    if pdf is None:
        return jsonify({'detail': 'Error al generar el PDF (xhtml2pdf no disponible)'}), 500

    wa_link = _whatsapp_link(proveedor, contacto, folio, items_view)

    _audit(user, f"OC express PDF {folio} → {proveedor} ({len(items_view)} ítems)")
    db.session.commit()

    response = send_file(
        pdf,
        mimetype='application/pdf',
        as_attachment=False,
        download_name=f'{folio}.pdf',
    )
    response.headers['X-Whatsapp-Link'] = wa_link
    response.headers['X-Folio'] = folio
    # Expose para que el SPA pueda leer estos headers desde JS (CORS).
    response.headers['Access-Control-Expose-Headers'] = 'X-Whatsapp-Link, X-Folio'
    return response
