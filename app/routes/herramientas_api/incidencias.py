"""Incidencias de herramienta.

Registra:
  /incidencias-herramienta/                          POST, GET
  /incidencias-herramienta/<int:iid>/atender         PATCH
"""
import datetime

from flask import jsonify, request
from sqlalchemy.orm import joinedload

from app.extensions import db, limiter, get_real_client_ip_flask
from app.realtime import emit_to_role
from app.models import (
    HerramientaUnidad, IncidenciaHerramienta,
    ESTADOS_INCIDENCIA,
    crear_evento_herramienta, crear_notif_inventario,
)
from ._core import (
    bp,
    _HERR_ROLES,
    _require_login,
    _require_inventario_admin,
    _parse_or_422,
    _audit,
    IncidenciaCreateSchema,
    IncidenciaAtenderSchema,
    _incidencia_to_dict,
    _puede_ver_unidad,
)


@bp.route('/incidencias-herramienta/', methods=['POST'])
@limiter.limit('20/minute', key_func=lambda: f"ip:{get_real_client_ip_flask()}")
@_require_login
def crear_incidencia():
    user = request.current_user
    if user.role not in ('solicitante_material', 'inventario', 'admin', 'super_admin'):
        return jsonify({'detail': 'Forbidden'}), 403
    data, err = _parse_or_422(IncidenciaCreateSchema(), request.get_json(silent=True))
    if err: return err

    unidad = HerramientaUnidad.query.filter(HerramientaUnidad.id == data['unidad_id']).first()
    if not unidad:
        return jsonify({'detail': 'Unidad no encontrada'}), 404
    if not _puede_ver_unidad(user, unidad):
        return jsonify({'detail': 'Forbidden'}), 403

    inc = IncidenciaHerramienta(
        unidad_id=unidad.id,
        reportado_por_id=user.id,
        tipo=data['tipo'],
        descripcion=data['descripcion'],
        estado='ABIERTA',
        fecha_reporte=datetime.datetime.utcnow(),
    )
    db.session.add(inc)
    db.session.flush()
    crear_evento_herramienta(
        unidad, 'INCIDENCIA', user,
        observaciones=f"{data['tipo']}: {data['descripcion'][:120]}",
        referencia_id=inc.id, referencia_tipo='incidencia',
    )
    crear_notif_inventario(
        'HERRAMIENTA_INCIDENCIA',
        f"Incidencia en {unidad.codigo_interno}",
        f"{user.username} reportó {data['tipo']} en {unidad.codigo_interno}",
        url=f"/inventario/herramientas/unidades/{unidad.id}",
    )
    _audit(user, f"Incidencia #{inc.id} reportada en unidad #{unidad.id}")
    db.session.commit()
    db.session.refresh(inc)
    emit_to_role(_HERR_ROLES, 'incidencia:changed', {
        'id': inc.id, 'unidad_id': unidad.id, 'action': 'created',
    })
    return jsonify(_incidencia_to_dict(inc)), 201


@bp.route('/incidencias-herramienta/', methods=['GET'])
@_require_login
def list_incidencias():
    user = request.current_user
    if user.role not in ('solicitante_material', 'coordinador', 'inventario', 'admin', 'super_admin'):
        return jsonify({'detail': 'Forbidden'}), 403
    estado = request.args.get('estado', type=str)
    unidad_id = request.args.get('unidad_id', type=int)
    query = IncidenciaHerramienta.query.options(joinedload(IncidenciaHerramienta.reportado_por))
    # Roles "solicitantes" solo ven las incidencias que ellos reportaron.
    if user.role in ('solicitante_material', 'coordinador'):
        query = query.filter(IncidenciaHerramienta.reportado_por_id == user.id)
    if estado:
        if estado not in ESTADOS_INCIDENCIA:
            return jsonify({'detail': 'estado inválido'}), 422
        query = query.filter(IncidenciaHerramienta.estado == estado)
    if unidad_id:
        query = query.filter(IncidenciaHerramienta.unidad_id == unidad_id)
    incs = query.order_by(IncidenciaHerramienta.fecha_reporte.desc()).limit(500).all()
    return jsonify([_incidencia_to_dict(i) for i in incs])


@bp.route('/incidencias-herramienta/<int:iid>/atender', methods=['PATCH'])
@limiter.limit('30/minute', key_func=lambda: f"ip:{get_real_client_ip_flask()}")
@_require_inventario_admin
def atender_incidencia(iid: int):
    data, err = _parse_or_422(IncidenciaAtenderSchema(), request.get_json(silent=True))
    if err: return err
    inc = IncidenciaHerramienta.query.filter(IncidenciaHerramienta.id == iid).first()
    if not inc:
        return jsonify({'detail': 'Incidencia no encontrada'}), 404
    if inc.estado in ('RESUELTA', 'RECHAZADA'):
        return jsonify({'detail': 'Incidencia ya cerrada'}), 409

    user = request.current_user
    inc.estado = data['estado']
    inc.atendido_por_id = user.id
    if data.get('resolucion'):
        inc.resolucion = data['resolucion']
    if data['estado'] in ('RESUELTA', 'RECHAZADA'):
        inc.fecha_cierre = datetime.datetime.utcnow()
    _audit(user, f"Incidencia #{iid} → {data['estado']}")
    db.session.commit()
    db.session.refresh(inc)
    emit_to_role(_HERR_ROLES, 'incidencia:changed', {
        'id': iid, 'unidad_id': inc.unidad_id, 'action': data['estado'],
    })
    return jsonify(_incidencia_to_dict(inc))
