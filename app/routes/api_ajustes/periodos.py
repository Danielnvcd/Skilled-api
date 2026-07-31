"""Periodos de ajuste Inbursa: listar, crear, detalle, cerrar, Excel.

Registra:
  GET  /periodos                           listar_periodos
  POST /periodos                           crear_periodo
  GET  /periodos/<int:periodo_id>          detalle_periodo
  POST /periodos/<int:periodo_id>/cerrar   cerrar_periodo
  GET  /periodos/<int:periodo_id>/excel    excel_periodo
"""
import traceback
from datetime import datetime
from decimal import Decimal
from io import BytesIO

from flask import current_app, jsonify, request
from sqlalchemy.orm import selectinload

from app.extensions import db
from app.models import AjusteDescuento, AjustePeriodo, AjusteTrabajadorPeriodo
from app.realtime import emit_to_role
from app.routes._api_helpers import _aplicar_estilos_y_retornar, _sanitize_rows, require_admin
from app.routes.api_auth import jwt_required
from app.utils import log_action, to_dec

from ._core import bp, _num, _periodo_row


@bp.route('/periodos', methods=['GET'])
@jwt_required
def listar_periodos():
    denied = require_admin()
    if denied:
        return denied

    page = max(1, request.args.get('page', 1, type=int))
    per_page = min(100, max(1, request.args.get('per_page', 20, type=int)))
    q = (request.args.get('q') or '').strip()

    query = AjustePeriodo.query.options(
        selectinload(AjustePeriodo.trabajadores_periodo),
        selectinload(AjustePeriodo.descuentos),
    )
    if q:
        query = query.filter(AjustePeriodo.nombre.ilike(f'%{q}%'))

    pagination = query.order_by(AjustePeriodo.created_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False,
    )
    return jsonify({
        'items': [_periodo_row(p) for p in pagination.items],
        'total': pagination.total,
        'page': pagination.page,
        'pages': pagination.pages,
        'per_page': pagination.per_page,
    })


@bp.route('/periodos', methods=['POST'])
@jwt_required
def crear_periodo():
    denied = require_admin()
    if denied:
        return denied

    data = request.get_json(silent=True) or {}
    nombre = (data.get('nombre') or '').strip()
    fecha_inicio_str = data.get('fecha_inicio')
    fecha_fin_str = data.get('fecha_fin')
    trabajadores = data.get('trabajadores') or []  # [{trabajador_id, monto_meta}]

    if not nombre or not fecha_inicio_str or not fecha_fin_str:
        return jsonify({'error': 'Nombre y fechas son obligatorios'}), 400

    try:
        fecha_inicio = datetime.strptime(fecha_inicio_str, '%Y-%m-%d').date()
        fecha_fin = datetime.strptime(fecha_fin_str, '%Y-%m-%d').date()
    except ValueError:
        return jsonify({'error': 'Formato de fecha inválido'}), 400

    if fecha_inicio >= fecha_fin:
        return jsonify({'error': 'La fecha de inicio debe ser anterior a la fecha fin'}), 400

    overlap = AjustePeriodo.query.filter(
        AjustePeriodo.fecha_inicio <= fecha_fin,
        AjustePeriodo.fecha_fin >= fecha_inicio,
    ).first()
    if overlap:
        return jsonify({
            'error': f"Ya existe un periodo en esas fechas: '{overlap.nombre}' "
                     f"({overlap.fecha_inicio.strftime('%d/%m/%Y')} - {overlap.fecha_fin.strftime('%d/%m/%Y')})."
        }), 409

    if not trabajadores:
        return jsonify({'error': 'Debes incluir al menos un trabajador'}), 400

    try:
        periodo = AjustePeriodo(nombre=nombre, fecha_inicio=fecha_inicio, fecha_fin=fecha_fin)
        db.session.add(periodo)
        db.session.flush()

        creados = 0
        for entry in trabajadores:
            try:
                t_id = int(entry.get('trabajador_id'))
                monto = Decimal(str(entry.get('monto_meta', 0)))
            except (TypeError, ValueError):
                continue
            if monto <= 0:
                continue
            db.session.add(AjusteTrabajadorPeriodo(
                periodo_id=periodo.id,
                trabajador_id=t_id,
                monto_meta=monto,
            ))
            creados += 1

        if creados == 0:
            db.session.rollback()
            return jsonify({'error': 'Ningún trabajador tiene meta válida (> 0)'}), 400

        db.session.commit()
        log_action(f'API: periodo de ajuste creado: {nombre}')
        emit_to_role(['admin', 'super_admin', 'finanzas'], 'ajuste:changed', {
            'id': periodo.id, 'action': 'created',
        })
        return jsonify({'id': periodo.id, 'creados': creados}), 201
    except Exception:
        db.session.rollback()
        current_app.logger.error("Error creando periodo: %s", traceback.format_exc())
        return jsonify({'error': 'Error al crear el periodo'}), 500


@bp.route('/periodos/<int:periodo_id>', methods=['GET'])
@jwt_required
def detalle_periodo(periodo_id):
    denied = require_admin()
    if denied:
        return denied

    periodo = db.get_or_404(AjustePeriodo, periodo_id, options=[
        selectinload(AjustePeriodo.trabajadores_periodo).selectinload(AjusteTrabajadorPeriodo.trabajador),
        selectinload(AjustePeriodo.descuentos).selectinload(AjusteDescuento.trabajador),
    ])

    descuentos_por_trab = {}
    for d in periodo.descuentos:
        descuentos_por_trab.setdefault(d.trabajador_id, []).append(d)

    trabajadores = []
    for tp in periodo.trabajadores_periodo:
        t = tp.trabajador
        if not t:
            continue
        descs = descuentos_por_trab.get(t.id, [])
        total_desc = sum((d.monto or Decimal('0') for d in descs), Decimal('0'))
        meta = to_dec(tp.monto_meta)
        restante = meta - total_desc
        porcentaje = int(min(100, (total_desc / meta * 100))) if meta > 0 else 0
        trabajadores.append({
            'trabajador_id': t.id,
            'no_empleado': t.no_empleado,
            'nombre_completo': t.nombre_completo,
            'monto_meta': float(meta),
            'total_descontado': float(total_desc),
            'restante': float(restante),
            'porcentaje': porcentaje,
            'descuentos': [
                {
                    'id': d.id,
                    'fecha_descuento': d.fecha_descuento.isoformat(),
                    'monto': _num(d.monto),
                    'notas': d.notas or '',
                    'cobrado': bool(d.cobrado),
                }
                for d in sorted(descs, key=lambda x: x.fecha_descuento)
            ],
        })

    total_meta = sum(t['monto_meta'] for t in trabajadores)
    total_desc = sum(t['total_descontado'] for t in trabajadores)

    return jsonify({
        'id': periodo.id,
        'nombre': periodo.nombre,
        'fecha_inicio': periodo.fecha_inicio.isoformat(),
        'fecha_fin': periodo.fecha_fin.isoformat(),
        'estado': periodo.estado,
        'editable': periodo.estado == 'ABIERTO',
        'trabajadores': trabajadores,
        'total_meta': total_meta,
        'total_descontado': total_desc,
    })


@bp.route('/periodos/<int:periodo_id>/cerrar', methods=['POST'])
@jwt_required
def cerrar_periodo(periodo_id):
    denied = require_admin()
    if denied:
        return denied

    periodo = db.get_or_404(AjustePeriodo, periodo_id)
    if periodo.estado != 'ABIERTO':
        return jsonify({'error': 'Este periodo ya está cerrado'}), 400

    try:
        periodo.estado = 'CERRADO'
        db.session.commit()
        log_action(f'API: periodo de ajuste cerrado: {periodo.nombre}')
        emit_to_role(['admin', 'super_admin', 'finanzas'], 'ajuste:changed', {
            'id': periodo.id, 'action': 'cerrado',
        })
        return jsonify({'estado': periodo.estado})
    except Exception:
        db.session.rollback()
        current_app.logger.error("Error cerrando periodo: %s", traceback.format_exc())
        return jsonify({'error': 'Error al cerrar el periodo'}), 500


# ── Excel ─────────────────────────────────────────────────────────────────────

@bp.route('/periodos/<int:periodo_id>/excel', methods=['GET'])
@jwt_required
def excel_periodo(periodo_id):
    """Exporta a Excel un periodo de Ajuste Inbursa con dos hojas:
    Resumen Trabajadores y Detalle Descuentos. Mismo formato que el blueprint
    clásico (header azul, zebra, fila TOTAL, formato moneda)."""
    denied = require_admin()
    if denied:
        return denied

    import pandas as pd

    periodo = db.get_or_404(AjustePeriodo, periodo_id)

    trabajadores_periodo = AjusteTrabajadorPeriodo.query.filter_by(periodo_id=periodo.id).all()
    ajustes = (
        AjusteDescuento.query
        .filter_by(periodo_id=periodo.id)
        .order_by(AjusteDescuento.fecha_descuento)
        .all()
    )

    if not trabajadores_periodo and not ajustes:
        return jsonify({'error': 'Este periodo aún no tiene información.'}), 404

    output = BytesIO()
    writer = pd.ExcelWriter(output, engine='openpyxl')

    # Hoja 1 — Resumen por trabajador (meta vs descontado)
    descuentos_por_trabajador = {}
    for d in ajustes:
        descuentos_por_trabajador.setdefault(d.trabajador_id, []).append(d)

    data_resumen = []
    for tp in trabajadores_periodo:
        total_desc = sum(float(d.monto or 0) for d in descuentos_por_trabajador.get(tp.trabajador_id, []))
        data_resumen.append({
            'No. Empleado': tp.trabajador.no_empleado if tp.trabajador else '',
            'Nombre del Empleado': tp.trabajador.nombre_completo if tp.trabajador else '',
            'Meta (Depósito)': float(tp.monto_meta or 0),
            'Total Descontado': total_desc,
            'Saldo Restante': float(tp.monto_meta or 0) - total_desc,
        })

    if data_resumen:
        data_resumen.append({
            'No. Empleado': 'TOTAL',
            'Nombre del Empleado': '',
            'Meta (Depósito)': sum(d['Meta (Depósito)'] for d in data_resumen),
            'Total Descontado': sum(d['Total Descontado'] for d in data_resumen),
            'Saldo Restante': sum(d['Saldo Restante'] for d in data_resumen),
        })
        df_resumen = pd.DataFrame(_sanitize_rows(data_resumen))
        df_resumen.to_excel(writer, sheet_name='Resumen Trabajadores', index=False)

    # Hoja 2 — Detalle de descuentos (fecha a fecha)
    data_detalle = []
    for aj in ajustes:
        data_detalle.append({
            'No. Empleado': aj.trabajador.no_empleado if aj.trabajador else '',
            'Nombre del Empleado': aj.trabajador.nombre_completo if aj.trabajador else '',
            'Fecha Aplicación': aj.fecha_descuento.strftime('%Y-%m-%d') if aj.fecha_descuento else '',
            'Monto Descontado': float(aj.monto or 0),
            'Notas': aj.notas or '',
        })

    if data_detalle:
        data_detalle.append({
            'No. Empleado': 'TOTAL',
            'Nombre del Empleado': '',
            'Fecha Aplicación': '',
            'Monto Descontado': sum(d['Monto Descontado'] for d in data_detalle),
            'Notas': '',
        })
        df_detalle = pd.DataFrame(_sanitize_rows(data_detalle))
        df_detalle.to_excel(writer, sheet_name='Detalle Descuentos', index=False)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    nombre_file = f"Ajuste_Inbursa_{periodo.id}_{timestamp}.xlsx"
    return _aplicar_estilos_y_retornar(writer, output, nombre_file)
