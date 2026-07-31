"""Kardex: histórico de movimientos de un producto con saldo corrido."""
import datetime
from decimal import Decimal

from flask import jsonify, request
from sqlalchemy.orm import joinedload

from app.models import MovimientoInventario, Producto

from .._core import (
    bp,
    _require_inventario_admin,
    _int_arg,
)


@bp.route('/productos/<int:producto_id>/kardex', methods=['GET'])
@_require_inventario_admin
def get_producto_kardex(producto_id: int):
    """Kardex (historial cronológico con saldo corrido) de un producto — Pausa 3.

    Query params:
      - desde (YYYY-MM-DD): default 30 días atrás.
      - hasta (YYYY-MM-DD): default hoy.
      - tipo: filtra ENTRADA/SALIDA/AJUSTE/TRASPASO (opcional).
      - limit: tope de filas (1..2000, default 500).

    Cálculo del saldo:
      saldo_inicial = stock_actual − Σ(deltas posteriores a `desde`)
    Luego se aplica el delta de cada movimiento en orden cronológico ascendente
    para obtener `saldo` por fila. TRASPASO no cambia el saldo total (mueve
    entre bodegas) pero se muestra para trazabilidad.
    """
    producto = Producto.query.filter(Producto.id == producto_id).first()
    if not producto:
        return jsonify({'detail': 'Producto no encontrado'}), 404

    # Rango por defecto: últimos 30 días.
    hoy = datetime.date.today()
    try:
        desde_str = request.args.get('desde')
        desde = datetime.date.fromisoformat(desde_str) if desde_str else (hoy - datetime.timedelta(days=30))
    except (TypeError, ValueError):
        return jsonify({'detail': "Parámetro 'desde' debe ser YYYY-MM-DD"}), 422
    try:
        hasta_str = request.args.get('hasta')
        hasta = datetime.date.fromisoformat(hasta_str) if hasta_str else hoy
    except (TypeError, ValueError):
        return jsonify({'detail': "Parámetro 'hasta' debe ser YYYY-MM-DD"}), 422
    if desde > hasta:
        return jsonify({'detail': "'desde' no puede ser mayor que 'hasta'"}), 422

    limit, err = _int_arg('limit', 500, 1, 2000)
    if err: return err

    tipo_filtro = request.args.get('tipo')
    if tipo_filtro and tipo_filtro not in ('ENTRADA', 'SALIDA', 'AJUSTE', 'TRASPASO'):
        return jsonify({'detail': "Parámetro 'tipo' inválido"}), 422

    # Helper: convierte (tipo, cantidad) en delta firmado para el saldo total.
    def _delta(mov: MovimientoInventario) -> Decimal:
        cant = mov.cantidad or Decimal('0')
        if mov.tipo == 'ENTRADA':
            return cant
        if mov.tipo == 'SALIDA':
            return -cant
        if mov.tipo == 'AJUSTE':
            return cant  # ya viene firmada
        return Decimal('0')  # TRASPASO no altera total

    # Datetime para filtros (incluyendo todo el día 'hasta').
    desde_dt = datetime.datetime.combine(desde, datetime.time.min)
    hasta_dt = datetime.datetime.combine(hasta, datetime.time.max)

    # 1) Calcular saldo inicial: stock_actual − Σ deltas posteriores a 'desde'.
    movs_post = (
        MovimientoInventario.query
        .filter(
            MovimientoInventario.producto_id == producto_id,
            MovimientoInventario.fecha >= desde_dt,
        )
        .all()
    )
    delta_post = sum((_delta(m) for m in movs_post), Decimal('0'))
    saldo_inicial = (producto.stock_actual or Decimal('0')) - delta_post

    # 2) Cargar movimientos del rango (con join a usuarios y almacenes para
    # evitar lazy queries en la serialización).
    q = (
        MovimientoInventario.query
        .options(
            joinedload(MovimientoInventario.usuario),
            joinedload(MovimientoInventario.almacen_origen),
            joinedload(MovimientoInventario.almacen_destino),
        )
        .filter(
            MovimientoInventario.producto_id == producto_id,
            MovimientoInventario.fecha >= desde_dt,
            MovimientoInventario.fecha <= hasta_dt,
        )
    )
    if tipo_filtro:
        q = q.filter(MovimientoInventario.tipo == tipo_filtro)

    # Orden ASC para calcular saldo corrido; al frontend le viene útil ASC
    # para timeline cronológico de arriba a abajo, pero también lo invertimos
    # opcionalmente en la UI.
    movs = q.order_by(MovimientoInventario.fecha.asc(), MovimientoInventario.id.asc()).limit(limit).all()

    saldo = saldo_inicial
    filas = []
    for m in movs:
        d = _delta(m)
        saldo = saldo + d
        filas.append({
            'id': m.id,
            'fecha': m.fecha.isoformat() if m.fecha else None,
            'tipo': m.tipo,
            'cantidad': float(m.cantidad or 0),
            'delta': float(d),
            'saldo': float(saldo),
            'almacen_origen': m.almacen_origen.nombre if m.almacen_origen else None,
            'almacen_destino': m.almacen_destino.nombre if m.almacen_destino else None,
            'usuario': m.usuario.username if m.usuario else None,
            'motivo': m.motivo or '',
        })

    return jsonify({
        'producto': {
            'id': producto.id,
            'codigo': producto.codigo,
            'descripcion': producto.descripcion,
            'unidad': producto.unidad,
            'categoria': producto.categoria,
            'stock_actual': float(producto.stock_actual or 0),
            'stock_minimo': float(producto.stock_minimo or 0),
        },
        'desde': desde.isoformat(),
        'hasta': hasta.isoformat(),
        'saldo_inicial': float(saldo_inicial),
        'saldo_final': float(saldo),
        'total_movimientos': len(filas),
        'movimientos': filas,
    })
