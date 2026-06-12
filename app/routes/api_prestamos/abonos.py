"""Operaciones sobre el saldo del préstamo: abonar (parcial) y liquidar (total)."""
import traceback
from datetime import datetime
from decimal import Decimal, InvalidOperation

from flask import current_app, jsonify, request

from app.extensions import db
from app.models import AbonoPrestamo, Prestamo
from app.realtime import emit_to_role
from app.routes._api_helpers import current_user, require_admin
from app.routes.api_auth import jwt_required
from app.utils import log_action, to_dec

from ._core import _num, _recalcular_prenominas_abiertas, bp


@bp.route('/<int:id>/abonar', methods=['POST'])
@jwt_required
def abonar(id):
    denied = require_admin()
    if denied:
        return denied

    data = request.get_json(silent=True) or {}
    if 'monto' not in data:
        return jsonify({'error': 'monto requerido'}), 400

    try:
        monto = Decimal(str(data['monto']))
    except (TypeError, ValueError, InvalidOperation):
        # `Decimal('texto')` lanza `InvalidOperation`, no `ValueError`.
        return jsonify({'error': 'Monto inválido'}), 400

    if monto <= 0:
        return jsonify({'error': 'El monto debe ser mayor a cero'}), 400

    p = Prestamo.query.get_or_404(id)
    if p.estado == 'LIQUIDADO':
        return jsonify({'error': 'Préstamo ya liquidado'}), 400

    try:
        p.monto_restante = max(Decimal('0'), to_dec(p.monto_restante) - monto)
        if p.monto_restante <= 0:
            p.monto_restante = 0
            p.estado = 'LIQUIDADO'
            p.activo = False

        db.session.add(AbonoPrestamo(
            prestamo_id=p.id,
            monto=monto,
            fecha_abono=datetime.now().date(),
            tipo='MANUAL',
            registrado_por_id=current_user().id,
            notas=data.get('notas') or 'Abono extraordinario manual',
        ))
        db.session.commit()
        _recalcular_prenominas_abiertas(p.trabajador_id)
        log_action(f'API: abono ${monto} al préstamo #{id}. Restante: ${p.monto_restante}')
        emit_to_role(['admin', 'super_admin', 'finanzas'], 'prestamo:changed', {
            'id': p.id, 'action': 'abonado',
        })
        return jsonify({
            'monto_restante': _num(p.monto_restante),
            'estado': p.estado,
        })
    except Exception:
        db.session.rollback()
        current_app.logger.error("Error al abonar: %s", traceback.format_exc())
        return jsonify({'error': 'Error al registrar el abono'}), 500


@bp.route('/<int:id>/liquidar', methods=['POST'])
@jwt_required
def liquidar(id):
    denied = require_admin()
    if denied:
        return denied

    p = Prestamo.query.get_or_404(id)
    saldo = to_dec(p.monto_restante)

    try:
        if saldo > 0:
            db.session.add(AbonoPrestamo(
                prestamo_id=p.id,
                monto=saldo,
                fecha_abono=datetime.now().date(),
                tipo='MANUAL',
                registrado_por_id=current_user().id,
                notas='Liquidación total manual',
            ))

        p.monto_restante = 0
        p.estado = 'LIQUIDADO'
        p.activo = False
        db.session.commit()
        _recalcular_prenominas_abiertas(p.trabajador_id)
        log_action(f'API: préstamo #{id} liquidado manualmente (saldo ${saldo})')
        emit_to_role(['admin', 'super_admin', 'finanzas'], 'prestamo:changed', {
            'id': p.id, 'action': 'liquidado',
        })
        return jsonify({'estado': p.estado, 'monto_restante': 0.0})
    except Exception:
        db.session.rollback()
        current_app.logger.error("Error al liquidar: %s", traceback.format_exc())
        return jsonify({'error': 'Error al liquidar'}), 500
