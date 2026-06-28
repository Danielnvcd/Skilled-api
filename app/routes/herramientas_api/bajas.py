"""Solicitudes de baja + baja directa.

Registra:
  /solicitudes-baja-herramienta/                       POST, GET
  /solicitudes-baja-herramienta/<int:sid>/autorizar    PATCH
  /solicitudes-baja-herramienta/<int:sid>/rechazar     PATCH
  /solicitudes-baja-herramienta/<int:sid>/ejecutar     POST
  /herramientas-unidades/<int:uid>/dar-baja            POST   (atajo admin)
"""
import datetime

from flask import jsonify, request
from sqlalchemy.orm import joinedload

from app.extensions import db, limiter, get_real_client_ip_flask
from app.realtime import emit_to_role
from app.models import (
    HerramientaUnidad, SolicitudBajaHerramienta,
    ESTADOS_SOLICITUD_BAJA,
    crear_evento_herramienta, crear_notif_inventario,
)
from app.routes.inventario_api import (
    _require_login, _require_inventario_admin,
    _parse_or_422, _audit,
)
from ._core import (
    bp, _HERR_ROLES,
    SolicitudBajaCreateSchema, SolicitudBajaAutorizarSchema,
    _solicitud_baja_to_dict, _puede_ver_unidad,
)


@bp.route('/solicitudes-baja-herramienta/', methods=['POST'])
@limiter.limit('10/minute', key_func=lambda: f"ip:{get_real_client_ip_flask()}")
@_require_login
def crear_solicitud_baja():
    user = request.current_user
    if user.role not in ('solicitante_material', 'coordinador', 'inventario', 'admin', 'super_admin'):
        return jsonify({'detail': 'Forbidden'}), 403
    data, err = _parse_or_422(SolicitudBajaCreateSchema(), request.get_json(silent=True))
    if err: return err

    unidad = HerramientaUnidad.query.filter(HerramientaUnidad.id == data['unidad_id']).first()
    if not unidad:
        return jsonify({'detail': 'Unidad no encontrada'}), 404
    if not _puede_ver_unidad(user, unidad):
        return jsonify({'detail': 'Forbidden'}), 403
    if unidad.estado == 'DADA_DE_BAJA':
        return jsonify({'detail': 'Unidad ya está dada de baja'}), 400

    # Evitar duplicados: no permitir abrir una segunda solicitud PENDIENTE/APROBADA
    # para la misma unidad mientras la anterior siga abierta.
    pendiente_previa = SolicitudBajaHerramienta.query.filter(
        SolicitudBajaHerramienta.unidad_id == unidad.id,
        SolicitudBajaHerramienta.estado.in_(('PENDIENTE', 'APROBADA')),
    ).first()
    if pendiente_previa:
        return jsonify({
            'detail': 'Ya existe una solicitud de baja activa para esta unidad',
            'solicitud_id': pendiente_previa.id,
            'estado': pendiente_previa.estado,
        }), 409

    sol = SolicitudBajaHerramienta(
        unidad_id=unidad.id,
        solicitante_id=user.id,
        motivo=data['motivo'],
        estado='PENDIENTE',
        fecha_solicitud=datetime.datetime.utcnow(),
    )
    db.session.add(sol)
    db.session.flush()
    crear_evento_herramienta(
        unidad, 'BAJA_SOLICITUD', user,
        observaciones=f"Solicitud de baja: {data['motivo'][:120]}",
        referencia_id=sol.id, referencia_tipo='solicitud_baja',
    )
    crear_notif_inventario(
        'HERRAMIENTA_BAJA_SOLICITUD',
        f"Solicitud de baja {unidad.codigo_interno}",
        f"{user.username} solicitó baja de {unidad.codigo_interno}",
        url=f"/inventario/herramientas/incidencias",
    )
    _audit(user, f"Solicitud de baja #{sol.id} para unidad #{unidad.id}")
    db.session.commit()
    db.session.refresh(sol)
    emit_to_role(_HERR_ROLES, 'baja:changed', {
        'id': sol.id, 'unidad_id': unidad.id, 'action': 'created',
    })
    return jsonify(_solicitud_baja_to_dict(sol)), 201


@bp.route('/solicitudes-baja-herramienta/', methods=['GET'])
@_require_login
def list_solicitudes_baja():
    user = request.current_user
    if user.role not in ('solicitante_material', 'coordinador', 'inventario', 'admin', 'super_admin'):
        return jsonify({'detail': 'Forbidden'}), 403
    estado = request.args.get('estado', type=str)
    query = SolicitudBajaHerramienta.query.options(joinedload(SolicitudBajaHerramienta.solicitante))
    # Roles "solicitantes" (sin permiso de gestión) solo ven sus propias bajas.
    if user.role in ('solicitante_material', 'coordinador'):
        query = query.filter(SolicitudBajaHerramienta.solicitante_id == user.id)
    if estado:
        if estado not in ESTADOS_SOLICITUD_BAJA:
            return jsonify({'detail': 'estado inválido'}), 422
        query = query.filter(SolicitudBajaHerramienta.estado == estado)
    sols = query.order_by(SolicitudBajaHerramienta.fecha_solicitud.desc()).limit(500).all()
    return jsonify([_solicitud_baja_to_dict(s) for s in sols])


@bp.route('/solicitudes-baja-herramienta/<int:sid>/autorizar', methods=['PATCH'])
@limiter.limit('20/minute', key_func=lambda: f"ip:{get_real_client_ip_flask()}")
@_require_inventario_admin
def autorizar_baja(sid: int):
    data, err = _parse_or_422(SolicitudBajaAutorizarSchema(), request.get_json(silent=True))
    if err: return err
    sol = SolicitudBajaHerramienta.query.filter(SolicitudBajaHerramienta.id == sid).first()
    if not sol:
        return jsonify({'detail': 'Solicitud no encontrada'}), 404
    if sol.estado != 'PENDIENTE':
        return jsonify({'detail': 'Solo se pueden autorizar solicitudes PENDIENTES'}), 409

    user = request.current_user
    sol.estado = 'APROBADA'
    sol.autorizado_por_id = user.id
    sol.fecha_autorizacion = datetime.datetime.utcnow()
    if data.get('observaciones'):
        sol.observaciones = data['observaciones']
    unidad = HerramientaUnidad.query.filter(HerramientaUnidad.id == sol.unidad_id).first()
    if unidad:
        crear_evento_herramienta(
            unidad, 'BAJA_APROBADA', user,
            observaciones='Baja autorizada',
            referencia_id=sol.id, referencia_tipo='solicitud_baja',
        )
    _audit(user, f"Solicitud baja #{sid} autorizada")
    db.session.commit()
    db.session.refresh(sol)
    emit_to_role(_HERR_ROLES, 'baja:changed', {
        'id': sid, 'unidad_id': sol.unidad_id, 'action': 'autorizada',
    })
    return jsonify(_solicitud_baja_to_dict(sol))


@bp.route('/solicitudes-baja-herramienta/<int:sid>/rechazar', methods=['PATCH'])
@limiter.limit('20/minute', key_func=lambda: f"ip:{get_real_client_ip_flask()}")
@_require_inventario_admin
def rechazar_baja(sid: int):
    data, err = _parse_or_422(SolicitudBajaAutorizarSchema(), request.get_json(silent=True))
    if err: return err
    sol = SolicitudBajaHerramienta.query.filter(SolicitudBajaHerramienta.id == sid).first()
    if not sol:
        return jsonify({'detail': 'Solicitud no encontrada'}), 404
    if sol.estado != 'PENDIENTE':
        return jsonify({'detail': 'Solo se pueden rechazar solicitudes PENDIENTES'}), 409

    user = request.current_user
    sol.estado = 'RECHAZADA'
    sol.autorizado_por_id = user.id
    sol.fecha_autorizacion = datetime.datetime.utcnow()
    if data.get('observaciones'):
        sol.observaciones = data['observaciones']
    unidad = HerramientaUnidad.query.filter(HerramientaUnidad.id == sol.unidad_id).first()
    if unidad:
        crear_evento_herramienta(
            unidad, 'BAJA_RECHAZADA', user,
            observaciones=f"Rechazo: {(data.get('observaciones') or '')[:120]}",
            referencia_id=sol.id, referencia_tipo='solicitud_baja',
        )
    _audit(user, f"Solicitud baja #{sid} rechazada")
    db.session.commit()
    db.session.refresh(sol)
    emit_to_role(_HERR_ROLES, 'baja:changed', {
        'id': sid, 'unidad_id': sol.unidad_id, 'action': 'rechazada',
    })
    return jsonify(_solicitud_baja_to_dict(sol))


@bp.route('/solicitudes-baja-herramienta/<int:sid>/ejecutar', methods=['POST'])
@limiter.limit('20/minute', key_func=lambda: f"ip:{get_real_client_ip_flask()}")
@_require_inventario_admin
def ejecutar_baja(sid: int):
    sol = SolicitudBajaHerramienta.query.filter(SolicitudBajaHerramienta.id == sid).first()
    if not sol:
        return jsonify({'detail': 'Solicitud no encontrada'}), 404
    if sol.estado != 'APROBADA':
        return jsonify({'detail': 'Solo se ejecutan solicitudes APROBADAS'}), 409

    unidad = HerramientaUnidad.query.with_for_update(nowait=True).filter(
        HerramientaUnidad.id == sol.unidad_id
    ).first()
    if not unidad:
        return jsonify({'detail': 'Unidad no encontrada'}), 404
    if unidad.estado == 'DADA_DE_BAJA':
        return jsonify({'detail': 'Unidad ya dada de baja'}), 400

    user = request.current_user
    estado_anterior = unidad.estado
    unidad.estado = 'DADA_DE_BAJA'
    unidad.fecha_baja = datetime.datetime.utcnow()
    unidad.motivo_baja = (sol.motivo or '')[:250]
    unidad.asignado_trabajador_id = None

    # Cerrar asignación activa si existiera
    asig = next((a for a in unidad.asignaciones if a.estado == 'ACTIVA'), None)
    if asig:
        asig.estado = 'VENCIDA'
        asig.fecha_devolucion_real = datetime.datetime.utcnow()
        asig.observaciones_devolucion = '(Cerrada automáticamente por baja)'
        asig.recibido_por_id = user.id

    sol.estado = 'EJECUTADA'
    sol.ejecutado_por_id = user.id
    sol.fecha_ejecucion = datetime.datetime.utcnow()

    crear_evento_herramienta(
        unidad, 'BAJA_EJECUTADA', user,
        observaciones=f"Baja ejecutada (motivo: {(sol.motivo or '')[:120]})",
        estado_anterior=estado_anterior, estado_nuevo='DADA_DE_BAJA',
        referencia_id=sol.id, referencia_tipo='solicitud_baja',
    )
    _audit(user, f"Baja ejecutada #{sid}: unidad #{unidad.id}")
    db.session.commit()
    db.session.refresh(sol)
    emit_to_role(_HERR_ROLES, 'baja:changed', {
        'id': sid, 'unidad_id': unidad.id, 'action': 'ejecutada',
    })
    # Cambia también el estado de la unidad → notificar herramienta:changed
    # para que las vistas de catálogo/unidades refresquen sus stats.
    emit_to_role(_HERR_ROLES, 'herramienta:changed', {
        'id': unidad.herramienta_id, 'unidad_id': unidad.id, 'action': 'baja_ejecutada',
    })
    return jsonify(_solicitud_baja_to_dict(sol))


# ─── Baja directa (atajo admin) ─────────────────────────────────────────────

@bp.route('/herramientas-unidades/<int:uid>/dar-baja', methods=['POST'])
@limiter.limit('10/minute', key_func=lambda: f"ip:{get_real_client_ip_flask()}")
@_require_inventario_admin
def baja_directa(uid: int):
    """Atajo para admin/inventario: crea solicitud APROBADA y la ejecuta de inmediato."""
    payload = request.get_json(silent=True) or {}
    motivo = (payload.get('motivo') or '').strip()
    if len(motivo) < 10:
        return jsonify({'detail': 'Motivo debe tener al menos 10 caracteres'}), 422

    unidad = HerramientaUnidad.query.with_for_update(nowait=True).filter(
        HerramientaUnidad.id == uid
    ).first()
    if not unidad:
        return jsonify({'detail': 'Unidad no encontrada'}), 404
    if unidad.estado == 'DADA_DE_BAJA':
        return jsonify({'detail': 'Unidad ya dada de baja'}), 400

    user = request.current_user
    sol = SolicitudBajaHerramienta(
        unidad_id=unidad.id,
        solicitante_id=user.id,
        motivo=motivo,
        estado='EJECUTADA',
        autorizado_por_id=user.id,
        ejecutado_por_id=user.id,
        fecha_solicitud=datetime.datetime.utcnow(),
        fecha_autorizacion=datetime.datetime.utcnow(),
        fecha_ejecucion=datetime.datetime.utcnow(),
        observaciones='Baja directa por inventario/admin',
    )
    db.session.add(sol)
    db.session.flush()

    estado_anterior = unidad.estado
    unidad.estado = 'DADA_DE_BAJA'
    unidad.fecha_baja = datetime.datetime.utcnow()
    unidad.motivo_baja = motivo[:250]
    unidad.asignado_trabajador_id = None

    crear_evento_herramienta(
        unidad, 'BAJA_EJECUTADA', user,
        observaciones=f"Baja directa: {motivo[:120]}",
        estado_anterior=estado_anterior, estado_nuevo='DADA_DE_BAJA',
        referencia_id=sol.id, referencia_tipo='solicitud_baja',
    )
    _audit(user, f"Baja directa unidad #{uid}")
    db.session.commit()
    db.session.refresh(sol)
    emit_to_role(_HERR_ROLES, 'baja:changed', {
        'id': sol.id, 'unidad_id': unidad.id, 'action': 'baja_directa',
    })
    emit_to_role(_HERR_ROLES, 'herramienta:changed', {
        'id': unidad.herramienta_id, 'unidad_id': unidad.id, 'action': 'baja_directa',
    })
    return jsonify(_solicitud_baja_to_dict(sol)), 201
