"""Alta, listado, estado y cancelación de solicitudes de compra."""
import datetime
from decimal import Decimal

from flask import jsonify, request
from sqlalchemy.orm import joinedload, selectinload

from app.extensions import db, limiter, get_real_client_ip_flask
from app.models import (
    Producto, Proyecto, SolicitudCompra, SolicitudCompraDetalle,
)
from app.realtime import emit_to_role

from .._core import (
    bp,
    _parse_or_422, _int_arg, _audit,
    _unidad_permite_decimales,
)
from ._core import (
    _COMPRA_ROLES, _require_compras,
    CompraCreateSchema, CompraEstadoSchema, CompraDetallePatchSchema,
    _compra_to_dict, _load_compra,
)


# ─── CRUD + listado ───────────────────────────────────────────────────────────

@bp.route('/solicitudes-compra/', methods=['POST'])
@limiter.limit('15/minute', key_func=lambda: f"ip:{get_real_client_ip_flask()}")
@_require_compras
def create_solicitud_compra():
    user = request.current_user
    data, err = _parse_or_422(CompraCreateSchema(), request.get_json(silent=True))
    if err:
        return err

    # Proyecto opcional pero, si viene, debe existir.
    proyecto_id = data.get('proyecto_id')
    if proyecto_id is not None:
        if not Proyecto.query.filter(Proyecto.id == proyecto_id).first():
            return jsonify({'detail': f'Proyecto #{proyecto_id} no existe'}), 422

    # Validar líneas antes de persistir nada.
    lineas_validadas = []
    errores = []
    for idx, det in enumerate(data['detalles']):
        producto_id = det.get('producto_id')
        descripcion_libre = (det.get('descripcion_libre') or '').strip()
        unidad = (det.get('unidad') or '').strip() or None

        if producto_id:
            prod = Producto.query.filter(
                Producto.id == producto_id, Producto.activo == True  # noqa: E712
            ).first()
            if not prod:
                errores.append(f"Línea {idx+1}: producto #{producto_id} no existe o está inactivo")
                continue
            if not unidad:
                unidad = prod.unidad
            descripcion_libre = None
        elif descripcion_libre:
            producto_id = None
        else:
            errores.append(f"Línea {idx+1}: indica un producto del catálogo o una descripción")
            continue

        cant = Decimal(str(det['cantidad_solicitada']))
        # Decimales según unidad (igual que el resto del sistema): pieza/caja →
        # enteros; kg/m/litro → admiten decimales.
        if not _unidad_permite_decimales(unidad) and cant != cant.to_integral_value():
            errores.append(f"Línea {idx+1}: '{unidad or 'pza'}' se pide en cantidades enteras (sin decimales)")
            continue

        lineas_validadas.append({
            'producto_id': producto_id,
            'descripcion_libre': descripcion_libre,
            'unidad': unidad,
            'cantidad_solicitada': cant,
            'precio_estimado': (
                Decimal(str(det['precio_estimado'])) if det.get('precio_estimado') is not None else None
            ),
            'notas': (det.get('notas') or '').strip() or None,
        })

    if errores:
        return jsonify({'detail': errores}), 400

    nueva = SolicitudCompra(
        solicitado_por_id=user.id,
        proveedor_sugerido=(data.get('proveedor_sugerido') or '').strip() or None,
        proveedor_contacto=(data.get('proveedor_contacto') or '').strip() or None,
        proyecto_id=proyecto_id,
        prioridad=data.get('prioridad') or 'MEDIA',
        notas=(data.get('notas') or '').strip() or None,
        estatus='PENDIENTE',
    )
    db.session.add(nueva)
    db.session.flush()

    for ln in lineas_validadas:
        db.session.add(SolicitudCompraDetalle(solicitud_compra_id=nueva.id, **ln))

    _audit(user, f"Nueva solicitud de compra {nueva.folio} ({len(lineas_validadas)} líneas)")
    db.session.commit()

    sol = _load_compra(nueva.id)
    emit_to_role(_COMPRA_ROLES, 'compra:changed', {'id': sol.id, 'action': 'created'})
    return jsonify(_compra_to_dict(sol))


@bp.route('/solicitudes-compra/', methods=['GET'])
@_require_compras
def list_solicitudes_compra():
    skip, err = _int_arg('skip', 0, 0, 1_000_000)
    if err:
        return err
    limit, err = _int_arg('limit', 200, 0, 2000)
    if err:
        return err

    query = SolicitudCompra.query.options(
        joinedload(SolicitudCompra.solicitado_por),
        joinedload(SolicitudCompra.proyecto_ref),
        selectinload(SolicitudCompra.detalles).joinedload(SolicitudCompraDetalle.producto),
    )

    estatus = (request.args.get('estatus') or '').strip().upper()
    if estatus:
        query = query.filter(SolicitudCompra.estatus == estatus)

    proyecto_id = request.args.get('proyecto_id')
    if proyecto_id:
        try:
            query = query.filter(SolicitudCompra.proyecto_id == int(proyecto_id))
        except (TypeError, ValueError):
            return jsonify({'detail': "proyecto_id debe ser entero"}), 422

    proveedor = (request.args.get('proveedor') or '').strip()
    if proveedor:
        query = query.filter(SolicitudCompra.proveedor_sugerido.ilike(f'%{proveedor}%'))

    sols = (
        query.order_by(SolicitudCompra.fecha_creacion.desc())
        .offset(skip).limit(limit).all()
    )
    return jsonify([_compra_to_dict(s) for s in sols])


@bp.route('/solicitudes-compra/<int:sol_id>', methods=['GET'])
@_require_compras
def get_solicitud_compra(sol_id: int):
    sol = _load_compra(sol_id)
    if not sol:
        return jsonify({'detail': 'Solicitud de compra no encontrada'}), 404
    return jsonify(_compra_to_dict(sol))


@bp.route('/solicitudes-compra/<int:sol_id>/estado', methods=['PATCH'])
@_require_compras
def update_solicitud_compra_estado(sol_id: int):
    data, err = _parse_or_422(CompraEstadoSchema(), request.get_json(silent=True))
    if err:
        return err

    sol = _load_compra(sol_id)
    if not sol:
        return jsonify({'detail': 'Solicitud de compra no encontrada'}), 404

    previo = sol.estatus
    nuevo = data['estatus']

    TRANSICIONES = {
        'PENDIENTE': {'ORDENADA', 'CANCELADA'},
        'ORDENADA':  {'PENDIENTE', 'CANCELADA'},
        'RECIBIDA':  {'ORDENADA'},   # reabrir para recibir más / corregir
        'CANCELADA': {'PENDIENTE'},
    }
    if nuevo != previo and nuevo not in TRANSICIONES.get(previo, set()):
        return jsonify({
            'detail': f'Transición inválida: {previo} → {nuevo}',
            'permitidas': sorted(TRANSICIONES.get(previo, set())),
        }), 409

    sol.estatus = nuevo
    if nuevo == 'ORDENADA' and not sol.fecha_orden:
        sol.fecha_orden = datetime.datetime.now()
    if nuevo == 'CANCELADA':
        sol.fecha_cierre = datetime.datetime.now()
    elif nuevo in ('PENDIENTE', 'ORDENADA'):
        sol.fecha_cierre = None

    if previo != nuevo:
        _audit(request.current_user, f"Solicitud de compra {sol.folio}: {previo} → {nuevo}")
    db.session.commit()

    sol = _load_compra(sol_id)
    if previo != nuevo:
        emit_to_role(_COMPRA_ROLES, 'compra:changed', {'id': sol.id, 'action': f'estado:{nuevo}'})
    return jsonify(_compra_to_dict(sol))


@bp.route('/solicitudes-compra/<int:sol_id>/detalles/<int:det_id>', methods=['PATCH'])
@_require_compras
def patch_solicitud_compra_detalle(sol_id: int, det_id: int):
    """Edita cantidad/precio/notas de una línea — solo mientras está PENDIENTE."""
    data, err = _parse_or_422(CompraDetallePatchSchema(), request.get_json(silent=True))
    if err:
        return err

    sol = SolicitudCompra.query.filter(SolicitudCompra.id == sol_id).first()
    if not sol:
        return jsonify({'detail': 'Solicitud de compra no encontrada'}), 404
    if sol.estatus != 'PENDIENTE':
        return jsonify({
            'detail': f'Solo solicitudes PENDIENTES permiten editar líneas (actual: {sol.estatus})'
        }), 409

    det = SolicitudCompraDetalle.query.filter(
        SolicitudCompraDetalle.id == det_id,
        SolicitudCompraDetalle.solicitud_compra_id == sol_id,
    ).first()
    if not det:
        return jsonify({'detail': 'Línea no encontrada'}), 404

    if data.get('cantidad_solicitada') is not None:
        det.cantidad_solicitada = Decimal(str(data['cantidad_solicitada']))
    if 'precio_estimado' in (request.get_json(silent=True) or {}):
        pe = data.get('precio_estimado')
        det.precio_estimado = Decimal(str(pe)) if pe is not None else None
    if 'notas' in (request.get_json(silent=True) or {}):
        det.notas = (data.get('notas') or '').strip() or None

    db.session.commit()
    sol = _load_compra(sol_id)
    emit_to_role(_COMPRA_ROLES, 'compra:changed', {'id': sol_id, 'action': 'detalle_updated'})
    return jsonify(_compra_to_dict(sol))


@bp.route('/solicitudes-compra/<int:sol_id>', methods=['DELETE'])
@_require_compras
def cancelar_solicitud_compra(sol_id: int):
    """Cancela (soft) la solicitud de compra. No se borra el registro: queda
    CANCELADA para conservar la bitácora."""
    sol = SolicitudCompra.query.filter(SolicitudCompra.id == sol_id).first()
    if not sol:
        return jsonify({'detail': 'Solicitud de compra no encontrada'}), 404
    if sol.estatus == 'RECIBIDA':
        return jsonify({'detail': 'No se puede cancelar una solicitud ya recibida'}), 409

    previo = sol.estatus
    sol.estatus = 'CANCELADA'
    sol.fecha_cierre = datetime.datetime.now()
    _audit(request.current_user, f"Solicitud de compra {sol.folio} cancelada (era {previo})")
    db.session.commit()
    emit_to_role(_COMPRA_ROLES, 'compra:changed', {'id': sol_id, 'action': 'estado:CANCELADA'})
    return jsonify({'detail': 'Solicitud de compra cancelada', 'id': sol_id})
