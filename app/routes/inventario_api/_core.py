"""Núcleo compartido del paquete `inventario_api`.

Define el blueprint `bp`, los decoradores de auth, los schemas Marshmallow,
los serializers comunes, helpers de validación y los helpers de stock /
reservas. Cada submódulo del paquete importa lo que necesita desde aquí.

No registres rutas en este archivo (salvo /health, que es trivial).
Las rutas viven en los submódulos por dominio: productos.py, almacenes.py,
movimientos.py, solicitudes.py, catalogo.py, reportes.py, etiquetas.py, tomas.py.
"""
import io
import uuid
import logging
import datetime
from decimal import Decimal, InvalidOperation
from functools import wraps

import qrcode
from flask import Blueprint, jsonify, request, Response, abort, send_file, render_template, current_app, g
from marshmallow import Schema, fields, validate, ValidationError, EXCLUDE
from sqlalchemy import distinct as sql_distinct
from sqlalchemy.orm import joinedload, selectinload

from app.extensions import db, limiter, get_real_client_ip_flask
from app.realtime import emit_to_role
from app.models import (
    Almacen, Estante, Producto, MovimientoInventario, StockPorAlmacen,
    ProductoEstante, TomaInventario, TomaInventarioDetalle, ESTADOS_TOMA,
    SolicitudMaterial, SolicitudMaterialDetalle, User, AuditLog, Proyecto,
    CategoriaConfig, Herramienta, HerramientaUnidad, AsignacionHerramienta,
    Trabajador, NotificacionUmbral, crear_notif_inventario,
    crear_evento_herramienta,
)
from app.utils import log_action
from app.routes.api_auth import jwt_required

logger = logging.getLogger(__name__)

bp = Blueprint('inventario_api', __name__, url_prefix='/api/v1')


# ─── Roles que reciben eventos en tiempo real ────────────────────────────────

# Roles que reciben eventos de inventario (productos, almacenes, movimientos,
# tomas). Coordinador/solicitante ven productos en sus solicitudes pero no
# editan; igual les incluimos para que sus listas (catalogo en SolicitudForm)
# refresquen al cambiar el stock.
_INV_ROLES = ['admin', 'super_admin', 'inventario', 'coordinador', 'solicitante_material']
# Las solicitudes involucran a todos: solicitante las crea, inventario aprueba,
# coordinador puede crearlas también.
_SOL_ROLES = ['admin', 'super_admin', 'inventario', 'coordinador', 'solicitante_material']


# ─── Auth helpers ─────────────────────────────────────────────────────────────

def _require_login(view):
    @wraps(view)
    @jwt_required
    def wrapper(*args, **kwargs):
        request.current_user = g._jwt_user
        return view(*args, **kwargs)
    return wrapper


def _require_inventario(view):
    """Lectura: solicitantes, coordinadores, inventario, admin y super_admin.
    Coordinador agregado para que pueda ver catálogo y armar pedidos."""
    @wraps(view)
    @_require_login
    def wrapper(*args, **kwargs):
        if request.current_user.role not in ['inventario', 'solicitante_material', 'coordinador', 'admin', 'super_admin']:
            log_action(f"API 403 lectura '{request.path}' (rol: {request.current_user.role})")
            return jsonify({'detail': 'Forbidden: Required permissions missing'}), 403
        return view(*args, **kwargs)
    return wrapper


def _require_inventario_admin(view):
    """Escritura/borrado: inventario, admin y super_admin."""
    @wraps(view)
    @_require_login
    def wrapper(*args, **kwargs):
        if request.current_user.role not in ['inventario', 'admin', 'super_admin']:
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
    # Pausa 9: proveedor default (opcional al crear)
    proveedor_default_nombre = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=150))
    proveedor_default_contacto = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=150))


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
    proveedor_default_nombre = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=150))
    proveedor_default_contacto = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=150))


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
    # XOR a nivel app: si tipo_item=MATERIAL → producto_id; si HERRAMIENTA → herramienta_id
    tipo_item = fields.Str(load_default='MATERIAL', validate=validate.OneOf(['MATERIAL', 'HERRAMIENTA']))
    producto_id = fields.Int(load_default=None, allow_none=True)
    herramienta_id = fields.Int(load_default=None, allow_none=True)
    cantidad_solicitada = fields.Float(required=True, validate=validate.Range(min=0.0001, max=10_000))
    fecha_uso_inicio = fields.Date(load_default=None, allow_none=True)
    fecha_uso_fin = fields.Date(load_default=None, allow_none=True)
    justificacion = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=2000))
    complementos = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=500))


class SolicitudCreateSchema(_BaseSchema):
    # Proyecto obligatorio: no se permiten solicitudes sin proyecto asociado.
    proyecto = fields.Str(required=True, validate=validate.Length(min=1, max=200))
    notas = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=2000))
    detalles = fields.List(
        fields.Nested(SolicitudDetalleCreateSchema),
        required=True,
        validate=validate.Length(min=1, max=100),
    )


class SolicitudUpdateEstadoSchema(_BaseSchema):
    estatus = fields.Str(required=True, validate=validate.OneOf(['APROBADA', 'RECHAZADA', 'ENTREGADA', 'PENDIENTE']))


# Pausa 8b — Editar cantidad_aprobada de una línea.
class SolicitudDetallePatchSchema(_BaseSchema):
    cantidad_aprobada = fields.Float(required=True, validate=validate.Range(min=0, max=100_000))


# Pausa 8b — Entrega total o parcial de una solicitud APROBADA.
class EntregaItemSchema(_BaseSchema):
    detalle_id = fields.Int(required=True)
    cantidad_entregada = fields.Float(required=True, validate=validate.Range(min=0, max=100_000))


class EntregarSolicitudSchema(_BaseSchema):
    almacen_origen_id = fields.Int(load_default=None, allow_none=True)
    motivo = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=250))
    # Para líneas HERRAMIENTA: fecha de devolución prevista que se copia a cada
    # AsignacionHerramienta generada. Opcional.
    fecha_devolucion_prevista = fields.DateTime(load_default=None, allow_none=True)
    entregas = fields.List(
        fields.Nested(EntregaItemSchema),
        required=True,
        validate=validate.Length(min=1, max=100),
    )


class CategoriaConfigUpsertSchema(_BaseSchema):
    imagen_url = fields.Str(load_default=None, allow_none=True, validate=[
        validate.Length(max=500),
        validate.Regexp(_IMAGEN_URL_REGEX, error='imagen_url debe ser HTTPS o un path absoluto a imagen local'),
    ])


# ─── Serializers ──────────────────────────────────────────────────────────────

def _producto_to_dict(p: Producto) -> dict:
    actual = float(p.stock_actual or 0)
    reservado = float(p.stock_reservado or 0)
    return {
        'id': p.id,
        'codigo': p.codigo,
        'descripcion': p.descripcion,
        'categoria': p.categoria,
        'unidad': p.unidad,
        'stock_actual': actual,
        'stock_reservado': reservado,         # Pausa 2-bis: apartado por solicitudes APROBADAS
        'stock_disponible': actual - reservado,  # lo que sí se puede mover
        'stock_minimo': float(p.stock_minimo or 0),
        'imagen_url': p.imagen_url,
        # Pausa 9: proveedor default para Compras express.
        'proveedor_default_nombre': p.proveedor_default_nombre,
        'proveedor_default_contacto': p.proveedor_default_contacto,
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
    # Datos del producto embebidos: con miles de productos el SPA ya no puede
    # descargar el catálogo completo solo para resolver el nombre por id.
    prod = m.producto
    return {
        'id': m.id,
        'tipo': m.tipo,
        'producto_id': m.producto_id,
        'producto_codigo': prod.codigo if prod else None,
        'producto_descripcion': prod.descripcion if prod else None,
        'producto_unidad': prod.unidad if prod else None,
        'cantidad': float(m.cantidad or 0),
        'fecha': m.fecha.isoformat() if m.fecha else None,
        'almacen_origen_id': m.almacen_origen_id,
        'almacen_destino_id': m.almacen_destino_id,
        'usuario_id': m.usuario_id,
        'motivo': m.motivo,
    }


def _solicitud_detalle_to_dict(d: SolicitudMaterialDetalle) -> dict:
    tipo = (d.tipo_item or 'MATERIAL').upper()
    base = {
        'id': d.id,
        'tipo_item': tipo,
        'cantidad_solicitada': float(d.cantidad_solicitada or 0),
        'cantidad_aprobada': float(d.cantidad_aprobada or 0),
        'cantidad_entregada': float(d.cantidad_entregada or 0),
        'fecha_uso_inicio': d.fecha_uso_inicio.isoformat() if d.fecha_uso_inicio else None,
        'fecha_uso_fin': d.fecha_uso_fin.isoformat() if d.fecha_uso_fin else None,
        'justificacion': d.justificacion,
        'complementos': d.complementos,
    }
    if tipo == 'HERRAMIENTA':
        base['herramienta_id'] = d.herramienta_id
        base['producto_id'] = None
        base['item_descripcion'] = d.herramienta.descripcion if d.herramienta else 'Herramienta eliminada'
        base['item_codigo'] = d.herramienta.sku if d.herramienta else '---'
        base['item_unidad'] = d.herramienta.unidad if d.herramienta else 'pza'
        # Compat: claves antiguas para que el SPA siga renderizando
        base['producto_descripcion'] = base['item_descripcion']
        base['producto_codigo'] = base['item_codigo']
        base['producto_unidad'] = base['item_unidad']
    else:
        base['producto_id'] = d.producto_id
        base['herramienta_id'] = None
        base['item_descripcion'] = d.producto.descripcion if d.producto else 'Producto eliminado'
        base['item_codigo'] = d.producto.codigo if d.producto else '---'
        base['item_unidad'] = d.producto.unidad if d.producto else 'pza'
        base['producto_descripcion'] = base['item_descripcion']
        base['producto_codigo'] = base['item_codigo']
        base['producto_unidad'] = base['item_unidad']
    return base


def _solicitud_to_dict(s: SolicitudMaterial) -> dict:
    return {
        'id': s.id,
        'solicitante_id': s.solicitante_id,
        'proyecto': s.proyecto,
        'notas': s.notas,
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


# ─── Reservas de stock (Pausa 2-bis del plan) ─────────────────────────────────

def _reservas_de_solicitud(sol: 'SolicitudMaterial') -> dict[int, Decimal]:
    """Suma por producto la cantidad que esta solicitud debe tener APARTADA.

    Solo cuenta detalles MATERIAL (no HERRAMIENTAS).

    Pausa 8b: la reserva por línea es `cantidad_aprobada - cantidad_entregada`.
    Si `cantidad_aprobada` es 0 (caso recién-aprobada sin tocar líneas), cae
    al fallback `cantidad_solicitada` para preservar el comportamiento previo
    a 8b (todas las solicitudes pre-8b se aprobaban "tal cual se solicitó").

    Una solicitud puede repetir el mismo producto en varias líneas — los
    agrupamos para hacer un solo update por producto.
    """
    out: dict[int, Decimal] = {}
    for d in (sol.detalles or []):
        if d.producto_id is None:
            continue
        cant_aprob = Decimal(str(d.cantidad_aprobada or 0))
        cant_sol = Decimal(str(d.cantidad_solicitada or 0))
        cant_ent = Decimal(str(d.cantidad_entregada or 0))
        base = cant_aprob if cant_aprob > 0 else cant_sol
        reserva = base - cant_ent
        if reserva > 0:
            out[d.producto_id] = out.get(d.producto_id, Decimal('0')) + reserva
    return out


def _intentar_reservar(reservas: dict[int, Decimal]) -> list[str]:
    """Locks + valida disponibilidad. Aplica las reservas si TODOS los productos
    alcanzan. Si alguno no, NO aplica nada (caller debe hacer rollback) y
    devuelve lista de errores legibles para el SPA.
    Disponible = stock_actual − stock_reservado."""
    if not reservas:
        return []
    errores = []
    a_aplicar = []
    for prod_id, cant in reservas.items():
        prod = (
            Producto.query
            .with_for_update(nowait=True)
            .filter(Producto.id == prod_id)
            .first()
        )
        if not prod:
            errores.append(f"Producto #{prod_id} no encontrado")
            continue
        actual = Decimal(str(prod.stock_actual or 0))
        reservado = Decimal(str(prod.stock_reservado or 0))
        disponible = actual - reservado
        if disponible < cant:
            errores.append(
                f"{prod.codigo} — {prod.descripcion}: requiere {cant} {prod.unidad} "
                f"pero solo hay {disponible} disponibles (stock {actual}, ya apartado {reservado})"
            )
            continue
        a_aplicar.append((prod, cant))
    if errores:
        return errores
    for prod, cant in a_aplicar:
        prod.stock_reservado = (prod.stock_reservado or Decimal('0')) + cant
    return []


def _liberar_reservas(reservas: dict[int, Decimal]):
    """Resta reservas (con clamp a 0 por seguridad). No falla si el producto
    no existe — la reserva ya quedó liberada conceptualmente."""
    for prod_id, cant in reservas.items():
        prod = (
            Producto.query
            .with_for_update(nowait=True)
            .filter(Producto.id == prod_id)
            .first()
        )
        if not prod:
            continue
        actual = Decimal(str(prod.stock_reservado or 0))
        nuevo = actual - cant
        prod.stock_reservado = nuevo if nuevo > 0 else Decimal('0')


# ─── Stock por almacén (Pausa 2 del plan) ─────────────────────────────────────

def _almacen_default_id() -> int | None:
    """Devuelve el id del almacén activo de menor id. Sirve como fallback cuando
    un movimiento llega sin almacén ni estante (clientes viejos del SPA antes
    del refactor a stock por almacén). Devuelve None si no hay ninguno."""
    row = (
        db.session.query(Almacen.id)
        .filter(Almacen.activo == True)  # noqa: E712
        .order_by(Almacen.id.asc())
        .first()
    )
    return row[0] if row else None


def _lock_stock(producto_id: int, almacen_id: int) -> StockPorAlmacen:
    """SELECT ... FOR UPDATE sobre la fila (producto, almacen). Crea la fila
    en 0 si no existe — útil para ENTRADAs hacia bodegas nuevas que aún no
    tienen registro de este producto."""
    fila = (
        db.session.query(StockPorAlmacen)
        .with_for_update(nowait=True)
        .filter(
            StockPorAlmacen.producto_id == producto_id,
            StockPorAlmacen.almacen_id == almacen_id,
        )
        .first()
    )
    if fila is None:
        fila = StockPorAlmacen(producto_id=producto_id, almacen_id=almacen_id, cantidad=Decimal('0'))
        db.session.add(fila)
        db.session.flush()  # asegura que existe antes de bloquear
    return fila


def _recalcular_cache_stock(producto: Producto):
    """Actualiza el cache denormalizado `Producto.stock_actual` con la suma
    de todas las filas de stock_por_almacen del producto. Se llama dentro de
    la misma transacción que modificó stock_por_almacen, así nunca queda
    desfasado en commits exitosos."""
    total = (
        db.session.query(db.func.coalesce(db.func.sum(StockPorAlmacen.cantidad), 0))
        .filter(StockPorAlmacen.producto_id == producto.id)
        .scalar()
    )
    producto.stock_actual = Decimal(str(total or 0))


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
