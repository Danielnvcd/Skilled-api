"""CRUD de unidades de herramienta + eventos + QR.

Registra:
  /herramientas-unidades/                       GET, POST
  /herramientas-unidades/<int:uid>              GET, PUT
  /herramientas-unidades/<int:uid>/eventos      GET
  /herramientas-unidades/<int:uid>/qr-image     GET
  /herramientas-unidades/<qr_code>/validar      GET
"""
import io
import uuid
from decimal import Decimal

import qrcode
from flask import jsonify, request, Response
from sqlalchemy import or_
from sqlalchemy.orm import joinedload, selectinload

from app.extensions import db
from app.realtime import emit_to_role
from app.models import (
    Almacen, Estante,
    Herramienta, HerramientaUnidad,
    EventoHerramienta,
    ESTADOS_UNIDAD,
    crear_evento_herramienta,
)
from ._core import (
    bp,
    _HERR_ROLES,
    _require_login,
    _require_inventario,
    _require_inventario_admin,
    _parse_or_422,
    _int_arg,
    _audit,
    UnidadCreateSchema,
    UnidadUpdateSchema,
    _unidad_to_dict,
    _asignacion_to_dict,
    _media_to_dict,
    _evento_to_dict,
    _next_codigo_interno,
    _puede_ver_unidad,
    _redactar_para_rol,
)


# ─── Unidades ────────────────────────────────────────────────────────────────

@bp.route('/herramientas-unidades/', methods=['GET'])
@_require_login
def list_unidades():
    user = request.current_user
    if user.role not in ('inventario', 'admin', 'super_admin', 'solicitante_material', 'coordinador'):
        return jsonify({'detail': 'Forbidden'}), 403

    skip, err = _int_arg('skip', 0, 0, 1_000_000)
    if err: return err
    limit, err = _int_arg('limit', 200, 0, 1000)
    if err: return err

    estado = request.args.get('estado', type=str)
    herramienta_id = request.args.get('herramienta_id', type=int)
    almacen_id = request.args.get('almacen_id', type=int)
    trabajador_id = request.args.get('trabajador_id', type=int)
    q = request.args.get('q', '', type=str).strip()
    solo_mias = request.args.get('solo_mias', '0') == '1'

    query = HerramientaUnidad.query.options(
        joinedload(HerramientaUnidad.herramienta),
        joinedload(HerramientaUnidad.almacen),
        joinedload(HerramientaUnidad.estante),
        joinedload(HerramientaUnidad.asignado_trabajador),
        selectinload(HerramientaUnidad.media),
    )

    # Roles "solicitantes" (solicitante_material, coordinador) solo ven las
    # unidades asignadas a su propio trabajador.
    if user.role in ('solicitante_material', 'coordinador'):
        if not user.trabajador_id:
            return jsonify([])
        query = query.filter(HerramientaUnidad.asignado_trabajador_id == user.trabajador_id)
    elif solo_mias and user.trabajador_id:
        query = query.filter(HerramientaUnidad.asignado_trabajador_id == user.trabajador_id)

    if estado:
        if estado not in ESTADOS_UNIDAD:
            return jsonify({'detail': 'estado inválido'}), 422
        query = query.filter(HerramientaUnidad.estado == estado)
    if herramienta_id:
        query = query.filter(HerramientaUnidad.herramienta_id == herramienta_id)
    if almacen_id:
        query = query.filter(HerramientaUnidad.almacen_id == almacen_id)
    if trabajador_id:
        query = query.filter(HerramientaUnidad.asignado_trabajador_id == trabajador_id)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(
            HerramientaUnidad.no_serie.ilike(like),
            HerramientaUnidad.codigo_interno.ilike(like),
            HerramientaUnidad.complementos.ilike(like),
        ))

    unidades = query.order_by(HerramientaUnidad.codigo_interno).offset(skip).limit(limit).all()
    redactar = _redactar_para_rol(user)
    return jsonify([_unidad_to_dict(u, incluir_relacion=True, redactar=redactar) for u in unidades])


@bp.route('/herramientas-unidades/<int:uid>', methods=['GET'])
@_require_login
def get_unidad(uid: int):
    u = (
        HerramientaUnidad.query
        .options(joinedload(HerramientaUnidad.herramienta),
                 joinedload(HerramientaUnidad.almacen),
                 joinedload(HerramientaUnidad.estante),
                 joinedload(HerramientaUnidad.asignado_trabajador),
                 selectinload(HerramientaUnidad.media))
        .filter(HerramientaUnidad.id == uid).first()
    )
    if not u:
        return jsonify({'detail': 'Unidad no encontrada'}), 404
    if not _puede_ver_unidad(request.current_user, u):
        return jsonify({'detail': 'Forbidden'}), 403

    data = _unidad_to_dict(u, incluir_relacion=True, redactar=_redactar_para_rol(request.current_user))
    asignacion_activa = next((a for a in u.asignaciones if a.estado == 'ACTIVA'), None)
    data['asignacion_activa'] = _asignacion_to_dict(asignacion_activa) if asignacion_activa else None
    data['fotos'] = [_media_to_dict(m) for m in u.media if m.tipo == 'FOTO_HERRAMIENTA']
    return jsonify(data)


@bp.route('/herramientas-unidades/', methods=['POST'])
@_require_inventario_admin
def create_unidad():
    data, err = _parse_or_422(UnidadCreateSchema(), request.get_json(silent=True))
    if err: return err

    h = Herramienta.query.filter(Herramienta.id == data['herramienta_id'],
                                  Herramienta.activo == True).first()
    if not h:
        return jsonify({'detail': 'Herramienta no encontrada o inactiva'}), 404

    if h.serializada and not data.get('no_serie'):
        return jsonify({'detail': 'no_serie requerido para herramientas serializadas'}), 422

    if data.get('no_serie') and HerramientaUnidad.query.filter(
        HerramientaUnidad.no_serie == data['no_serie']
    ).first():
        return jsonify({'detail': 'no_serie ya existe en otra unidad'}), 400

    if data.get('almacen_id') and not Almacen.query.filter(Almacen.id == data['almacen_id']).first():
        return jsonify({'detail': 'almacen_id no existe'}), 404
    if data.get('estante_id') and not Estante.query.filter(Estante.id == data['estante_id']).first():
        return jsonify({'detail': 'estante_id no existe'}), 404

    user = request.current_user
    nueva = HerramientaUnidad(
        herramienta_id=h.id,
        no_serie=data.get('no_serie') or None,
        codigo_interno=_next_codigo_interno(),
        qr_code=str(uuid.uuid4()),
        estado='DISPONIBLE',
        almacen_id=data.get('almacen_id'),
        estante_id=data.get('estante_id'),
        cantidad=Decimal(str(data.get('cantidad', 1))),
        complementos=data.get('complementos'),
        fecha_adquisicion=data.get('fecha_adquisicion'),
        costo_adquisicion=Decimal(str(data['costo_adquisicion'])) if data.get('costo_adquisicion') is not None else None,
        vida_util_meses=data.get('vida_util_meses'),
        observaciones=data.get('observaciones'),
    )
    db.session.add(nueva)
    db.session.flush()
    # Si el código interno chocó por race condition, intentar uno nuevo
    crear_evento_herramienta(
        nueva, 'ALTA', user,
        observaciones=f"Unidad creada (serie: {nueva.no_serie or 'N/A'})",
        estado_nuevo='DISPONIBLE',
    )
    _audit(user, f"Unidad herramienta creada: {nueva.codigo_interno} (herr #{h.id})")
    db.session.commit()
    db.session.refresh(nueva)
    emit_to_role(_HERR_ROLES, 'herramienta:changed', {
        'id': h.id, 'unidad_id': nueva.id, 'action': 'unidad_created',
    })
    return jsonify(_unidad_to_dict(nueva, incluir_relacion=True)), 201


def _validar_ubicacion(u: HerramientaUnidad, data: dict):
    """Valida que, TRAS el update, el estante de la unidad siga perteneciendo a
    su bodega. Devuelve una respuesta de error, o None si todo cuadra.

    Se valida el RESULTADO, no solo lo que trae el payload: mover la unidad de
    bodega mandando únicamente `almacen_id` dejaba colgado el estante anterior
    —la unidad quedaba diciendo que está en la bodega B, en un estante que
    físicamente está en la A—. No hay constraint en base que lo impida, así que
    este guard es el único que sostiene el invariante.

    Solo se valida cuando el PUT toca la ubicación: una edición de otros campos
    no debe rebotar por una incoherencia que ya estuviera guardada.
    """
    almacen_pedido = data.get('almacen_id')
    estante_pedido = data.get('estante_id')
    if almacen_pedido is None and estante_pedido is None:
        return None

    if almacen_pedido and not Almacen.query.filter(Almacen.id == almacen_pedido).first():
        return jsonify({'detail': 'almacen_id no existe'}), 404

    almacen_final = almacen_pedido if almacen_pedido is not None else u.almacen_id
    estante_final = estante_pedido if estante_pedido is not None else u.estante_id
    if not estante_final:
        return None

    est = Estante.query.filter(Estante.id == estante_final).first()
    if est is None:
        # Un estante_id inválido en el payload es error del cliente; uno ya
        # guardado que desapareció no debe bloquear la edición.
        if estante_pedido:
            return jsonify({'detail': 'estante_id no existe'}), 404
        return None

    if almacen_final and est.almacen_id != almacen_final:
        if estante_pedido:
            return jsonify({'detail': 'estante_id no pertenece al almacen_id indicado'}), 422
        # Cambió la bodega y el estante que ya traía la unidad es de otra:
        # el cliente debe decidir a qué estante va (o vaciarlo) en la misma
        # petición, no dejamos la unidad en un estado imposible.
        return jsonify({
            'detail': (
                f"La unidad está colocada en el estante '{est.nombre}', que pertenece "
                f"a otra bodega. Indica también el estante destino (estante_id) al "
                f"cambiar de bodega."
            ),
        }), 422
    return None


@bp.route('/herramientas-unidades/<int:uid>', methods=['PUT'])
@_require_inventario_admin
def update_unidad(uid: int):
    data, err = _parse_or_422(UnidadUpdateSchema(), request.get_json(silent=True))
    if err: return err
    u = HerramientaUnidad.query.filter(HerramientaUnidad.id == uid).first()
    if not u:
        return jsonify({'detail': 'Unidad no encontrada'}), 404

    if u.estado == 'DADA_DE_BAJA':
        return jsonify({'detail': 'No se puede editar una unidad dada de baja'}), 400

    if data.get('no_serie') and data['no_serie'] != u.no_serie:
        if HerramientaUnidad.query.filter(HerramientaUnidad.no_serie == data['no_serie'],
                                           HerramientaUnidad.id != uid).first():
            return jsonify({'detail': 'no_serie ya existe en otra unidad'}), 400
        u.no_serie = data['no_serie']

    err = _validar_ubicacion(u, data)
    if err: return err

    for campo in ('almacen_id', 'estante_id', 'complementos', 'fecha_adquisicion',
                  'vida_util_meses', 'observaciones'):
        if data.get(campo) is not None:
            setattr(u, campo, data[campo])
    if data.get('cantidad') is not None:
        u.cantidad = Decimal(str(data['cantidad']))
    if data.get('costo_adquisicion') is not None:
        u.costo_adquisicion = Decimal(str(data['costo_adquisicion']))

    crear_evento_herramienta(u, 'EDICION', request.current_user,
                              observaciones='Datos de unidad actualizados')
    _audit(request.current_user, f"Unidad #{uid} editada")
    db.session.commit()
    db.session.refresh(u)
    emit_to_role(_HERR_ROLES, 'herramienta:changed', {
        'id': u.herramienta_id, 'unidad_id': uid, 'action': 'unidad_updated',
    })
    return jsonify(_unidad_to_dict(u, incluir_relacion=True))


@bp.route('/herramientas-unidades/<int:uid>/eventos', methods=['GET'])
@_require_login
def get_eventos_unidad(uid: int):
    u = HerramientaUnidad.query.filter(HerramientaUnidad.id == uid).first()
    if not u:
        return jsonify({'detail': 'Unidad no encontrada'}), 404
    if not _puede_ver_unidad(request.current_user, u):
        return jsonify({'detail': 'Forbidden'}), 403
    eventos = (
        EventoHerramienta.query
        .options(joinedload(EventoHerramienta.usuario))
        .filter(EventoHerramienta.unidad_id == uid)
        .order_by(EventoHerramienta.fecha.desc())
        .limit(500).all()
    )
    return jsonify([_evento_to_dict(e) for e in eventos])


@bp.route('/herramientas-unidades/<int:uid>/qr-image', methods=['GET'])
@_require_inventario
def get_unidad_qr_image(uid: int):
    u = HerramientaUnidad.query.filter(HerramientaUnidad.id == uid).first()
    if not u:
        return jsonify({'detail': 'Unidad no encontrada'}), 404
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(u.qr_code)
    qr.make(fit=True)
    img = qr.make_image(fill_color='black', back_color='white')
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return Response(buf.getvalue(), mimetype='image/png')


@bp.route('/herramientas-unidades/<qr_code>/validar', methods=['GET'])
@_require_login
def validar_unidad_qr(qr_code: str):
    u = (
        HerramientaUnidad.query
        .options(joinedload(HerramientaUnidad.herramienta))
        .filter(HerramientaUnidad.qr_code == qr_code).first()
    )
    if not u:
        return jsonify({'detail': 'QR no encontrado'}), 404
    if not _puede_ver_unidad(request.current_user, u):
        return jsonify({'detail': 'Forbidden'}), 403
    return jsonify(_unidad_to_dict(u, incluir_relacion=True, redactar=_redactar_para_rol(request.current_user)))
