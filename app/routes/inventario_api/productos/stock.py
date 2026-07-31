"""Existencias de un producto: ajuste por bucket, desglose por bodega y
disponibilidad por proyecto."""
from decimal import Decimal

from flask import jsonify, request

from app.extensions import db
from app.models import (
    Almacen, Producto, Proyecto, SolicitudMaterial, SolicitudMaterialDetalle,
    StockAlmacenProyecto, StockPorAlmacen, User,
)
from app.realtime import emit_to_role

from .._core import (
    bp,
    ErrorDeNegocio, transaccion_de_stock,
    _require_inventario_admin,
    _parse_or_422,
    AjusteBucketsSchema,
    _producto_to_dict,
    _audit,
    _ajustar_bucket, _stock_proyecto_total, _reserva_derivada,
    _INV_ROLES,
)
from .reglas import _validar_stock_entero


def _cantidad_en_bucket(producto_id: int, almacen_id: int, proyecto_id: int | None) -> Decimal:
    """Cantidad que hay hoy en el bucket (producto, almacén, proyecto|general)."""
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
    return Decimal(str(actual or 0))


@bp.route('/productos/<int:producto_id>/ajustar-buckets', methods=['POST'])
@_require_inventario_admin
@transaccion_de_stock
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

    prod = (
        Producto.query
        .with_for_update(nowait=True)
        .filter(Producto.id == producto_id, Producto.activo == True)  # noqa: E712
        .first()
    )
    if not prod:
        raise ErrorDeNegocio('Producto no encontrado', 404)

    # Regla de decimales por unidad sobre las cantidades objetivo.
    err_dec = _validar_stock_entero(prod.unidad, *[b['cantidad_objetivo'] for b in buckets])
    if err_dec:
        raise ErrorDeNegocio(err_dec, 422)

    cambios = 0
    for b in buckets:
        almacen_id = b['almacen_id']
        proyecto_id = b.get('proyecto_id')
        objetivo = Decimal(str(b['cantidad_objetivo']))
        delta = objetivo - _cantidad_en_bucket(producto_id, almacen_id, proyecto_id)
        if delta == 0:
            continue
        err_bucket = _ajustar_bucket(prod, almacen_id, proyecto_id, delta, user, motivo)
        if err_bucket:
            raise ErrorDeNegocio(f'No se pudo ajustar el bucket: {err_bucket}', 409)
        cambios += 1

    # Guard global: no dejar el total por debajo de lo apartado (reservas).
    reservado = Decimal(str(prod.stock_reservado or 0))
    total_post = Decimal(str(prod.stock_actual or 0))
    if total_post < reservado:
        raise ErrorDeNegocio(
            f'El ajuste dejaría el stock total en {total_post}, por debajo de lo '
            f'apartado por solicitudes aprobadas ({reservado}). Libera/rechaza '
            f'solicitudes antes de reducir tanto.', 409,
        )

    if cambios:
        _audit(user, f"Ajuste de buckets producto #{producto_id}: {cambios} bucket(s) — {motivo}")
    db.session.commit()

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


@bp.route('/productos/disponibilidad-buckets', methods=['GET'])
@_require_inventario_admin
def get_disponibilidad_buckets():
    """Cuánto puede salir de una bodega, separado en «del proyecto» y «libre».

    Existe porque las pantallas de varias líneas —entrega directa, entrega de
    una solicitud— validaban contra el stock GLOBAL del producto mientras el
    backend descuenta POR BUCKET. El resultado era decirle al usuario que hay
    500 y luego fallar con «disponible 60 (proyecto 40 + general 20)» al
    guardar. Aquí se responde la pregunta que esas pantallas necesitan hacer,
    para todas sus líneas de una vez en lugar de una petición por renglón.

    Se devuelven los DOS totales porque el sistema usa dos reglas distintas y
    llamarlas por su regla —no por el tipo de movimiento— evita repetir en el
    cliente la tabla de qué tipo usa cuál:

      con_fallback = proyecto + general   SALIDA, AJUSTE−, entregas
      exacto       = solo el bucket       TRASPASO, REASIGNACION

    Sin `proyecto_id` ambos valen lo mismo: el bucket general.
    """
    ids_crudos = (request.args.get('ids') or '').strip()
    if not ids_crudos:
        return jsonify({'detail': 'Se requiere ids'}), 422
    try:
        ids = [int(x) for x in ids_crudos.split(',') if x.strip()]
    except ValueError:
        return jsonify({'detail': 'ids debe ser una lista de enteros separados por coma'}), 422
    if not ids:
        return jsonify({'detail': 'Se requiere al menos un id'}), 422
    # Tope alineado con el máximo de líneas de una entrega. Sin él, una URL
    # larga podría pedir el catálogo entero en una sola consulta.
    if len(ids) > 500:
        return jsonify({'detail': 'Máximo 500 productos por consulta'}), 422

    # `almacen_id` es OPCIONAL. Con bodega se responde «¿puedo mover esto desde
    # aquí?» —la pregunta de un movimiento o una entrega—. Sin ella se suma
    # todas las bodegas activas y se responde «¿existe esto para este proyecto,
    # en algún lado?», que es la pregunta al pedir material: una solicitud aún
    # no elige bodega. En ese caso `exacto` deja de servir para validar un
    # traspaso (que sí es por bodega) y solo indica cuánto tiene el proyecto.
    almacen_id = request.args.get('almacen_id', type=int)
    if almacen_id and not Almacen.query.filter(
        Almacen.id == almacen_id, Almacen.activo == True,  # noqa: E712
    ).first():
        return jsonify({'detail': 'Almacén no encontrado'}), 404

    proyecto_id = request.args.get('proyecto_id', type=int)
    if proyecto_id and not db.session.get(Proyecto, proyecto_id):
        return jsonify({'detail': 'Proyecto no encontrado'}), 404

    filas = (
        db.session.query(
            StockAlmacenProyecto.producto_id,
            StockAlmacenProyecto.proyecto_id,
            StockAlmacenProyecto.cantidad,
        )
        .join(Almacen, Almacen.id == StockAlmacenProyecto.almacen_id)
        .filter(
            StockAlmacenProyecto.producto_id.in_(ids),
            Almacen.activo == True,  # noqa: E712
            db.or_(
                StockAlmacenProyecto.proyecto_id.is_(None),
                StockAlmacenProyecto.proyecto_id == proyecto_id,
            ) if proyecto_id else StockAlmacenProyecto.proyecto_id.is_(None),
        )
    )
    if almacen_id:
        filas = filas.filter(StockAlmacenProyecto.almacen_id == almacen_id)
    filas = filas.all()

    buckets = {}
    for pid, proy, cant in filas:
        d = buckets.setdefault(pid, {'proyecto': Decimal('0'), 'general': Decimal('0')})
        d['general' if proy is None else 'proyecto'] += Decimal(str(cant or 0))

    productos = {
        p.id: p for p in
        Producto.query.filter(Producto.id.in_(ids), Producto.activo == True).all()  # noqa: E712
    }

    items = []
    for pid in ids:
        p = productos.get(pid)
        if not p:
            continue
        d = buckets.get(pid, {'proyecto': Decimal('0'), 'general': Decimal('0')})
        items.append({
            'producto_id': pid,
            'codigo': p.codigo,
            'unidad': p.unidad,
            'proyecto': float(d['proyecto']),
            'general': float(d['general']),
            'con_fallback': float(d['proyecto'] + d['general']),
            'exacto': float(d['proyecto'] if proyecto_id else d['general']),
        })

    return jsonify({
        'almacen_id': almacen_id,
        'proyecto_id': proyecto_id,
        'items': items,
    })
