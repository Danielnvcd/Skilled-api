"""PDF de la orden de compra + link de WhatsApp al proveedor."""
from flask import jsonify, request, send_file

from app.extensions import db

from .._core import bp, _audit, cantidad_legible
from ..etiquetas import _render_oc_express_pdf, _whatsapp_link
from ._core import _require_compras, _load_compra


# ─── PDF de la solicitud de compra ────────────────────────────────────────────

@bp.route('/solicitudes-compra/<int:sol_id>/pdf', methods=['GET'])
@_require_compras
def imprimir_solicitud_compra(sol_id: int):
    """Genera el PDF de la orden (reutiliza la plantilla de OC express) y expone
    el link de WhatsApp en el header `X-Whatsapp-Link`."""
    sol = _load_compra(sol_id)
    if not sol:
        return jsonify({'detail': 'Solicitud de compra no encontrada'}), 404

    proveedor = (sol.proveedor_sugerido or '').strip() or 'Sin proveedor'
    contacto = (sol.proveedor_contacto or '').strip()
    fecha_str = sol.fecha_creacion.strftime('%d/%m/%Y %H:%M') if sol.fecha_creacion else ''
    solicitante = (sol.solicitado_por.full_name or sol.solicitado_por.username) if sol.solicitado_por else '—'

    items_view = []
    for d in (sol.detalles or []):
        prod = d.producto
        items_view.append({
            'codigo': (prod.codigo if prod else None) or '—',
            'descripcion': (prod.descripcion if prod else None) or d.descripcion_libre or '—',
            'unidad': d.unidad or (prod.unidad if prod else '') or '',
            'cantidad': cantidad_legible(d.cantidad_solicitada),
        })

    pdf = _render_oc_express_pdf(
        folio=sol.folio, fecha_str=fecha_str,
        proveedor=proveedor, contacto=contacto,
        notas=(sol.notas or ''), solicitante=solicitante,
        items=items_view,
    )
    if pdf is None:
        return jsonify({'detail': 'Error al generar el PDF (xhtml2pdf no disponible)'}), 500

    _audit(request.current_user, f"PDF solicitud de compra {sol.folio}")
    db.session.commit()

    response = send_file(pdf, mimetype='application/pdf', as_attachment=False, download_name=f'{sol.folio}.pdf')
    wa_link = _whatsapp_link(proveedor, contacto, sol.folio, items_view)
    response.headers['X-Whatsapp-Link'] = wa_link
    response.headers['X-Folio'] = sol.folio
    response.headers['Access-Control-Expose-Headers'] = 'X-Whatsapp-Link, X-Folio'
    return response
