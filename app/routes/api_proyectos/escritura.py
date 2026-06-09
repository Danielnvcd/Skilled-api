"""Crear y actualizar proyectos. Mantiene la sincronización de campos del
trabajador (`no_proyecto`, `ubicacion_actual`, `coord_a_cargo`) cuando se
altera la lista de participantes, para que el dato visible en el módulo de
empleados quede coherente.
"""
import traceback

from flask import current_app, jsonify, request

from app.extensions import db
from app.models import Proyecto, Trabajador
from app.realtime import emit_to_role
from app.routes._api_helpers import require_admin
from app.routes.api_auth import jwt_required
from app.utils import log_action

from ._core import (
    _parse_bool,
    _proyecto_detail,
    _sync_trabajador_from_proyecto,
    _validar_coordinador,
    bp,
)


@bp.route('', methods=['POST'])
@jwt_required
def crear():
    denied = require_admin()
    if denied:
        return denied

    data = request.get_json(silent=True) or {}
    numero_proyecto = (data.get('numero_proyecto') or '').strip()
    nombre = (data.get('nombre') or '').strip()

    if not numero_proyecto or not nombre:
        return jsonify({'error': 'Número de Proyecto y Nombre son obligatorios'}), 400

    if Proyecto.query.filter_by(numero_proyecto=numero_proyecto).first():
        return jsonify({'error': 'El Número de Proyecto ya existe'}), 409

    coord_id = data.get('coordinador_id') or None
    try:
        coord_id = int(coord_id) if coord_id else None
    except (TypeError, ValueError):
        return jsonify({'error': 'coordinador_id inválido'}), 400

    # HIGH-05: validar que el coord existe y tiene rol apropiado ANTES de crear
    sup, err = _validar_coordinador(coord_id)
    if err:
        return err

    try:
        nuevo = Proyecto(
            numero_proyecto=numero_proyecto,
            nombre=nombre,
            activo=_parse_bool(data.get('activo', True)),
            coordinador_id=coord_id,
        )

        coord_name = sup.username if sup else None

        participantes_ids = data.get('participantes_ids') or []
        for t_id in participantes_ids:
            t = Trabajador.query.get(t_id)
            if t:
                nuevo.participantes.append(t)
                _sync_trabajador_from_proyecto(t, nuevo, coord_name)

        db.session.add(nuevo)
        db.session.commit()
        log_action(f"Creó el proyecto {nuevo.numero_proyecto} - {nuevo.nombre}")
        emit_to_role(['admin', 'super_admin', 'coordinador'], 'proyecto:changed', {
            'id': nuevo.id, 'action': 'created',
        })
        return jsonify(_proyecto_detail(nuevo)), 201

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error creando proyecto: {e}\n{traceback.format_exc()}")
        return jsonify({'error': 'Error al crear el proyecto'}), 500


@bp.route('/<int:id>', methods=['PUT'])
@jwt_required
def actualizar(id):
    denied = require_admin()
    if denied:
        return denied

    p = Proyecto.query.get_or_404(id)
    data = request.get_json(silent=True) or {}

    nuevo_numero = (data.get('numero_proyecto') or '').strip()
    nuevo_nombre = (data.get('nombre') or '').strip()
    if not nuevo_numero or not nuevo_nombre:
        return jsonify({'error': 'Número de Proyecto y Nombre son obligatorios'}), 400

    if nuevo_numero != p.numero_proyecto and Proyecto.query.filter_by(numero_proyecto=nuevo_numero).first():
        return jsonify({'error': 'El Número de Proyecto ya existe'}), 409

    coord_id = data.get('coordinador_id') or None
    try:
        coord_id = int(coord_id) if coord_id else None
    except (TypeError, ValueError):
        return jsonify({'error': 'coordinador_id inválido'}), 400

    # HIGH-05: validar que el coord existe y tiene rol apropiado
    sup, err = _validar_coordinador(coord_id)
    if err:
        return err

    try:
        old_numero = p.numero_proyecto

        p.numero_proyecto = nuevo_numero
        p.nombre = nuevo_nombre
        p.activo = _parse_bool(data.get('activo', p.activo))
        p.coordinador_id = coord_id

        coord_name = sup.username if sup else None

        nuevos_ids = set(int(x) for x in (data.get('participantes_ids') or []))

        # Limpiar campos del trabajador removido del proyecto (solo si el dato
        # del trabajador todavía apunta al número viejo de este proyecto).
        for past in list(p.participantes):
            if past.id not in nuevos_ids and past.no_proyecto == old_numero:
                past.no_proyecto = None
                past.ubicacion_actual = None
                past.coord_a_cargo = None

        p.participantes = []
        for t_id in nuevos_ids:
            t = Trabajador.query.get(t_id)
            if t:
                p.participantes.append(t)
                _sync_trabajador_from_proyecto(t, p, coord_name)

        db.session.commit()
        log_action(f"Actualizó el proyecto {p.numero_proyecto} - {p.nombre}")
        emit_to_role(['admin', 'super_admin', 'coordinador'], 'proyecto:changed', {
            'id': p.id, 'action': 'updated',
        })
        return jsonify(_proyecto_detail(p))

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error actualizando proyecto {id}: {e}\n{traceback.format_exc()}")
        return jsonify({'error': 'Error al actualizar el proyecto'}), 500
