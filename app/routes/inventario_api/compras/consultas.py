"""Productos con una compra en curso: alimenta los indicadores del catálogo
y de bajo mínimo para no pedir dos veces lo mismo."""
from flask import jsonify

from app.extensions import db
from app.models import SolicitudCompra, SolicitudCompraDetalle

from .._core import bp
from ._core import _require_compras


# ─── Productos con compra activa (indicadores en catálogo / bajo-mínimo) ──────

@bp.route('/solicitudes-compra/productos-activos', methods=['GET'])
@_require_compras
def productos_con_compra_activa():
    """Devuelve, por producto, la compra activa (PENDIENTE u ORDENADA) que lo
    incluye. El SPA lo usa para marcar 'este producto ya tiene compra en curso'
    en el catálogo y en bajo-mínimo.

    Response: `[{producto_id, solicitud_id, folio, estatus, cantidad_solicitada,
    cantidad_recibida}]`. Si un producto está en varias compras activas se
    devuelve una fila por compra; el front se queda con la más reciente.
    """
    rows = (
        db.session.query(
            SolicitudCompraDetalle.producto_id,
            SolicitudCompra.id,
            SolicitudCompra.estatus,
            SolicitudCompraDetalle.cantidad_solicitada,
            SolicitudCompraDetalle.cantidad_recibida,
        )
        .join(SolicitudCompra, SolicitudCompra.id == SolicitudCompraDetalle.solicitud_compra_id)
        .filter(
            SolicitudCompraDetalle.producto_id.isnot(None),
            SolicitudCompra.estatus.in_(['PENDIENTE', 'ORDENADA']),
        )
        .order_by(SolicitudCompra.id.desc())
        .all()
    )
    return jsonify([
        {
            'producto_id': pid,
            'solicitud_id': sid,
            'folio': f'SC-{sid:06d}',
            'estatus': estatus,
            'cantidad_solicitada': float(c_sol or 0),
            'cantidad_recibida': float(c_rec or 0),
        }
        for (pid, sid, estatus, c_sol, c_rec) in rows
    ])
