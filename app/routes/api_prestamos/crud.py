"""CRUD y catálogo de préstamos: listar, trabajadores-disponibles, detalle,
crear, editar."""
import traceback
from datetime import datetime
from decimal import Decimal, InvalidOperation

from flask import current_app, jsonify, request
from sqlalchemy import or_
from sqlalchemy.orm import selectinload

from app.extensions import db
from app.models import AbonoPrestamo, Prestamo, Trabajador
from app.realtime import emit_to_role
from app.routes._api_helpers import require_admin
from app.routes.api_auth import jwt_required
from app.utils import log_action, to_dec

from ._core import _num, _prestamo_row, _recalcular_prenominas_abiertas, bp


@bp.route('', methods=['GET'])
@jwt_required
def listar():
    denied = require_admin()
    if denied:
        return denied

    page = max(1, request.args.get('page', 1, type=int))
    per_page = min(100, max(1, request.args.get('per_page', 20, type=int)))
    q = (request.args.get('q') or '').strip()
    estado = (request.args.get('estado') or '').strip().upper()
    sort = (request.args.get('sort') or '').lower()
    direccion = (request.args.get('dir') or 'asc').lower()

    query = Prestamo.query.options(selectinload(Prestamo.trabajador))
    joined_trabajador = False
    if q:
        query = query.join(Trabajador, Prestamo.trabajador_id == Trabajador.id).filter(or_(
            Trabajador.nombre.ilike(f'%{q}%'),
            Trabajador.nombre_apellidos.ilike(f'%{q}%'),
            Trabajador.no_empleado.ilike(f'%{q}%'),
            db.cast(Prestamo.id, db.String).ilike(f'%{q}%'),
        ))
        joined_trabajador = True
    if estado in ('ACTIVO', 'LIQUIDADO'):
        query = query.filter(Prestamo.estado == estado)

    # Orden por columna con whitelist; sort desconocido/vacío cae al default
    # histórico (creado_en desc). 'trabajador' ordena por nombre y requiere el
    # join — outerjoin para no ocultar préstamos huérfanos, y solo si `q` no
    # lo agregó ya (un segundo join a la misma tabla revienta la query).
    sortables = {
        'trabajador': db.func.lower(Trabajador.nombre),
        'monto': Prestamo.monto_total,
        'restante': Prestamo.monto_restante,
        'descuento': Prestamo.descuento_semanal,
        'inicio': Prestamo.fecha_inicio,
        'estado': Prestamo.estado,
    }
    if sort in sortables:
        if sort == 'trabajador' and not joined_trabajador:
            query = query.outerjoin(Trabajador, Prestamo.trabajador_id == Trabajador.id)
        col = sortables[sort]
        orden = col.desc() if direccion == 'desc' else col.asc()
        query = query.order_by(orden.nullslast(), Prestamo.creado_en.desc(), Prestamo.id)
    else:
        query = query.order_by(Prestamo.creado_en.desc())

    pagination = query.paginate(
        page=page, per_page=per_page, error_out=False,
    )

    return jsonify({
        'items': [_prestamo_row(p) for p in pagination.items],
        'total': pagination.total,
        'page': pagination.page,
        'pages': pagination.pages,
        'per_page': pagination.per_page,
    })


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


@bp.route('/<int:id>', methods=['GET'])
@jwt_required
def detalle(id):
    denied = require_admin()
    if denied:
        return denied

    p = Prestamo.query.options(
        selectinload(Prestamo.abonos),
        selectinload(Prestamo.trabajador),
    ).filter_by(id=id).first_or_404()

    abonos = [
        {
            'id': a.id,
            'fecha_abono': a.fecha_abono.isoformat(),
            'monto': _num(a.monto),
            'tipo': a.tipo,
            'notas': a.notas or '',
        }
        for a in sorted(p.abonos, key=lambda a: a.fecha_abono, reverse=True)
    ]
    total_abonado = sum(a['monto'] for a in abonos)

    base = _prestamo_row(p)
    base['abonos'] = abonos
    base['total_abonado'] = total_abonado
    return jsonify(base)


@bp.route('', methods=['POST'])
@jwt_required
def crear():
    denied = require_admin()
    if denied:
        return denied

    data = request.get_json(silent=True) or {}
    required = ['trabajador_id', 'monto_total', 'plazo_semanas', 'descuento_semanal']
    if any(data.get(c) in (None, '') for c in required):
        return jsonify({'error': f'Faltan campos: {", ".join(required)}'}), 400

    try:
        trabajador_id = int(data['trabajador_id'])
        monto_total = Decimal(str(data['monto_total']))
        plazo_semanas = int(data['plazo_semanas'])
        descuento_semanal = Decimal(str(data['descuento_semanal']))
    except (TypeError, ValueError, InvalidOperation):
        # Decimal('texto') lanza InvalidOperation (no ValueError) — sin ese
        # branch el endpoint devuelve 500 ante un payload basura.
        return jsonify({'error': 'Valores numéricos inválidos'}), 400

    if monto_total <= 0 or descuento_semanal <= 0 or plazo_semanas <= 0:
        return jsonify({'error': 'Monto, plazo y descuento deben ser mayores a cero'}), 400

    fecha_inicio_str = data.get('fecha_inicio')
    try:
        fecha_inicio = datetime.strptime(fecha_inicio_str, '%Y-%m-%d').date() if fecha_inicio_str else None
    except ValueError:
        return jsonify({'error': 'Formato de fecha inválido'}), 400

    try:
        prestamo = Prestamo(
            trabajador_id=trabajador_id,
            monto_total=monto_total,
            plazo_semanas=plazo_semanas,
            descuento_semanal=descuento_semanal,
            monto_restante=monto_total,
            motivo=data.get('motivo') or '',
            frecuencia=data.get('frecuencia') or 'semanal',
            fecha_inicio=fecha_inicio,
            estado='ACTIVO',
            activo=True,
        )
        db.session.add(prestamo)
        db.session.commit()
        _recalcular_prenominas_abiertas(trabajador_id)
        log_action(f'API: préstamo #{prestamo.id} creado para trab #{trabajador_id} por ${monto_total}')
        emit_to_role(['admin', 'super_admin', 'finanzas'], 'prestamo:changed', {
            'id': prestamo.id, 'action': 'created',
        })
        return jsonify({'id': prestamo.id}), 201
    except Exception:
        db.session.rollback()
        current_app.logger.error("Error creando préstamo: %s", traceback.format_exc())
        return jsonify({'error': 'Error al crear el préstamo'}), 500


@bp.route('/<int:id>', methods=['PUT'])
@jwt_required
def editar(id):
    denied = require_admin()
    if denied:
        return denied

    p = Prestamo.query.get_or_404(id)
    if p.estado == 'LIQUIDADO':
        return jsonify({'error': 'No se puede editar un préstamo liquidado'}), 400

    data = request.get_json(silent=True) or {}
    try:
        nuevo_monto = to_dec(data.get('monto_total', p.monto_total))
        nuevo_descuento = to_dec(data.get('descuento_semanal', p.descuento_semanal))
        nuevo_plazo = int(data.get('plazo_semanas', p.plazo_semanas))
    except (TypeError, ValueError):
        return jsonify({'error': 'Valores numéricos inválidos'}), 400

    if nuevo_monto <= 0 or nuevo_descuento <= 0 or nuevo_plazo <= 0:
        return jsonify({'error': 'Monto, plazo y descuento deben ser mayores a cero'}), 400

    total_abonado = sum(
        (to_dec(a.monto) for a in AbonoPrestamo.query.filter_by(prestamo_id=id).all()),
        Decimal('0'),
    )
    if nuevo_monto < total_abonado:
        return jsonify({
            'error': f'El monto total (${nuevo_monto:.2f}) no puede ser menor a lo ya abonado (${total_abonado:.2f}).'
        }), 400

    try:
        p.monto_total = nuevo_monto
        p.plazo_semanas = nuevo_plazo
        p.descuento_semanal = nuevo_descuento
        p.motivo = data.get('motivo', p.motivo)
        p.frecuencia = data.get('frecuencia', p.frecuencia)
        p.monto_restante = nuevo_monto - total_abonado
        if data.get('fecha_inicio'):
            try:
                p.fecha_inicio = datetime.strptime(data['fecha_inicio'], '%Y-%m-%d').date()
            except ValueError:
                return jsonify({'error': 'Formato de fecha inválido'}), 400

        db.session.commit()
        _recalcular_prenominas_abiertas(p.trabajador_id)
        log_action(f'API: préstamo #{id} modificado. Total: ${nuevo_monto:.2f}, Restante: ${p.monto_restante:.2f}')
        emit_to_role(['admin', 'super_admin', 'finanzas'], 'prestamo:changed', {
            'id': p.id, 'action': 'updated',
        })
        return jsonify(_prestamo_row(p))
    except Exception:
        db.session.rollback()
        current_app.logger.error("Error editando préstamo: %s", traceback.format_exc())
        return jsonify({'error': 'Error al editar el préstamo'}), 500
