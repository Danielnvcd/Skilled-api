"""Cambios de estatus de una solicitud y edición de la cantidad aprobada.

El estatus manda sobre las reservas de stock: aprobar aparta, rechazar/reabrir
libera. Toda transición pasa por `_TRANSICIONES_VALIDAS` para que no existan
saltos de estado no contemplados.
"""
import datetime
from decimal import Decimal

from flask import jsonify, request
from sqlalchemy.orm import selectinload

from app.extensions import db
from app.models import SolicitudMaterial, SolicitudMaterialDetalle
from app.realtime import emit_to_role

from .._core import (
    bp,
    ErrorDeNegocio, transaccion_de_stock,
    _require_inventario_admin,
    _parse_or_422,
    SolicitudUpdateEstadoSchema, SolicitudDetallePatchSchema,
    _solicitud_to_dict, _solicitud_detalle_to_dict,
    _audit,
    _reservas_de_solicitud, _intentar_reservar, _liberar_reservas,
    _unidad_permite_decimales, _es_entero,
    _SOL_ROLES,
)


# Estados a los que puede pasar una solicitud desde cada estado.
_TRANSICIONES_VALIDAS = {
    'PENDIENTE':  {'APROBADA', 'RECHAZADA'},
    'APROBADA':   {'ENTREGADA', 'RECHAZADA', 'PENDIENTE'},
    'RECHAZADA':  {'PENDIENTE'},
    'ENTREGADA':  {'PENDIENTE'},
}


def _sembrar_cantidades_aprobadas(sol: SolicitudMaterial):
    """Pausa 8b: al APROBAR, siembra cantidad_aprobada = cantidad_solicitada en
    cada línea MATERIAL que aún esté en 0 (default del modelo). Así la reserva,
    la entrega parcial y el PATCH de detalle trabajan sobre un campo explícito y
    dejamos de depender del fallback a cantidad_solicitada."""
    for d in (sol.detalles or []):
        if d.tipo_item != 'MATERIAL' or not d.producto_id:
            continue
        if Decimal(str(d.cantidad_aprobada or 0)) == 0:
            d.cantidad_aprobada = Decimal(str(d.cantidad_solicitada or 0))


def _aplicar_reservas_de_transicion(sol: SolicitudMaterial, previo: str, nuevo: str):
    """Efecto de la transición sobre `Producto.stock_reservado`.

    La reserva/aprobación es GLOBAL por producto; el candado por proyecto se
    aplica en la ENTREGA (consumo proyecto→general). Las transiciones no
    listadas (PENDIENTE↔RECHAZADA, RECHAZADA→PENDIENTE) no tienen efecto porque
    no había nada apartado.
    """
    reservas = _reservas_de_solicitud(sol)

    if previo == 'PENDIENTE' and nuevo == 'APROBADA':
        errores = _intentar_reservar(reservas)
        if errores:
            raise ErrorDeNegocio(
                'No se puede aprobar: stock insuficiente', 409, errores=errores,
            )

    elif previo == 'ENTREGADA' and nuevo == 'PENDIENTE':
        # Reabrir entregada: re-reservar. Si el stock ya se movió a otra
        # solicitud entre tanto, falla.
        errores = _intentar_reservar(reservas)
        if errores:
            raise ErrorDeNegocio(
                'No se puede reabrir (entregada): stock ya no disponible', 409,
                errores=errores,
            )

    elif previo == 'APROBADA' and nuevo in ('RECHAZADA', 'PENDIENTE', 'ENTREGADA'):
        # Liberar lo que se había reservado al aprobar.
        _liberar_reservas(reservas)


def _marcar_resolucion(sol: SolicitudMaterial, nuevo: str, user):
    """Fecha de cierre y trazabilidad de quién resolvió la solicitud."""
    sol.estatus = nuevo
    sol.fecha_cierre = None if nuevo == 'PENDIENTE' else datetime.datetime.now()
    if nuevo == 'APROBADA':
        sol.aprobada_por_id = user.id
        sol.entregada_por_id = None
    elif nuevo == 'ENTREGADA':
        sol.entregada_por_id = user.id
    elif nuevo in ('PENDIENTE', 'RECHAZADA'):
        # Al reabrir o rechazar deja de estar aprobada/entregada por alguien.
        sol.aprobada_por_id = None
        sol.entregada_por_id = None


@bp.route('/solicitudes/<int:sol_id>/estado', methods=['PATCH'])
@_require_inventario_admin
@transaccion_de_stock
def update_solicitud_estado(sol_id: int):
    """Cambia el estatus de una solicitud aplicando reservas de stock (Pausa 2-bis).

    Transiciones y efecto en stock_reservado:
      - PENDIENTE → APROBADA:  RESERVA (puede fallar 409 si no hay disponible).
      - APROBADA → RECHAZADA:  LIBERA reservas.
      - APROBADA → PENDIENTE:  LIBERA reservas (se re-aprobará después).
      - APROBADA → ENTREGADA:  LIBERA reservas. NO descuenta stock — la SALIDA
        real la registra el almacenista por separado en /movimientos. Cuando
        llegue Pausa 8b (entrega parcial), ese endpoint sí descuenta stock.
      - ENTREGADA → PENDIENTE: RE-RESERVA (puede fallar 409).
      - RECHAZADA → PENDIENTE: sin efecto (no había reserva).
      - PENDIENTE → RECHAZADA: sin efecto.
    """
    data, err = _parse_or_422(SolicitudUpdateEstadoSchema(), request.get_json(silent=True))
    if err: return err

    sol = (
        SolicitudMaterial.query
        .options(selectinload(SolicitudMaterial.detalles))
        .filter(SolicitudMaterial.id == sol_id)
        .first()
    )
    if not sol:
        return jsonify({'detail': 'Solicitud no encontrada'}), 404

    estado_previo = sol.estatus
    nuevo_estado = data['estatus']

    permitidas = _TRANSICIONES_VALIDAS.get(estado_previo, set())
    if nuevo_estado != estado_previo and nuevo_estado not in permitidas:
        return jsonify({
            'detail': f"Transición inválida: {estado_previo} → {nuevo_estado}",
            'permitidas': sorted(permitidas),
        }), 409

    if estado_previo == 'PENDIENTE' and nuevo_estado == 'APROBADA':
        _sembrar_cantidades_aprobadas(sol)

    _aplicar_reservas_de_transicion(sol, estado_previo, nuevo_estado)
    _marcar_resolucion(sol, nuevo_estado, request.current_user)

    if estado_previo != nuevo_estado:
        _audit(request.current_user, f"Solicitud #{sol_id} estatus: {estado_previo} → {nuevo_estado}")

    db.session.commit()

    db.session.refresh(sol)
    _ = list(sol.detalles)
    if estado_previo != nuevo_estado:
        emit_to_role(_SOL_ROLES, 'solicitud:changed', {
            'id': sol.id, 'action': f'estado:{nuevo_estado}',
        })
    return jsonify(_solicitud_to_dict(sol))


# ─── Pausa 8b: edición de cantidad aprobada y entrega parcial ────────────────

@bp.route('/solicitudes/<int:sol_id>/detalles/<int:det_id>', methods=['PATCH'])
@_require_inventario_admin
@transaccion_de_stock
def patch_solicitud_detalle(sol_id: int, det_id: int):
    """Edita `cantidad_aprobada` de una línea de solicitud APROBADA (Pausa 8b).

    Reglas:
      - Solicitud debe estar en APROBADA.
      - Línea debe ser MATERIAL con producto_id.
      - 0 ≤ cantidad_aprobada ≤ cantidad_solicitada.
      - cantidad_aprobada ≥ cantidad_entregada (no se aprueba menos de lo ya salido).
      - Ajusta `Producto.stock_reservado` por el delta:
          delta > 0 → intenta reservar (puede fallar 409 si no hay disponible).
          delta < 0 → libera.
    """
    data, err = _parse_or_422(SolicitudDetallePatchSchema(), request.get_json(silent=True))
    if err: return err

    det = (
        SolicitudMaterialDetalle.query
        .filter(
            SolicitudMaterialDetalle.id == det_id,
            SolicitudMaterialDetalle.solicitud_id == sol_id,
        )
        .first()
    )
    if not det:
        return jsonify({'detail': 'Detalle no encontrado'}), 404
    if (det.tipo_item or 'MATERIAL').upper() != 'MATERIAL' or not det.producto_id:
        return jsonify({'detail': 'Solo líneas de MATERIAL pueden editarse aquí'}), 422

    sol = SolicitudMaterial.query.filter(SolicitudMaterial.id == sol_id).first()
    if not sol:
        return jsonify({'detail': 'Solicitud no encontrada'}), 404
    if sol.estatus != 'APROBADA':
        return jsonify({
            'detail': f'Solo solicitudes APROBADAS permiten editar cantidad_aprobada (actual: {sol.estatus})'
        }), 409

    nueva_aprob = Decimal(str(data['cantidad_aprobada']))
    cant_sol = Decimal(str(det.cantidad_solicitada or 0))
    cant_ent = Decimal(str(det.cantidad_entregada or 0))
    cant_aprob_actual = Decimal(str(det.cantidad_aprobada or 0))

    unidad_mat = det.producto.unidad if det.producto else None
    if not _unidad_permite_decimales(unidad_mat) and not _es_entero(nueva_aprob):
        return jsonify({
            'detail': f'Este material se maneja en cantidades enteras (unidad: {unidad_mat or "pieza"})'
        }), 422

    if nueva_aprob > cant_sol:
        return jsonify({
            'detail': f'cantidad_aprobada ({nueva_aprob}) no puede exceder cantidad_solicitada ({cant_sol})'
        }), 422
    if nueva_aprob < cant_ent:
        return jsonify({
            'detail': f'cantidad_aprobada ({nueva_aprob}) no puede ser menor a cantidad_entregada ({cant_ent})'
        }), 422

    # Baseline para reserva previa: lo que el código antiguo asumía si cant_aprob=0.
    baseline_anterior = cant_aprob_actual if cant_aprob_actual > 0 else cant_sol
    reserva_anterior = baseline_anterior - cant_ent
    reserva_nueva = nueva_aprob - cant_ent
    delta = reserva_nueva - reserva_anterior

    if delta > 0:
        errores = _intentar_reservar({det.producto_id: delta})
        if errores:
            raise ErrorDeNegocio(
                'No se puede aumentar la cantidad aprobada: stock insuficiente', 409,
                errores=errores,
            )
    elif delta < 0:
        _liberar_reservas({det.producto_id: -delta})

    det.cantidad_aprobada = nueva_aprob
    _audit(
        request.current_user,
        f"Solicitud #{sol_id} det #{det_id} cantidad_aprobada: {cant_aprob_actual} → {nueva_aprob}",
    )
    db.session.commit()

    db.session.refresh(det)
    emit_to_role(_SOL_ROLES, 'solicitud:changed', {
        'id': sol_id, 'detalle_id': det_id, 'action': 'detalle_updated',
    })
    return jsonify(_solicitud_detalle_to_dict(det))
