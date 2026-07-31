"""Mantenimientos de herramienta.

Registra:
  /mantenimientos-herramienta/                       POST, GET
  /mantenimientos-herramienta/<int:mid>/cerrar       PATCH
"""
from decimal import Decimal

from flask import jsonify, request

from app.extensions import db, limiter, get_real_client_ip_flask
from app.realtime import emit_to_role
from app.models._base import _now_utc
from app.models import (
    HerramientaUnidad, MantenimientoHerramienta,
    ESTADOS_MANTENIMIENTO,
    crear_evento_herramienta,
)
from ._core import (
    bp,
    _HERR_ROLES,
    _require_login,
    _require_inventario_admin,
    _parse_or_422,
    _audit,
    MantenimientoCreateSchema,
    MantenimientoCierreSchema,
    _mantenimiento_to_dict,
)


@bp.route('/mantenimientos-herramienta/', methods=['POST'])
@limiter.limit('30/minute', key_func=lambda: f"ip:{get_real_client_ip_flask()}")
@_require_inventario_admin
def crear_mantenimiento():
    data, err = _parse_or_422(MantenimientoCreateSchema(), request.get_json(silent=True))
    if err: return err

    unidad = HerramientaUnidad.query.with_for_update(nowait=True).filter(
        HerramientaUnidad.id == data['unidad_id']
    ).first()
    if not unidad:
        return jsonify({'detail': 'Unidad no encontrada'}), 404
    if unidad.estado not in ('DISPONIBLE', 'ASIGNADA', 'DAÑADA'):
        return jsonify({'detail': f'Unidad en estado {unidad.estado}, no apta para mantenimiento'}), 409

    user = request.current_user
    estado_anterior = unidad.estado

    # Si está asignada, cerramos la asignación como VENCIDA
    asig_activa = next((a for a in unidad.asignaciones if a.estado == 'ACTIVA'), None)
    if asig_activa:
        asig_activa.estado = 'VENCIDA'
        asig_activa.fecha_devolucion_real = _now_utc()
        asig_activa.observaciones_devolucion = '(Cerrada automáticamente por envío a mantenimiento)'
        asig_activa.recibido_por_id = user.id

    mant = MantenimientoHerramienta(
        unidad_id=unidad.id,
        tipo=data['tipo'],
        motivo=data['motivo'],
        proveedor=data.get('proveedor'),
        fecha_inicio=_now_utc(),
        costo=Decimal(str(data['costo'])) if data.get('costo') is not None else None,
        observaciones=data.get('observaciones'),
        estado='ABIERTO',
        abierto_por_id=user.id,
    )
    db.session.add(mant)
    db.session.flush()

    unidad.estado = 'EN_MANTENIMIENTO'
    unidad.asignado_trabajador_id = None
    crear_evento_herramienta(
        unidad, 'MANTENIMIENTO_IN', user,
        observaciones=f"{data['tipo']}: {data['motivo']}",
        estado_anterior=estado_anterior, estado_nuevo='EN_MANTENIMIENTO',
        referencia_id=mant.id, referencia_tipo='mantenimiento',
    )
    _audit(user, f"Mantenimiento #{mant.id} abierto en unidad {unidad.codigo_interno}")
    db.session.commit()
    db.session.refresh(mant)
    emit_to_role(_HERR_ROLES, 'mantenimiento:changed', {
        'id': mant.id, 'unidad_id': unidad.id, 'action': 'abierto',
    })
    return jsonify(_mantenimiento_to_dict(mant)), 201


@bp.route('/mantenimientos-herramienta/', methods=['GET'])
@_require_login
def list_mantenimientos():
    user = request.current_user
    if user.role not in ('inventario', 'admin', 'super_admin', 'solicitante_material'):
        return jsonify({'detail': 'Forbidden'}), 403

    estado = request.args.get('estado', type=str)
    unidad_id = request.args.get('unidad_id', type=int)
    query = MantenimientoHerramienta.query

    # Solicitante solo puede ver mantenimientos de unidades que tiene/tuvo asignadas.
    if user.role == 'solicitante_material':
        if not user.trabajador_id:
            return jsonify([])
        unidad_ids_visibles = [
            u.id for u in HerramientaUnidad.query.filter(
                HerramientaUnidad.asignado_trabajador_id == user.trabajador_id
            ).all()
        ]
        if not unidad_ids_visibles:
            return jsonify([])
        query = query.filter(MantenimientoHerramienta.unidad_id.in_(unidad_ids_visibles))

    if estado:
        if estado not in ESTADOS_MANTENIMIENTO:
            return jsonify({'detail': 'estado inválido'}), 422
        query = query.filter(MantenimientoHerramienta.estado == estado)
    if unidad_id:
        query = query.filter(MantenimientoHerramienta.unidad_id == unidad_id)
    mants = query.order_by(MantenimientoHerramienta.fecha_inicio.desc()).limit(500).all()
    return jsonify([_mantenimiento_to_dict(m) for m in mants])


@bp.route('/mantenimientos-herramienta/<int:mid>/cerrar', methods=['PATCH'])
@limiter.limit('30/minute', key_func=lambda: f"ip:{get_real_client_ip_flask()}")
@_require_inventario_admin
def cerrar_mantenimiento(mid: int):
    data, err = _parse_or_422(MantenimientoCierreSchema(), request.get_json(silent=True))
    if err: return err

    mant = MantenimientoHerramienta.query.filter(MantenimientoHerramienta.id == mid).first()
    if not mant:
        return jsonify({'detail': 'Mantenimiento no encontrado'}), 404
    if mant.estado == 'CERRADO':
        return jsonify({'detail': 'Mantenimiento ya cerrado'}), 409

    unidad = HerramientaUnidad.query.with_for_update(nowait=True).filter(
        HerramientaUnidad.id == mant.unidad_id
    ).first()
    user = request.current_user
    estado_anterior = unidad.estado
    nuevo_estado = data['estado_final_unidad']

    mant.estado = 'CERRADO'
    mant.fecha_fin = _now_utc()
    mant.cerrado_por_id = user.id
    mant.estado_final_unidad = nuevo_estado
    if data.get('costo_real') is not None:
        mant.costo = Decimal(str(data['costo_real']))
    if data.get('observaciones'):
        mant.observaciones = (mant.observaciones or '') + f"\n[Cierre] {data['observaciones']}"

    unidad.estado = nuevo_estado
    if nuevo_estado == 'DADA_DE_BAJA':
        unidad.fecha_baja = _now_utc()
        unidad.motivo_baja = f"Mantenimiento #{mid}: irrecuperable"

    crear_evento_herramienta(
        unidad, 'MANTENIMIENTO_OUT', user,
        observaciones=f"Cierre mantenimiento (estado final: {nuevo_estado})",
        estado_anterior=estado_anterior, estado_nuevo=nuevo_estado,
        referencia_id=mant.id, referencia_tipo='mantenimiento',
    )
    _audit(user, f"Mantenimiento #{mid} cerrado: unidad → {nuevo_estado}")
    db.session.commit()
    db.session.refresh(mant)
    emit_to_role(_HERR_ROLES, 'mantenimiento:changed', {
        'id': mid, 'unidad_id': unidad.id, 'action': 'cerrado',
    })
    return jsonify(_mantenimiento_to_dict(mant))
