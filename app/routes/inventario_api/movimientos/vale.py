"""Vale (PDF) de un movimiento, con espacio de firmas de quien entrega y recibe."""
from flask import jsonify, send_file
from sqlalchemy.orm import joinedload

from app.models import MovimientoInventario

from .._core import (
    bp,
    _require_inventario_admin,
    renderizar_pdf, cantidad_legible,
)


# ─── Vale (PDF) de un movimiento ─────────────────────────────────────────────

_TIPO_LABEL = {
    'ENTRADA': 'Entrada de mercancía',
    'SALIDA': 'Salida de mercancía',
    'AJUSTE': 'Ajuste de inventario',
    'TRASPASO': 'Traspaso entre bodegas',
    'REASIGNACION': 'Reasignación entre proyectos',
}


def _render_movimiento_pdf(mov: MovimientoInventario):
    """Vale PDF de un movimiento: producto, cantidad, bodegas, proyecto, motivo y
    las partes que entregan/reciben. Devuelve None si no se pudo generar."""
    prod = mov.producto
    return renderizar_pdf(
        'movimiento_vale_pdf.html',
        folio=f'MOV-{mov.id:06d}',
        tipo=mov.tipo,
        tipo_label=_TIPO_LABEL.get(mov.tipo, mov.tipo),
        fecha=mov.fecha.strftime('%d/%m/%Y %H:%M') if mov.fecha else '',
        producto_codigo=prod.codigo if prod else '—',
        producto_descripcion=prod.descripcion if prod else 'Producto eliminado',
        cable_tipo=(prod.cable_tipo if prod else None) or '',
        cable_calibre=(prod.cable_calibre if prod else None) or '',
        cantidad=cantidad_legible(mov.cantidad),
        unidad=prod.unidad if prod else '',
        almacen_origen=mov.almacen_origen.nombre if mov.almacen_origen else None,
        almacen_destino=mov.almacen_destino.nombre if mov.almacen_destino else None,
        proyecto_origen=mov.proyecto_origen.numero_proyecto if mov.proyecto_origen else None,
        proyecto_destino=mov.proyecto_destino.numero_proyecto if mov.proyecto_destino else None,
        motivo=mov.motivo or '',
        entrega=mov.entrega_display or '',
        recibe=mov.recibe_display or '',
    )


@bp.route('/movimientos/<int:mov_id>/pdf', methods=['GET'])
@_require_inventario_admin
def imprimir_movimiento(mov_id: int):
    """Vale PDF de un movimiento ya registrado: producto, cantidad, bodegas,
    proyecto, motivo y las partes (entrega/recibe) con espacio de firmas."""
    mov = (
        MovimientoInventario.query
        .options(
            joinedload(MovimientoInventario.producto),
            joinedload(MovimientoInventario.almacen_origen),
            joinedload(MovimientoInventario.almacen_destino),
            joinedload(MovimientoInventario.proyecto_origen),
            joinedload(MovimientoInventario.proyecto_destino),
            joinedload(MovimientoInventario.entrega_trabajador),
            joinedload(MovimientoInventario.recibe_trabajador),
        )
        .filter(MovimientoInventario.id == mov_id)
        .first()
    )
    if not mov:
        return jsonify({'detail': 'Movimiento no encontrado'}), 404
    pdf = _render_movimiento_pdf(mov)
    if pdf is None:
        return jsonify({'detail': 'Error al generar el PDF'}), 500
    return send_file(
        pdf, mimetype='application/pdf', as_attachment=False,
        download_name=f'MOV-{mov.id:06d}.pdf',
    )
