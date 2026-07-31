"""Descuentos individuales + picker de trabajadores.

Registra:
  GET    /trabajadores-disponibles                  trabajadores_disponibles
  POST   /periodos/<int:periodo_id>/descuentos      agregar_descuento
  DELETE /descuentos/<int:descuento_id>             eliminar_descuento
  POST   /descuentos/bulk-delete                    eliminar_descuentos_bulk
"""
import traceback
from datetime import datetime
from decimal import Decimal

from flask import current_app, jsonify, request
from sqlalchemy.orm import selectinload

from app.extensions import db
from app.models import AjusteDescuento, AjustePeriodo, AjusteTrabajadorPeriodo, Trabajador
from app.realtime import emit_to_role
from app.routes._api_helpers import require_admin
from app.routes.api_auth import jwt_required
from app.utils import log_action

from ._core import bp, _num


@bp.route('/trabajadores-disponibles', methods=['GET'])
@jwt_required
def trabajadores_disponibles():
    denied = require_admin()
    if denied:
        return denied
    trabajadores = Trabajador.query.filter_by(activo=True).order_by(Trabajador.nombre).all()
    return jsonify([
        {
            'id': t.id,
            'no_empleado': t.no_empleado,
            'nombre': t.nombre,
            'nombre_apellidos': t.nombre_apellidos,
            'nombre_completo': t.nombre_completo,
        } for t in trabajadores
    ])


@bp.route('/periodos/<int:periodo_id>/descuentos', methods=['POST'])
@jwt_required
def agregar_descuento(periodo_id):
    denied = require_admin()
    if denied:
        return denied

    periodo = db.get_or_404(AjustePeriodo, periodo_id)
    if periodo.estado != 'ABIERTO':
        return jsonify({'error': 'Este periodo ya está cerrado'}), 400

    data = request.get_json(silent=True) or {}
    trabajador_id = data.get('trabajador_id')
    monto_in = data.get('monto')
    fecha_str = data.get('fecha_descuento')
    notas = (data.get('notas') or '').strip()

    if not trabajador_id or monto_in in (None, '') or not fecha_str:
        return jsonify({'error': 'trabajador_id, monto y fecha_descuento son obligatorios'}), 400

    try:
        trabajador_id = int(trabajador_id)
        monto = Decimal(str(monto_in))
        fecha = datetime.strptime(fecha_str, '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return jsonify({'error': 'Valores inválidos'}), 400

    if monto <= 0:
        return jsonify({'error': 'El monto debe ser mayor a $0.00'}), 400

    # Validar que la fecha cae dentro del periodo
    if not (periodo.fecha_inicio <= fecha <= periodo.fecha_fin):
        return jsonify({'error': 'La fecha está fuera del rango del periodo'}), 400

    # Validar que el trabajador está en el periodo
    tp = AjusteTrabajadorPeriodo.query.filter_by(
        periodo_id=periodo.id, trabajador_id=trabajador_id,
    ).first()
    if not tp:
        return jsonify({'error': 'Trabajador no asignado a este periodo'}), 400

    try:
        desc = AjusteDescuento(
            periodo_id=periodo.id,
            trabajador_id=trabajador_id,
            monto=monto,
            fecha_descuento=fecha,
            notas=notas or None,
        )
        db.session.add(desc)
        db.session.commit()
        log_action(f'API: descuento ajuste #{desc.id} agregado en periodo {periodo.nombre}')
        emit_to_role(['admin', 'super_admin', 'finanzas'], 'ajuste:changed', {
            'id': periodo.id, 'descuento_id': desc.id, 'action': 'descuento_agregado',
        })
        return jsonify({
            'id': desc.id,
            'fecha_descuento': desc.fecha_descuento.isoformat(),
            'monto': _num(desc.monto),
            'notas': desc.notas or '',
            'cobrado': False,
        }), 201
    except Exception:
        db.session.rollback()
        current_app.logger.error("Error agregando descuento ajuste: %s", traceback.format_exc())
        return jsonify({'error': 'Error al agregar el descuento'}), 500


@bp.route('/descuentos/<int:descuento_id>', methods=['DELETE'])
@jwt_required
def eliminar_descuento(descuento_id):
    denied = require_admin()
    if denied:
        return denied

    desc = db.get_or_404(AjusteDescuento, descuento_id)
    if desc.periodo.estado != 'ABIERTO':
        return jsonify({'error': 'No se pueden eliminar descuentos de un periodo cerrado'}), 400
    if getattr(desc, 'cobrado', False):
        return jsonify({'error': 'Este descuento ya fue cobrado en la prenómina'}), 400

    try:
        periodo_id = desc.periodo_id
        db.session.delete(desc)
        db.session.commit()
        emit_to_role(['admin', 'super_admin', 'finanzas'], 'ajuste:changed', {
            'id': periodo_id, 'descuento_id': descuento_id, 'action': 'descuento_eliminado',
        })
        return jsonify({'ok': True})
    except Exception:
        db.session.rollback()
        current_app.logger.error("Error eliminando descuento ajuste: %s", traceback.format_exc())
        return jsonify({'error': 'Error al eliminar'}), 500


@bp.route('/descuentos/bulk-delete', methods=['POST'])
@jwt_required
def eliminar_descuentos_bulk():
    """Elimina varios descuentos en una sola transacción.

    Body: { "descuento_ids": [int, ...] }  (1..200 ids).

    Respeta las mismas reglas de negocio que el delete individual: salta
    (no falla) los descuentos cuyo periodo esté cerrado o ya estén cobrados,
    y los reporta en `skipped` para que la UI los muestre.
    """
    denied = require_admin()
    if denied:
        return denied

    payload = request.get_json(silent=True) or {}
    raw_ids = payload.get('descuento_ids') or []
    if not isinstance(raw_ids, list) or not raw_ids:
        return jsonify({'error': 'Lista de descuentos vacía'}), 422
    if len(raw_ids) > 200:
        return jsonify({'error': 'Máximo 200 descuentos por operación'}), 422
    try:
        ids = sorted({int(i) for i in raw_ids})
    except (TypeError, ValueError):
        return jsonify({'error': 'IDs deben ser enteros'}), 422

    descuentos = (
        AjusteDescuento.query
        .options(selectinload(AjusteDescuento.periodo))
        .filter(AjusteDescuento.id.in_(ids))
        .all()
    )
    found_ids = {d.id for d in descuentos}
    skipped = [{'id': i, 'reason': 'no_encontrado'} for i in ids if i not in found_ids]

    deleted_ids = []
    periodo_ids = set()
    for d in descuentos:
        if d.periodo.estado != 'ABIERTO':
            skipped.append({'id': d.id, 'reason': 'periodo_cerrado'})
            continue
        if getattr(d, 'cobrado', False):
            skipped.append({'id': d.id, 'reason': 'ya_cobrado'})
            continue
        periodo_ids.add(d.periodo_id)
        deleted_ids.append(d.id)
        db.session.delete(d)

    if not deleted_ids:
        return jsonify({'ok': True, 'deleted': 0, 'ids': [], 'skipped': skipped})

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.error('Error bulk delete descuentos: %s', traceback.format_exc())
        return jsonify({'error': 'Error al eliminar descuentos'}), 500

    log_action(f'API bulk-delete descuentos ajuste: {len(deleted_ids)} eliminados, ids={deleted_ids}')
    emit_to_role(['admin', 'super_admin', 'finanzas'], 'ajuste:changed', {
        'action': 'descuentos_bulk_eliminados',
        'periodo_ids': sorted(periodo_ids),
        'descuento_ids': deleted_ids,
    })
    return jsonify({
        'ok': True,
        'deleted': len(deleted_ids),
        'ids': deleted_ids,
        'periodo_ids': sorted(periodo_ids),
        'skipped': skipped,
    })
