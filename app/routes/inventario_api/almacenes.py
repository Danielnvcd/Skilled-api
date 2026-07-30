"""Endpoints de Almacenes y Estantes."""
import io
import uuid

import qrcode
from flask import jsonify, request, Response

from app.extensions import db
from app.models import (
    Almacen, Estante, Producto, ProductoEstante, StockPorAlmacen,
    StockAlmacenProyecto, Proyecto,
)

from ._core import (
    bp,
    _require_inventario, _require_inventario_admin,
    _parse_or_422, _int_arg,
    AlmacenCreateSchema, AlmacenUpdateSchema,
    EstanteCreateSchema, EstanteUpdateSchema, EstanteLayoutSchema,
    _almacen_to_dict, _estante_to_dict, _producto_to_dict,
    _producto_almacen_stock, _cantidad_en_celdas_almacen,
    _audit,
    _INV_ROLES,
)
from decimal import Decimal
from app.realtime import emit_to_role


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


# ─── Estantes ─────────────────────────────────────────────────────────────────

@bp.route('/estantes/', methods=['GET'])
@_require_inventario
def get_estantes():
    estantes = Estante.query.filter(Estante.activo == True).all()
    return jsonify([_estante_to_dict(e) for e in estantes])


@bp.route('/estantes/', methods=['POST'])
@_require_inventario_admin
def create_estante():
    data, err = _parse_or_422(EstanteCreateSchema(), request.get_json(silent=True))
    if err: return err

    almacen = Almacen.query.filter(Almacen.id == data['almacen_id']).first()
    if not almacen:
        return jsonify({'detail': 'Almacén no encontrado'}), 404

    nuevo = Estante(
        nombre=data['nombre'],
        descripcion=data.get('descripcion'),
        almacen_id=data['almacen_id'],
        filas=data.get('filas') or 1,
        columnas=data.get('columnas') or 1,
        qr_code=str(uuid.uuid4()),
    )
    db.session.add(nuevo)
    _audit(request.current_user, f"Estante creado: {data['nombre']} en almacén #{data['almacen_id']}")
    db.session.commit()
    db.session.refresh(nuevo)
    emit_to_role(_INV_ROLES, 'estante:changed', {
        'id': nuevo.id, 'almacen_id': nuevo.almacen_id, 'action': 'created',
    })
    return jsonify(_estante_to_dict(nuevo))


@bp.route('/estantes/<int:estante_id>', methods=['PUT'])
@_require_inventario_admin
def update_estante(estante_id: int):
    data, err = _parse_or_422(EstanteUpdateSchema(), request.get_json(silent=True))
    if err: return err

    est = Estante.query.filter(Estante.id == estante_id, Estante.activo == True).first()
    if not est:
        return jsonify({'detail': 'Estante no encontrado'}), 404

    if data.get('nombre') is not None: est.nombre = data['nombre']
    if data.get('descripcion') is not None: est.descripcion = data['descripcion']
    almacen_cambio = False
    if data.get('almacen_id') is not None and data['almacen_id'] != est.almacen_id:
        almacen = Almacen.query.filter(Almacen.id == data['almacen_id']).first()
        if not almacen:
            return jsonify({'detail': 'Bodega destino no encontrada'}), 404
        est.almacen_id = data['almacen_id']
        almacen_cambio = True
    if data.get('filas') is not None: est.filas = data['filas']
    if data.get('columnas') is not None: est.columnas = data['columnas']

    # Al MOVER el estante a otro almacén, sus celdas (sub-libro de StockPorAlmacen
    # del almacén origen) dejarían de cuadrar contra el almacén destino: el
    # invariante Σceldas ≤ stock es POR almacén. Las unidades físicas no "viajan"
    # con el registro del estante, así que reseteamos sus colocaciones a "sin
    # ubicar" (fila/columna NULL) y cantidad 0. El almacenista reacomoda en el
    # destino contra el stock real de ese almacén.
    if almacen_cambio:
        for pe in list(est.productos_asignados):
            pe.fila = None
            pe.columna = None
            pe.cantidad = Decimal('0')

    # Al reducir la rejilla, las colocaciones que caen fuera quedan "sin ubicar"
    # (fila/columna NULL). No se borra su `cantidad`: el almacenista puede
    # reubicarlas. Esto evita posiciones inválidas tras un resize.
    nf, nc = est.filas or 1, est.columnas or 1
    for pe in list(est.productos_asignados):
        if (pe.fila is not None and pe.fila > nf) or (pe.columna is not None and pe.columna > nc):
            pe.fila = None
            pe.columna = None
    _audit(request.current_user, f"Estante #{estante_id} editado")
    db.session.commit()
    db.session.refresh(est)
    emit_to_role(_INV_ROLES, 'estante:changed', {
        'id': est.id, 'almacen_id': est.almacen_id, 'action': 'updated',
    })
    return jsonify(_estante_to_dict(est))


@bp.route('/estantes/<int:estante_id>', methods=['DELETE'])
@_require_inventario_admin
def delete_estante(estante_id: int):
    est = Estante.query.filter(Estante.id == estante_id).first()
    if not est:
        return jsonify({'detail': 'Estante no encontrado'}), 404
    est.activo = False
    _audit(request.current_user, f"Estante #{estante_id} ({est.nombre}) desactivado (soft delete)")
    db.session.commit()
    emit_to_role(_INV_ROLES, 'estante:changed', {
        'id': est.id, 'almacen_id': est.almacen_id, 'action': 'deleted',
    })
    return Response(status=204)


@bp.route('/estantes/<qr_code>/validar', methods=['GET'])
@_require_inventario
def validar_estante(qr_code: str):
    est = Estante.query.filter(Estante.qr_code == qr_code, Estante.activo == True).first()
    if not est:
        return jsonify({'detail': 'Estante no encontrado o QR inválido'}), 404
    return jsonify(_estante_to_dict(est))


@bp.route('/estantes/<qr_code>/inventario', methods=['GET'])
@_require_inventario
def inventario_estante(qr_code: str):
    """Devuelve los productos del estante con su ubicación en la rejilla
    (Pausa 4 + 11). Al escanear el QR del rack se ve qué hay y dónde está.

    Ordenado por celda (fila, columna) y luego código. Cada producto incluye
    `ubicacion: {fila, columna, cantidad}` de su colocación en este estante.
    Lista vacía si el estante no tiene productos asignados."""
    est = Estante.query.filter(Estante.qr_code == qr_code, Estante.activo == True).first()
    if not est:
        return jsonify({'detail': 'Estante no encontrado o QR inválido'}), 404
    filas = (
        db.session.query(ProductoEstante, Producto)
        .join(Producto, Producto.id == ProductoEstante.producto_id)
        .filter(ProductoEstante.estante_id == est.id, Producto.activo == True)
        .order_by(ProductoEstante.fila.asc().nullslast(),
                  ProductoEstante.columna.asc().nullslast(),
                  Producto.codigo.asc())
        .all()
    )
    productos = []
    for pe, p in filas:
        d = _producto_to_dict(p)
        d['ubicacion'] = {
            'fila': pe.fila,
            'columna': pe.columna,
            'cantidad': float(pe.cantidad or 0),
        }
        productos.append(d)
    return jsonify({
        'estante': _estante_to_dict(est),
        'productos': productos,
    })


@bp.route('/estantes/<int:estante_id>/productos', methods=['GET'])
@_require_inventario
def estante_productos(estante_id: int):
    """Lista de productos asignados a un estante (por id, para la UI de admin)."""
    est = Estante.query.get_or_404(estante_id)
    productos = (
        Producto.query
        .join(ProductoEstante, ProductoEstante.producto_id == Producto.id)
        .filter(ProductoEstante.estante_id == est.id)
        .order_by(Producto.codigo)
        .all()
    )
    return jsonify([_producto_to_dict(p) for p in productos])


@bp.route('/estantes/<int:estante_id>/productos', methods=['PUT'])
@_require_inventario_admin
def set_estante_productos(estante_id: int):
    """Reemplaza la lista de productos asignados al estante.
    Body: `{producto_ids: [int]}`. Idempotente."""
    est = Estante.query.get_or_404(estante_id)
    body = request.get_json(silent=True) or {}
    ids = body.get('producto_ids')
    if not isinstance(ids, list):
        return jsonify({'detail': 'producto_ids debe ser una lista'}), 422
    ids = [int(x) for x in ids if isinstance(x, int) or (isinstance(x, str) and x.isdigit())]
    if ids:
        existentes = {p.id for p in Producto.query.filter(Producto.id.in_(ids)).all()}
        faltantes = set(ids) - existentes
        if faltantes:
            return jsonify({'detail': f'Productos inexistentes: {sorted(faltantes)}'}), 404

    # Preserva la posición/cantidad ya capturada para los productos que siguen
    # asignados (no queremos que el flujo "rápido" borre el acomodo de la
    # rejilla). Solo se eliminan los que ya no están en la lista; los nuevos
    # entran "sin ubicar" (fila/columna NULL, cantidad 0).
    nuevos = set(ids)
    existentes = {pe.producto_id: pe for pe in est.productos_asignados}
    for pid, pe in existentes.items():
        if pid not in nuevos:
            db.session.delete(pe)
    for pid in nuevos:
        if pid not in existentes:
            db.session.add(ProductoEstante(producto_id=pid, estante_id=est.id))
    _audit(request.current_user, f"Estante #{est.id} ({est.nombre}): {len(nuevos)} productos asignados")
    db.session.commit()
    emit_to_role(_INV_ROLES, 'estante:changed', {
        'id': est.id, 'almacen_id': est.almacen_id, 'action': 'productos_asignados',
    })
    return jsonify({'success': True, 'asignados': len(set(ids))})


# ─── Rejilla visual + stock por celda (Pausa 11) ─────────────────────────────

def _producto_estante_to_dict(pe: ProductoEstante) -> dict:
    """Una colocación: el producto + su posición y cantidad en la celda."""
    return {
        'producto': _producto_to_dict(pe.producto) if pe.producto else None,
        'producto_id': pe.producto_id,
        'fila': pe.fila,
        'columna': pe.columna,
        'cantidad': float(pe.cantidad or 0),
    }


@bp.route('/estantes/<int:estante_id>/layout', methods=['GET'])
@_require_inventario
def get_estante_layout(estante_id: int):
    """Rejilla del estante para la vista visual.

    Devuelve dimensiones, las colocaciones (producto + fila/columna + cantidad)
    y, por producto, el stock del almacén y cuánto sobra colocado de más
    (`Σceldas − stock`) para marcar "necesita reacomodo".
    """
    est = Estante.query.filter(Estante.id == estante_id).first()
    if not est:
        return jsonify({'detail': 'Estante no encontrado'}), 404

    colocaciones = (
        ProductoEstante.query
        .filter(ProductoEstante.estante_id == est.id)
        .all()
    )
    celdas = [_producto_estante_to_dict(pe) for pe in colocaciones]
    pids = [pe.producto_id for pe in colocaciones]
    stock_almacen = _producto_almacen_stock(pids, est.almacen_id)

    # Sobrante por producto: lo colocado en TODO el almacén que excede el stock.
    sobrante = {}
    for pid in set(pids):
        colocado = _cantidad_en_celdas_almacen(pid, est.almacen_id)
        exceso = colocado - stock_almacen.get(pid, Decimal('0'))
        if exceso > 0:
            sobrante[pid] = float(exceso)

    return jsonify({
        'estante': _estante_to_dict(est),
        'celdas': celdas,
        'stock_almacen': {str(k): float(v) for k, v in stock_almacen.items()},
        'sobrante_por_producto': sobrante,
    })


@bp.route('/estantes/<int:estante_id>/layout', methods=['PUT'])
@_require_inventario_admin
def set_estante_layout(estante_id: int):
    """Reemplaza el acomodo del estante: `{posiciones: [{producto_id, fila,
    columna, cantidad}]}`. Idempotente.

    Validaciones:
      - fila/columna dentro de la rejilla (o ambos NULL = sin ubicar).
      - producto_id existe.
      - Invariante por producto: cantidad_aquí + Σ(otros estantes del almacén)
        ≤ StockPorAlmacen(producto, almacén). 422 con detalle si no alcanza.
    """
    est = Estante.query.filter(Estante.id == estante_id).first()
    if not est:
        return jsonify({'detail': 'Estante no encontrado'}), 404

    data, err = _parse_or_422(EstanteLayoutSchema(), request.get_json(silent=True))
    if err: return err

    posiciones = data['posiciones']
    nf, nc = est.filas or 1, est.columnas or 1

    # Validación de rangos + posición XOR (fila y columna van juntas).
    vistos = set()
    limpias = []
    for item in posiciones:
        pid = item['producto_id']
        if pid in vistos:
            return jsonify({'detail': f'Producto #{pid} repetido en el layout'}), 422
        vistos.add(pid)
        fila, col = item.get('fila'), item.get('columna')
        if (fila is None) != (col is None):
            return jsonify({'detail': f'Producto #{pid}: fila y columna deben venir juntas (o ambas vacías)'}), 422
        if fila is not None and (fila > nf or col > nc):
            return jsonify({
                'detail': f'Producto #{pid}: posición (f{fila},c{col}) fuera de la rejilla {nf}×{nc}'
            }), 422
        limpias.append({
            'producto_id': pid,
            'fila': fila,
            'columna': col,
            'cantidad': Decimal(str(item.get('cantidad') or 0)),
        })

    if vistos:
        existentes = {p.id for p in Producto.query.filter(Producto.id.in_(vistos)).all()}
        faltantes = vistos - existentes
        if faltantes:
            return jsonify({'detail': f'Productos inexistentes: {sorted(faltantes)}'}), 404

    # Invariante de stock: lo que se coloca aquí + lo ya colocado en otros
    # estantes del mismo almacén no puede exceder el stock del producto en ese
    # almacén. Mismo estilo de error que _intentar_reservar.
    stock_almacen = _producto_almacen_stock(list(vistos), est.almacen_id)
    errores = []
    for item in limpias:
        pid = item['producto_id']
        en_otros = _cantidad_en_celdas_almacen(pid, est.almacen_id, excluir_estante_id=est.id)
        total = en_otros + item['cantidad']
        disponible = stock_almacen.get(pid, Decimal('0'))
        if total > disponible:
            prod = Producto.query.get(pid)
            etiqueta = f"{prod.codigo} — {prod.descripcion}" if prod else f"#{pid}"
            errores.append(
                f"{etiqueta}: quieres colocar {item['cantidad']} aquí + {en_otros} en otros "
                f"estantes = {total}, pero el almacén solo tiene {disponible}."
            )
    if errores:
        return jsonify({
            'detail': 'No se puede guardar: colocas más unidades de las que hay en el almacén',
            'errores': errores,
        }), 422

    # Reemplazo: borra las colocaciones de ESTE estante y reinserta.
    ProductoEstante.query.filter_by(estante_id=est.id).delete()
    for item in limpias:
        db.session.add(ProductoEstante(
            producto_id=item['producto_id'],
            estante_id=est.id,
            fila=item['fila'],
            columna=item['columna'],
            cantidad=item['cantidad'],
        ))
    _audit(request.current_user, f"Estante #{est.id} ({est.nombre}): layout actualizado ({len(limpias)} productos)")
    db.session.commit()
    emit_to_role(_INV_ROLES, 'estante:changed', {
        'id': est.id, 'almacen_id': est.almacen_id, 'action': 'layout',
    })
    return jsonify({'success': True, 'colocados': len(limpias)})


@bp.route('/estantes/<int:estante_id>/qr-image', methods=['GET'])
@_require_inventario
def get_estante_qr_image(estante_id: int):
    est = Estante.query.filter(Estante.id == estante_id).first()
    if not est:
        return jsonify({'detail': 'Estante no encontrado'}), 404

    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(est.qr_code)
    qr.make(fit=True)
    img = qr.make_image(fill_color='black', back_color='white')

    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return Response(buf.getvalue(), mimetype='image/png')
