"""Endpoints de Almacenes y Estantes."""
import io
import uuid

import qrcode
from flask import jsonify, request, Response

from app.extensions import db
from app.models import Almacen, Estante, Producto, ProductoEstante

from ._core import (
    bp,
    _require_inventario, _require_inventario_admin,
    _parse_or_422,
    AlmacenCreateSchema, AlmacenUpdateSchema,
    EstanteCreateSchema, EstanteUpdateSchema,
    _almacen_to_dict, _estante_to_dict, _producto_to_dict,
    _audit,
    _INV_ROLES,
)
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
    if data.get('almacen_id') is not None:
        almacen = Almacen.query.filter(Almacen.id == data['almacen_id']).first()
        if not almacen:
            return jsonify({'detail': 'Bodega destino no encontrada'}), 404
        est.almacen_id = data['almacen_id']
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
    """Devuelve los productos asignados al estante via ProductoEstante (Pausa 4).
    Si el estante no tiene productos asignados, lista vacía."""
    est = Estante.query.filter(Estante.qr_code == qr_code, Estante.activo == True).first()
    if not est:
        return jsonify({'detail': 'Estante no encontrado o QR inválido'}), 404
    productos = (
        Producto.query
        .join(ProductoEstante, ProductoEstante.producto_id == Producto.id)
        .filter(ProductoEstante.estante_id == est.id, Producto.activo == True)
        .order_by(Producto.codigo)
        .all()
    )
    return jsonify({
        'estante': _estante_to_dict(est),
        'productos': [_producto_to_dict(p) for p in productos],
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

    ProductoEstante.query.filter_by(estante_id=est.id).delete()
    for pid in set(ids):
        db.session.add(ProductoEstante(producto_id=pid, estante_id=est.id))
    _audit(request.current_user, f"Estante #{est.id} ({est.nombre}): {len(set(ids))} productos asignados")
    db.session.commit()
    emit_to_role(_INV_ROLES, 'estante:changed', {
        'id': est.id, 'almacen_id': est.almacen_id, 'action': 'productos_asignados',
    })
    return jsonify({'success': True, 'asignados': len(set(ids))})


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
