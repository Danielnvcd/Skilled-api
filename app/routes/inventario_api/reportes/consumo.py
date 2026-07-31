"""Reportes de consumo: material entregado por proyecto y solicitudes."""
import datetime
from decimal import Decimal

from flask import jsonify, request
from sqlalchemy.orm import joinedload, selectinload

from app.extensions import db, limiter, get_real_client_ip_flask
from app.models import SolicitudMaterial, SolicitudMaterialDetalle

from .._core import bp, _require_inventario_admin, _audit
from ._excel import REPORTES_MAX_FILAS, _parse_fecha_arg, _stream_excel


@bp.route('/reportes/consumo-proyecto.xlsx', methods=['GET'])
@limiter.limit(
    "10/minute",
    key_func=lambda: f"ip:{get_real_client_ip_flask()}",
)
@_require_inventario_admin
def reporte_consumo_proyecto():
    """Consumo por proyecto agrupado, basado en solicitudes entregadas o con
    entrega parcial dentro del rango.

    Query params:
      - desde, hasta (YYYY-MM-DD): default últimos 90 días.
      - estatus: por defecto ['ENTREGADA','APROBADA'] (incluye parciales).
    """
    hoy = datetime.date.today()
    desde, err = _parse_fecha_arg('desde', hoy - datetime.timedelta(days=90))
    if err: return err
    hasta, err = _parse_fecha_arg('hasta', hoy)
    if err: return err
    if desde > hasta:
        return jsonify({'detail': "'desde' no puede ser mayor que 'hasta'"}), 422

    desde_dt = datetime.datetime.combine(desde, datetime.time.min)
    hasta_dt = datetime.datetime.combine(hasta, datetime.time.max)

    estatus_arg = (request.args.get('estatus') or '').strip()
    if estatus_arg:
        estatus_list = [e.strip().upper() for e in estatus_arg.split(',') if e.strip()]
        if any(e not in ('PENDIENTE', 'APROBADA', 'RECHAZADA', 'ENTREGADA') for e in estatus_list):
            return jsonify({'detail': "Parámetro 'estatus' inválido"}), 422
    else:
        estatus_list = ['ENTREGADA', 'APROBADA']

    sols = (
        SolicitudMaterial.query
        .options(
            selectinload(SolicitudMaterial.detalles).joinedload(SolicitudMaterialDetalle.producto),
        )
        .filter(
            SolicitudMaterial.fecha_creacion >= desde_dt,
            SolicitudMaterial.fecha_creacion <= hasta_dt,
            SolicitudMaterial.estatus.in_(estatus_list),
        )
        .all()
    )

    # Agregamos por (proyecto, código). Usamos cantidad_entregada (real); para
    # ENTREGADAs pre-8b sin cantidad_entregada, caemos a cantidad_solicitada.
    agg: dict[tuple, dict] = {}
    for s in sols:
        proyecto = (s.proyecto or 'Sin proyecto').strip() or 'Sin proyecto'
        for d in (s.detalles or []):
            if (d.tipo_item or 'MATERIAL').upper() != 'MATERIAL' or not d.producto_id:
                continue
            cant_ent = Decimal(str(d.cantidad_entregada or 0))
            if cant_ent <= 0 and s.estatus == 'ENTREGADA':
                # Legacy: solicitudes pre-8b marcadas como ENTREGADA sin descontar.
                cant_ent = Decimal(str(d.cantidad_solicitada or 0))
            if cant_ent <= 0:
                continue
            codigo = d.producto.codigo if d.producto else f'(#{d.producto_id})'
            descripcion = d.producto.descripcion if d.producto else 'Producto eliminado'
            unidad = d.producto.unidad if d.producto else ''
            key = (proyecto, codigo)
            if key not in agg:
                agg[key] = {
                    'Proyecto': proyecto,
                    'Código': codigo,
                    'Descripción': descripcion,
                    'Unidad': unidad,
                    'Cantidad entregada': 0.0,
                    'Solicitudes': set(),
                }
            agg[key]['Cantidad entregada'] += float(cant_ent)
            agg[key]['Solicitudes'].add(s.id)

    rows = []
    for v in sorted(agg.values(), key=lambda r: (r['Proyecto'], r['Código'])):
        rows.append({
            'Proyecto': v['Proyecto'],
            'Código': v['Código'],
            'Descripción': v['Descripción'],
            'Unidad': v['Unidad'],
            'Cantidad entregada': round(v['Cantidad entregada'], 4),
            '# Solicitudes': len(v['Solicitudes']),
        })

    _audit(
        request.current_user,
        f"Reporte consumo por proyecto {desde} a {hasta} ({len(rows)} filas)",
    )
    db.session.commit()

    return _stream_excel(
        {'Consumo por proyecto': rows},
        f'consumo_proyecto_{desde}_{hasta}.xlsx',
    )


@bp.route('/reportes/solicitudes.xlsx', methods=['GET'])
@limiter.limit(
    "10/minute",
    key_func=lambda: f"ip:{get_real_client_ip_flask()}",
)
@_require_inventario_admin
def reporte_solicitudes():
    """Reporte de solicitudes del periodo con totales por solicitud.

    Query params:
      - desde, hasta (YYYY-MM-DD): default últimos 30 días.
      - estatus: PENDIENTE / APROBADA / RECHAZADA / ENTREGADA (opcional).
    """
    hoy = datetime.date.today()
    desde, err = _parse_fecha_arg('desde', hoy - datetime.timedelta(days=30))
    if err: return err
    hasta, err = _parse_fecha_arg('hasta', hoy)
    if err: return err
    if desde > hasta:
        return jsonify({'detail': "'desde' no puede ser mayor que 'hasta'"}), 422

    estatus = (request.args.get('estatus') or '').strip().upper()
    if estatus and estatus not in ('PENDIENTE', 'APROBADA', 'RECHAZADA', 'ENTREGADA'):
        return jsonify({'detail': "Parámetro 'estatus' inválido"}), 422

    desde_dt = datetime.datetime.combine(desde, datetime.time.min)
    hasta_dt = datetime.datetime.combine(hasta, datetime.time.max)

    q = (
        SolicitudMaterial.query
        .options(
            joinedload(SolicitudMaterial.solicitante),
            selectinload(SolicitudMaterial.detalles),
        )
        .filter(
            SolicitudMaterial.fecha_creacion >= desde_dt,
            SolicitudMaterial.fecha_creacion <= hasta_dt,
        )
    )
    if estatus:
        q = q.filter(SolicitudMaterial.estatus == estatus)
    sols = q.order_by(SolicitudMaterial.fecha_creacion.desc()).limit(REPORTES_MAX_FILAS).all()

    rows = []
    for s in sols:
        total_sol = sum(float(d.cantidad_solicitada or 0) for d in (s.detalles or []))
        total_aprob = sum(float(d.cantidad_aprobada or 0) for d in (s.detalles or []))
        total_ent = sum(float(d.cantidad_entregada or 0) for d in (s.detalles or []))
        rows.append({
            'ID': s.id,
            'Fecha': s.fecha_creacion.strftime('%Y-%m-%d %H:%M') if s.fecha_creacion else '',
            'Cierre': s.fecha_cierre.strftime('%Y-%m-%d %H:%M') if s.fecha_cierre else '',
            'Estatus': s.estatus,
            'Solicitante': s.solicitante.username if s.solicitante else '',
            'Proyecto': s.proyecto or '',
            'Líneas': len(s.detalles or []),
            'Total solicitado': round(total_sol, 4),
            'Total aprobado': round(total_aprob, 4),
            'Total entregado': round(total_ent, 4),
        })

    _audit(
        request.current_user,
        f"Reporte solicitudes {desde} a {hasta} ({len(rows)} filas)",
    )
    db.session.commit()

    return _stream_excel(
        {'Solicitudes': rows},
        f'solicitudes_{desde}_{hasta}.xlsx',
    )
