"""Lectura del catálogo: búsqueda, filtros, paginado y bajo mínimo."""
import datetime

from flask import jsonify, request

from app.extensions import db
from app.models import (
    MovimientoInventario, Producto, SolicitudCompra, SolicitudCompraDetalle,
)

from .._core import (
    bp,
    _require_inventario,
    _int_arg,
    _producto_to_dict,
)


@bp.route('/productos/by-codigo/<string:codigo>', methods=['GET'])
@_require_inventario
def get_producto_por_codigo(codigo: str):
    """Lookup de producto por su código (usado por el scanner móvil cuando
    el QR escaneado no es estante ni herramienta)."""
    codigo = (codigo or '').strip()
    if not codigo:
        return jsonify({'detail': 'codigo requerido'}), 422
    prod = Producto.query.filter(Producto.codigo == codigo, Producto.activo == True).first()
    if not prod:
        return jsonify({'detail': f'Producto {codigo} no encontrado'}), 404
    return jsonify(_producto_to_dict(prod))


def _productos_filtered_query():
    """Construye el query de Productos activos aplicando los filtros server-side
    de la query string: `categoria` (match exacto), `q` (búsqueda en
    código/descripción/categoría), `stock` (bajo/sin), `imagen` (con/sin),
    `unidad` (match exacto) y `compra=activa` (con compra en curso).

    Devuelve el query SIN orden ni paginación — lo comparten el listado por
    array (`GET /productos/`) y el paginado (`GET /productos/paginado`), así los
    filtros se mantienen idénticos en ambos sin duplicar lógica.
    """
    categoria = (request.args.get('categoria') or '').strip()
    q = (request.args.get('q') or '').strip()

    query = Producto.query.filter(Producto.activo == True)
    if categoria:
        query = query.filter(Producto.categoria == categoria)
    if q:
        like = f'%{q}%'
        query = query.filter(db.or_(
            Producto.codigo.ilike(like),
            Producto.descripcion.ilike(like),
            Producto.categoria.ilike(like),
            Producto.marca.ilike(like),
        ))

    # Filtros avanzados (combinables con categoría/búsqueda).
    # `stock`: 'bajo' = en o por debajo del mínimo; 'sin' = sin existencias.
    stock_f = (request.args.get('stock') or '').strip().lower()
    if stock_f == 'bajo':
        query = query.filter(Producto.stock_actual <= Producto.stock_minimo)
    elif stock_f == 'sin':
        query = query.filter(Producto.stock_actual <= 0)
    # `imagen`: 'con' = tiene foto cargada; 'sin' = falta foto.
    imagen_f = (request.args.get('imagen') or '').strip().lower()
    if imagen_f == 'con':
        query = query.filter(Producto.imagen_url.isnot(None), Producto.imagen_url != '')
    elif imagen_f == 'sin':
        query = query.filter(db.or_(Producto.imagen_url.is_(None), Producto.imagen_url == ''))
    # `unidad`: match exacto (pza, m, kg…).
    unidad_f = (request.args.get('unidad') or '').strip()
    if unidad_f:
        query = query.filter(Producto.unidad == unidad_f)
    # `compra=activa`: solo productos con una solicitud de compra en curso
    # (PENDIENTE u ORDENADA). Mismo criterio que /solicitudes-compra/productos-activos.
    if (request.args.get('compra') or '').strip().lower() in ('activa', '1', 'true', 'si'):
        sub = (
            db.session.query(SolicitudCompraDetalle.producto_id)
            .join(SolicitudCompra, SolicitudCompra.id == SolicitudCompraDetalle.solicitud_compra_id)
            .filter(
                SolicitudCompraDetalle.producto_id.isnot(None),
                SolicitudCompra.estatus.in_(['PENDIENTE', 'ORDENADA']),
            )
        )
        query = query.filter(Producto.id.in_(sub))
    return query


@bp.route('/productos/', methods=['GET'])
@_require_inventario
def get_productos():
    skip, err = _int_arg('skip', 0, 0, 1_000_000)
    if err: return err
    limit, err = _int_arg('limit', 200, 0, 1000)
    if err: return err

    productos = (
        _productos_filtered_query()
        .order_by(Producto.id)  # orden determinista: sin esto, offset/limit en
                                # Postgres devuelve filas arbitrarias y la
                                # paginación puede saltarse/duplicar productos.
        .offset(skip)
        .limit(limit)
        .all()
    )
    return jsonify([_producto_to_dict(p) for p in productos])


@bp.route('/productos/paginado', methods=['GET'])
@_require_inventario
def get_productos_paginado():
    """Listado paginado por páginas: mismos filtros que GET /productos/ pero
    devuelve `total` y `pages` para pintar un paginador numérico
    (Anterior/Siguiente + Página X de Y) sin bajar todo el catálogo de golpe.

    Params: `page` (1-based, def. 1) y `per_page` (def. 50, máx. 200).
    Respuesta: { items, total, page, per_page, pages } — misma forma que el
    resto de listados paginados del sistema (empleados, proyectos, etc.).
    """
    page, err = _int_arg('page', 1, 1, 1_000_000)
    if err: return err
    per_page, err = _int_arg('per_page', 50, 1, 200)
    if err: return err

    query = _productos_filtered_query()
    # count() sobre el query filtrado (sin orden, para no arrastrar el ORDER BY
    # al COUNT). Es la única query extra respecto al listado por array.
    total = query.order_by(None).count()
    productos = (
        query
        .order_by(Producto.id)
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    pages = max(1, (total + per_page - 1) // per_page)
    return jsonify({
        'items': [_producto_to_dict(p) for p in productos],
        'total': total,
        'page': page,
        'per_page': per_page,
        'pages': pages,
    })


@bp.route('/productos/unidades/', methods=['GET'])
@_require_inventario
def get_unidades():
    """Unidades distintas en uso, para el select de filtro del catálogo."""
    rows = (
        Producto.query
        .with_entities(Producto.unidad)
        .filter(Producto.activo == True, Producto.unidad != None, Producto.unidad != '')  # noqa: E711,E712
        .distinct()
        .all()
    )
    return jsonify(sorted({r[0] for r in rows}, key=lambda s: s.lower()))


@bp.route('/productos/bajo-minimo/', methods=['GET'])
@_require_inventario
def get_productos_bajo_minimo():
    """Productos en o bajo el mínimo, con consumo promedio y días restantes (Pausa 5).

    consumo_promedio_30d = SUM(SALIDAs últimos 30 días) / 30 (unidades/día).
    dias_de_stock_restante = stock_actual / consumo_promedio_30d (None si consumo=0).
    Orden: mayor urgencia primero (menos días restantes; consumo=0 al final).
    """
    productos = (
        Producto.query
        .filter(Producto.activo == True, Producto.stock_actual <= Producto.stock_minimo)  # noqa: E712
        .all()
    )
    if not productos:
        return jsonify([])

    # Una sola query para el consumo de todos los productos bajo mínimo
    # (en lugar de N queries dentro del loop).
    hace_30 = datetime.datetime.now() - datetime.timedelta(days=30)
    ids = [p.id for p in productos]
    consumos = dict(
        db.session.query(
            MovimientoInventario.producto_id,
            db.func.coalesce(db.func.sum(MovimientoInventario.cantidad), 0),
        )
        .filter(
            MovimientoInventario.producto_id.in_(ids),
            MovimientoInventario.tipo == 'SALIDA',
            MovimientoInventario.fecha >= hace_30,
        )
        .group_by(MovimientoInventario.producto_id)
        .all()
    )

    out = []
    for p in productos:
        consumo_total = float(consumos.get(p.id, 0) or 0)
        consumo_diario = round(consumo_total / 30.0, 2)
        stock = float(p.stock_actual or 0)
        minimo = float(p.stock_minimo or 0)
        # División por cero: si no hay consumo, no hay forma de estimar días.
        if consumo_diario > 0:
            dias_restantes = round(stock / consumo_diario, 1)
        else:
            dias_restantes = None
        # Urgencia para que el SPA coloree sin recalcular.
        if dias_restantes is None:
            urgencia = 'estatico'  # bajo mínimo pero sin consumo: producto parado
        elif dias_restantes < 7:
            urgencia = 'critico'
        elif dias_restantes < 14:
            urgencia = 'alto'
        else:
            urgencia = 'medio'
        out.append({
            'id': p.id,
            'codigo': p.codigo,
            'descripcion': p.descripcion,
            'categoria': p.categoria,
            'unidad': p.unidad,
            'stock_actual': stock,
            'stock_minimo': minimo,
            'faltante': max(0.0, minimo - stock),
            'consumo_promedio_30d': consumo_diario,
            'dias_de_stock_restante': dias_restantes,
            'urgencia': urgencia,
        })

    # Orden: críticos → altos → medios → estáticos. Dentro de cada grupo,
    # menos días primero.
    URGENCIA_ORDEN = {'critico': 0, 'alto': 1, 'medio': 2, 'estatico': 3}
    out.sort(key=lambda x: (
        URGENCIA_ORDEN[x['urgencia']],
        x['dias_de_stock_restante'] if x['dias_de_stock_restante'] is not None else 99999,
    ))
    return jsonify(out)
