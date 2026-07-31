"""Asignaciones de unidades a trabajadores.

Registra:
  /asignaciones-herramienta/                       POST, GET
  /asignaciones-herramienta/<int:aid>/devolver     PATCH
"""
import datetime

from flask import jsonify, request
from sqlalchemy.orm import joinedload

from app.extensions import db, limiter, get_real_client_ip_flask
from app.realtime import emit_to_role
from app.models import (
    Trabajador,
    HerramientaUnidad, AsignacionHerramienta,
    SolicitudMaterial,
    ESTADOS_ASIGNACION,
    crear_evento_herramienta,
)
from ._core import (
    bp,
    _HERR_ROLES,
    _require_login,
    _require_inventario_admin,
    _parse_or_422,
    _int_arg,
    _audit,
    AsignacionCreateSchema,
    DevolucionSchema,
    _asignacion_to_dict,
)


@bp.route('/asignaciones-herramienta/', methods=['POST'])
@limiter.limit('30/minute', key_func=lambda: f"ip:{get_real_client_ip_flask()}")
@_require_inventario_admin
def crear_asignacion():
    data, err = _parse_or_422(AsignacionCreateSchema(), request.get_json(silent=True))
    if err: return err

    # Bloquear la unidad contra race condition: dos requests no pueden asignar la misma a la vez.
    unidad = (
        HerramientaUnidad.query
        .with_for_update(nowait=True)
        .filter(HerramientaUnidad.id == data['unidad_id']).first()
    )
    if not unidad:
        return jsonify({'detail': 'Unidad no encontrada'}), 404
    if unidad.estado != 'DISPONIBLE':
        db.session.rollback()
        return jsonify({'detail': f'Unidad en estado {unidad.estado}, no disponible'}), 409

    trab = Trabajador.query.filter(Trabajador.id == data['trabajador_id'],
                                     Trabajador.activo == True).first()
    if not trab:
        return jsonify({'detail': 'Trabajador no encontrado o inactivo'}), 404

    if data.get('solicitud_id'):
        sol = SolicitudMaterial.query.filter(SolicitudMaterial.id == data['solicitud_id']).first()
        if not sol:
            return jsonify({'detail': 'Solicitud no encontrada'}), 404

    user = request.current_user
    estado_anterior = unidad.estado
    asig = AsignacionHerramienta(
        unidad_id=unidad.id,
        trabajador_id=trab.id,
        solicitud_id=data.get('solicitud_id'),
        proyecto=data.get('proyecto'),
        fecha_entrega=datetime.datetime.utcnow(),
        fecha_devolucion_prevista=data.get('fecha_devolucion_prevista'),
        estado='ACTIVA',
        condicion_entrega=data.get('condicion_entrega', 'BUENA'),
        observaciones_entrega=data.get('observaciones_entrega'),
        entregado_por_id=user.id,
    )
    db.session.add(asig)
    db.session.flush()

    unidad.estado = 'ASIGNADA'
    unidad.asignado_trabajador_id = trab.id
    crear_evento_herramienta(
        unidad, 'ASIGNACION', user,
        observaciones=f"Asignada a {trab.nombre_completo} (proyecto: {data.get('proyecto') or 'N/A'})",
        estado_anterior=estado_anterior, estado_nuevo='ASIGNADA',
        referencia_id=asig.id, referencia_tipo='asignacion',
    )
    _audit(user, f"Asignación herramienta #{asig.id}: unidad {unidad.codigo_interno} → {trab.nombre_completo}")
    db.session.commit()
    db.session.refresh(asig)
    emit_to_role(_HERR_ROLES, 'asignacion:changed', {
        'id': asig.id, 'unidad_id': unidad.id, 'action': 'created',
    })
    return jsonify(_asignacion_to_dict(asig)), 201


@bp.route('/asignaciones-herramienta/', methods=['GET'])
@_require_login
def list_asignaciones():
    user = request.current_user
    if user.role not in ('inventario', 'admin', 'super_admin', 'solicitante_material'):
        return jsonify({'detail': 'Forbidden'}), 403

    estado = request.args.get('estado', type=str)
    trabajador_id = request.args.get('trabajador_id', type=int)
    unidad_id = request.args.get('unidad_id', type=int)
    skip, err = _int_arg('skip', 0, 0, 1_000_000)
    if err: return err
    limit, err = _int_arg('limit', 200, 0, 500)
    if err: return err

    query = AsignacionHerramienta.query.options(
        joinedload(AsignacionHerramienta.unidad).joinedload(HerramientaUnidad.herramienta),
        joinedload(AsignacionHerramienta.trabajador),
        joinedload(AsignacionHerramienta.entregado_por),
    )
    if user.role == 'solicitante_material':
        if not user.trabajador_id:
            return jsonify([])
        query = query.filter(AsignacionHerramienta.trabajador_id == user.trabajador_id)
    if estado:
        if estado not in ESTADOS_ASIGNACION:
            return jsonify({'detail': 'estado inválido'}), 422
        query = query.filter(AsignacionHerramienta.estado == estado)
    if trabajador_id:
        query = query.filter(AsignacionHerramienta.trabajador_id == trabajador_id)
    if unidad_id:
        query = query.filter(AsignacionHerramienta.unidad_id == unidad_id)

    asigs = query.order_by(AsignacionHerramienta.fecha_entrega.desc()).offset(skip).limit(limit).all()
    return jsonify([_asignacion_to_dict(a) for a in asigs])


@bp.route('/asignaciones-herramienta/<int:aid>/devolver', methods=['PATCH'])
@limiter.limit('30/minute', key_func=lambda: f"ip:{get_real_client_ip_flask()}")
@_require_inventario_admin
def devolver_asignacion(aid: int):
    data, err = _parse_or_422(DevolucionSchema(), request.get_json(silent=True))
    if err: return err

    asig = AsignacionHerramienta.query.filter(AsignacionHerramienta.id == aid).first()
    if not asig:
        return jsonify({'detail': 'Asignación no encontrada'}), 404
    if asig.estado != 'ACTIVA':
        return jsonify({'detail': f'Asignación en estado {asig.estado}, no se puede devolver'}), 409

    unidad = (
        HerramientaUnidad.query.with_for_update(nowait=True)
        .filter(HerramientaUnidad.id == asig.unidad_id).first()
    )
    if not unidad:
        return jsonify({'detail': 'Unidad no encontrada'}), 404

    user = request.current_user
    estado_anterior = unidad.estado
    nuevo_estado = data['nuevo_estado_unidad']

    asig.estado = 'DEVUELTA'
    asig.fecha_devolucion_real = datetime.datetime.utcnow()
    asig.condicion_devolucion = data['condicion_devolucion']
    asig.observaciones_devolucion = data.get('observaciones_devolucion')
    asig.recibido_por_id = user.id

    unidad.estado = nuevo_estado
    unidad.asignado_trabajador_id = None

    crear_evento_herramienta(
        unidad, 'DEVOLUCION', user,
        observaciones=f"Devolución condición: {data['condicion_devolucion']}",
        estado_anterior=estado_anterior, estado_nuevo=nuevo_estado,
        referencia_id=asig.id, referencia_tipo='asignacion',
    )
    _audit(user, f"Devolución asignación #{aid}: {estado_anterior} → {nuevo_estado}")
    db.session.commit()
    db.session.refresh(asig)
    emit_to_role(_HERR_ROLES, 'asignacion:changed', {
        'id': aid, 'unidad_id': unidad.id, 'action': 'devuelta',
    })
    return jsonify(_asignacion_to_dict(asig))
