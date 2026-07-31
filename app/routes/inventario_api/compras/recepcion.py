"""Recepción de una compra: acumula lo recibido y da ENTRADA al stock."""
import datetime
from decimal import Decimal

from flask import jsonify, request
from sqlalchemy.orm import selectinload

from app.extensions import db, limiter, get_real_client_ip_flask
from app.models import (
    MovimientoInventario, Producto, SolicitudCompra, SolicitudCompraDetalle,
)
from app.realtime import emit_to_role

from .._core import (
    bp,
    ErrorDeNegocio, transaccion_de_stock,
    _parse_or_422, _audit,
    _depositar, _recalcular_caches,
    _unidad_permite_decimales,
    resolver_almacen_activo,
    _INV_ROLES,
)
from ._core import (
    _COMPRA_ROLES, _require_compras,
    RecibirCompraSchema,
    _compra_to_dict, _load_compra,
)


# ─── Recibir (atender) → ENTRADA al stock ─────────────────────────────────────

def _compra_completa(sol: SolicitudCompra) -> bool:
    """¿Ya llegó todo? Toda línea con solicitada > 0 debe tener recibida ≥ solicitada."""
    for d in (sol.detalles or []):
        solicitada = Decimal(str(d.cantidad_solicitada or 0))
        recibida = Decimal(str(d.cantidad_recibida or 0))
        if solicitada > 0 and recibida < solicitada:
            return False
    return True


@bp.route('/solicitudes-compra/<int:sol_id>/recibir', methods=['POST'])
@limiter.limit('20/minute', key_func=lambda: f"ip:{get_real_client_ip_flask()}")
@_require_compras
@transaccion_de_stock
def recibir_solicitud_compra(sol_id: int):
    """Recepción total o parcial. Por cada línea con producto del catálogo crea
    una ENTRADA al almacén destino (sube stock). Las líneas de texto libre solo
    acumulan `cantidad_recibida` (no hay producto que mover).

    Cuando todas las líneas con cantidad > 0 quedan completamente recibidas, la
    solicitud pasa a RECIBIDA. Si era PENDIENTE, una recepción la avanza a
    ORDENADA (compra que ya llegó sin haberse marcado como ordenada).
    """
    data, err = _parse_or_422(RecibirCompraSchema(), request.get_json(silent=True))
    if err:
        return err

    sol = (
        SolicitudCompra.query
        .options(selectinload(SolicitudCompra.detalles).joinedload(SolicitudCompraDetalle.producto))
        .filter(SolicitudCompra.id == sol_id)
        .first()
    )
    if not sol:
        return jsonify({'detail': 'Solicitud de compra no encontrada'}), 404
    if sol.estatus not in ('PENDIENTE', 'ORDENADA'):
        return jsonify({
            'detail': f'Solo solicitudes PENDIENTE u ORDENADA pueden recibirse (actual: {sol.estatus})'
        }), 409

    detalles_por_id = {d.id: d for d in (sol.detalles or [])}
    vistos: set[int] = set()
    # (det, delta) por recepción con cantidad > 0
    recepciones: list[tuple[SolicitudCompraDetalle, Decimal]] = []

    for item in data['recepciones']:
        det_id = item['detalle_id']
        if det_id in vistos:
            return jsonify({'detail': f'Línea #{det_id} duplicada en el payload'}), 422
        vistos.add(det_id)

        det = detalles_por_id.get(det_id)
        if not det:
            return jsonify({'detail': f'Línea #{det_id} no pertenece a la solicitud {sol.folio}'}), 422

        delta = Decimal(str(item['cantidad_recibida']))
        if delta < 0:
            return jsonify({'detail': f'Línea #{det_id}: cantidad_recibida no puede ser negativa'}), 422
        if delta == 0:
            continue
        unidad_det = det.unidad or (det.producto.unidad if det.producto else None)
        if not _unidad_permite_decimales(unidad_det) and delta != delta.to_integral_value():
            return jsonify({'detail': f'Línea #{det_id}: este ítem se recibe en cantidades enteras (sin decimales)'}), 422

        sol_c = Decimal(str(det.cantidad_solicitada or 0))
        rec_c = Decimal(str(det.cantidad_recibida or 0))
        pendiente = sol_c - rec_c
        if delta > pendiente:
            return jsonify({
                'detail': (
                    f'Línea #{det_id}: recibir {delta} excede el pendiente ({pendiente}). '
                    f'Solicitado {sol_c}, ya recibido {rec_c}.'
                )
            }), 422
        recepciones.append((det, delta))

    if not recepciones:
        return jsonify({'detail': 'Ninguna línea con cantidad mayor a 0 para recibir'}), 422

    # ¿Hay líneas con producto del catálogo? Solo entonces necesitamos almacén.
    hay_producto = any(det.producto_id for det, _ in recepciones)
    almacen_id = None
    if hay_producto:
        almacen_id = resolver_almacen_activo(
            data.get('almacen_destino_id'),
            mensaje_sin_bodegas='No hay bodegas registradas para registrar la entrada',
        ).id

    # Sumar deltas por producto (una línea puede repetir producto en otra línea).
    delta_por_producto: dict[int, Decimal] = {}
    for det, delta in recepciones:
        if det.producto_id:
            delta_por_producto[det.producto_id] = (
                delta_por_producto.get(det.producto_id, Decimal('0')) + delta
            )

    user = request.current_user
    motivo_base = (data.get('motivo') or '').strip() or f'Recepción compra {sol.folio}'

    # Lock determinístico (producto id asc) + ENTRADA por producto.
    productos_locked: dict[int, Producto] = {}
    for prod_id in sorted(delta_por_producto.keys()):
        producto = (
            Producto.query.with_for_update(nowait=True)
            .filter(Producto.id == prod_id).first()
        )
        if not producto:
            raise ErrorDeNegocio(f'Producto #{prod_id} no encontrado', 404)
        productos_locked[prod_id] = producto

        # ENTRADA al bucket del proyecto de la compra (feature stock por
        # proyecto): lo recibido para un proyecto queda etiquetado a él;
        # una compra sin proyecto cae en el bucket general.
        cant_total = delta_por_producto[prod_id]
        _depositar(prod_id, almacen_id, sol.proyecto_id, cant_total)
        db.session.add(MovimientoInventario(
            tipo='ENTRADA',
            producto_id=prod_id,
            cantidad=cant_total,
            almacen_destino_id=almacen_id,
            proyecto_destino_id=sol.proyecto_id,
            motivo=motivo_base,
            usuario_id=user.id,
        ))

    for producto in productos_locked.values():
        _recalcular_caches(producto, almacen_id)

    # Acumular cantidad_recibida por línea.
    for det, delta in recepciones:
        det.cantidad_recibida = Decimal(str(det.cantidad_recibida or 0)) + delta

    if _compra_completa(sol):
        sol.estatus = 'RECIBIDA'
        sol.fecha_cierre = datetime.datetime.now()
    elif sol.estatus == 'PENDIENTE':
        # Recepción parcial sobre una solicitud aún no ordenada: la avanzamos.
        sol.estatus = 'ORDENADA'
        if not sol.fecha_orden:
            sol.fecha_orden = datetime.datetime.now()

    _audit(
        user,
        f"Recepción compra {sol.folio} "
        f"({len(recepciones)} líneas{f', almacén #{almacen_id}' if almacen_id else ''}) "
        f"→ {sol.estatus}",
    )
    db.session.commit()

    sol = _load_compra(sol_id)
    emit_to_role(_COMPRA_ROLES, 'compra:changed', {
        'id': sol.id, 'action': 'recibida' if sol.estatus == 'RECIBIDA' else 'recepcion_parcial',
    })
    if hay_producto:
        # Subió stock real → refrescar catálogo / bajo-mínimo / kardex.
        emit_to_role(_INV_ROLES, 'movimiento:changed', {'origen': 'compra_recepcion', 'compra_id': sol.id})
        emit_to_role(_INV_ROLES, 'producto:changed', {'origen': 'compra_recepcion'})
    return jsonify(_compra_to_dict(sol))
