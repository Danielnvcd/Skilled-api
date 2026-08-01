"""Alta, edición y baja de productos del catálogo."""
from decimal import Decimal

from flask import jsonify, request, Response

from app.extensions import db
from app.models import Almacen, Producto, Proyecto
from app.realtime import emit_to_role

from .._core import (
    bp,
    ErrorDeNegocio, transaccion_de_stock,
    _require_inventario_admin,
    _parse_or_422,
    ProductoCreateSchema, ProductoUpdateSchema,
    _producto_to_dict,
    _audit, _almacen_default_id,
    _depositar, _recalcular_caches,
    _INV_ROLES,
)
from .reglas import _validar_normalizar_cable, _validar_stock_entero


@bp.route('/productos/', methods=['POST'])
@_require_inventario_admin
@transaccion_de_stock
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
        # El precio venía en el schema y lo manda el SPA, pero no se estaba
        # guardando al crear: el alta a mano perdía el precio (quedaba en 0) y
        # solo se podía capturar volviendo a editar el producto. La importación
        # sí lo guardaba, así que los dos caminos daban resultados distintos.
        precio_unitario=Decimal(str(data['precio_unitario'])),
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
                raise ErrorDeNegocio(f'Almacén #{almacen_id} no existe', 422)
            if proyecto_id and not Proyecto.query.filter(Proyecto.id == proyecto_id).first():
                raise ErrorDeNegocio(f'Proyecto #{proyecto_id} no existe', 422)
            # El producto acaba de crearse, así que en la práctica nadie más
            # tiene su bucket bloqueado; el guard (en @transaccion_de_stock) es
            # por consistencia con el resto de endpoints que mutan stock, y por
            # si el almacén destino está siendo tocado por otra operación.
            _depositar(nuevo.id, almacen_id, proyecto_id, stock_inicial)
            _recalcular_caches(nuevo, almacen_id)

    _audit(user, f"Producto creado: {data['codigo']} — {data['descripcion']}")
    db.session.commit()
    db.session.refresh(nuevo)
    # Pipeline de imágenes → R2 (no-op salvo producción con R2 configurado):
    # si la imagen es una URL externa, se marca y se encola su descarga a WebP+R2.
    from ..imagenes import marcar_para_sync, encolar_sync
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
    from ..imagenes import marcar_para_sync, encolar_sync
    if data.get('imagen_url') is not None and marcar_para_sync(prod, prod.imagen_url):
        db.session.commit()
        encolar_sync(request.current_user.id, [prod.id])
    emit_to_role(_INV_ROLES, 'producto:changed', {
        'id': prod.id, 'action': 'updated',
    })
    return jsonify(_producto_to_dict(prod))


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
