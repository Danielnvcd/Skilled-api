"""Bodegas: CRUD, validación por QR y existencias por bodega."""
import uuid

from flask import jsonify, request, Response

from app.extensions import db
from app.models import (
    Almacen, Estante, Producto, Proyecto, StockAlmacenProyecto, StockPorAlmacen,
)
from app.realtime import emit_to_role

from .._core import (
    bp,
    _require_inventario, _require_inventario_admin,
    _parse_or_422, _int_arg,
    AlmacenCreateSchema, AlmacenUpdateSchema,
    _almacen_to_dict, _estante_to_dict,
    _audit,
    _INV_ROLES,
)


# ─── Almacenes ────────────────────────────────────────────────────────────────

@bp.route('/almacenes/', methods=['GET'])
@_require_inventario
def get_almacenes():
    almacenes = Almacen.query.filter(Almacen.activo == True).all()
    return jsonify([_almacen_to_dict(a) for a in almacenes])


@bp.route('/almacenes/', methods=['POST'])
@_require_inventario_admin
def create_almacen():
    data, err = _parse_or_422(AlmacenCreateSchema(), request.get_json(silent=True))
    if err: return err

    nuevo = Almacen(
        nombre=data['nombre'],
        ubicacion=data.get('ubicacion'),
        activo=data.get('activo', True),
        qr_code=str(uuid.uuid4()),
    )
    db.session.add(nuevo)
    _audit(request.current_user, f"Almacén creado: {data['nombre']}")
    db.session.commit()
    db.session.refresh(nuevo)
    emit_to_role(_INV_ROLES, 'almacen:changed', {
        'id': nuevo.id, 'action': 'created',
    })
    return jsonify(_almacen_to_dict(nuevo))


@bp.route('/almacenes/<int:almacen_id>', methods=['PUT'])
@_require_inventario_admin
def update_almacen(almacen_id: int):
    data, err = _parse_or_422(AlmacenUpdateSchema(), request.get_json(silent=True))
    if err: return err

    alm = Almacen.query.filter(Almacen.id == almacen_id).first()
    if not alm:
        return jsonify({'detail': 'Bodega no encontrada'}), 404

    if data.get('nombre') is not None: alm.nombre = data['nombre']
    if data.get('ubicacion') is not None: alm.ubicacion = data['ubicacion']
    if data.get('activo') is not None: alm.activo = data['activo']
    _audit(request.current_user, f"Almacén #{almacen_id} editado")
    db.session.commit()
    db.session.refresh(alm)
    emit_to_role(_INV_ROLES, 'almacen:changed', {
        'id': alm.id, 'action': 'updated',
    })
    return jsonify(_almacen_to_dict(alm))


@bp.route('/almacenes/<int:almacen_id>', methods=['DELETE'])
@_require_inventario_admin
def delete_almacen(almacen_id: int):
    alm = Almacen.query.filter(Almacen.id == almacen_id).first()
    if not alm:
        return jsonify({'detail': 'Bodega no encontrada'}), 404
    alm.activo = False
    _audit(request.current_user, f"Almacén #{almacen_id} ({alm.nombre}) desactivado (soft delete)")
    db.session.commit()
    emit_to_role(_INV_ROLES, 'almacen:changed', {
        'id': alm.id, 'action': 'deleted',
    })
    return Response(status=204)


@bp.route('/almacenes/<qr_code>/validar', methods=['GET'])
@_require_inventario
def validar_almacen(qr_code: str):
    alm = Almacen.query.filter(Almacen.qr_code == qr_code).first()
    if not alm:
        return jsonify({'detail': 'Almacén no encontrado o QR inválido'}), 404
    return jsonify(_almacen_to_dict(alm))


@bp.route('/almacenes/<int:almacen_id>/estantes', methods=['GET'])
@_require_inventario
def get_estantes_por_almacen(almacen_id: int):
    estantes = (
        Estante.query
        .filter(Estante.almacen_id == almacen_id, Estante.activo == True)
        .all()
    )
    return jsonify([_estante_to_dict(e) for e in estantes])


# ─── Portada: existencias por almacén (carátula del catálogo) ─────────────────

@bp.route('/almacenes/resumen', methods=['GET'])
@_require_inventario
def get_almacenes_resumen():
    """Resumen de existencias por almacén para la portada del rol inventario.

    Por cada almacén activo devuelve: nº de productos distintos con existencia
    (cantidad > 0), unidades totales y cuántos de esos productos tienen foto.
    Un almacén sin existencias aparece igual con ceros — así el almacenista lo
    ve listado aunque esté vacío. Server-side: no baja el catálogo al cliente.
    """
    # Agregado por almacén sobre stock_por_almacen ⋈ productos ACTIVOS con
    # existencia. Se hace en una subconsulta y luego se cruza (LEFT JOIN) contra
    # los almacenes para conservar los que no tienen stock.
    sub = (
        db.session.query(
            StockPorAlmacen.almacen_id.label('almacen_id'),
            db.func.count(db.distinct(StockPorAlmacen.producto_id)).label('total_productos'),
            db.func.coalesce(db.func.sum(StockPorAlmacen.cantidad), 0).label('total_unidades'),
            db.func.coalesce(db.func.sum(
                db.case(
                    (db.and_(Producto.imagen_url.isnot(None), Producto.imagen_url != ''), 1),
                    else_=0,
                )
            ), 0).label('con_imagen'),
        )
        .join(Producto, Producto.id == StockPorAlmacen.producto_id)
        .filter(Producto.activo == True, StockPorAlmacen.cantidad > 0)  # noqa: E712
        .group_by(StockPorAlmacen.almacen_id)
        .subquery()
    )
    rows = (
        db.session.query(
            Almacen,
            sub.c.total_productos,
            sub.c.total_unidades,
            sub.c.con_imagen,
        )
        .outerjoin(sub, sub.c.almacen_id == Almacen.id)
        .filter(Almacen.activo == True)  # noqa: E712
        .order_by(Almacen.nombre.asc())
        .all()
    )
    return jsonify([
        {
            'almacen_id': alm.id,
            'nombre': alm.nombre,
            'ubicacion': alm.ubicacion or '',
            'total_productos': int(total_prod or 0),
            'total_unidades': float(total_uds or 0),
            'con_imagen': int(con_img or 0),
        }
        for alm, total_prod, total_uds, con_img in rows
    ])


@bp.route('/almacenes/resumen-proyectos', methods=['GET'])
@_require_inventario
def get_resumen_proyectos():
    """Resumen de existencias por PROYECTO y ALMACÉN para la portada de inventario.

    Agrega `stock_almacen_proyecto` (fuente de verdad del stock por proyecto)
    sobre almacenes activos y buckets con cantidad > 0, agrupando por
    (proyecto|General, almacén). Devuelve una matriz lista para pintar:
      - `almacenes`: columnas (id, nombre) — todos los almacenes activos.
      - `filas`: una por proyecto con existencias (General = `proyecto_id` null),
        con total de unidades, nº de productos distintos y el desglose por almacén.
      - `total_unidades`: gran total. Ordenadas: General primero, luego por
        unidades desc.

    Solo lectura; agregación en SQL (no baja el catálogo al cliente).
    """
    # Columnas: almacenes activos (aunque no tengan existencias, para una matriz
    # estable).
    almacenes = (
        db.session.query(Almacen.id, Almacen.nombre)
        .filter(Almacen.activo == True)  # noqa: E712
        .order_by(Almacen.nombre.asc())
        .all()
    )
    columnas = [{'id': a.id, 'nombre': a.nombre} for a in almacenes]

    base = (
        db.session.query(StockAlmacenProyecto)
        .join(Almacen, Almacen.id == StockAlmacenProyecto.almacen_id)
        .join(Producto, Producto.id == StockAlmacenProyecto.producto_id)
        .filter(
            Almacen.activo == True,        # noqa: E712
            Producto.activo == True,       # noqa: E712
            StockAlmacenProyecto.cantidad > 0,
        )
    )

    # Celdas: unidades (sumables) + productos distintos por (proyecto, almacén).
    celdas = (
        base.with_entities(
            StockAlmacenProyecto.proyecto_id.label('proyecto_id'),
            StockAlmacenProyecto.almacen_id.label('almacen_id'),
            db.func.coalesce(db.func.sum(StockAlmacenProyecto.cantidad), 0).label('unidades'),
            db.func.count(db.distinct(StockAlmacenProyecto.producto_id)).label('productos'),
        )
        .group_by(StockAlmacenProyecto.proyecto_id, StockAlmacenProyecto.almacen_id)
        .all()
    )

    # Nº de productos distintos por PROYECTO (no sumable desde las celdas: un
    # producto en 2 almacenes se contaría doble). Query aparte por proyecto.
    productos_por_proy = dict(
        base.with_entities(
            StockAlmacenProyecto.proyecto_id,
            db.func.count(db.distinct(StockAlmacenProyecto.producto_id)),
        )
        .group_by(StockAlmacenProyecto.proyecto_id)
        .all()
    )

    # Nombres de proyecto (solo los que aparecen).
    proy_ids = {c.proyecto_id for c in celdas if c.proyecto_id is not None}
    proy_info = {}
    if proy_ids:
        for p in Proyecto.query.filter(Proyecto.id.in_(proy_ids)).all():
            proy_info[p.id] = {'numero': p.numero_proyecto, 'nombre': p.nombre}

    filas_map: dict = {}
    for c in celdas:
        f = filas_map.get(c.proyecto_id)
        if f is None:
            info = proy_info.get(c.proyecto_id) if c.proyecto_id is not None else None
            f = {
                'proyecto_id': c.proyecto_id,
                'es_general': c.proyecto_id is None,
                'proyecto_nombre': (info['numero'] if info else ('General' if c.proyecto_id is None else f'#{c.proyecto_id}')),
                'proyecto_descripcion': (info['nombre'] if info else None),
                'total_unidades': 0.0,
                'total_productos': int(productos_por_proy.get(c.proyecto_id, 0) or 0),
                'celdas': {},  # almacen_id(str) -> {unidades, productos}
            }
            filas_map[c.proyecto_id] = f
        f['celdas'][str(c.almacen_id)] = {
            'unidades': float(c.unidades or 0),
            'productos': int(c.productos or 0),
        }
        f['total_unidades'] += float(c.unidades or 0)

    # Orden: General primero, luego por unidades desc.
    filas = sorted(
        filas_map.values(),
        key=lambda f: (0 if f['es_general'] else 1, -f['total_unidades']),
    )
    total_unidades = sum(f['total_unidades'] for f in filas)
    # Productos distintos CON EXISTENCIA en todo el inventario (para el KPI del
    # inicio). Distinct global: no sumable desde filas/celdas.
    total_productos = base.with_entities(
        db.func.count(db.distinct(StockAlmacenProyecto.producto_id))
    ).scalar() or 0

    return jsonify({
        'almacenes': columnas,
        'filas': filas,
        'total_unidades': total_unidades,
        'total_productos': int(total_productos),
    })


@bp.route('/almacenes/<int:almacen_id>/stock', methods=['GET'])
@_require_inventario
def get_almacen_stock(almacen_id: int):
    """Galería paginada de productos con existencia en un almacén (portada).

    Cada ítem trae foto, existencia en ESTE almacén, unidad y stock mínimo.
    Params: `page` (1-based, def 1), `per_page` (def 24, máx 100), y filtros
    opcionales `q` (código/descripción), `categoria` y `imagen` (con/sin).
    Orden: mayor existencia primero, luego código. Devuelve además las unidades
    totales del almacén (con el filtro aplicado) para el encabezado.
    """
    alm = Almacen.query.filter(Almacen.id == almacen_id, Almacen.activo == True).first()  # noqa: E712
    if not alm:
        return jsonify({'detail': 'Almacén no encontrado'}), 404

    page, err = _int_arg('page', 1, 1, 1_000_000)
    if err: return err
    per_page, err = _int_arg('per_page', 24, 1, 100)
    if err: return err

    q = (request.args.get('q') or '').strip()
    categoria = (request.args.get('categoria') or '').strip()
    imagen_f = (request.args.get('imagen') or '').strip().lower()

    # Filtro opcional por proyecto (feature stock por proyecto): sin el param se
    # usa el total del almacén (StockPorAlmacen); con `proyecto_id=<id>` o
    # `proyecto_id=general` la galería muestra el bucket de ese proyecto usando
    # stock_almacen_proyecto, y `cantidad` pasa a ser la del bucket.
    proyecto_arg = (request.args.get('proyecto_id') or '').strip()
    if proyecto_arg:
        StockModel = StockAlmacenProyecto
        base = (
            db.session.query(StockAlmacenProyecto, Producto)
            .join(Producto, Producto.id == StockAlmacenProyecto.producto_id)
            .filter(
                StockAlmacenProyecto.almacen_id == almacen_id,
                StockAlmacenProyecto.cantidad > 0,
                Producto.activo == True,  # noqa: E712
            )
        )
        if proyecto_arg.lower() == 'general':
            base = base.filter(StockAlmacenProyecto.proyecto_id.is_(None))
        else:
            try:
                base = base.filter(StockAlmacenProyecto.proyecto_id == int(proyecto_arg))
            except ValueError:
                return jsonify({'detail': "proyecto_id debe ser un entero o 'general'"}), 422
    else:
        StockModel = StockPorAlmacen
        base = (
            db.session.query(StockPorAlmacen, Producto)
            .join(Producto, Producto.id == StockPorAlmacen.producto_id)
            .filter(
                StockPorAlmacen.almacen_id == almacen_id,
                StockPorAlmacen.cantidad > 0,
                Producto.activo == True,  # noqa: E712
            )
        )
    if q:
        like = f'%{q}%'
        base = base.filter(db.or_(
            Producto.codigo.ilike(like),
            Producto.descripcion.ilike(like),
        ))
    if categoria:
        base = base.filter(Producto.categoria == categoria)
    if imagen_f == 'con':
        base = base.filter(Producto.imagen_url.isnot(None), Producto.imagen_url != '')
    elif imagen_f == 'sin':
        base = base.filter(db.or_(Producto.imagen_url.is_(None), Producto.imagen_url == ''))

    total = base.order_by(None).count()
    total_unidades = (
        base.with_entities(db.func.coalesce(db.func.sum(StockModel.cantidad), 0)).scalar()
    )
    rows = (
        base
        .order_by(StockModel.cantidad.desc(), Producto.codigo.asc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    pages = max(1, (total + per_page - 1) // per_page)

    items = [
        {
            'producto_id': p.id,
            'codigo': p.codigo,
            'descripcion': p.descripcion,
            'categoria': p.categoria,
            'unidad': p.unidad,
            'imagen_url': p.imagen_url,
            'imagen_estado': p.imagen_estado,
            'cable_tipo': p.cable_tipo,
            'cable_calibre': p.cable_calibre,
            'cantidad': float(s.cantidad or 0),
            'stock_minimo': float(p.stock_minimo or 0),
            'stock_actual': float(p.stock_actual or 0),
            'precio_unitario': float(p.precio_unitario or 0),
        }
        for s, p in rows
    ]

    return jsonify({
        'almacen': _almacen_to_dict(alm),
        'items': items,
        'total': total,
        'total_unidades': float(total_unidades or 0),
        'page': page,
        'per_page': per_page,
        'pages': pages,
    })
