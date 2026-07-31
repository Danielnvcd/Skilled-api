"""Credenciales de planta (vista bulk + edición individual).

Registra:
  GET  /credenciales-lista
  POST /<int:id>/credenciales
"""
import json

from flask import current_app, jsonify, request
from sqlalchemy import func, or_
from sqlalchemy.orm import joinedload, selectinload

from app.extensions import db
from app.models import Proyecto, Trabajador
from app.realtime import emit_to_role
from app.routes._api_helpers import current_user, is_admin
from app.routes.api_auth import jwt_required
from app.utils import log_action

from ._core import bp, _replace_credenciales


def _credencial_row(t: Trabajador) -> dict:
    """Fila para la pantalla de Credenciales: incluye credenciales + datos de la ficha."""
    proyectos_activos = (
        t.proyectos.options(joinedload(Proyecto.coordinador)).filter_by(activo=True).all()
    )
    proyectos_nombres = [p.nombre for p in proyectos_activos if p.nombre]
    coordinadores = []
    for p in proyectos_activos:
        if p.coordinador:
            nombre = p.coordinador.full_name or p.coordinador.username
            if nombre and nombre not in coordinadores:
                coordinadores.append(nombre)

    return {
        'id': t.id,
        'no_empleado': t.no_empleado,
        'nombre': t.nombre,
        'nombre_apellidos': t.nombre_apellidos,
        'area': t.area,
        'puesto': t.puesto,
        'tipo_nomina': t.tipo_nomina,
        'celular': t.celular,
        'ubicacion_actual': t.ubicacion_actual,
        'ubicacion_estado': t.ubicacion_estado,
        'observaciones': t.observaciones,
        'foto_perfil': t.foto_perfil,
        # Solo coordinadores de proyectos ACTIVOS — sin fallback al string
        # legacy: si el proyecto se desactivó o sacaron al trabajador, aquí
        # ya no debe figurar esa relación.
        'coord_a_cargo': ', '.join(coordinadores),
        'proyectos_activos': ', '.join(proyectos_nombres) if proyectos_nombres else '',
        'credenciales': [
            {
                'planta': c.planta,
                'credencial_id': c.credencial_id,
                'fecha_caducidad': c.fecha_caducidad.isoformat() if c.fecha_caducidad else None,
            }
            for c in t.credenciales
        ],
    }


@bp.route('/credenciales-lista', methods=['GET'])
@jwt_required
def listar_credenciales():
    page = request.args.get('page', 1, type=int)
    per_page = min(request.args.get('per_page', 20, type=int), 100)
    q = (request.args.get('q') or '').strip()

    query = Trabajador.query.options(
        selectinload(Trabajador.credenciales),
    ).filter(Trabajador.activo == True, Trabajador.fecha_baja == None)  # noqa: E712

    if current_user().role == 'coordinador':
        mis_proyectos = Proyecto.query.options(selectinload(Proyecto.participantes)).filter_by(
            activo=True, coordinador_id=current_user().id,
        ).all()
        ids_trabajadores = {t.id for p in mis_proyectos for t in p.participantes}
        query = query.filter(Trabajador.id.in_(ids_trabajadores))

    if q:
        like = f'%{q}%'
        query = query.filter(or_(
            Trabajador.nombre.ilike(like),
            Trabajador.nombre_apellidos.ilike(like),
            Trabajador.no_empleado.ilike(like),
            Trabajador.rfc.ilike(like),
        ))

    pagination = query.order_by(func.lower(Trabajador.nombre)).paginate(
        page=page, per_page=per_page, error_out=False,
    )
    return jsonify({
        'items': [_credencial_row(t) for t in pagination.items],
        'page': pagination.page,
        'per_page': pagination.per_page,
        'total': pagination.total,
        'pages': pagination.pages,
        'has_next': pagination.has_next,
        'has_prev': pagination.has_prev,
    })


@bp.route('/<int:id>/credenciales', methods=['POST'])
@jwt_required
def guardar_credenciales(id):
    if not is_admin():
        return jsonify({'error': 'Solo admin puede editar credenciales'}), 403
    t = db.session.get(Trabajador, id)
    if not t:
        return jsonify({'error': 'No encontrado'}), 404
    try:
        data = request.get_json(silent=True) or {}
        if 'observaciones' in data:
            t.observaciones = data.get('observaciones')
        _replace_credenciales(t, json.dumps(data.get('credenciales', [])))
        db.session.commit()
        log_action(f'Actualizó credenciales del trabajador {t.nombre} ({t.no_empleado})')
        emit_to_role(['admin', 'super_admin', 'coordinador'], 'credencial:changed', {
            'trabajador_id': t.id,
        })
        return jsonify({'ok': True})
    except ValueError as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        db.session.rollback()
        current_app.logger.error('Error credenciales: %s', e)
        return jsonify({'error': 'Error al guardar credenciales'}), 500
