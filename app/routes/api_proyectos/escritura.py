"""Crear y actualizar proyectos.

Tras cualquier mutación se recalculan los campos derivados del trabajador
(`no_proyecto`, `ubicacion_actual`, `coord_a_cargo`) desde sus proyectos
ACTIVOS — ver `recalcular_campos_proyecto` en `_core.py`. Eso cubre todos los
casos: sacar a un trabajador, desactivar/reactivar el proyecto, cambiar o
quitar coordinador, renombrar el proyecto, y trabajadores en varios proyectos.
"""
from flask import jsonify, request
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import Proyecto, Trabajador
from app.realtime import emit_to_role
from app.routes._api_helpers import api_transactional, require_admin
from app.routes.api_auth import jwt_required
from app.utils import log_action

from ._core import (
    _parse_bool,
    _proyecto_detail,
    _validar_coordinador,
    bp,
    recalcular_campos_proyecto,
)


def _parse_participantes_ids(data):
    """Normaliza `participantes_ids` a lista de ints.

    Devuelve (ids|None, error_response|None) — error 400 si algún id no es
    numérico (payload malformado, no lo ignoramos en silencio).
    """
    raw = data.get('participantes_ids') or []
    try:
        return [int(x) for x in raw], None
    except (TypeError, ValueError):
        return None, (jsonify({'error': 'participantes_ids inválido'}), 400)


def _cargar_participantes(ids):
    """Trae los trabajadores en un solo query. Los ids que no existen no
    truenan: se reportan en `warnings` para que el SPA lo muestre."""
    encontrados = (
        Trabajador.query.filter(Trabajador.id.in_(ids)).all() if ids else []
    )
    faltantes = sorted(set(ids) - {t.id for t in encontrados})
    warnings = [f'El trabajador con id {i} no existe; se ignoró' for i in faltantes]
    return encontrados, warnings


def _parse_coordinador(data):
    coord_id = data.get('coordinador_id') or None
    try:
        return (int(coord_id) if coord_id else None), None
    except (TypeError, ValueError):
        return None, (jsonify({'error': 'coordinador_id inválido'}), 400)


@bp.route('', methods=['POST'])
@jwt_required
@api_transactional('Error al crear el proyecto')
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

    coord_id, err = _parse_coordinador(data)
    if err:
        return err

    # HIGH-05: validar que el coord existe y tiene rol apropiado ANTES de crear
    _, err = _validar_coordinador(coord_id)
    if err:
        return err

    ids, err = _parse_participantes_ids(data)
    if err:
        return err
    participantes, warnings = _cargar_participantes(ids)

    nuevo = Proyecto(
        numero_proyecto=numero_proyecto,
        nombre=nombre,
        activo=_parse_bool(data.get('activo', True)),
        coordinador_id=coord_id,
    )
    nuevo.participantes = participantes
    db.session.add(nuevo)

    # El flush materializa proyecto + filas M:N (recalcular las lee de BD) y
    # dispara el unique de numero_proyecto: dos POST simultáneos pasan ambos
    # el check de arriba, pero solo uno sobrevive aquí — 409, no 500.
    try:
        db.session.flush()
    except IntegrityError:
        db.session.rollback()
        return jsonify({'error': 'El Número de Proyecto ya existe'}), 409

    for t in participantes:
        recalcular_campos_proyecto(t)

    db.session.commit()
    log_action(f"Creó el proyecto {nuevo.numero_proyecto} - {nuevo.nombre}")
    emit_to_role(['admin', 'super_admin', 'coordinador'], 'proyecto:changed', {
        'id': nuevo.id, 'action': 'created',
    })
    # Los participantes cambian sus campos derivados (no_proyecto, ubicación,
    # coordinador): avisar para que la lista de empleados refresque en vivo.
    if participantes:
        emit_to_role(['admin', 'super_admin', 'coordinador'], 'empleado:changed', {
            'ids': [t.id for t in participantes], 'action': 'proyecto_asignado',
        })
    body = _proyecto_detail(nuevo)
    body['warnings'] = warnings
    return jsonify(body), 201


@bp.route('/<int:id>', methods=['PUT'])
@jwt_required
@api_transactional('Error al actualizar el proyecto')
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

    coord_id, err = _parse_coordinador(data)
    if err:
        return err

    # HIGH-05: validar que el coord existe y tiene rol apropiado
    _, err = _validar_coordinador(coord_id)
    if err:
        return err

    ids, err = _parse_participantes_ids(data)
    if err:
        return err
    participantes, warnings = _cargar_participantes(ids)

    # Afectados = los que estaban ∪ los que quedan. Los removidos necesitan
    # recálculo para soltar este proyecto; los que permanecen, para reflejar
    # renombres / cambio de coordinador / toggle de activo.
    afectados = {t.id: t for t in p.participantes}

    p.numero_proyecto = nuevo_numero
    p.nombre = nuevo_nombre
    p.activo = _parse_bool(data.get('activo', p.activo))
    p.coordinador_id = coord_id
    p.participantes = participantes
    for t in participantes:
        afectados[t.id] = t

    try:
        db.session.flush()
    except IntegrityError:
        db.session.rollback()
        return jsonify({'error': 'El Número de Proyecto ya existe'}), 409

    for t in afectados.values():
        recalcular_campos_proyecto(t)

    db.session.commit()
    log_action(f"Actualizó el proyecto {p.numero_proyecto} - {p.nombre}")
    emit_to_role(['admin', 'super_admin', 'coordinador'], 'proyecto:changed', {
        'id': p.id, 'action': 'updated',
    })
    # Todos los afectados (los que entran y los que salen) recalcularon sus
    # campos derivados: avisar para refrescar la lista de empleados en vivo.
    if afectados:
        emit_to_role(['admin', 'super_admin', 'coordinador'], 'empleado:changed', {
            'ids': list(afectados.keys()), 'action': 'proyecto_reasignado',
        })
    body = _proyecto_detail(p)
    body['warnings'] = warnings
    return jsonify(body)
