"""
Endpoints JSON del módulo de Inventario.

Migración de `app/api_fastapi/` a Flask puro para consolidar la stack en una sola
tecnología. Mantiene exactamente el mismo contrato HTTP (`/api/v1/*`) y los mismos
códigos de respuesta que la versión FastAPI original.

Características:
  - Validación con marshmallow (422 en errores de schema).
  - Rate limit con Flask-Limiter (decorador @limiter.limit, comparte storage Redis).
  - Auth con JWT Bearer token (ver app/routes/api_auth.py). Los decoradores
    _require_inventario / _require_inventario_admin validan rol.
  - CSRF exento del blueprint (ver app/__init__.py): la protección viene del JWT
    en el header Authorization. Aplica el mismo patrón que el resto de blueprints
    API (api_auth, api_trabajadores, etc.) consumidos por el SPA React.
"""
import io
import uuid
import logging
import datetime
from decimal import Decimal, InvalidOperation
from functools import wraps

import qrcode
from flask import Blueprint, jsonify, request, session, Response, abort, send_file
from marshmallow import Schema, fields, validate, ValidationError, EXCLUDE
from sqlalchemy import distinct as sql_distinct
from sqlalchemy.orm import joinedload, selectinload

from app.extensions import db, limiter, get_real_client_ip_flask
from app.models import (
    Almacen, Estante, Producto, MovimientoInventario,
    SolicitudMaterial, SolicitudMaterialDetalle, User, AuditLog, Proyecto,
    CategoriaConfig,
)
from app.utils import log_action

logger = logging.getLogger(__name__)

bp = Blueprint('inventario_api', __name__, url_prefix='/api/v1')


# ─── Auth helpers ─────────────────────────────────────────────────────────────

from app.routes.api_auth import jwt_required
from flask import g

def _require_login(view):
    @wraps(view)
    @jwt_required
    def wrapper(*args, **kwargs):
        request.current_user = g._jwt_user
        return view(*args, **kwargs)
    return wrapper


def _require_inventario(view):
    """Lectura: solicitantes, inventario y admin."""
    @wraps(view)
    @_require_login
    def wrapper(*args, **kwargs):
        if request.current_user.role not in ['inventario', 'solicitante_material', 'admin']:
            log_action(f"API 403 lectura '{request.path}' (rol: {request.current_user.role})")
            return jsonify({'detail': 'Forbidden: Required permissions missing'}), 403
        return view(*args, **kwargs)
    return wrapper


def _require_inventario_admin(view):
    """Escritura/borrado: solo inventario y admin."""
    @wraps(view)
    @_require_login
    def wrapper(*args, **kwargs):
        if request.current_user.role not in ['inventario', 'admin']:
            log_action(f"API 403 escritura '{request.path}' (rol: {request.current_user.role})")
            return jsonify({'detail': 'Se requiere rol de inventario o administrador'}), 403
        return view(*args, **kwargs)
    return wrapper


# ─── Marshmallow schemas ──────────────────────────────────────────────────────

CODIGO_REGEX = r'^[A-Za-z0-9\-_\.\/]+$'

# Anti-SSRF/phishing: cuando guardamos URLs de imagen (categorías, productos) la
# UI las pinta como <img src=...>. Si dejamos cualquier URL, un admin malicioso
# podría meter `javascript:`, `data:text/html`, URLs a otros dominios para
# tracking pixels, o intranet (SSRF si el browser corre detrás de un proxy).
# Forzamos HTTPS + dominios públicos o paths relativos al propio backend.
_IMAGEN_URL_REGEX = r'^(?:https://[A-Za-z0-9.\-_]+(?::\d+)?(?:/[^\s<>"\']*)?|/[A-Za-z0-9.\-_/]+\.(?:png|jpe?g|webp|gif|svg))$'


class _BaseSchema(Schema):
    class Meta:
        # Coincide con el comportamiento por defecto de Pydantic: ignora campos extra.
        unknown = EXCLUDE


class ProductoCreateSchema(_BaseSchema):
    codigo = fields.Str(required=True, validate=[
        validate.Length(min=1, max=50),
        validate.Regexp(CODIGO_REGEX),
    ])
    descripcion = fields.Str(required=True, validate=validate.Length(min=1, max=250))
    categoria = fields.Str(required=True, validate=validate.Length(min=1, max=100))
    unidad = fields.Str(required=True, validate=validate.Length(min=1, max=50))
    stock_actual = fields.Float(load_default=0.0, validate=validate.Range(min=0, max=1_000_000))
    stock_minimo = fields.Float(load_default=0.0, validate=validate.Range(min=0, max=1_000_000))
    imagen_url = fields.Str(load_default=None, allow_none=True, validate=[
        validate.Length(max=500),
        # Anti-XSS/SSRF: solo HTTPS o path absoluto local
        validate.Regexp(_IMAGEN_URL_REGEX, error='imagen_url debe ser HTTPS o un path absoluto a imagen local'),
    ])


class ProductoUpdateSchema(_BaseSchema):
    codigo = fields.Str(load_default=None, allow_none=True, validate=[
        validate.Length(min=1, max=50),
        validate.Regexp(CODIGO_REGEX),
    ])
    descripcion = fields.Str(load_default=None, allow_none=True, validate=validate.Length(min=1, max=250))
    categoria = fields.Str(load_default=None, allow_none=True, validate=validate.Length(min=1, max=100))
    unidad = fields.Str(load_default=None, allow_none=True, validate=validate.Length(min=1, max=50))
    stock_actual = fields.Float(load_default=None, allow_none=True, validate=validate.Range(min=0, max=1_000_000))
    stock_minimo = fields.Float(load_default=None, allow_none=True, validate=validate.Range(min=0, max=1_000_000))
    imagen_url = fields.Str(load_default=None, allow_none=True, validate=[
        validate.Length(max=500),
        validate.Regexp(_IMAGEN_URL_REGEX, error='imagen_url debe ser HTTPS o un path absoluto a imagen local'),
    ])


class AlmacenCreateSchema(_BaseSchema):
    nombre = fields.Str(required=True, validate=validate.Length(min=1, max=100))
    ubicacion = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=250))
    activo = fields.Bool(load_default=True)


class AlmacenUpdateSchema(_BaseSchema):
    nombre = fields.Str(load_default=None, allow_none=True, validate=validate.Length(min=1, max=100))
    ubicacion = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=250))
    activo = fields.Bool(load_default=None, allow_none=True)


class EstanteCreateSchema(_BaseSchema):
    nombre = fields.Str(required=True, validate=validate.Length(min=1, max=100))
    descripcion = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=250))
    almacen_id = fields.Int(required=True)


class EstanteUpdateSchema(_BaseSchema):
    nombre = fields.Str(load_default=None, allow_none=True, validate=validate.Length(min=1, max=100))
    descripcion = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=250))
    almacen_id = fields.Int(load_default=None, allow_none=True)


class MovimientoCreateSchema(_BaseSchema):
    tipo = fields.Str(required=True, validate=validate.OneOf(['ENTRADA', 'SALIDA', 'AJUSTE', 'TRASPASO']))
    producto_id = fields.Int(required=True)
    cantidad = fields.Float(required=True, validate=validate.Range(min=-100_000, max=100_000))
    almacen_origen_id = fields.Int(load_default=None, allow_none=True)
    almacen_destino_id = fields.Int(load_default=None, allow_none=True)
    estante_id = fields.Int(load_default=None, allow_none=True)
    motivo = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=250))


class SolicitudDetalleCreateSchema(_BaseSchema):
    producto_id = fields.Int(required=True)
    cantidad_solicitada = fields.Float(required=True, validate=validate.Range(min=0.0001, max=10_000))


class SolicitudCreateSchema(_BaseSchema):
    proyecto = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=200))
    detalles = fields.List(
        fields.Nested(SolicitudDetalleCreateSchema),
        required=True,
        validate=validate.Length(min=1, max=100),
    )


class SolicitudUpdateEstadoSchema(_BaseSchema):
    estatus = fields.Str(required=True, validate=validate.OneOf(['APROBADA', 'RECHAZADA', 'ENTREGADA', 'PENDIENTE']))


class CategoriaConfigUpsertSchema(_BaseSchema):
    imagen_url = fields.Str(load_default=None, allow_none=True, validate=[
        validate.Length(max=500),
        validate.Regexp(_IMAGEN_URL_REGEX, error='imagen_url debe ser HTTPS o un path absoluto a imagen local'),
    ])


# ─── Serializers ──────────────────────────────────────────────────────────────

def _producto_to_dict(p: Producto) -> dict:
    return {
        'id': p.id,
        'codigo': p.codigo,
        'descripcion': p.descripcion,
        'categoria': p.categoria,
        'unidad': p.unidad,
        'stock_actual': float(p.stock_actual or 0),
        'stock_minimo': float(p.stock_minimo or 0),
        'imagen_url': p.imagen_url,
        'activo': bool(p.activo),
        'created_at': p.created_at.isoformat() if p.created_at else None,
        'updated_at': p.updated_at.isoformat() if p.updated_at else None,
        'created_by_id': p.created_by_id,
    }


def _almacen_to_dict(a: Almacen) -> dict:
    return {
        'id': a.id,
        'nombre': a.nombre,
        'ubicacion': a.ubicacion,
        'activo': bool(a.activo),
        'qr_code': a.qr_code,
    }


def _estante_to_dict(e: Estante) -> dict:
    return {
        'id': e.id,
        'nombre': e.nombre,
        'descripcion': e.descripcion,
        'almacen_id': e.almacen_id,
        'qr_code': e.qr_code,
        'activo': bool(e.activo),
        'created_at': e.created_at.isoformat() if e.created_at else None,
    }


def _movimiento_to_dict(m: MovimientoInventario) -> dict:
    return {
        'id': m.id,
        'tipo': m.tipo,
        'producto_id': m.producto_id,
        'cantidad': float(m.cantidad or 0),
        'fecha': m.fecha.isoformat() if m.fecha else None,
        'almacen_origen_id': m.almacen_origen_id,
        'almacen_destino_id': m.almacen_destino_id,
        'usuario_id': m.usuario_id,
        'motivo': m.motivo,
    }


def _solicitud_detalle_to_dict(d: SolicitudMaterialDetalle) -> dict:
    return {
        'id': d.id,
        'producto_id': d.producto_id,
        'cantidad_solicitada': float(d.cantidad_solicitada or 0),
        'cantidad_aprobada': float(d.cantidad_aprobada or 0),
        'cantidad_entregada': float(d.cantidad_entregada or 0),
        'producto_descripcion': d.producto.descripcion if d.producto else 'Producto eliminado',
        'producto_codigo': d.producto.codigo if d.producto else '---',
        'producto_unidad': d.producto.unidad if d.producto else 'pza',
    }


def _solicitud_to_dict(s: SolicitudMaterial) -> dict:
    return {
        'id': s.id,
        'solicitante_id': s.solicitante_id,
        'proyecto': s.proyecto,
        'estatus': s.estatus,
        'fecha_creacion': s.fecha_creacion.isoformat() if s.fecha_creacion else None,
        'fecha_cierre': s.fecha_cierre.isoformat() if s.fecha_cierre else None,
        'solicitante_nombre': s.solicitante.username if s.solicitante else 'Desconocido',
        'detalles': [_solicitud_detalle_to_dict(d) for d in s.detalles],
    }


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _parse_or_422(schema: Schema, data):
    """Valida `data` con `schema`. Si falla, devuelve tuple (None, response_422).
    Si pasa, devuelve (dict, None).
    """
    if not isinstance(data, dict):
        return None, (jsonify({'detail': 'Payload debe ser un objeto JSON'}), 422)
    try:
        return schema.load(data), None
    except ValidationError as err:
        return None, (jsonify({'detail': err.messages}), 422)


def _int_arg(name: str, default: int, minimum: int, maximum: int):
    """Lee un query param int, lo recorta al rango y devuelve (valor, error_response).
    Devuelve 422 si el valor no es numérico o queda fuera del rango.
    """
    raw = request.args.get(name)
    if raw is None:
        return default, None
    try:
        val = int(raw)
    except (TypeError, ValueError):
        return None, (jsonify({'detail': f"Parámetro '{name}' debe ser entero"}), 422)
    if val < minimum or val > maximum:
        return None, (jsonify({'detail': f"Parámetro '{name}' fuera de rango"}), 422)
    return val, None


def _audit(user: User, action: str):
    """Escribe AuditLog usando la IP real (anti-spoofing en get_real_client_ip_flask).
    MED-03: pasa por _safe_log_value para evitar log forging vía CRLF."""
    from app.extensions import get_real_client_ip_flask
    from app.utils import _safe_log_value
    ip = get_real_client_ip_flask()
    entry = AuditLog(
        user=_safe_log_value(user.username, 80),
        action=_safe_log_value(action, 200),
        ip=ip,
    )
    db.session.add(entry)


# ─── Health ───────────────────────────────────────────────────────────────────

@bp.route('/health', methods=['GET'])
def health_check():
    return jsonify({'status': 'ok'})


# ─── Productos ────────────────────────────────────────────────────────────────

@bp.route('/productos/', methods=['GET'])
@_require_inventario
def get_productos():
    skip, err = _int_arg('skip', 0, 0, 1_000_000)
    if err: return err
    limit, err = _int_arg('limit', 200, 0, 1000)
    if err: return err

    productos = (
        Producto.query
        .filter(Producto.activo == True)
        .offset(skip)
        .limit(limit)
        .all()
    )
    return jsonify([_producto_to_dict(p) for p in productos])


@bp.route('/productos/bajo-minimo/', methods=['GET'])
@_require_inventario
def get_productos_bajo_minimo():
    productos = (
        Producto.query
        .filter(Producto.activo == True, Producto.stock_actual <= Producto.stock_minimo)
        .order_by(Producto.categoria, Producto.descripcion)
        .all()
    )
    return jsonify([{
        'id': p.id,
        'codigo': p.codigo,
        'descripcion': p.descripcion,
        'categoria': p.categoria,
        'unidad': p.unidad,
        'stock_actual': float(p.stock_actual),
        'stock_minimo': float(p.stock_minimo),
    } for p in productos])


@bp.route('/productos/', methods=['POST'])
@_require_inventario_admin
def create_producto():
    data, err = _parse_or_422(ProductoCreateSchema(), request.get_json(silent=True))
    if err: return err

    if Producto.query.filter(Producto.codigo == data['codigo']).first():
        return jsonify({'detail': 'El código de producto ya existe'}), 400

    user = request.current_user
    nuevo = Producto(
        codigo=data['codigo'],
        descripcion=data['descripcion'],
        categoria=data['categoria'],
        unidad=data['unidad'],
        stock_actual=Decimal(str(data['stock_actual'])),
        stock_minimo=Decimal(str(data['stock_minimo'])),
        imagen_url=data.get('imagen_url') or None,
        created_by_id=user.id,
    )
    db.session.add(nuevo)
    _audit(user, f"Producto creado: {data['codigo']} — {data['descripcion']}")
    db.session.commit()
    db.session.refresh(nuevo)
    return jsonify(_producto_to_dict(nuevo))


@bp.route('/productos/<int:producto_id>', methods=['PUT'])
@_require_inventario_admin
def update_producto(producto_id: int):
    data, err = _parse_or_422(ProductoUpdateSchema(), request.get_json(silent=True))
    if err: return err

    prod = Producto.query.filter(Producto.id == producto_id, Producto.activo == True).first()
    if not prod:
        return jsonify({'detail': 'Producto no encontrado'}), 404

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

    if cambios:
        _audit(request.current_user, f"Producto #{producto_id} editado: {'; '.join(cambios)}")

    db.session.commit()
    db.session.refresh(prod)
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
    return Response(status=204)


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
    return Response(status=204)


@bp.route('/estantes/<qr_code>/validar', methods=['GET'])
@_require_inventario
def validar_estante(qr_code: str):
    est = Estante.query.filter(Estante.qr_code == qr_code, Estante.activo == True).first()
    if not est:
        return jsonify({'detail': 'Estante no encontrado o QR inválido'}), 404
    return jsonify(_estante_to_dict(est))


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


# ─── Movimientos ──────────────────────────────────────────────────────────────

@bp.route('/movimientos/', methods=['GET'])
@_require_inventario
def get_movimientos():
    producto_id = request.args.get('producto_id', type=int)
    tipo = request.args.get('tipo', type=str)
    limit, err = _int_arg('limit', 200, 0, 1000)
    if err: return err

    q = MovimientoInventario.query
    if producto_id:
        q = q.filter(MovimientoInventario.producto_id == producto_id)
    if tipo:
        if len(tipo) > 20:
            return jsonify({'detail': "Parámetro 'tipo' demasiado largo"}), 422
        q = q.filter(MovimientoInventario.tipo == tipo.upper())
    movs = q.order_by(MovimientoInventario.fecha.desc()).limit(limit).all()
    return jsonify([_movimiento_to_dict(m) for m in movs])


@bp.route('/movimientos/', methods=['POST'])
@limiter.limit(
    "20/minute",
    # key por IP real para que requests sin sesión también cuenten al contador.
    # Si pusiéramos @_require_inventario antes, los 401 no incrementarían el contador
    # y un atacante anónimo podría martillear el endpoint sin freno hasta el límite global.
    key_func=lambda: f"ip:{get_real_client_ip_flask()}",
)
@_require_inventario
def create_movimiento():
    data, err = _parse_or_422(MovimientoCreateSchema(), request.get_json(silent=True))
    if err: return err

    tipo = data['tipo']
    cantidad_raw = data['cantidad']

    # ENTRADA/SALIDA/TRASPASO requieren cantidad estrictamente positiva.
    # AJUSTE permite negativo (mermas) — eso lo controla la lógica de stock más abajo.
    if tipo in ['ENTRADA', 'SALIDA', 'TRASPASO'] and cantidad_raw <= 0:
        return jsonify({'detail': 'La cantidad debe ser positiva para este tipo de movimiento'}), 422

    # with_for_update bloquea la fila contra concurrencia; previene over-selling
    # cuando dos requests intentan reducir stock al mismo tiempo.
    producto = (
        Producto.query
        .with_for_update(nowait=True)
        .filter(Producto.id == data['producto_id'])
        .first()
    )
    if not producto:
        return jsonify({'detail': 'Producto no encontrado'}), 404

    cantidad_decimal = Decimal(str(cantidad_raw))

    if tipo in ['SALIDA', 'TRASPASO']:
        if producto.stock_actual < cantidad_decimal:
            db.session.rollback()
            return jsonify({'detail': f'Stock insuficiente. Disponible: {producto.stock_actual}'}), 400
        producto.stock_actual -= cantidad_decimal
    elif tipo == 'ENTRADA':
        producto.stock_actual += cantidad_decimal
    elif tipo == 'AJUSTE':
        if producto.stock_actual + cantidad_decimal < 0:
            db.session.rollback()
            return jsonify({'detail': 'Ajuste provocaría stock negativo'}), 400
        producto.stock_actual += cantidad_decimal
    else:
        db.session.rollback()
        return jsonify({'detail': 'Tipo de movimiento inválido'}), 400

    # Inferir almacen_destino/origen desde el estante si solo vino estante_id.
    # Mantiene compatibilidad con el flujo móvil que escanea un QR de estante
    # y no conoce el almacen_id explícito.
    almacen_destino_id = data.get('almacen_destino_id')
    almacen_origen_id = data.get('almacen_origen_id')
    estante_id = data.get('estante_id')
    if estante_id and (not almacen_destino_id and not almacen_origen_id):
        estante = Estante.query.filter(Estante.id == estante_id).first()
        if estante:
            if tipo == 'ENTRADA':
                almacen_destino_id = estante.almacen_id
            else:
                almacen_origen_id = estante.almacen_id

    user = request.current_user
    nuevo_mov = MovimientoInventario(
        tipo=tipo,
        producto_id=data['producto_id'],
        cantidad=cantidad_decimal,
        almacen_origen_id=almacen_origen_id,
        almacen_destino_id=almacen_destino_id,
        motivo=data.get('motivo') or (f"Estante #{estante_id}" if estante_id else None),
        usuario_id=user.id,
    )
    db.session.add(nuevo_mov)
    _audit(user, f"Movimiento {tipo} — producto #{data['producto_id']} — cantidad: {cantidad_raw}")
    db.session.commit()
    db.session.refresh(nuevo_mov)
    return jsonify(_movimiento_to_dict(nuevo_mov))


# ─── Solicitudes ──────────────────────────────────────────────────────────────

@bp.route('/solicitudes/', methods=['POST'])
@limiter.limit(
    "10/minute",
    # key por IP real: ver comentario en create_movimiento sobre por qué el limiter
    # debe correr ANTES del check de auth (replica el patrón del FastAPI original).
    key_func=lambda: f"ip:{get_real_client_ip_flask()}",
)
@_require_login
def create_solicitud():
    user = request.current_user
    if user.role not in ['solicitante_material', 'admin', 'inventario']:
        return jsonify({'detail': 'No tienes permiso para crear solicitudes'}), 403

    data, err = _parse_or_422(SolicitudCreateSchema(), request.get_json(silent=True))
    if err: return err

    nueva = SolicitudMaterial(
        solicitante_id=user.id,
        proyecto=data.get('proyecto'),
        estatus='PENDIENTE',
    )
    db.session.add(nueva)
    db.session.flush()  # Necesario para obtener nueva.id antes de crear detalles

    ids_invalidos = []
    for det in data['detalles']:
        producto = Producto.query.filter(Producto.id == det['producto_id'], Producto.activo == True).first()
        if not producto:
            ids_invalidos.append(det['producto_id'])
            continue
        db.session.add(SolicitudMaterialDetalle(
            solicitud_id=nueva.id,
            producto_id=det['producto_id'],
            cantidad_solicitada=Decimal(str(det['cantidad_solicitada'])),
        ))

    if ids_invalidos:
        db.session.rollback()
        return jsonify({
            'detail': f'Los siguientes producto_id no existen o están inactivos: {ids_invalidos}'
        }), 400

    _audit(user, f"Nueva solicitud de material — proyecto: {data.get('proyecto') or 'Sin proyecto'}")
    db.session.commit()
    db.session.refresh(nueva)
    return jsonify(_solicitud_to_dict(nueva))


@bp.route('/solicitudes/', methods=['GET'])
@_require_login
def get_solicitudes():
    user = request.current_user
    skip, err = _int_arg('skip', 0, 0, 1_000_000)
    if err: return err
    limit, err = _int_arg('limit', 200, 0, 500)
    if err: return err

    query = SolicitudMaterial.query
    if user.role == 'solicitante_material':
        query = query.filter(SolicitudMaterial.solicitante_id == user.id)
    elif user.role not in ['inventario', 'admin']:
        return jsonify({'detail': 'No tienes permiso'}), 403

    solicitudes = (
        query
        .options(
            joinedload(SolicitudMaterial.solicitante),
            selectinload(SolicitudMaterial.detalles).joinedload(SolicitudMaterialDetalle.producto),
        )
        .order_by(SolicitudMaterial.fecha_creacion.desc())
        .offset(skip).limit(limit)
        .all()
    )
    return jsonify([_solicitud_to_dict(s) for s in solicitudes])


@bp.route('/solicitudes/<int:sol_id>/estado', methods=['PATCH'])
@_require_inventario_admin
def update_solicitud_estado(sol_id: int):
    data, err = _parse_or_422(SolicitudUpdateEstadoSchema(), request.get_json(silent=True))
    if err: return err

    sol = SolicitudMaterial.query.filter(SolicitudMaterial.id == sol_id).first()
    if not sol:
        return jsonify({'detail': 'Solicitud no encontrada'}), 404

    # Capturar estado previo: revertir ENTREGADA→PENDIENTE oculta entregas reales
    # y debe quedar trazado en AuditLog.
    estado_previo = sol.estatus
    nuevo_estado = data['estatus']

    sol.estatus = nuevo_estado
    if nuevo_estado == 'PENDIENTE':
        # Reabrir: limpiar fecha_cierre (bug histórico: antes no se limpiaba).
        sol.fecha_cierre = None
    else:
        sol.fecha_cierre = datetime.datetime.now()

    if estado_previo != nuevo_estado:
        _audit(request.current_user, f"Solicitud #{sol_id} estatus: {estado_previo} → {nuevo_estado}")

    db.session.commit()
    db.session.refresh(sol)
    # Forzar carga de detalles para serializar respuesta sin lazy queries
    _ = list(sol.detalles)
    return jsonify(_solicitud_to_dict(sol))


# ─── Proyectos ────────────────────────────────────────────────────────────────

@bp.route('/proyectos/', methods=['GET'])
@_require_login
def get_proyectos():
    proyectos = (
        Proyecto.query
        .filter(Proyecto.activo == True)
        .order_by(Proyecto.numero_proyecto)
        .all()
    )
    return jsonify([
        {'id': p.id, 'numero_proyecto': p.numero_proyecto, 'nombre': p.nombre or ''}
        for p in proyectos
    ])


# ─── Categorías ───────────────────────────────────────────────────────────────

@bp.route('/categorias/', methods=['GET'])
@_require_inventario
def get_categorias():
    """Devuelve la unión de categorías presentes en el catálogo de productos
    y las registradas en `categorias_config` (admin pudo crear categorías sin
    haber capturado aún ningún producto)."""
    prod_rows = (
        db.session.query(sql_distinct(Producto.categoria))
        .filter(Producto.activo == True, Producto.categoria != None, Producto.categoria != '')
        .all()
    )
    cfg_rows = db.session.query(CategoriaConfig.nombre).all()
    nombres = {r[0] for r in prod_rows} | {r[0] for r in cfg_rows}
    return jsonify(sorted(nombres))


# ─── CategoriaConfig (metadatos visuales por categoría) ──────────────────────

def _categoria_config_to_dict(c: CategoriaConfig) -> dict:
    return {
        'nombre': c.nombre,
        'imagen_url': c.imagen_url,
        'updated_at': c.updated_at.isoformat() if c.updated_at else None,
    }


@bp.route('/categorias-config/', methods=['GET'])
@_require_login
def get_categorias_config():
    """Lista todas las configuraciones (imagen, etc.) por nombre de categoría.
    Lectura abierta a cualquier usuario autenticado: el dashboard de inventario
    también lo consume desde el rol solicitante_material."""
    rows = CategoriaConfig.query.order_by(CategoriaConfig.nombre).all()
    return jsonify([_categoria_config_to_dict(c) for c in rows])


@bp.route('/categorias-config/<string:nombre>', methods=['PUT'])
@_require_inventario_admin
def upsert_categoria_config(nombre: str):
    """Crea o actualiza la config de la categoría con `nombre`. Si imagen_url
    viene null o vacío, persiste null (UI lo trata como "quitar imagen")."""
    nombre = (nombre or '').strip()
    if not nombre or len(nombre) > 100:
        return jsonify({'detail': "Nombre de categoría inválido (1..100 caracteres)"}), 422

    data, err = _parse_or_422(CategoriaConfigUpsertSchema(), request.get_json(silent=True))
    if err: return err

    imagen = (data.get('imagen_url') or '').strip() or None

    cfg = CategoriaConfig.query.filter(CategoriaConfig.nombre == nombre).first()
    if cfg is None:
        cfg = CategoriaConfig(
            nombre=nombre,
            imagen_url=imagen,
            created_by_id=request.current_user.id,
        )
        db.session.add(cfg)
        _audit(request.current_user, f"Categoría '{nombre}' creada/actualizada")
    else:
        cfg.imagen_url = imagen
        _audit(request.current_user, f"Categoría '{nombre}' actualizada")

    db.session.commit()
    db.session.refresh(cfg)
    return jsonify(_categoria_config_to_dict(cfg))


@bp.route('/categorias-config/<string:nombre>', methods=['DELETE'])
@_require_inventario_admin
def delete_categoria_config(nombre: str):
    """Elimina la fila de config (no afecta productos, solo limpia los metadatos visuales)."""
    cfg = CategoriaConfig.query.filter(CategoriaConfig.nombre == nombre).first()
    if not cfg:
        return jsonify({'detail': 'Categoría no encontrada en config'}), 404
    db.session.delete(cfg)
    _audit(request.current_user, f"Categoría '{nombre}' config eliminada")
    db.session.commit()
    return Response(status=204)


# ─── Importar materiales desde Excel ─────────────────────────────────────────

@bp.route('/productos/plantilla-importar', methods=['GET'])
@_require_inventario
def get_plantilla_materiales():
    """Genera y sirve un Excel de plantilla para carga masiva de productos."""
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment
        from openpyxl.utils import get_column_letter
    except ImportError:
        return jsonify({'detail': 'openpyxl no instalado en el servidor'}), 500

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Materiales'

    headers = ['Código (SKU)', 'Descripción', 'Categoría', 'Unidad', 'Stock Inicial', 'Stock Mínimo']
    header_fill = PatternFill(start_color='1E40AF', end_color='1E40AF', fill_type='solid')
    header_font = Font(bold=True, color='FFFFFF', size=11)

    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=header)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal='center')
        ws.column_dimensions[get_column_letter(col)].width = 24

    # Fila de ejemplo
    ws.append(['SKU-001', 'Tornillo hexagonal 1/4"', 'Tornillería', 'Pza', 100, 20])
    ws.append(['SKU-002', 'Tuerca hexagonal 3/8"', 'Tuercas', 'Pza', 50, 10])

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    return send_file(
        buf,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name='plantilla_materiales.xlsx',
    )


@bp.route('/productos/importar', methods=['POST'])
@_require_inventario_admin
@limiter.limit('5 per minute')
def importar_materiales():
    """Importa productos en masa desde un archivo .xlsx/.xls."""
    try:
        import pandas as pd
    except ImportError:
        return jsonify({'detail': 'pandas no instalado en el servidor'}), 500

    file = request.files.get('archivo') or request.files.get('archivo_excel')
    if not file or not file.filename:
        return jsonify({'detail': 'No se envió archivo'}), 400
    if not file.filename.lower().endswith(('.xlsx', '.xls')):
        return jsonify({'detail': 'Formato no válido. Debe ser .xlsx o .xls'}), 400

    try:
        df = pd.read_excel(file)
    except Exception:
        return jsonify({'detail': 'Error al leer el Excel. Usa la plantilla correcta.'}), 400

    user = request.current_user
    exitosos = 0
    errores = []

    for idx, row in df.iterrows():
        fila = idx + 2
        try:
            import pandas as _pd
            codigo      = str(row.get('Código (SKU)', '') or '').strip()
            descripcion = str(row.get('Descripción', '') or '').strip()
            categoria   = str(row.get('Categoría', '') or '').strip()
            unidad      = str(row.get('Unidad', 'Pza') or 'Pza').strip() or 'Pza'

            if not codigo or codigo == 'nan':
                errores.append(f'Fila {fila}: Código vacío, se omite.')
                continue
            if not descripcion or descripcion == 'nan':
                errores.append(f'Fila {fila}: Descripción vacía, se omite.')
                continue
            if not categoria or categoria == 'nan':
                errores.append(f'Fila {fila}: Categoría vacía, se omite.')
                continue

            if Producto.query.filter(Producto.codigo == codigo).first():
                errores.append(f'Fila {fila}: Código "{codigo}" ya existe, se omite.')
                continue

            try:
                stock_actual = float(row.get('Stock Inicial', 0) or 0)
                stock_minimo = float(row.get('Stock Mínimo', 0) or 0)
            except (ValueError, TypeError):
                stock_actual = 0.0
                stock_minimo = 0.0

            nuevo = Producto(
                codigo=codigo,
                descripcion=descripcion,
                categoria=categoria,
                unidad=unidad,
                stock_actual=Decimal(str(stock_actual)),
                stock_minimo=Decimal(str(stock_minimo)),
                created_by_id=user.id,
            )
            db.session.add(nuevo)
            exitosos += 1

        except Exception as e:
            errores.append(f'Fila {fila}: Error inesperado — {str(e)[:80]}')

    try:
        _audit(user, f'Importación masiva: {exitosos} productos creados, {len(errores)} errores')
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({'detail': 'Error al guardar en base de datos.'}), 500

    return jsonify({'exitosos': exitosos, 'errores': errores})
