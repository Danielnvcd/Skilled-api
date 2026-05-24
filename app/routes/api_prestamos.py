"""API JSON para Préstamos (SPA React).

Espejo de `prestamos.py` con JWT. Reusa `_recalcular_prenominas_abiertas`
del módulo clásico para mantener consistencia en el recálculo de prenóminas
abiertas cada vez que cambian los datos de un préstamo.
"""
import traceback
from datetime import datetime
from decimal import Decimal

from flask import Blueprint, current_app, g, jsonify, request
from sqlalchemy import or_
from sqlalchemy.orm import selectinload

from app.extensions import db
from app.models import AbonoPrestamo, Prestamo, Trabajador
from app.routes.api_auth import jwt_required
from app.routes.prestamos import _recalcular_prenominas_abiertas
from app.routes.reportes import _aplicar_estilos_y_retornar, _sanitize_rows
from app.utils import log_action, to_dec

bp = Blueprint('api_prestamos', __name__, url_prefix='/api/prestamos')


# ── Helpers ────────────────────────────────────────────────────────────────────

def _u():
    return g._jwt_user


def _admin_required():
    if _u().role not in ('admin', 'super_admin'):
        return jsonify({'error': 'Acceso denegado'}), 403
    return None


def _num(v) -> float:
    return float(to_dec(v)) if v is not None else 0.0


def _prestamo_row(p: Prestamo) -> dict:
    t = p.trabajador
    return {
        'id': p.id,
        'trabajador_id': p.trabajador_id,
        'trabajador': {
            'id': t.id,
            'no_empleado': t.no_empleado,
            'nombre': t.nombre,
            'nombre_apellidos': t.nombre_apellidos,
            'nombre_completo': t.nombre_completo,
        } if t else None,
        'monto_total': _num(p.monto_total),
        'monto_restante': _num(p.monto_restante),
        'plazo_semanas': p.plazo_semanas,
        'descuento_semanal': _num(p.descuento_semanal),
        'motivo': p.motivo or '',
        'frecuencia': p.frecuencia or 'semanal',
        'fecha_inicio': p.fecha_inicio.isoformat() if p.fecha_inicio else None,
        'estado': p.estado,
        'activo': bool(p.activo),
        'creado_en': p.creado_en.isoformat() if p.creado_en else None,
    }


# ── Endpoints ─────────────────────────────────────────────────────────────────

@bp.route('', methods=['GET'])
@jwt_required
def listar():
    denied = _admin_required()
    if denied:
        return denied

    page = max(1, request.args.get('page', 1, type=int))
    per_page = min(100, max(1, request.args.get('per_page', 20, type=int)))
    q = (request.args.get('q') or '').strip()
    estado = (request.args.get('estado') or '').strip().upper()

    query = Prestamo.query.options(selectinload(Prestamo.trabajador))
    if q:
        query = query.join(Trabajador, Prestamo.trabajador_id == Trabajador.id).filter(or_(
            Trabajador.nombre.ilike(f'%{q}%'),
            Trabajador.nombre_apellidos.ilike(f'%{q}%'),
            Trabajador.no_empleado.ilike(f'%{q}%'),
            db.cast(Prestamo.id, db.String).ilike(f'%{q}%'),
        ))
    if estado in ('ACTIVO', 'LIQUIDADO'):
        query = query.filter(Prestamo.estado == estado)

    pagination = query.order_by(Prestamo.creado_en.desc()).paginate(
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
    denied = _admin_required()
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
    denied = _admin_required()
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
    denied = _admin_required()
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
    except (TypeError, ValueError):
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
        return jsonify({'id': prestamo.id}), 201
    except Exception:
        db.session.rollback()
        current_app.logger.error("Error creando préstamo: %s", traceback.format_exc())
        return jsonify({'error': 'Error al crear el préstamo'}), 500


@bp.route('/<int:id>', methods=['PUT'])
@jwt_required
def editar(id):
    denied = _admin_required()
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
        return jsonify(_prestamo_row(p))
    except Exception:
        db.session.rollback()
        current_app.logger.error("Error editando préstamo: %s", traceback.format_exc())
        return jsonify({'error': 'Error al editar el préstamo'}), 500


@bp.route('/<int:id>/abonar', methods=['POST'])
@jwt_required
def abonar(id):
    denied = _admin_required()
    if denied:
        return denied

    data = request.get_json(silent=True) or {}
    if 'monto' not in data:
        return jsonify({'error': 'monto requerido'}), 400

    try:
        monto = Decimal(str(data['monto']))
    except (TypeError, ValueError):
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
            registrado_por_id=_u().id,
            notas=data.get('notas') or 'Abono extraordinario manual',
        ))
        db.session.commit()
        _recalcular_prenominas_abiertas(p.trabajador_id)
        log_action(f'API: abono ${monto} al préstamo #{id}. Restante: ${p.monto_restante}')
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
    denied = _admin_required()
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
                registrado_por_id=_u().id,
                notas='Liquidación total manual',
            ))

        p.monto_restante = 0
        p.estado = 'LIQUIDADO'
        p.activo = False
        db.session.commit()
        _recalcular_prenominas_abiertas(p.trabajador_id)
        log_action(f'API: préstamo #{id} liquidado manualmente (saldo ${saldo})')
        return jsonify({'estado': p.estado, 'monto_restante': 0.0})
    except Exception:
        db.session.rollback()
        current_app.logger.error("Error al liquidar: %s", traceback.format_exc())
        return jsonify({'error': 'Error al liquidar'}), 500


# ── Excel ─────────────────────────────────────────────────────────────────────

@bp.route('/trabajadores/<int:trabajador_id>/excel', methods=['GET'])
@jwt_required
def excel_prestamos_trabajador(trabajador_id):
    """Exporta a Excel todos los préstamos de un trabajador (activos y liquidados)
    con el mismo formato del blueprint clásico (header azul, zebra, fila TOTAL)."""
    denied = _admin_required()
    if denied:
        return denied

    from io import BytesIO
    import pandas as pd

    trabajador = Trabajador.query.get_or_404(trabajador_id)
    prestamos = (
        Prestamo.query
        .filter_by(trabajador_id=trabajador.id)
        .order_by(Prestamo.creado_en.desc())
        .all()
    )
    if not prestamos:
        return jsonify({'error': 'Este trabajador no tiene préstamos registrados.'}), 404

    output = BytesIO()
    writer = pd.ExcelWriter(output, engine='openpyxl')

    data = []
    for pr in prestamos:
        total_abonado = sum(float(a.monto or 0) for a in pr.abonos)
        saldo = float(pr.monto_total or 0) - total_abonado
        data.append({
            'ID Préstamo': pr.id,
            'Fecha Registro': pr.creado_en.strftime('%Y-%m-%d') if pr.creado_en else '',
            'Monto Original': float(pr.monto_total or 0),
            'Total Abonado': total_abonado,
            'Saldo Restante': saldo,
            'Descuento Semanal': float(pr.descuento_semanal or 0),
            'Estado': pr.estado,
            'Motivo': pr.motivo or '',
        })

    if data:
        data.append({
            'ID Préstamo': 'TOTAL',
            'Fecha Registro': '',
            'Monto Original': sum(d['Monto Original'] for d in data),
            'Total Abonado': sum(d['Total Abonado'] for d in data),
            'Saldo Restante': sum(d['Saldo Restante'] for d in data),
            'Descuento Semanal': sum(d['Descuento Semanal'] for d in data),
            'Estado': '',
            'Motivo': '',
        })

    df = pd.DataFrame(_sanitize_rows(data))
    df.to_excel(writer, sheet_name='Préstamos', index=False)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    nombre_file = f"Prestamos_{trabajador.no_empleado}_{timestamp}.xlsx"
    return _aplicar_estilos_y_retornar(writer, output, nombre_file)
