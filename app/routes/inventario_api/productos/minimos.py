"""Stock mínimo en masa: sugerirlo por consumo y aplicarlo a varios productos.

El mínimo es lo que dispara "Bajo mínimo" y las alertas de compra, pero se
capturaba producto por producto. Con un catálogo de miles de SKUs eso significa
que casi nadie lo llena, y las alertas quedan mudas justo donde más se necesitan.

La sugerencia sale del consumo real: cuánto salió del almacén por día en los
últimos N días, multiplicado por los días de cobertura que se quieran tener.
"""
import datetime
from decimal import Decimal

from flask import jsonify, request

from app.extensions import db
from app.models import MovimientoInventario, Producto
from app.realtime import emit_to_role

from .._core import (
    bp,
    _require_inventario_admin,
    _audit,
    _unidad_permite_decimales,
    _INV_ROLES,
)

# Topes de los parámetros, para que nadie pida una ventana absurda.
DIAS_CONSUMO_MIN, DIAS_CONSUMO_MAX = 7, 365
DIAS_COBERTURA_MIN, DIAS_COBERTURA_MAX = 1, 180
MAX_PRODUCTOS_LOTE = 1000


def _consumo_diario(producto_ids, dias: int) -> dict[int, float]:
    """Unidades que salen por día de cada producto en los últimos `dias`.

    Una sola consulta agrupada (no una por producto): con cientos de SKUs
    seleccionados, preguntar de a uno sería una espera larga en el navegador.
    """
    if not producto_ids:
        return {}
    desde = datetime.datetime.now() - datetime.timedelta(days=dias)
    filas = (
        db.session.query(
            MovimientoInventario.producto_id,
            db.func.coalesce(db.func.sum(MovimientoInventario.cantidad), 0),
        )
        .filter(
            MovimientoInventario.producto_id.in_(producto_ids),
            MovimientoInventario.tipo == 'SALIDA',
            MovimientoInventario.fecha >= desde,
        )
        .group_by(MovimientoInventario.producto_id)
        .all()
    )
    return {pid: float(total or 0) / dias for pid, total in filas}


def _redondear_minimo(valor: float, unidad: str) -> Decimal:
    """Redondea la sugerencia con el grano de la unidad: las piezas no se piden
    a medias, los metros y los kilos sí."""
    if _unidad_permite_decimales(unidad):
        return Decimal(str(round(valor, 2)))
    import math
    return Decimal(str(int(math.ceil(valor))))


@bp.route('/productos/minimos/sugerencia', methods=['POST'])
@_require_inventario_admin
def sugerir_minimos():
    """Sugiere un stock mínimo por producto según su consumo.

    Body: {producto_ids: [int], dias_consumo?: 30, dias_cobertura?: 15}
    Devuelve por producto el consumo diario, el mínimo actual y el sugerido, para
    que el usuario vea de dónde sale el número antes de aplicarlo.
    """
    data = request.get_json(silent=True) or {}
    ids = data.get('producto_ids') or []
    if not isinstance(ids, list) or not ids:
        return jsonify({'detail': 'Selecciona al menos un producto'}), 422
    if len(ids) > MAX_PRODUCTOS_LOTE:
        return jsonify({'detail': f'Máximo {MAX_PRODUCTOS_LOTE} productos por vez'}), 422
    try:
        ids = [int(i) for i in ids]
    except (TypeError, ValueError):
        return jsonify({'detail': 'producto_ids debe traer solo números'}), 422

    try:
        dias_consumo = int(data.get('dias_consumo') or 30)
        dias_cobertura = int(data.get('dias_cobertura') or 15)
    except (TypeError, ValueError):
        return jsonify({'detail': 'Los días deben ser números enteros'}), 422
    if not (DIAS_CONSUMO_MIN <= dias_consumo <= DIAS_CONSUMO_MAX):
        return jsonify({'detail': f'dias_consumo debe estar entre {DIAS_CONSUMO_MIN} y {DIAS_CONSUMO_MAX}'}), 422
    if not (DIAS_COBERTURA_MIN <= dias_cobertura <= DIAS_COBERTURA_MAX):
        return jsonify({'detail': f'dias_cobertura debe estar entre {DIAS_COBERTURA_MIN} y {DIAS_COBERTURA_MAX}'}), 422

    productos = Producto.query.filter(Producto.id.in_(ids), Producto.activo == True).all()  # noqa: E712
    consumo = _consumo_diario([p.id for p in productos], dias_consumo)

    items = []
    for p in productos:
        diario = consumo.get(p.id, 0.0)
        sugerido = _redondear_minimo(diario * dias_cobertura, p.unidad)
        items.append({
            'id': p.id,
            'codigo': p.codigo,
            'descripcion': p.descripcion,
            'unidad': p.unidad,
            'stock_actual': float(p.stock_actual or 0),
            'stock_minimo': float(p.stock_minimo or 0),
            'consumo_diario': round(diario, 3),
            # Sin consumo en la ventana no hay base para sugerir: se devuelve el
            # dato en 0 y el SPA lo marca como "sin movimiento", en vez de
            # proponer un mínimo inventado.
            'sugerido': float(sugerido),
            'sin_consumo': diario <= 0,
        })
    items.sort(key=lambda i: i['consumo_diario'], reverse=True)
    return jsonify({
        'dias_consumo': dias_consumo,
        'dias_cobertura': dias_cobertura,
        'items': items,
    })


@bp.route('/productos/minimos', methods=['PATCH'])
@_require_inventario_admin
def actualizar_minimos():
    """Fija el stock mínimo de varios productos de una vez.

    Body: {items: [{id, stock_minimo}]}  ó  {producto_ids: [...], stock_minimo: n}

    No toca ningún otro campo ni el stock real: el mínimo es solo el umbral de
    alerta. Las filas inválidas se reportan y el resto sí se aplica, igual que en
    la importación.
    """
    data = request.get_json(silent=True) or {}
    items = data.get('items')
    if not items:
        ids = data.get('producto_ids') or []
        if not isinstance(ids, list) or not ids or data.get('stock_minimo') is None:
            return jsonify({'detail': 'Manda items[] o producto_ids[] + stock_minimo'}), 422
        items = [{'id': i, 'stock_minimo': data['stock_minimo']} for i in ids]
    if not isinstance(items, list):
        return jsonify({'detail': 'items debe ser una lista'}), 422
    if len(items) > MAX_PRODUCTOS_LOTE:
        return jsonify({'detail': f'Máximo {MAX_PRODUCTOS_LOTE} productos por vez'}), 422

    porid = {}
    errores = []
    for it in items:
        try:
            pid = int(it['id'])
            valor = Decimal(str(it['stock_minimo']))
        except (KeyError, TypeError, ValueError, ArithmeticError):
            errores.append(f'Fila inválida: {str(it)[:60]}')
            continue
        if valor < 0 or valor > 1_000_000:
            errores.append(f'Producto #{pid}: el mínimo debe estar entre 0 y 1,000,000')
            continue
        porid[pid] = valor

    productos = Producto.query.filter(Producto.id.in_(list(porid.keys()))).all() if porid else []
    encontrados = {p.id for p in productos}
    for pid in porid:
        if pid not in encontrados:
            errores.append(f'Producto #{pid}: no existe')

    actualizados, sin_cambios = 0, 0
    for p in productos:
        nuevo = porid[p.id]
        # Misma regla que el alta manual: las unidades por pieza no admiten
        # mínimos con decimales.
        if not _unidad_permite_decimales(p.unidad) and nuevo != nuevo.to_integral_value():
            errores.append(
                f'{p.codigo}: la unidad "{p.unidad or "pza"}" maneja cantidades enteras')
            continue
        if Decimal(str(p.stock_minimo or 0)) == nuevo:
            sin_cambios += 1
            continue
        p.stock_minimo = nuevo
        actualizados += 1

    if actualizados:
        _audit(request.current_user, f'Stock mínimo actualizado en masa: {actualizados} productos')
    db.session.commit()

    if actualizados:
        emit_to_role(_INV_ROLES, 'producto:changed', {
            'action': 'bulk_minimos', 'count': actualizados,
        })

    return jsonify({
        'actualizados': actualizados,
        'sin_cambios': sin_cambios,
        'errores': errores,
    })
