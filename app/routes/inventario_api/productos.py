"""Endpoints de Productos (catálogo + stock + kardex)."""
import datetime
from decimal import Decimal

from flask import jsonify, request, Response
from sqlalchemy.orm import joinedload

from app.extensions import db
from app.models import (
    Producto, MovimientoInventario, StockPorAlmacen, Almacen,
    SolicitudMaterial, SolicitudMaterialDetalle, User,
    SolicitudCompra, SolicitudCompraDetalle,
)

from ._core import (
    bp,
    _require_inventario, _require_inventario_admin,
    _parse_or_422, _int_arg,
    ProductoCreateSchema, ProductoUpdateSchema,
    _producto_to_dict,
    _audit, _almacen_default_id,
    _INV_ROLES,
)
from app.realtime import emit_to_role
from .solicitudes import _unidad_permite_decimales


def _validar_stock_entero(unidad, *valores):
    """Si la unidad es por pieza (no admite decimales), exige que los valores de
    stock dados sean enteros. Devuelve un mensaje de error o None. Replica la
    regla `_unidad_permite_decimales` usada en solicitudes/compras — así el
    catálogo funciona igual que el resto del sistema."""
    if _unidad_permite_decimales(unidad):
        return None
    for v in valores:
        if v is None:
            continue
        d = Decimal(str(v))
        if d != d.to_integral_value():
            return (f"La unidad '{unidad or 'pza'}' maneja cantidades enteras: "
                    f"el stock no puede tener decimales")
    return None


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


@bp.route('/productos/', methods=['GET'])
@_require_inventario
def get_productos():
    skip, err = _int_arg('skip', 0, 0, 1_000_000)
    if err: return err
    limit, err = _int_arg('limit', 200, 0, 1000)
    if err: return err

    # Filtros server-side: con miles de productos el catálogo NO puede bajar
    # todo y filtrar en el cliente. `categoria` (match exacto) y `q` (búsqueda
    # en código/descripción/categoría) acotan el resultado en la DB.
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

    productos = (
        query
        .order_by(Producto.id)  # orden determinista: sin esto, offset/limit en
                                # Postgres devuelve filas arbitrarias y la
                                # paginación puede saltarse/duplicar productos.
        .offset(skip)
        .limit(limit)
        .all()
    )
    return jsonify([_producto_to_dict(p) for p in productos])


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


@bp.route('/productos/', methods=['POST'])
@_require_inventario_admin
def create_producto():
    data, err = _parse_or_422(ProductoCreateSchema(), request.get_json(silent=True))
    if err: return err

    if Producto.query.filter(Producto.codigo == data['codigo']).first():
        return jsonify({'detail': 'El código de producto ya existe'}), 400

    err_dec = _validar_stock_entero(data['unidad'], data['stock_actual'], data['stock_minimo'])
    if err_dec:
        return jsonify({'detail': err_dec}), 422

    user = request.current_user
    stock_inicial = Decimal(str(data['stock_actual']))
    nuevo = Producto(
        codigo=data['codigo'],
        descripcion=data['descripcion'],
        categoria=data['categoria'],
        unidad=data['unidad'],
        stock_actual=stock_inicial,
        stock_minimo=Decimal(str(data['stock_minimo'])),
        imagen_url=data.get('imagen_url') or None,
        proveedor_default_nombre=(data.get('proveedor_default_nombre') or None),
        proveedor_default_contacto=(data.get('proveedor_default_contacto') or None),
        created_by_id=user.id,
    )
    db.session.add(nuevo)
    db.session.flush()  # obtener nuevo.id

    # Pausa 2: depositar el stock inicial en la bodega default. Sin esto,
    # Producto.stock_actual (cache) y stock_por_almacen (verdad) divergen
    # desde el primer movimiento.
    if stock_inicial > 0:
        default_id = _almacen_default_id()
        if default_id:
            db.session.add(StockPorAlmacen(
                producto_id=nuevo.id,
                almacen_id=default_id,
                cantidad=stock_inicial,
            ))

    _audit(user, f"Producto creado: {data['codigo']} — {data['descripcion']}")
    db.session.commit()
    db.session.refresh(nuevo)
    emit_to_role(_INV_ROLES, 'producto:changed', {
        'id': nuevo.id, 'action': 'created',
    })
    return jsonify(_producto_to_dict(nuevo))


@bp.route('/productos/<int:producto_id>', methods=['PUT'])
@_require_inventario_admin
def update_producto(producto_id: int):
    data, err = _parse_or_422(ProductoUpdateSchema(), request.get_json(silent=True))
    if err: return err

    prod = Producto.query.filter(Producto.id == producto_id, Producto.activo == True).first()
    if not prod:
        return jsonify({'detail': 'Producto no encontrado'}), 404

    # Decimales según unidad: si la unidad (nueva o actual) es por pieza, el
    # stock que se vaya a guardar debe ser entero.
    unidad_efectiva = data['unidad'] if data.get('unidad') is not None else prod.unidad
    err_dec = _validar_stock_entero(
        unidad_efectiva,
        data.get('stock_actual'),
        data.get('stock_minimo'),
    )
    if err_dec:
        return jsonify({'detail': err_dec}), 422

    cambios = []
    if data.get('codigo') is not None and data['codigo'] != prod.codigo:
        if Producto.query.filter(Producto.codigo == data['codigo']).first():
            return jsonify({'detail': 'El código ya existe en otro producto'}), 400
        cambios.append(f"codigo: {prod.codigo}→{data['codigo']}")
        prod.codigo = data['codigo']
    if data.get('descripcion') is not None:
        cambios.append("descripcion actualizada")
        prod.descripcion = data['descripcion']
    if data.get('categoria') is not None: prod.categoria = data['categoria']
    if data.get('unidad') is not None: prod.unidad = data['unidad']
    if data.get('imagen_url') is not None: prod.imagen_url = data['imagen_url'] or None
    if data.get('stock_actual') is not None:
        cambios.append(f"stock_actual: {prod.stock_actual}→{data['stock_actual']}")
        prod.stock_actual = Decimal(str(data['stock_actual']))
    if data.get('stock_minimo') is not None:
        prod.stock_minimo = Decimal(str(data['stock_minimo']))
    if data.get('precio_unitario') is not None:
        cambios.append(f"precio_unitario: {prod.precio_unitario}→{data['precio_unitario']}")
        prod.precio_unitario = Decimal(str(data['precio_unitario']))
    if data.get('proveedor_default_nombre') is not None:
        prod.proveedor_default_nombre = data['proveedor_default_nombre'] or None
    if data.get('proveedor_default_contacto') is not None:
        prod.proveedor_default_contacto = data['proveedor_default_contacto'] or None

    if cambios:
        _audit(request.current_user, f"Producto #{producto_id} editado: {'; '.join(cambios)}")

    db.session.commit()
    db.session.refresh(prod)
    emit_to_role(_INV_ROLES, 'producto:changed', {
        'id': prod.id, 'action': 'updated',
    })
    return jsonify(_producto_to_dict(prod))


@bp.route('/productos/<int:producto_id>/stocks', methods=['GET'])
@_require_inventario_admin
def get_producto_stocks(producto_id: int):
    """Desglose de stock por almacén para un producto (Pausa 2).

    Devuelve solo filas con cantidad > 0 por defecto. Pasar
    `?incluir_vacios=1` para ver también las bodegas con cantidad 0 (útil al
    decidir destino de un TRASPASO).
    """
    if not Producto.query.filter(Producto.id == producto_id, Producto.activo == True).first():  # noqa: E712
        return jsonify({'detail': 'Producto no encontrado'}), 404

    incluir_vacios = request.args.get('incluir_vacios') in ('1', 'true', 'yes')

    q = (
        db.session.query(StockPorAlmacen, Almacen)
        .join(Almacen, Almacen.id == StockPorAlmacen.almacen_id)
        .filter(StockPorAlmacen.producto_id == producto_id, Almacen.activo == True)  # noqa: E712
        .order_by(Almacen.nombre)
    )
    if not incluir_vacios:
        q = q.filter(StockPorAlmacen.cantidad > 0)

    rows = q.all()
    total = sum(float(s.cantidad or 0) for s, _ in rows)
    return jsonify({
        'producto_id': producto_id,
        'total': total,
        'stocks': [
            {
                'almacen_id': a.id,
                'almacen_nombre': a.nombre,
                'almacen_ubicacion': a.ubicacion or '',
                'cantidad': float(s.cantidad or 0),
                'updated_at': s.updated_at.isoformat() if s.updated_at else None,
            }
            for s, a in rows
        ],
    })


@bp.route('/productos/<int:producto_id>/disponibilidad', methods=['GET'])
@_require_inventario_admin
def get_producto_disponibilidad(producto_id: int):
    """Stock real / reservado / disponible de un producto (Pausa 2-bis).
    Incluye lista de solicitudes APROBADAS no entregadas que están apartando
    stock, para que el SPA muestre por qué hay reservas."""
    p = Producto.query.filter(Producto.id == producto_id).first()
    if not p:
        return jsonify({'detail': 'Producto no encontrado'}), 404

    actual = float(p.stock_actual or 0)
    reservado = float(p.stock_reservado or 0)
    disponible = actual - reservado

    # Solicitudes que generan la reserva: APROBADAS con detalles de este producto.
    rows = (
        db.session.query(
            SolicitudMaterial.id,
            SolicitudMaterial.proyecto,
            SolicitudMaterial.fecha_creacion,
            User.username,
            db.func.sum(
                db.func.greatest(
                    SolicitudMaterialDetalle.cantidad_solicitada
                    - db.func.coalesce(SolicitudMaterialDetalle.cantidad_entregada, 0),
                    0,
                )
            ).label('pendiente'),
        )
        .join(SolicitudMaterialDetalle, SolicitudMaterialDetalle.solicitud_id == SolicitudMaterial.id)
        .outerjoin(User, User.id == SolicitudMaterial.solicitante_id)
        .filter(
            SolicitudMaterial.estatus == 'APROBADA',
            SolicitudMaterialDetalle.producto_id == producto_id,
        )
        .group_by(SolicitudMaterial.id, SolicitudMaterial.proyecto,
                  SolicitudMaterial.fecha_creacion, User.username)
        .order_by(SolicitudMaterial.fecha_creacion.desc())
        .all()
    )

    return jsonify({
        'producto_id': producto_id,
        'codigo': p.codigo,
        'unidad': p.unidad,
        'stock_actual': actual,
        'stock_reservado': reservado,
        'stock_disponible': disponible,
        'reservas': [
            {
                'solicitud_id': r.id,
                'folio': f'SOL-{r.id:06d}',
                'proyecto': r.proyecto or '',
                'solicitante': r.username or '',
                'fecha': r.fecha_creacion.isoformat() if r.fecha_creacion else None,
                'cantidad': float(r.pendiente or 0),
            }
            for r in rows
        ],
    })


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


@bp.route('/productos/<int:producto_id>', methods=['DELETE'])
@_require_inventario_admin
def delete_producto(producto_id: int):
    prod = Producto.query.filter(Producto.id == producto_id).first()
    if not prod:
        return jsonify({'detail': 'Producto no encontrado'}), 404
    prod.activo = False  # Soft delete: mantener histórico de movimientos/solicitudes
    _audit(request.current_user, f"Producto #{producto_id} ({prod.codigo}) desactivado (soft delete)")
    db.session.commit()
    emit_to_role(_INV_ROLES, 'producto:changed', {
        'id': producto_id, 'action': 'deleted',
    })
    return Response(status=204)
