"""Reportes semanales: listar, abrir, detalle, cerrar.

Registra:
  /reportes                              GET, POST
  /proyectos-disponibles                 GET
  /reportes/<int:reporte_id>             GET
  /reportes/<int:reporte_id>/cerrar      POST
"""
import traceback
from datetime import datetime

from flask import current_app, jsonify, request
from sqlalchemy.orm import joinedload, selectinload

from app.extensions import db
from app.models import Proyecto, ReporteSemanal
from app.realtime import emit_to_role
from app.routes._api_helpers import current_user
from app.routes.api_auth import jwt_required
from app.utils import log_action

from ._core import (
    bp, INCIDENCIAS,
    _is_coordinador, _puede_acceder_proyecto,
    _registro_dict, _reporte_row, _semana_fechas, _trabajador_row,
)


@bp.route('/reportes', methods=['GET'])
@jwt_required
def listar_reportes():
    page = max(1, request.args.get('page', 1, type=int))
    per_page = min(100, max(1, request.args.get('per_page', 20, type=int)))
    q = (request.args.get('q') or '').strip()
    estado = (request.args.get('estado') or '').strip().upper()

    query = ReporteSemanal.query.options(
        joinedload(ReporteSemanal.proyecto),
        selectinload(ReporteSemanal.registros),
    )

    if _is_coordinador():
        proyecto_ids = [p.id for p in Proyecto.query.filter_by(coordinador_id=current_user().id).all()]
        if not proyecto_ids:
            return jsonify({'items': [], 'total': 0, 'page': page, 'pages': 0, 'per_page': per_page})
        query = query.filter(ReporteSemanal.proyecto_id.in_(proyecto_ids))

    if q:
        query = query.join(Proyecto).filter(db.or_(
            Proyecto.nombre.ilike(f'%{q}%'),
            Proyecto.numero_proyecto.ilike(f'%{q}%'),
        ))

    if estado in ('BORRADOR', 'TERMINADO', 'PRENOMINA_CERRADA'):
        query = query.filter(ReporteSemanal.estado == estado)

    pagination = query.order_by(ReporteSemanal.created_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False,
    )

    return jsonify({
        'items': [_reporte_row(r) for r in pagination.items],
        'total': pagination.total,
        'page': pagination.page,
        'pages': pagination.pages,
        'per_page': pagination.per_page,
    })


@bp.route('/proyectos-disponibles', methods=['GET'])
@jwt_required
def proyectos_disponibles():
    """Proyectos activos para abrir un reporte; respeta ownership de coordinador."""
    query = Proyecto.query.filter_by(activo=True)
    if _is_coordinador():
        query = query.filter_by(coordinador_id=current_user().id)
    proyectos = query.order_by(Proyecto.numero_proyecto).all()
    return jsonify([
        {
            'id': p.id,
            'numero_proyecto': p.numero_proyecto,
            'nombre': p.nombre or '',
        } for p in proyectos
    ])


@bp.route('/reportes', methods=['POST'])
@jwt_required
def crear_reporte():
    data = request.get_json(silent=True) or {}
    proyecto_id = data.get('proyecto_id')
    fecha_inicio_str = data.get('fecha_inicio')
    fecha_fin_str = data.get('fecha_fin')

    if not proyecto_id or not fecha_inicio_str or not fecha_fin_str:
        return jsonify({'error': 'proyecto_id, fecha_inicio y fecha_fin son obligatorios'}), 400

    proyecto = Proyecto.query.get_or_404(proyecto_id)
    if not _puede_acceder_proyecto(proyecto):
        return jsonify({'error': 'Acceso denegado. No eres coordinador de este proyecto.'}), 403

    try:
        fecha_inicio = datetime.strptime(fecha_inicio_str, '%Y-%m-%d').date()
        fecha_fin = datetime.strptime(fecha_fin_str, '%Y-%m-%d').date()
    except ValueError:
        return jsonify({'error': 'Formato de fecha inválido (YYYY-MM-DD)'}), 400

    if fecha_inicio >= fecha_fin:
        return jsonify({'error': 'La fecha de inicio debe ser anterior a la fecha de fin'}), 400

    # Bloqueo si la prenómina ya cerró esa ventana
    semana_cerrada = ReporteSemanal.query.filter(
        ReporteSemanal.estado == 'PRENOMINA_CERRADA',
        ReporteSemanal.fecha_inicio_semana <= fecha_fin,
        ReporteSemanal.fecha_fin_semana >= fecha_inicio,
    ).first()
    if semana_cerrada:
        return jsonify({'error': f'La prenómina del {fecha_inicio_str} al {fecha_fin_str} ya está CERRADA.'}), 409

    # Bloqueo si el proyecto ya tiene un reporte solapado
    overlap = ReporteSemanal.query.filter(
        ReporteSemanal.proyecto_id == proyecto_id,
        ReporteSemanal.fecha_inicio_semana <= fecha_fin,
        ReporteSemanal.fecha_fin_semana >= fecha_inicio,
    ).first()
    if overlap:
        return jsonify({'error': 'Este proyecto ya tiene un reporte en esa semana.'}), 409

    try:
        nuevo = ReporteSemanal(
            proyecto_id=proyecto_id,
            fecha_inicio_semana=fecha_inicio,
            fecha_fin_semana=fecha_fin,
            estado='BORRADOR',
            creado_por_id=current_user().id,
        )
        db.session.add(nuevo)
        db.session.commit()
        log_action(f"API: abrió reporte semanal para proyecto ID {proyecto_id}")
        emit_to_role(['admin', 'super_admin', 'coordinador'], 'reporte:lista_changed', {
            'id': nuevo.id, 'action': 'created',
        })
        return jsonify({'id': nuevo.id, 'estado': nuevo.estado}), 201
    except Exception:
        db.session.rollback()
        current_app.logger.error("Error creando reporte: %s", traceback.format_exc())
        return jsonify({'error': 'Error al abrir el reporte'}), 500


@bp.route('/reportes/<int:reporte_id>', methods=['GET'])
@jwt_required
def detalle_reporte(reporte_id):
    r = ReporteSemanal.query.options(
        joinedload(ReporteSemanal.proyecto).selectinload(Proyecto.participantes),
        selectinload(ReporteSemanal.registros),
    ).get_or_404(reporte_id)

    if not _puede_acceder_proyecto(r.proyecto):
        return jsonify({'error': 'Acceso denegado'}), 403

    trabajadores = sorted(r.proyecto.participantes, key=lambda t: (t.nombre or '').lower())

    return jsonify({
        'id': r.id,
        'estado': r.estado,
        'fecha_inicio': r.fecha_inicio_semana.isoformat(),
        'fecha_fin': r.fecha_fin_semana.isoformat(),
        'proyecto': {
            'id': r.proyecto.id,
            'numero_proyecto': r.proyecto.numero_proyecto,
            'nombre': r.proyecto.nombre or '',
        },
        'editable': r.estado == 'BORRADOR' and _puede_acceder_proyecto(r.proyecto),
        'trabajadores': [_trabajador_row(t) for t in trabajadores],
        'semana_fechas': _semana_fechas(r.fecha_inicio_semana, r.fecha_fin_semana),
        'registros': [_registro_dict(reg) for reg in r.registros],
        'incidencias': INCIDENCIAS,
    })


@bp.route('/reportes/<int:reporte_id>/cerrar', methods=['POST'])
@jwt_required
def cerrar_reporte(reporte_id):
    r = ReporteSemanal.query.get_or_404(reporte_id)
    if not _puede_acceder_proyecto(r.proyecto):
        return jsonify({'error': 'Acceso denegado'}), 403
    if r.estado != 'BORRADOR':
        return jsonify({'error': 'El reporte ya está cerrado'}), 409
    if not r.registros:
        return jsonify({'error': 'No se puede cerrar sin registros'}), 400

    try:
        r.estado = 'TERMINADO'
        db.session.commit()
        log_action(f"API: cerró reporte semanal ID {r.id} ({r.proyecto.numero_proyecto})")
        emit_to_role(['admin', 'super_admin', 'coordinador'], 'reporte:lista_changed', {
            'id': r.id, 'action': 'cerrado',
        })

        try:
            from app.models import crear_notif_admins
            num = r.proyecto.numero_proyecto if r.proyecto else '—'
            semana = r.fecha_inicio_semana.strftime('%d/%m/%Y')
            crear_notif_admins(
                tipo='REPORTE_CERRADO',
                titulo=f'Reporte de horas cerrado — Proyecto {num}',
                mensaje=f'El reporte semanal del proyecto {num} (semana del {semana}) está listo para prenómina.',
                url='/prenomina/',
            )
            db.session.commit()
        except Exception:
            current_app.logger.warning("No se pudo crear notificación de reporte cerrado", exc_info=True)

        return jsonify({'ok': True, 'estado': r.estado})
    except Exception:
        db.session.rollback()
        current_app.logger.error("Error cerrando reporte: %s", traceback.format_exc())
        return jsonify({'error': 'Error al cerrar el reporte'}), 500
