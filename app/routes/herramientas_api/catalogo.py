"""Catálogo de herramientas y categorías.

Registra:
  /herramientas/                       GET, POST
  /herramientas/<int:hid>              GET, PUT, DELETE
  /herramientas/clasificaciones        GET
  /herramientas/stats                  GET
  /herramientas-categorias/            GET
  /herramientas-categorias/<nombre>    PUT
"""
import datetime

from flask import jsonify, request, Response
from sqlalchemy import or_, func
from sqlalchemy.orm import selectinload

from app.extensions import db
from app.realtime import emit_to_role
from app.models import (
    Herramienta, HerramientaUnidad, HerramientaCategoria,
    AsignacionHerramienta, IncidenciaHerramienta, SolicitudBajaHerramienta,
    ESTADOS_UNIDAD,
)
from app.routes.inventario_api import (
    _require_login, _require_inventario, _require_inventario_admin,
    _parse_or_422, _int_arg, _audit,
)
from ._core import (
    bp, _HERR_ROLES,
    HerramientaCreateSchema, HerramientaUpdateSchema, CategoriaUpsertSchema,
    _herramienta_to_dict, _asignacion_to_dict, _categoria_to_dict,
)


# ─── Catálogo ────────────────────────────────────────────────────────────────

@bp.route('/herramientas/', methods=['GET'])
@_require_inventario
def list_herramientas():
    q = request.args.get('q', '', type=str).strip()
    clasif = request.args.get('clasificacion', '', type=str).strip()
    serializada = request.args.get('serializada', type=str)
    incluir_inactivas = request.args.get('incluir_inactivas', '0') == '1'

    skip, err = _int_arg('skip', 0, 0, 1_000_000)
    if err: return err
    limit, err = _int_arg('limit', 200, 0, 1000)
    if err: return err

    query = Herramienta.query
    if not incluir_inactivas:
        query = query.filter(Herramienta.activo == True)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(
            Herramienta.sku.ilike(like),
            Herramienta.descripcion.ilike(like),
            Herramienta.marca.ilike(like),
            Herramienta.modelo.ilike(like),
        ))
    if clasif:
        query = query.filter(Herramienta.clasificacion == clasif)
    if serializada in ('true', 'false'):
        query = query.filter(Herramienta.serializada == (serializada == 'true'))

    herramientas = (
        query.options(selectinload(Herramienta.unidades))
             .order_by(Herramienta.descripcion)
             .offset(skip).limit(limit).all()
    )
    return jsonify([_herramienta_to_dict(h, incluir_stats=True) for h in herramientas])


@bp.route('/herramientas/<int:hid>', methods=['GET'])
@_require_inventario
def get_herramienta(hid: int):
    h = Herramienta.query.options(selectinload(Herramienta.unidades)).filter(Herramienta.id == hid).first()
    if not h:
        return jsonify({'detail': 'Herramienta no encontrada'}), 404
    return jsonify(_herramienta_to_dict(h, incluir_stats=True))


@bp.route('/herramientas/', methods=['POST'])
@_require_inventario_admin
def create_herramienta():
    data, err = _parse_or_422(HerramientaCreateSchema(), request.get_json(silent=True))
    if err: return err

    if Herramienta.query.filter(Herramienta.sku == data['sku']).first():
        return jsonify({'detail': 'El SKU ya existe'}), 400

    user = request.current_user
    nueva = Herramienta(
        sku=data['sku'],
        descripcion=data['descripcion'],
        clasificacion=data['clasificacion'],
        marca=data.get('marca'),
        modelo=data.get('modelo'),
        uso=data.get('uso', 'OTRO'),
        unidad=data['unidad'],
        piezas=data.get('piezas', 1),
        serializada=data.get('serializada', True),
        imagen_url=data.get('imagen_url') or None,
        created_by_id=user.id,
    )
    db.session.add(nueva)
    _audit(user, f"Herramienta creada: {data['sku']} — {data['descripcion']}")
    db.session.commit()
    db.session.refresh(nueva)
    emit_to_role(_HERR_ROLES, 'herramienta:changed', {
        'id': nueva.id, 'action': 'created',
    })
    return jsonify(_herramienta_to_dict(nueva, incluir_stats=True)), 201


@bp.route('/herramientas/<int:hid>', methods=['PUT'])
@_require_inventario_admin
def update_herramienta(hid: int):
    data, err = _parse_or_422(HerramientaUpdateSchema(), request.get_json(silent=True))
    if err: return err

    h = Herramienta.query.filter(Herramienta.id == hid, Herramienta.activo == True).first()
    if not h:
        return jsonify({'detail': 'Herramienta no encontrada'}), 404

    if data.get('sku') and data['sku'] != h.sku:
        if Herramienta.query.filter(Herramienta.sku == data['sku']).first():
            return jsonify({'detail': 'SKU ya existe'}), 400
        h.sku = data['sku']
    for campo in ('descripcion', 'clasificacion', 'marca', 'modelo', 'uso', 'unidad', 'piezas'):
        if data.get(campo) is not None:
            setattr(h, campo, data[campo])
    if data.get('imagen_url') is not None:
        h.imagen_url = data['imagen_url'] or None

    _audit(request.current_user, f"Herramienta #{hid} editada")
    db.session.commit()
    db.session.refresh(h)
    emit_to_role(_HERR_ROLES, 'herramienta:changed', {
        'id': h.id, 'action': 'updated',
    })
    return jsonify(_herramienta_to_dict(h, incluir_stats=True))


@bp.route('/herramientas/<int:hid>', methods=['DELETE'])
@_require_inventario_admin
def soft_delete_herramienta(hid: int):
    h = Herramienta.query.filter(Herramienta.id == hid).first()
    if not h:
        return jsonify({'detail': 'Herramienta no encontrada'}), 404

    bloqueantes = (
        HerramientaUnidad.query
        .filter(HerramientaUnidad.herramienta_id == hid,
                HerramientaUnidad.estado.in_(['ASIGNADA', 'EN_MANTENIMIENTO', 'EXTRAVIADA']))
        .count()
    )
    if bloqueantes > 0:
        return jsonify({'detail': f'No se puede desactivar: {bloqueantes} unidad(es) activa(s)'}), 400

    h.activo = False
    _audit(request.current_user, f"Herramienta #{hid} ({h.sku}) desactivada")
    db.session.commit()
    emit_to_role(_HERR_ROLES, 'herramienta:changed', {
        'id': hid, 'action': 'deleted',
    })
    return Response(status=204)


@bp.route('/herramientas/clasificaciones', methods=['GET'])
@_require_inventario
def list_clasificaciones():
    rows = (
        db.session.query(Herramienta.clasificacion)
        .filter(Herramienta.activo == True, Herramienta.clasificacion != None,
                Herramienta.clasificacion != '')
        .distinct().all()
    )
    cats = db.session.query(HerramientaCategoria.nombre).all()
    nombres = sorted({r[0] for r in rows} | {c[0] for c in cats})
    return jsonify(nombres)


@bp.route('/herramientas-categorias/', methods=['GET'])
@_require_login
def list_categorias_h():
    cats = HerramientaCategoria.query.order_by(HerramientaCategoria.nombre).all()
    return jsonify([_categoria_to_dict(c) for c in cats])


@bp.route('/herramientas-categorias/<string:nombre>', methods=['PUT'])
@_require_inventario_admin
def upsert_categoria_h(nombre: str):
    nombre = (nombre or '').strip()
    if not nombre or len(nombre) > 100:
        return jsonify({'detail': 'Nombre inválido'}), 422
    data, err = _parse_or_422(CategoriaUpsertSchema(), request.get_json(silent=True))
    if err: return err

    cat = HerramientaCategoria.query.filter(HerramientaCategoria.nombre == nombre).first()
    if not cat:
        cat = HerramientaCategoria(nombre=nombre, created_by_id=request.current_user.id)
        db.session.add(cat)
    if data.get('imagen_url') is not None:
        cat.imagen_url = (data['imagen_url'] or '').strip() or None
    if data.get('icono') is not None:
        cat.icono = (data['icono'] or '').strip() or None
    if data.get('color') is not None:
        cat.color = (data['color'] or '').strip() or None
    _audit(request.current_user, f"Categoría herramienta '{nombre}' upsert")
    db.session.commit()
    db.session.refresh(cat)
    return jsonify(_categoria_to_dict(cat))


# ─── Dashboard / stats herramientas ─────────────────────────────────────────

@bp.route('/herramientas/stats', methods=['GET'])
@_require_inventario
def stats_herramientas():
    """Resumen para el InventarioDashboard."""
    total_h = Herramienta.query.filter(Herramienta.activo == True).count()
    estado_counts = dict(
        db.session.query(HerramientaUnidad.estado, func.count(HerramientaUnidad.id))
        .group_by(HerramientaUnidad.estado).all()
    )
    incidencias_abiertas = IncidenciaHerramienta.query.filter(
        IncidenciaHerramienta.estado.in_(['ABIERTA', 'REVISION'])
    ).count()
    solicitudes_baja_pend = SolicitudBajaHerramienta.query.filter(
        SolicitudBajaHerramienta.estado == 'PENDIENTE'
    ).count()
    ahora = datetime.datetime.utcnow()
    en_3_dias = ahora + datetime.timedelta(days=3)
    proximas_devolver = (
        AsignacionHerramienta.query
        .filter(AsignacionHerramienta.estado == 'ACTIVA',
                AsignacionHerramienta.fecha_devolucion_prevista != None,
                AsignacionHerramienta.fecha_devolucion_prevista <= en_3_dias)
        .order_by(AsignacionHerramienta.fecha_devolucion_prevista)
        .limit(10).all()
    )
    return jsonify({
        'total_herramientas': total_h,
        'unidades_por_estado': {e: estado_counts.get(e, 0) for e in ESTADOS_UNIDAD},
        'incidencias_abiertas': incidencias_abiertas,
        'solicitudes_baja_pendientes': solicitudes_baja_pend,
        'proximas_devolver': [_asignacion_to_dict(a) for a in proximas_devolver],
    })
