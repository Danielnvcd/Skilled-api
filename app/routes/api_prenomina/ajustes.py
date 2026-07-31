"""Ajustes manuales sobre una prenómina abierta: descuentos, depósitos,
viáticos y festivos.

Registra:
  /descuentos               POST
  /descuentos/<int:id>      DELETE
  /depositos                POST
  /depositos/<int:id>       DELETE
  /viaticos                 PATCH
  /festivos                 PATCH
"""
import traceback
from decimal import Decimal

from flask import current_app, jsonify, request

from app.extensions import db
from app.models import DepositoExtra, DescuentoPrenomina, Prenomina
from app.realtime import emit_to_role
from app.routes._api_helpers import require_admin
from app.routes.api_auth import jwt_required
from app.utils import log_action, recalcular_totales_prenomina

from ._core import bp, _parse_fecha, _num


def _validar_payload_monto(data, *campos):
    faltantes = [c for c in campos if data.get(c) in (None, '')]
    if faltantes:
        return None, (f'Faltan campos: {", ".join(faltantes)}', 400)
    try:
        out = []
        for c in campos:
            v = data[c]
            out.append(Decimal(str(v)) if 'monto' in c else int(v) if c.endswith('_id') else v)
        return out, None
    except (TypeError, ValueError):
        return None, ('Valores numéricos inválidos', 400)


# Enums permitidos por el modelo DescuentoPrenomina.
_DESCUENTO_TIPOS = {'INCIDENCIA', 'MANUAL', 'PRESTAMO'}
# Límite duro alineado con la columna BD String(250).
_CONCEPTO_MAX = 250
# Cap defensivo de montos para evitar errores tipográficos catastróficos
# (admin mete $9999999 por accidente) y posibles overflow.
_MONTO_MAX = Decimal('999999.99')


@bp.route('/descuentos', methods=['POST'])
@jwt_required
def agregar_descuento():
    denied = require_admin()
    if denied:
        return denied

    data = request.get_json(silent=True) or {}
    required = ['prenomina_id', 'tipo', 'concepto', 'monto']
    if any(data.get(c) in (None, '') for c in required):
        return jsonify({'error': f'Faltan campos: {", ".join(required)}'}), 400

    tipo = str(data['tipo']).strip().upper()
    if tipo not in _DESCUENTO_TIPOS:
        return jsonify({
            'error': f'tipo inválido. Permitidos: {", ".join(sorted(_DESCUENTO_TIPOS))}'
        }), 400

    concepto = str(data['concepto']).strip()
    if not concepto or len(concepto) > _CONCEPTO_MAX:
        return jsonify({'error': f'concepto vacío o > {_CONCEPTO_MAX} caracteres'}), 400

    try:
        prenomina_id = int(data['prenomina_id'])
        monto = Decimal(str(data['monto']))
    except (TypeError, ValueError):
        return jsonify({'error': 'prenomina_id y monto deben ser numéricos'}), 400

    if monto <= 0 or monto > _MONTO_MAX:
        return jsonify({'error': f'monto fuera de rango (0 < monto ≤ {_MONTO_MAX})'}), 400

    prenomina = db.get_or_404(Prenomina, prenomina_id)
    if prenomina.estado != 'ABIERTA':
        return jsonify({'error': 'Solo se pueden editar prenóminas ABIERTAS'}), 400

    try:
        fecha_inc_str = data.get('fecha_incidencia')
        fecha_inc = _parse_fecha(fecha_inc_str) if fecha_inc_str else None
        # Sanity: la fecha de incidencia no debería ser futura (datos limpios)
        from datetime import date as _date
        if fecha_inc and fecha_inc > _date.today():
            return jsonify({'error': 'fecha_incidencia no puede ser futura'}), 400
        desc = DescuentoPrenomina(
            prenomina_id=prenomina_id,
            trabajador_id=prenomina.trabajador_id,
            tipo=tipo,
            concepto=concepto,
            monto=monto,
            fecha_incidencia=fecha_inc,
        )
        db.session.add(desc)
        db.session.flush()
        db.session.expire(prenomina, ['descuentos_detalle'])
        recalcular_totales_prenomina(prenomina)
        db.session.commit()
        log_action(f"API descuento_agregado: prenomina_id={prenomina_id} monto={monto}")
        emit_to_role(['admin', 'super_admin', 'finanzas'], 'prenomina:changed', {
            'prenomina_id': prenomina_id, 'action': 'descuento_agregado',
        })
        return jsonify({
            'id': desc.id,
            'total_deducciones': _num(prenomina.total_deducciones),
            'total_a_pagar': _num(prenomina.total_a_pagar),
        }), 201
    except Exception:
        db.session.rollback()
        current_app.logger.error("Error agregando descuento: %s", traceback.format_exc())
        return jsonify({'error': 'Error al agregar el descuento'}), 500


@bp.route('/descuentos/<int:id>', methods=['DELETE'])
@jwt_required
def eliminar_descuento(id):
    denied = require_admin()
    if denied:
        return denied

    desc = db.get_or_404(DescuentoPrenomina, id)
    prenomina = desc.prenomina
    if prenomina.estado != 'ABIERTA':
        return jsonify({'error': 'Solo se pueden editar prenóminas ABIERTAS'}), 400

    try:
        db.session.delete(desc)
        db.session.flush()
        db.session.expire(prenomina, ['descuentos_detalle'])
        recalcular_totales_prenomina(prenomina)
        db.session.commit()
        log_action(f"API descuento_eliminado: descuento_id={id} prenomina_id={prenomina.id}")
        emit_to_role(['admin', 'super_admin', 'finanzas'], 'prenomina:changed', {
            'prenomina_id': prenomina.id, 'action': 'descuento_eliminado',
        })
        return jsonify({
            'total_deducciones': _num(prenomina.total_deducciones),
            'total_a_pagar': _num(prenomina.total_a_pagar),
        })
    except Exception:
        db.session.rollback()
        current_app.logger.error("Error eliminando descuento: %s", traceback.format_exc())
        return jsonify({'error': 'Error al eliminar el descuento'}), 500


@bp.route('/depositos', methods=['POST'])
@jwt_required
def agregar_deposito():
    denied = require_admin()
    if denied:
        return denied

    data = request.get_json(silent=True) or {}
    required = ['prenomina_id', 'concepto', 'monto']
    if any(data.get(c) in (None, '') for c in required):
        return jsonify({'error': f'Faltan campos: {", ".join(required)}'}), 400

    concepto = str(data['concepto']).strip()
    if not concepto or len(concepto) > _CONCEPTO_MAX:
        return jsonify({'error': f'concepto vacío o > {_CONCEPTO_MAX} caracteres'}), 400

    try:
        prenomina_id = int(data['prenomina_id'])
        monto = Decimal(str(data['monto']))
    except (TypeError, ValueError):
        return jsonify({'error': 'prenomina_id y monto deben ser numéricos'}), 400

    if monto <= 0 or monto > _MONTO_MAX:
        return jsonify({'error': f'monto fuera de rango (0 < monto ≤ {_MONTO_MAX})'}), 400

    prenomina = db.get_or_404(Prenomina, prenomina_id)
    if prenomina.estado != 'ABIERTA':
        return jsonify({'error': 'Solo se pueden editar prenóminas ABIERTAS'}), 400

    try:
        dep = DepositoExtra(
            prenomina_id=prenomina_id,
            trabajador_id=prenomina.trabajador_id,
            monto=monto,
            concepto=concepto,
        )
        db.session.add(dep)
        db.session.flush()
        db.session.expire(prenomina, ['depositos_detalle'])
        recalcular_totales_prenomina(prenomina)
        db.session.commit()
        log_action(f"API deposito_agregado: prenomina_id={prenomina_id} monto={monto}")
        emit_to_role(['admin', 'super_admin', 'finanzas'], 'prenomina:changed', {
            'prenomina_id': prenomina_id, 'action': 'deposito_agregado',
        })
        return jsonify({
            'id': dep.id,
            'total_percepciones': _num(prenomina.total_percepciones),
            'total_a_pagar': _num(prenomina.total_a_pagar),
        }), 201
    except Exception:
        db.session.rollback()
        current_app.logger.error("Error agregando depósito: %s", traceback.format_exc())
        return jsonify({'error': 'Error al agregar el depósito'}), 500


@bp.route('/depositos/<int:id>', methods=['DELETE'])
@jwt_required
def eliminar_deposito(id):
    denied = require_admin()
    if denied:
        return denied

    dep = db.get_or_404(DepositoExtra, id)
    prenomina = dep.prenomina
    if prenomina.estado != 'ABIERTA':
        return jsonify({'error': 'Solo se pueden editar prenóminas ABIERTAS'}), 400

    try:
        db.session.delete(dep)
        db.session.flush()
        db.session.expire(prenomina, ['depositos_detalle'])
        recalcular_totales_prenomina(prenomina)
        db.session.commit()
        log_action(f"API deposito_eliminado: deposito_id={id} prenomina_id={prenomina.id}")
        emit_to_role(['admin', 'super_admin', 'finanzas'], 'prenomina:changed', {
            'prenomina_id': prenomina.id, 'action': 'deposito_eliminado',
        })
        return jsonify({
            'total_percepciones': _num(prenomina.total_percepciones),
            'total_a_pagar': _num(prenomina.total_a_pagar),
        })
    except Exception:
        db.session.rollback()
        current_app.logger.error("Error eliminando depósito: %s", traceback.format_exc())
        return jsonify({'error': 'Error al eliminar el depósito'}), 500


def _patch_monto(field_name: str, payload_key: str):
    """Helper para los endpoints viáticos / festivos: validan + asignan + recalculan."""
    denied = require_admin()
    if denied:
        return denied

    data = request.get_json(silent=True) or {}
    prenomina_id = data.get('prenomina_id')
    monto = data.get(payload_key)
    if prenomina_id is None or monto is None:
        return jsonify({'error': f'Faltan campos: prenomina_id, {payload_key}'}), 400

    try:
        prenomina_id = int(prenomina_id)
        monto = Decimal(str(monto))
    except (TypeError, ValueError):
        return jsonify({'error': 'Valores numéricos inválidos'}), 400

    if monto < 0:
        return jsonify({'error': 'El monto no puede ser negativo'}), 400

    prenomina = db.get_or_404(Prenomina, prenomina_id)
    if prenomina.estado != 'ABIERTA':
        return jsonify({'error': 'Solo se pueden editar prenóminas ABIERTAS'}), 400

    try:
        setattr(prenomina, field_name, monto)
        recalcular_totales_prenomina(prenomina)
        db.session.commit()
        emit_to_role(['admin', 'super_admin', 'finanzas'], 'prenomina:changed', {
            'prenomina_id': prenomina_id, 'action': f'patch_{field_name}',
        })
        return jsonify({
            field_name: _num(getattr(prenomina, field_name)),
            'total_percepciones': _num(prenomina.total_percepciones),
            'total_a_pagar': _num(prenomina.total_a_pagar),
        })
    except Exception:
        db.session.rollback()
        current_app.logger.error("Error patch %s: %s", field_name, traceback.format_exc())
        return jsonify({'error': 'Error al actualizar'}), 500


@bp.route('/viaticos', methods=['PATCH'])
@jwt_required
def patch_viaticos():
    return _patch_monto('pago_viaticos', 'monto_viaticos')


@bp.route('/festivos', methods=['PATCH'])
@jwt_required
def patch_festivos():
    return _patch_monto('pago_festivos', 'monto_festivos')
