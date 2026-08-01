"""Comprobante (PDF) de movimientos, con espacio de firmas de quien entrega y recibe.

Un comprobante puede cubrir UN movimiento o los N de un lote: es el mismo documento.
Lo que cambia entre productos son las líneas, mientras que tipo, bodegas,
proyecto y las partes que firman son los mismos por definición dentro de una
tanda. Sacar N PDFs para lo que el almacenista entiende como una sola entrega
era pedirle que juntara papeles a mano.
"""
from flask import jsonify, request, send_file
from sqlalchemy.orm import joinedload

from app.models import MovimientoInventario

from .._core import (
    bp,
    _require_inventario_admin,
    renderizar_pdf, cantidad_legible,
)


# ─── Comprobante (PDF) de movimientos ────────────────────────────────────────

_TIPO_LABEL = {
    'ENTRADA': 'Entrada de mercancía',
    'SALIDA': 'Salida de mercancía',
    'AJUSTE': 'Ajuste de inventario',
    'TRASPASO': 'Traspaso entre bodegas',
    'REASIGNACION': 'Reasignación entre proyectos',
}

# Mismo tope que `MovimientoLoteSchema.items`: un comprobante que alguien va a
# firmar no puede tener más líneas de las que caben en una tanda.
_MAX_LINEAS_VALE = 100


def _cargar_movimientos(ids):
    """Movimientos por id con todo lo que el comprobante necesita, sin N+1 consultas.
    Devuelve la lista en el mismo orden en que se pidieron los ids."""
    movs = (
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
        .filter(MovimientoInventario.id.in_(ids))
        .all()
    )
    por_id = {m.id: m for m in movs}
    return [por_id[i] for i in ids if i in por_id]


def _linea(mov: MovimientoInventario):
    """Una fila del comprobante: qué producto y cuánto."""
    prod = mov.producto
    return {
        'folio': f'MOV-{mov.id:06d}',
        'codigo': prod.codigo if prod else '—',
        'descripcion': prod.descripcion if prod else 'Producto eliminado',
        'cable_tipo': (prod.cable_tipo if prod else None) or '',
        'cable_calibre': (prod.cable_calibre if prod else None) or '',
        'cantidad': cantidad_legible(mov.cantidad),
        'unidad': prod.unidad if prod else '',
    }


def _render_vale_pdf(movs):
    """Comprobante PDF de uno o varios movimientos: líneas de producto, bodegas,
    proyecto, motivo y las partes que entregan/reciben. None si falló el render.

    El encabezado se toma del primer movimiento: en un lote todos comparten
    tipo, bodegas, proyecto y partes, que es justo lo que hace que quepan en un
    mismo comprobante.
    """
    primero = movs[0]
    # El rango se calcula con el menor y el mayor, no con el primero y el último
    # de la lista: los ids llegan en el orden en que los pidió el cliente, así
    # que `?ids=9,3,5` habría impreso «MOV-000009 a MOV-000005» — un rango al
    # revés en un documento que se archiva y se audita.
    ids_ordenados = sorted(m.id for m in movs)
    return renderizar_pdf(
        'movimiento_vale_pdf.html',
        folio=f'MOV-{primero.id:06d}',
        # Con varias líneas el folio de una sola no identifica el comprobante; se
        # nombra el rango para poder rastrearlo en el historial.
        folio_detalle=(
            f'{len(movs)} movimientos · MOV-{ids_ordenados[0]:06d} a MOV-{ids_ordenados[-1]:06d}'
            if len(movs) > 1 else ''
        ),
        tipo=primero.tipo,
        tipo_label=_TIPO_LABEL.get(primero.tipo, primero.tipo),
        fecha=primero.fecha.strftime('%d/%m/%Y %H:%M') if primero.fecha else '',
        lineas=[_linea(m) for m in movs],
        almacen_origen=primero.almacen_origen.nombre if primero.almacen_origen else None,
        almacen_destino=primero.almacen_destino.nombre if primero.almacen_destino else None,
        proyecto_origen=primero.proyecto_origen.numero_proyecto if primero.proyecto_origen else None,
        proyecto_destino=primero.proyecto_destino.numero_proyecto if primero.proyecto_destino else None,
        motivo=primero.motivo or '',
        entrega=primero.entrega_display or '',
        recibe=primero.recibe_display or '',
    )


@bp.route('/movimientos/<int:mov_id>/pdf', methods=['GET'])
@_require_inventario_admin
def imprimir_movimiento(mov_id: int):
    """Comprobante PDF de un movimiento ya registrado: producto, cantidad, bodegas,
    proyecto, motivo y las partes (entrega/recibe) con espacio de firmas."""
    movs = _cargar_movimientos([mov_id])
    if not movs:
        return jsonify({'detail': 'Movimiento no encontrado'}), 404
    pdf = _render_vale_pdf(movs)
    if pdf is None:
        return jsonify({'detail': 'Error al generar el PDF'}), 500
    return send_file(
        pdf, mimetype='application/pdf', as_attachment=False,
        download_name=f'MOV-{mov_id:06d}.pdf',
    )


@bp.route('/movimientos/vale', methods=['GET'])
@_require_inventario_admin
def imprimir_vale_lote():
    """Comprobante PDF que cubre varios movimientos en un solo documento: `?ids=1,2,3`.

    Es lo que se imprime tras una tanda de POST /movimientos/lote. Se exige que
    sean del mismo tipo porque un comprobante que mezcla una entrada con una
    salida no es un documento que nadie pueda firmar.
    """
    crudos = (request.args.get('ids') or '').strip()
    if not crudos:
        return jsonify({'detail': 'Se requiere ids'}), 422
    try:
        ids = [int(x) for x in crudos.split(',') if x.strip()]
    except ValueError:
        return jsonify({'detail': 'ids debe ser una lista de enteros separados por coma'}), 422
    # Deduplicar conservando el orden: un id repetido pintaría la misma línea
    # dos veces (y además haría fallar el conteo de "existen todos") en lugar de
    # ser lo que es, una lista con un id de más.
    ids = list(dict.fromkeys(ids))
    if not ids:
        return jsonify({'detail': 'Se requiere al menos un id'}), 422
    if len(ids) > _MAX_LINEAS_VALE:
        return jsonify({'detail': f'Máximo {_MAX_LINEAS_VALE} movimientos por comprobante'}), 422

    movs = _cargar_movimientos(ids)
    if len(movs) != len(ids):
        faltan = sorted(set(ids) - {m.id for m in movs})
        return jsonify({'detail': f'Movimiento(s) inexistente(s): {faltan}'}), 404
    if len({m.tipo for m in movs}) > 1:
        return jsonify({'detail': 'Todos los movimientos del comprobante deben ser del mismo tipo'}), 422

    pdf = _render_vale_pdf(movs)
    if pdf is None:
        return jsonify({'detail': 'Error al generar el PDF'}), 500
    return send_file(
        pdf, mimetype='application/pdf', as_attachment=False,
        download_name=f'MOV-{movs[0].id:06d}{"-lote" if len(movs) > 1 else ""}.pdf',
    )
