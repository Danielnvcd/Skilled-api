"""Endpoints de Productos (catálogo + stock + kardex)."""
import datetime
from decimal import Decimal

from flask import jsonify, request, Response
from sqlalchemy.orm import joinedload

from app.extensions import db
from app.models import (
    Producto, MovimientoInventario, StockPorAlmacen, StockAlmacenProyecto,
    Almacen, Proyecto,
    SolicitudMaterial, SolicitudMaterialDetalle, User,
    SolicitudCompra, SolicitudCompraDetalle,
)

from ._core import (
    bp,
    _es_error_de_lock,
    _require_inventario, _require_inventario_admin,
    _parse_or_422, _int_arg,
    ProductoCreateSchema, ProductoUpdateSchema, AjusteBucketsSchema,
    _producto_to_dict,
    _audit, _almacen_default_id,
    _depositar, _recalcular_caches, _ajustar_bucket,
    _stock_proyecto_total, _reserva_derivada,
    _INV_ROLES,
)
from app.realtime import emit_to_role
from .solicitudes import _unidad_permite_decimales, _es_categoria_cable


# Unidad forzada para productos de cable (se pide/consume por metros).
_CABLE_UNIDAD = 'M'


def _validar_normalizar_cable(categoria, unidad, cable_tipo, cable_calibre):
    """Reglas de negocio de los productos de CABLE.

    - Si la categoría es de cable: `cable_tipo` y `cable_calibre` son
      obligatorios (no vacíos) y la unidad se FUERZA a 'M' (metros), sin
      importar lo que venga del cliente.
    - Si NO es cable: los campos de cable se limpian a None para no arrastrar
      datos huérfanos al cambiar de categoría.

    Devuelve (unidad_final, cable_tipo_final, cable_calibre_final, error) donde
    `error` es un mensaje str si falta algún dato obligatorio, o None si todo ok.
    """
    tipo = (cable_tipo or '').strip() or None
    calibre = (cable_calibre or '').strip() or None
    if _es_categoria_cable(categoria):
        if not tipo or not calibre:
            return None, None, None, 'Los productos de cable requieren Tipo y Tamaño (mm²/AWG)'
        return _CABLE_UNIDAD, tipo, calibre, None
    return unidad, None, None, None


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


@bp.route('/productos/', methods=['POST'])
@_require_inventario_admin
def create_producto():
    data, err = _parse_or_422(ProductoCreateSchema(), request.get_json(silent=True))
    if err: return err

    if Producto.query.filter(Producto.codigo == data['codigo']).first():
        return jsonify({'detail': 'El código de producto ya existe'}), 400

    # Cable: exige Tipo + Tamaño y fuerza unidad='M'. No-cable: limpia esos campos.
    unidad_final, cable_tipo, cable_calibre, err_cable = _validar_normalizar_cable(
        data['categoria'], data['unidad'], data.get('cable_tipo'), data.get('cable_calibre'),
    )
    if err_cable:
        return jsonify({'detail': err_cable}), 422

    err_dec = _validar_stock_entero(unidad_final, data['stock_actual'], data['stock_minimo'])
    if err_dec:
        return jsonify({'detail': err_dec}), 422

    user = request.current_user
    stock_inicial = Decimal(str(data['stock_actual']))
    nuevo = Producto(
        codigo=data['codigo'],
        descripcion=data['descripcion'],
        categoria=data['categoria'],
        marca=(data.get('marca') or None),
        unidad=unidad_final,
        cable_tipo=cable_tipo,
        cable_calibre=cable_calibre,
        stock_actual=stock_inicial,
        stock_minimo=Decimal(str(data['stock_minimo'])),
        imagen_url=data.get('imagen_url') or None,
        proveedor_default_nombre=(data.get('proveedor_default_nombre') or None),
        proveedor_default_contacto=(data.get('proveedor_default_contacto') or None),
        created_by_id=user.id,
    )
    db.session.add(nuevo)
    db.session.flush()  # obtener nuevo.id

    # Depositar el stock inicial en el bucket (almacén, proyecto|general) elegido
    # — feature stock por proyecto. Sin destino explícito cae en la bodega
    # default y el bucket general (compat con el comportamiento previo). Se
    # recalculan los caches (stock_por_almacen + stock_actual) para no divergir.
    if stock_inicial > 0:
        almacen_id = data.get('stock_inicial_almacen_id') or _almacen_default_id()
        proyecto_id = data.get('stock_inicial_proyecto_id')
        if almacen_id:
            if not Almacen.query.filter(Almacen.id == almacen_id).first():
                db.session.rollback()
                return jsonify({'detail': f'Almacén #{almacen_id} no existe'}), 422
            if proyecto_id and not Proyecto.query.filter(Proyecto.id == proyecto_id).first():
                db.session.rollback()
                return jsonify({'detail': f'Proyecto #{proyecto_id} no existe'}), 422
            # El producto acaba de crearse, así que en la práctica nadie más
            # tiene su bucket bloqueado; el guard es por consistencia con el
            # resto de endpoints que mutan stock (y por si el almacén destino
            # está siendo tocado por otra operación).
            try:
                _depositar(nuevo.id, almacen_id, proyecto_id, stock_inicial)
                _recalcular_caches(nuevo, almacen_id)
            except Exception as exc:
                db.session.rollback()
                if _es_error_de_lock(exc):
                    return jsonify({'detail': 'Stock bloqueado por otra operación, reintenta'}), 409
                raise

    _audit(user, f"Producto creado: {data['codigo']} — {data['descripcion']}")
    db.session.commit()
    db.session.refresh(nuevo)
    # Pipeline de imágenes → R2 (no-op salvo producción con R2 configurado):
    # si la imagen es una URL externa, se marca y se encola su descarga a WebP+R2.
    from .imagenes import marcar_para_sync, encolar_sync
    if marcar_para_sync(nuevo, nuevo.imagen_url):
        db.session.commit()
        encolar_sync(user.id, [nuevo.id])
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

    # Cable: reglas sobre la categoría/unidad/campos EFECTIVOS (los nuevos si
    # vienen, si no los actuales del producto). Un campo de cable en None se
    # interpreta como "no lo mandaron" → conserva el actual; así un update
    # parcial (p.ej. solo precio) no borra el Tipo/Tamaño de un cable existente.
    categoria_efectiva = data['categoria'] if data.get('categoria') is not None else prod.categoria
    unidad_efectiva = data['unidad'] if data.get('unidad') is not None else prod.unidad
    cable_tipo_eff = data.get('cable_tipo') if data.get('cable_tipo') is not None else prod.cable_tipo
    cable_calibre_eff = data.get('cable_calibre') if data.get('cable_calibre') is not None else prod.cable_calibre
    unidad_final, cable_tipo_final, cable_calibre_final, err_cable = _validar_normalizar_cable(
        categoria_efectiva, unidad_efectiva, cable_tipo_eff, cable_calibre_eff,
    )
    if err_cable:
        return jsonify({'detail': err_cable}), 422

    # Decimales según unidad: si la unidad (nueva/actual/forzada a M) es por pieza,
    # el stock que se vaya a guardar debe ser entero.
    err_dec = _validar_stock_entero(
        unidad_final,
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
    # Marca: None = "no la mandaron" → conserva la actual; '' = limpiar a NULL.
    if data.get('marca') is not None:
        nueva_marca = (data['marca'] or '').strip() or None
        if nueva_marca != prod.marca:
            cambios.append(f"marca: {prod.marca or '—'}→{nueva_marca or '—'}")
            prod.marca = nueva_marca
    # unidad_final ya considera el forzado a 'M' para cable; para no-cable es la
    # nueva unidad (si vino) o la actual, así que asignarla siempre es seguro.
    prod.unidad = unidad_final
    # Campos de cable normalizados (valores para cable; None para no-cable).
    if (prod.cable_tipo, prod.cable_calibre) != (cable_tipo_final, cable_calibre_final):
        cambios.append("datos de cable actualizados")
    prod.cable_tipo = cable_tipo_final
    prod.cable_calibre = cable_calibre_final
    if data.get('imagen_url') is not None: prod.imagen_url = data['imagen_url'] or None
    # `stock_actual` YA NO se edita desde el PUT: es un cache de la suma de buckets
    # (stock_almacen_proyecto). Pisarlo aquí lo desfasaría hasta el próximo
    # movimiento (que lo recalcula). El stock real se ajusta por bucket vía
    # POST /productos/<id>/ajustar-buckets (genera AJUSTES trazables). Se ignora
    # en silencio para no romper clientes que aún manden el campo.
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
    # Pipeline de imágenes → R2: si mandaron una imagen nueva y es URL externa,
    # se marca y se encola (no-op salvo producción con R2 configurado). El
    # background task cambiará imagen_url al dominio de R2 al terminar.
    from .imagenes import marcar_para_sync, encolar_sync
    if data.get('imagen_url') is not None and marcar_para_sync(prod, prod.imagen_url):
        db.session.commit()
        encolar_sync(request.current_user.id, [prod.id])
    emit_to_role(_INV_ROLES, 'producto:changed', {
        'id': prod.id, 'action': 'updated',
    })
    return jsonify(_producto_to_dict(prod))


@bp.route('/productos/<int:producto_id>/ajustar-buckets', methods=['POST'])
@_require_inventario_admin
def ajustar_buckets_producto(producto_id: int):
    """Editor de stock por bodega+proyecto (feature stock por proyecto).

    Recibe una lista de buckets `(almacen_id, proyecto_id|null)` con su
    `cantidad_objetivo`. Por cada bucket calcula el delta contra lo que hay hoy y
    genera un AJUSTE (via `_ajustar_bucket`) para cuadrarlo — así la fuente de
    verdad (`stock_almacen_proyecto`) queda consistente y el cambio es trazable en
    el kardex. Todo en UNA transacción; si un bucket no cuadra se revierte entero.

    No permite dejar el stock total por debajo de lo apartado por solicitudes
    aprobadas (`stock_reservado`).
    """
    data, err = _parse_or_422(AjusteBucketsSchema(), request.get_json(silent=True))
    if err: return err

    user = request.current_user
    buckets = data['buckets']
    motivo = (data.get('motivo') or '').strip() or 'Ajuste manual desde edición'

    # Rechazar buckets duplicados (mismo almacén+proyecto) para no aplicar dos
    # deltas contradictorios sobre la misma fila.
    vistos = set()
    for b in buckets:
        clave = (b['almacen_id'], b.get('proyecto_id') or 0)
        if clave in vistos:
            return jsonify({'detail': 'Hay buckets repetidos (mismo almacén y proyecto)'}), 422
        vistos.add(clave)

    # Validar existencia de almacenes/proyectos referenciados (422 legible).
    almacen_ids = {b['almacen_id'] for b in buckets}
    encontrados_alm = {a.id for a in Almacen.query.filter(Almacen.id.in_(almacen_ids)).all()}
    faltan_alm = almacen_ids - encontrados_alm
    if faltan_alm:
        return jsonify({'detail': f'Almacén(es) inexistente(s): {sorted(faltan_alm)}'}), 422
    proyecto_ids = {b['proyecto_id'] for b in buckets if b.get('proyecto_id')}
    if proyecto_ids:
        encontrados_proy = {p.id for p in Proyecto.query.filter(Proyecto.id.in_(proyecto_ids)).all()}
        faltan_proy = proyecto_ids - encontrados_proy
        if faltan_proy:
            return jsonify({'detail': f'Proyecto(s) inexistente(s): {sorted(faltan_proy)}'}), 422

    try:
        prod = (
            Producto.query
            .with_for_update(nowait=True)
            .filter(Producto.id == producto_id, Producto.activo == True)  # noqa: E712
            .first()
        )
        if not prod:
            db.session.rollback()
            return jsonify({'detail': 'Producto no encontrado'}), 404

        # Regla de decimales por unidad sobre las cantidades objetivo.
        err_dec = _validar_stock_entero(prod.unidad, *[b['cantidad_objetivo'] for b in buckets])
        if err_dec:
            db.session.rollback()
            return jsonify({'detail': err_dec}), 422

        cambios = 0
        for b in buckets:
            almacen_id = b['almacen_id']
            proyecto_id = b.get('proyecto_id')
            objetivo = Decimal(str(b['cantidad_objetivo']))
            actual = (
                db.session.query(db.func.coalesce(StockAlmacenProyecto.cantidad, 0))
                .filter(
                    StockAlmacenProyecto.producto_id == producto_id,
                    StockAlmacenProyecto.almacen_id == almacen_id,
                    StockAlmacenProyecto.proyecto_id.is_(None) if proyecto_id is None
                    else StockAlmacenProyecto.proyecto_id == proyecto_id,
                )
                .scalar()
            )
            actual = Decimal(str(actual or 0))
            delta = objetivo - actual
            if delta == 0:
                continue
            err_bucket = _ajustar_bucket(prod, almacen_id, proyecto_id, delta, user, motivo)
            if err_bucket:
                db.session.rollback()
                return jsonify({'detail': f'No se pudo ajustar el bucket: {err_bucket}'}), 409
            cambios += 1

        # Guard global: no dejar el total por debajo de lo apartado (reservas).
        reservado = Decimal(str(prod.stock_reservado or 0))
        total_post = Decimal(str(prod.stock_actual or 0))
        if total_post < reservado:
            db.session.rollback()
            return jsonify({
                'detail': (
                    f'El ajuste dejaría el stock total en {total_post}, por debajo de lo '
                    f'apartado por solicitudes aprobadas ({reservado}). Libera/rechaza '
                    f'solicitudes antes de reducir tanto.'
                ),
            }), 409

        if cambios:
            _audit(user, f"Ajuste de buckets producto #{producto_id}: {cambios} bucket(s) — {motivo}")
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        if _es_error_de_lock(exc):
            return jsonify({'detail': 'Stock bloqueado por otra operación, reintenta'}), 409
        raise

    db.session.refresh(prod)
    if cambios:
        emit_to_role(_INV_ROLES, 'producto:changed', {'id': prod.id, 'action': 'stock_ajustado'})
        emit_to_role(_INV_ROLES, 'movimiento:changed', {'producto_id': prod.id, 'tipo': 'AJUSTE'})
    return jsonify({'producto': _producto_to_dict(prod), 'buckets_ajustados': cambios})


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

    # Desglose por proyecto dentro de cada almacén (feature stock por proyecto).
    # proyecto_id NULL = bucket general. Se listan solo buckets con cantidad > 0
    # (o todos si incluir_vacios) para que la UI pinte "Almacén → [General 10,
    # Proyecto X 90]".
    pq = (
        db.session.query(StockAlmacenProyecto, Almacen, Proyecto)
        .join(Almacen, Almacen.id == StockAlmacenProyecto.almacen_id)
        .outerjoin(Proyecto, Proyecto.id == StockAlmacenProyecto.proyecto_id)
        .filter(StockAlmacenProyecto.producto_id == producto_id, Almacen.activo == True)  # noqa: E712
        .order_by(Almacen.nombre, Proyecto.numero_proyecto.nullsfirst())
    )
    if not incluir_vacios:
        pq = pq.filter(StockAlmacenProyecto.cantidad > 0)
    stocks_proyecto = [
        {
            'almacen_id': a.id,
            'almacen_nombre': a.nombre,
            'proyecto_id': p.id if p else None,
            'proyecto_nombre': (p.numero_proyecto if p else None),
            'proyecto_descripcion': (p.nombre if p else None),
            'cantidad': float(s.cantidad or 0),
        }
        for s, a, p in pq.all()
    ]

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
        'stocks_proyecto': stocks_proyecto,
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

    # Disponibilidad POR BUCKET del proyecto (feature stock por proyecto). Con
    # ?proyecto_id= devuelve lo que ese proyecto puede surtir bajo la regla
    # proyecto→general: (stockP − reservadoP) + máx(0, stockGen − reservadoGen).
    # Sirve para que el solicitante vea "disponible para tu proyecto".
    por_proyecto = None
    proyecto_id = request.args.get('proyecto_id', type=int)
    stock_gen = _stock_proyecto_total(producto_id, None)
    reservado_gen = _reserva_derivada(producto_id, None)
    disp_gen = stock_gen - reservado_gen
    if proyecto_id:
        stock_proj = _stock_proyecto_total(producto_id, proyecto_id)
        reservado_proj = _reserva_derivada(producto_id, proyecto_id)
        disp_proj_only = stock_proj - reservado_proj
        disponible_proyecto = disp_proj_only + (disp_gen if disp_gen > 0 else Decimal('0'))
        por_proyecto = {
            'proyecto_id': proyecto_id,
            'stock_proyecto': float(stock_proj),
            'reservado_proyecto': float(reservado_proj),
            'stock_general': float(stock_gen),
            'reservado_general': float(reservado_gen),
            # Lo que este proyecto puede surtir (bucket propio + general libre).
            'disponible_para_proyecto': float(disponible_proyecto),
        }

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
        'por_proyecto': por_proyecto,
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
