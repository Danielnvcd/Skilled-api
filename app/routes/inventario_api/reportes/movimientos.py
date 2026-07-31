"""Reportes de movimientos: bitácora del periodo y kardex de un producto."""
import datetime
from decimal import Decimal

from flask import jsonify, request
from sqlalchemy.orm import joinedload

from app.extensions import db, limiter, get_real_client_ip_flask
from app.models import MovimientoInventario, Producto

from .._core import bp, _require_inventario_admin, _audit, _int_arg
from ._excel import REPORTES_MAX_FILAS, _parse_fecha_arg, _stream_excel


@bp.route('/reportes/movimientos.xlsx', methods=['GET'])
@limiter.limit(
    "10/minute",
    key_func=lambda: f"ip:{get_real_client_ip_flask()}",
)
@_require_inventario_admin
def reporte_movimientos():
    """Reporte de movimientos de inventario con filtros.

    Query params:
      - desde, hasta (YYYY-MM-DD): default últimos 30 días.
      - tipo: ENTRADA / SALIDA / AJUSTE / TRASPASO (opcional).
      - producto_id (opcional).
      - usuario_id (opcional).
    """
    hoy = datetime.date.today()
    desde, err = _parse_fecha_arg('desde', hoy - datetime.timedelta(days=30))
    if err: return err
    hasta, err = _parse_fecha_arg('hasta', hoy)
    if err: return err
    if desde > hasta:
        return jsonify({'detail': "'desde' no puede ser mayor que 'hasta'"}), 422

    tipo = request.args.get('tipo')
    if tipo and tipo not in ('ENTRADA', 'SALIDA', 'AJUSTE', 'TRASPASO'):
        return jsonify({'detail': "Parámetro 'tipo' inválido"}), 422

    prod_id, err = _int_arg('producto_id', 0, 0, 1_000_000_000)
    if err: return err
    usr_id, err = _int_arg('usuario_id', 0, 0, 1_000_000_000)
    if err: return err

    desde_dt = datetime.datetime.combine(desde, datetime.time.min)
    hasta_dt = datetime.datetime.combine(hasta, datetime.time.max)

    q = (
        MovimientoInventario.query
        .options(
            joinedload(MovimientoInventario.producto),
            joinedload(MovimientoInventario.almacen_origen),
            joinedload(MovimientoInventario.almacen_destino),
            joinedload(MovimientoInventario.usuario),
        )
        .filter(MovimientoInventario.fecha >= desde_dt, MovimientoInventario.fecha <= hasta_dt)
    )
    if tipo:
        q = q.filter(MovimientoInventario.tipo == tipo)
    if prod_id:
        q = q.filter(MovimientoInventario.producto_id == prod_id)
    if usr_id:
        q = q.filter(MovimientoInventario.usuario_id == usr_id)

    movs = q.order_by(MovimientoInventario.fecha.desc()).limit(REPORTES_MAX_FILAS).all()

    rows = []
    for m in movs:
        rows.append({
            'Fecha': m.fecha.strftime('%Y-%m-%d %H:%M') if m.fecha else '',
            'Tipo': m.tipo,
            'Código': m.producto.codigo if m.producto else '',
            'Descripción': m.producto.descripcion if m.producto else '',
            'Cantidad': float(m.cantidad or 0),
            'Unidad': m.producto.unidad if m.producto else '',
            'Almacén origen': m.almacen_origen.nombre if m.almacen_origen else '',
            'Almacén destino': m.almacen_destino.nombre if m.almacen_destino else '',
            'Usuario': m.usuario.username if m.usuario else '',
            'Motivo': m.motivo or '',
        })

    _audit(
        request.current_user,
        f"Reporte movimientos {desde} a {hasta} ({len(rows)} filas)",
    )
    db.session.commit()

    return _stream_excel(
        {'Movimientos': rows},
        f'movimientos_{desde}_{hasta}.xlsx',
    )


@bp.route('/reportes/kardex.xlsx', methods=['GET'])
@limiter.limit(
    "10/minute",
    key_func=lambda: f"ip:{get_real_client_ip_flask()}",
)
@_require_inventario_admin
def reporte_kardex_xlsx():
    """Kardex de un producto exportado a Excel.

    Query params:
      - producto_id (requerido).
      - desde, hasta (YYYY-MM-DD): default últimos 30 días.
    """
    prod_id, err = _int_arg('producto_id', 0, 0, 1_000_000_000)
    if err: return err
    if not prod_id:
        return jsonify({'detail': "Parámetro 'producto_id' es requerido"}), 422

    producto = Producto.query.filter(Producto.id == prod_id).first()
    if not producto:
        return jsonify({'detail': 'Producto no encontrado'}), 404

    hoy = datetime.date.today()
    desde, err = _parse_fecha_arg('desde', hoy - datetime.timedelta(days=30))
    if err: return err
    hasta, err = _parse_fecha_arg('hasta', hoy)
    if err: return err
    if desde > hasta:
        return jsonify({'detail': "'desde' no puede ser mayor que 'hasta'"}), 422

    # Reutiliza la misma fórmula de saldo corrido del endpoint JSON.
    def _delta(m: MovimientoInventario) -> Decimal:
        cant = m.cantidad or Decimal('0')
        if m.tipo == 'ENTRADA':
            return cant
        if m.tipo == 'SALIDA':
            return -cant
        if m.tipo == 'AJUSTE':
            return cant
        return Decimal('0')  # TRASPASO no altera total

    desde_dt = datetime.datetime.combine(desde, datetime.time.min)
    hasta_dt = datetime.datetime.combine(hasta, datetime.time.max)

    # Saldo inicial = stock_actual − Σ deltas posteriores a 'desde'.
    movs_post = (
        MovimientoInventario.query
        .filter(
            MovimientoInventario.producto_id == prod_id,
            MovimientoInventario.fecha >= desde_dt,
        )
        .all()
    )
    delta_post = sum((_delta(m) for m in movs_post), Decimal('0'))
    saldo_inicial = (producto.stock_actual or Decimal('0')) - delta_post

    movs = (
        MovimientoInventario.query
        .options(
            joinedload(MovimientoInventario.usuario),
            joinedload(MovimientoInventario.almacen_origen),
            joinedload(MovimientoInventario.almacen_destino),
        )
        .filter(
            MovimientoInventario.producto_id == prod_id,
            MovimientoInventario.fecha >= desde_dt,
            MovimientoInventario.fecha <= hasta_dt,
        )
        .order_by(MovimientoInventario.fecha.asc(), MovimientoInventario.id.asc())
        .limit(REPORTES_MAX_FILAS)
        .all()
    )

    rows = [{
        'Fecha': '',
        'Tipo': '— Saldo inicial —',
        'Cantidad': '',
        'Delta': '',
        'Saldo': float(saldo_inicial),
        'Almacén origen': '',
        'Almacén destino': '',
        'Usuario': '',
        'Motivo': '',
    }]
    saldo = saldo_inicial
    for m in movs:
        d = _delta(m)
        saldo = saldo + d
        rows.append({
            'Fecha': m.fecha.strftime('%Y-%m-%d %H:%M') if m.fecha else '',
            'Tipo': m.tipo,
            'Cantidad': float(m.cantidad or 0),
            'Delta': float(d),
            'Saldo': float(saldo),
            'Almacén origen': m.almacen_origen.nombre if m.almacen_origen else '',
            'Almacén destino': m.almacen_destino.nombre if m.almacen_destino else '',
            'Usuario': m.usuario.username if m.usuario else '',
            'Motivo': m.motivo or '',
        })

    info = [
        {'Campo': 'Producto', 'Valor': f"{producto.codigo} — {producto.descripcion}"},
        {'Campo': 'Unidad', 'Valor': producto.unidad},
        {'Campo': 'Stock actual', 'Valor': float(producto.stock_actual or 0)},
        {'Campo': 'Periodo', 'Valor': f"{desde} a {hasta}"},
        {'Campo': 'Movimientos', 'Valor': len(movs)},
    ]

    _audit(request.current_user, f"Reporte kardex prod #{prod_id} {desde} a {hasta}")
    db.session.commit()

    return _stream_excel(
        {'Resumen': info, 'Kardex': rows},
        f'kardex_{producto.codigo}_{desde}_{hasta}.xlsx',
    )
