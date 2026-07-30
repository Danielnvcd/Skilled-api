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
from marshmallow import Schema, fields, validate, ValidationError, EXCLUDE, pre_load
from sqlalchemy import distinct as sql_distinct
from sqlalchemy.orm import joinedload, selectinload

from app.extensions import db, limiter, get_real_client_ip_flask
from app.realtime import emit_to_role
from app.models import (
    Almacen, Estante, Producto, MovimientoInventario, StockPorAlmacen,
    StockAlmacenProyecto,
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


def _require_plan_materiales(view):
    """Plan de materiales por proyecto: inventario, coordinador, admin y super_admin.

    El coordinador es dueño de sus proyectos y planea sus materiales (crea/abre el
    plan y selecciona los materiales). Guard aparte de `_require_inventario_admin`
    a propósito: le damos al coordinador SOLO el plan de materiales por proyecto,
    NO el resto de la escritura de inventario (movimientos, catálogo, tomas,
    entregas, compras…)."""
    @wraps(view)
    @_require_login
    def wrapper(*args, **kwargs):
        if request.current_user.role not in ['inventario', 'coordinador', 'admin', 'super_admin']:
            log_action(f"API 403 plan-materiales '{request.path}' (rol: {request.current_user.role})")
            return jsonify({'detail': 'Se requiere rol de inventario, coordinador o administrador'}), 403
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

    @pre_load
    def _blank_imagen_url_to_none(self, data, **kwargs):
        # Un `imagen_url` vacío ('') significa "sin imagen": la UI lo manda así
        # cuando dejas el campo opcional en blanco. Sin esto, el Regexp de
        # seguridad lo rechaza (`allow_none` solo cubre null, no ''), y editar un
        # producto sin imagen falla con 422 como si la imagen fuera obligatoria.
        if isinstance(data, dict):
            v = data.get('imagen_url')
            if isinstance(v, str) and not v.strip():
                data = {**data, 'imagen_url': None}
        return data


class ProductoCreateSchema(_BaseSchema):
    codigo = fields.Str(required=True, validate=[
        validate.Length(min=1, max=50),
        validate.Regexp(CODIGO_REGEX),
    ])
    descripcion = fields.Str(required=True, validate=validate.Length(min=1, max=250))
    categoria = fields.Str(required=True, validate=validate.Length(min=1, max=100))
    # Marca / fabricante (opcional, independiente del proveedor).
    marca = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=100))
    unidad = fields.Str(required=True, validate=validate.Length(min=1, max=50))
    # Atributos de cable (obligatorios SOLO cuando la categoría es cable — se
    # valida en la ruta, no aquí, porque depende del valor de `categoria`).
    cable_tipo = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=60))
    cable_calibre = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=40))
    stock_actual = fields.Float(load_default=0.0, validate=validate.Range(min=0, max=1_000_000))
    stock_minimo = fields.Float(load_default=0.0, validate=validate.Range(min=0, max=1_000_000))
    precio_unitario = fields.Float(load_default=0.0, validate=validate.Range(min=0, max=100_000_000))
    # Destino del stock inicial (feature stock por proyecto). Opcionales: si no
    # vienen, el stock inicial cae en el almacén default y bucket general (compat).
    stock_inicial_almacen_id = fields.Int(load_default=None, allow_none=True)
    stock_inicial_proyecto_id = fields.Int(load_default=None, allow_none=True)
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
    marca = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=100))
    unidad = fields.Str(load_default=None, allow_none=True, validate=validate.Length(min=1, max=50))
    cable_tipo = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=60))
    cable_calibre = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=40))
    stock_actual = fields.Float(load_default=None, allow_none=True, validate=validate.Range(min=0, max=1_000_000))
    stock_minimo = fields.Float(load_default=None, allow_none=True, validate=validate.Range(min=0, max=1_000_000))
    precio_unitario = fields.Float(load_default=None, allow_none=True, validate=validate.Range(min=0, max=100_000_000))
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
    # Pausa 11 — rejilla física. 1×1 = comportamiento plano previo.
    filas = fields.Int(load_default=1, validate=validate.Range(min=1, max=50))
    columnas = fields.Int(load_default=1, validate=validate.Range(min=1, max=50))


class EstanteUpdateSchema(_BaseSchema):
    nombre = fields.Str(load_default=None, allow_none=True, validate=validate.Length(min=1, max=100))
    descripcion = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=250))
    almacen_id = fields.Int(load_default=None, allow_none=True)
    filas = fields.Int(load_default=None, allow_none=True, validate=validate.Range(min=1, max=50))
    columnas = fields.Int(load_default=None, allow_none=True, validate=validate.Range(min=1, max=50))


class EstanteLayoutItemSchema(_BaseSchema):
    """Una colocación de producto en la rejilla del estante.
    fila/columna ambos None = asignado pero sin ubicar (no en una celda)."""
    producto_id = fields.Int(required=True)
    fila = fields.Int(load_default=None, allow_none=True, validate=validate.Range(min=1, max=50))
    columna = fields.Int(load_default=None, allow_none=True, validate=validate.Range(min=1, max=50))
    cantidad = fields.Float(load_default=0.0, validate=validate.Range(min=0, max=1_000_000))


class EstanteLayoutSchema(_BaseSchema):
    posiciones = fields.List(
        fields.Nested(EstanteLayoutItemSchema),
        required=True,
        validate=validate.Length(max=500),
    )


class MovimientoCreateSchema(_BaseSchema):
    tipo = fields.Str(required=True, validate=validate.OneOf(['ENTRADA', 'SALIDA', 'AJUSTE', 'TRASPASO', 'REASIGNACION']))
    producto_id = fields.Int(required=True)
    cantidad = fields.Float(required=True, validate=validate.Range(min=-100_000, max=100_000))
    almacen_origen_id = fields.Int(load_default=None, allow_none=True)
    almacen_destino_id = fields.Int(load_default=None, allow_none=True)
    estante_id = fields.Int(load_default=None, allow_none=True)
    # Bucket de proyecto del movimiento (None = general/sin proyecto):
    #   ENTRADA / AJUSTE+ → proyecto destino    SALIDA / AJUSTE− → proyecto origen
    #   TRASPASO          → bucket que se mueve entre almacenes
    proyecto_id = fields.Int(load_default=None, allow_none=True)
    # Solo REASIGNACION: mueve stock del bucket origen al destino en el MISMO
    # almacén (cualquiera de los dos puede ser None = general).
    proyecto_origen_id = fields.Int(load_default=None, allow_none=True)
    proyecto_destino_id = fields.Int(load_default=None, allow_none=True)
    motivo = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=250))
    # Partes del movimiento para el vale/PDF (feature "vale de movimiento"): quién
    # ENTREGA y quién RECIBE. Cada parte = trabajador del sistema (…_trabajador_id)
    # o nombre libre (…_nombre). Opcionales: no bloquean el flujo actual.
    entrega_trabajador_id = fields.Int(load_default=None, allow_none=True)
    entrega_nombre = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=200))
    recibe_trabajador_id = fields.Int(load_default=None, allow_none=True)
    recibe_nombre = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=200))


class AjusteBucketItemSchema(_BaseSchema):
    """Un bucket (almacén, proyecto|general) con su cantidad OBJETIVO — la que
    debe quedar tras el ajuste. `proyecto_id=None` = bucket general/libre."""
    almacen_id = fields.Int(required=True)
    proyecto_id = fields.Int(load_default=None, allow_none=True)
    cantidad_objetivo = fields.Float(required=True, validate=validate.Range(min=0, max=1_000_000))


class AjusteBucketsSchema(_BaseSchema):
    """Editor de stock por bodega+proyecto: fija la cantidad objetivo de cada
    bucket. El backend calcula el delta contra lo que hay y genera un AJUSTE por
    cada bucket que cambió (fuente de verdad = stock_almacen_proyecto)."""
    buckets = fields.List(
        fields.Nested(AjusteBucketItemSchema),
        required=True,
        validate=validate.Length(min=1, max=200),
    )
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
    # FK opcional al proyecto del sistema. El SPA la manda desde el dropdown;
    # si viene, liga la solicitud para atribuir consumo en el panel de proyectos.
    proyecto_id = fields.Int(load_default=None, allow_none=True)
    notas = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=2000))
    detalles = fields.List(
        fields.Nested(SolicitudDetalleCreateSchema),
        required=True,
        validate=validate.Length(min=1, max=500),
    )


class EntregaDirectaItemSchema(_BaseSchema):
    """Una línea de material de la entrega directa (mostrador)."""
    producto_id = fields.Int(required=True)
    cantidad = fields.Float(required=True, validate=validate.Range(min=0.0001, max=100_000))
    # Pausa 11 — opcional: de qué estante (celda) se surte, para descontar el
    # sub-libro de celdas. None = no toca celdas.
    estante_id = fields.Int(load_default=None, allow_none=True)


class EntregaDirectaSchema(_BaseSchema):
    """Entrega directa de mostrador: el de inventario surte material en el acto.

    El solicitante real es un trabajador del sistema (solicitante_trabajador_id)
    o un nombre libre (solicitante_nombre); al menos uno es obligatorio (se
    valida en la vista). Genera una SolicitudMaterial ya ENTREGADA + SALIDAs.
    """
    proyecto = fields.Str(required=True, validate=validate.Length(min=1, max=200))
    proyecto_id = fields.Int(load_default=None, allow_none=True)
    solicitante_trabajador_id = fields.Int(load_default=None, allow_none=True)
    solicitante_nombre = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=200))
    almacen_origen_id = fields.Int(load_default=None, allow_none=True)
    notas = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=2000))
    motivo = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=250))
    detalles = fields.List(
        fields.Nested(EntregaDirectaItemSchema),
        required=True,
        validate=validate.Length(min=1, max=500),
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
    # Pausa 11 — opcional: de qué estante (celda) se surte esta línea, para
    # descontar el sub-libro de celdas. None = no toca celdas (comportamiento previo).
    estante_id = fields.Int(load_default=None, allow_none=True)


class EntregarSolicitudSchema(_BaseSchema):
    almacen_origen_id = fields.Int(load_default=None, allow_none=True)
    motivo = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=250))
    # Para líneas HERRAMIENTA: fecha de devolución prevista que se copia a cada
    # AsignacionHerramienta generada. Opcional.
    fecha_devolucion_prevista = fields.DateTime(load_default=None, allow_none=True)
    entregas = fields.List(
        fields.Nested(EntregaItemSchema),
        required=True,
        validate=validate.Length(min=1, max=500),
    )


class CategoriaConfigUpsertSchema(_BaseSchema):
    imagen_url = fields.Str(load_default=None, allow_none=True, validate=[
        validate.Length(max=500),
        validate.Regexp(_IMAGEN_URL_REGEX, error='imagen_url debe ser HTTPS o un path absoluto a imagen local'),
    ])


# ─── Plan de materiales por proyecto (Inventario → Proyectos) ────────────────

class ProyectoPlanLineaSchema(_BaseSchema):
    producto_id = fields.Int(required=True)
    cantidad_planeada = fields.Float(required=True, validate=validate.Range(min=0, max=1_000_000))
    notas = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=500))


class ProyectoPlanUpsertSchema(_BaseSchema):
    # Reemplaza el plan completo del proyecto con estas líneas (upsert por
    # producto). Una lista vacía deja el plan sin líneas.
    lineas = fields.List(
        fields.Nested(ProyectoPlanLineaSchema),
        required=True,
        validate=validate.Length(max=500),
    )


# ─── Serializers ──────────────────────────────────────────────────────────────

def _producto_to_dict(p: Producto) -> dict:
    actual = float(p.stock_actual or 0)
    reservado = float(p.stock_reservado or 0)
    return {
        'id': p.id,
        'codigo': p.codigo,
        'descripcion': p.descripcion,
        'categoria': p.categoria,
        # Marca / fabricante (None si no se capturó). Independiente del proveedor.
        'marca': p.marca,
        'unidad': p.unidad,
        # Atributos de cable (None en productos que no son cable).
        'cable_tipo': p.cable_tipo,
        'cable_calibre': p.cable_calibre,
        'stock_actual': actual,
        'stock_reservado': reservado,         # Pausa 2-bis: apartado por solicitudes APROBADAS
        'stock_disponible': actual - reservado,  # lo que sí se puede mover
        'stock_minimo': float(p.stock_minimo or 0),
        'precio_unitario': float(p.precio_unitario or 0),
        'imagen_url': p.imagen_url,
        # Estado del pipeline de imágenes → R2 (None si no aplica / R2 apagado).
        # El SPA lo usa para mostrar un badge "procesando/error" si quiere.
        'imagen_estado': p.imagen_estado,
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
        'filas': e.filas or 1,
        'columnas': e.columnas or 1,
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
        # Atribución de proyecto (None = general). Se incluye el número de
        # proyecto para pintarlo sin resolver por id en el cliente.
        'proyecto_origen_id': m.proyecto_origen_id,
        'proyecto_destino_id': m.proyecto_destino_id,
        'proyecto_origen': m.proyecto_origen.numero_proyecto if m.proyecto_origen else None,
        'proyecto_destino': m.proyecto_destino.numero_proyecto if m.proyecto_destino else None,
        'usuario_id': m.usuario_id,
        'motivo': m.motivo,
        # Partes del vale (None si no se capturaron / movimiento interno).
        'entrega_trabajador_id': m.entrega_trabajador_id,
        'recibe_trabajador_id': m.recibe_trabajador_id,
        'entrega_nombre': m.entrega_display,
        'recibe_nombre': m.recibe_display,
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
        # Atributos de cable: solo aplican a MATERIAL de cable (None en el resto).
        'cable_tipo': None,
        'cable_calibre': None,
    }
    if tipo == 'HERRAMIENTA':
        base['herramienta_id'] = d.herramienta_id
        base['producto_id'] = None
        base['item_descripcion'] = d.herramienta.descripcion if d.herramienta else 'Herramienta eliminada'
        base['item_codigo'] = d.herramienta.sku if d.herramienta else '---'
        base['item_unidad'] = d.herramienta.unidad if d.herramienta else 'pza'
        base['imagen_url'] = None  # herramientas usan su propio sistema de media
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
        base['imagen_url'] = d.producto.imagen_url if d.producto else None
        if d.producto:
            base['cable_tipo'] = d.producto.cable_tipo
            base['cable_calibre'] = d.producto.cable_calibre
        base['producto_descripcion'] = base['item_descripcion']
        base['producto_codigo'] = base['item_codigo']
        base['producto_unidad'] = base['item_unidad']
    return base


def _solicitud_to_dict(s: SolicitudMaterial) -> dict:
    return {
        'id': s.id,
        'solicitante_id': s.solicitante_id,
        'proyecto': s.proyecto,
        'proyecto_id': s.proyecto_id,
        'notas': s.notas,
        'estatus': s.estatus,
        'fecha_creacion': s.fecha_creacion.isoformat() if s.fecha_creacion else None,
        'fecha_cierre': s.fecha_cierre.isoformat() if s.fecha_cierre else None,
        # Nombre del solicitante REAL (trabajador / texto libre en entregas
        # directas; el capturista en solicitudes normales). Ver solicitante_display.
        'solicitante_nombre': s.solicitante_display,
        # Quién la capturó (útil para distinguir mostrador del solicitante).
        'capturado_por': (s.solicitante.full_name or s.solicitante.username) if s.solicitante else None,
        'entrega_directa': bool(s.entrega_directa),
        'solicitante_trabajador_id': s.solicitante_trabajador_id,
        # Trazabilidad de la resolución.
        'aprobada_por': s._user_display(s.aprobada_por),
        'entregada_por': s._user_display(s.entregada_por),
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
    """Locks + valida disponibilidad GLOBAL. Aplica las reservas si TODOS los
    productos alcanzan. Si alguno no, NO aplica nada (caller debe hacer rollback)
    y devuelve lista de errores legibles para el SPA.
    Disponible = stock_actual − stock_reservado.

    Nota de diseño (feature stock por proyecto): la reserva/aprobación sigue
    siendo GLOBAL por producto (como antes) — permisiva a propósito, igual que el
    resto del sistema. El "candado" por proyecto (no consumir stock etiquetado a
    OTRO proyecto) se aplica en el consumo físico de la ENTREGA/SALIDA vía
    `_consumir_proyecto_luego_general`. La disponibilidad POR proyecto se expone
    aparte, solo informativa, en `GET /productos/<id>/disponibilidad?proyecto_id=`."""
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


def _producto_almacen_stock(producto_ids, almacen_id: int) -> dict[int, Decimal]:
    """Stock por almacén de varios productos: {producto_id: cantidad}.
    Devuelve 0 para los productos sin fila en StockPorAlmacen para ese almacén.
    Usado para validar el invariante Σceldas ≤ stock_almacen (Pausa 11)."""
    ids = [int(p) for p in set(producto_ids)]
    out: dict[int, Decimal] = {pid: Decimal('0') for pid in ids}
    if not ids:
        return out
    rows = (
        db.session.query(StockPorAlmacen.producto_id, StockPorAlmacen.cantidad)
        .filter(
            StockPorAlmacen.almacen_id == almacen_id,
            StockPorAlmacen.producto_id.in_(ids),
        )
        .all()
    )
    for pid, cant in rows:
        out[pid] = Decimal(str(cant or 0))
    return out


def _cantidad_en_celdas_almacen(producto_id: int, almacen_id: int, excluir_estante_id: int | None = None) -> Decimal:
    """Σ ProductoEstante.cantidad de un producto en los estantes (activos) de un
    almacén, opcionalmente excluyendo un estante (el que se está editando)."""
    q = (
        db.session.query(db.func.coalesce(db.func.sum(ProductoEstante.cantidad), 0))
        .join(Estante, Estante.id == ProductoEstante.estante_id)
        .filter(
            ProductoEstante.producto_id == producto_id,
            Estante.almacen_id == almacen_id,
            Estante.activo == True,  # noqa: E712
        )
    )
    if excluir_estante_id is not None:
        q = q.filter(ProductoEstante.estante_id != excluir_estante_id)
    return Decimal(str(q.scalar() or 0))


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


# ─── Stock por proyecto (feature "stock por proyecto") ───────────────────────
#
# `stock_almacen_proyecto` es la fuente de verdad de grano (producto, almacén,
# proyecto|NULL=general). `stock_por_almacen` y `Producto.stock_actual` quedan
# como caches, recalculados por `_recalcular_caches` tras cada mutación.

def _lock_stock_proyecto(producto_id: int, almacen_id: int,
                         proyecto_id: int | None) -> StockAlmacenProyecto:
    """SELECT ... FOR UPDATE sobre la fila (producto, almacén, proyecto|NULL).
    Crea la fila en 0 si no existe. `proyecto_id=None` = bucket general.
    nowait=True: si otra transacción la tiene, se levanta y el caller devuelve
    409 'reintenta' (mismo patrón que `_lock_stock`)."""
    q = (
        db.session.query(StockAlmacenProyecto)
        .with_for_update(nowait=True)
        .filter(
            StockAlmacenProyecto.producto_id == producto_id,
            StockAlmacenProyecto.almacen_id == almacen_id,
        )
    )
    q = q.filter(StockAlmacenProyecto.proyecto_id.is_(None)) if proyecto_id is None \
        else q.filter(StockAlmacenProyecto.proyecto_id == proyecto_id)
    fila = q.first()
    if fila is None:
        fila = StockAlmacenProyecto(
            producto_id=producto_id, almacen_id=almacen_id,
            proyecto_id=proyecto_id, cantidad=Decimal('0'),
        )
        db.session.add(fila)
        db.session.flush()
    return fila


def _recalcular_caches(producto: Producto, almacen_id: int):
    """Recalcula, en cascada, los dos caches tras mutar stock_almacen_proyecto:
      1. stock_por_almacen(producto, almacen) = Σ buckets de proyecto del almacén.
      2. Producto.stock_actual = Σ almacenes (vía `_recalcular_cache_stock`).
    Se llama dentro de la misma transacción que modificó los buckets."""
    total_alm = (
        db.session.query(db.func.coalesce(db.func.sum(StockAlmacenProyecto.cantidad), 0))
        .filter(
            StockAlmacenProyecto.producto_id == producto.id,
            StockAlmacenProyecto.almacen_id == almacen_id,
        )
        .scalar()
    )
    total_alm = Decimal(str(total_alm or 0))
    fila = (
        db.session.query(StockPorAlmacen)
        .filter(
            StockPorAlmacen.producto_id == producto.id,
            StockPorAlmacen.almacen_id == almacen_id,
        )
        .first()
    )
    if fila is None:
        db.session.add(StockPorAlmacen(
            producto_id=producto.id, almacen_id=almacen_id, cantidad=total_alm,
        ))
    else:
        fila.cantidad = total_alm
    db.session.flush()  # que el sum de _recalcular_cache_stock vea el cache nuevo
    _recalcular_cache_stock(producto)


def _depositar(producto_id: int, almacen_id: int, proyecto_id: int | None,
               cantidad) -> StockAlmacenProyecto:
    """Suma `cantidad` al bucket (producto, almacén, proyecto|general)."""
    fila = _lock_stock_proyecto(producto_id, almacen_id, proyecto_id)
    fila.cantidad = (fila.cantidad or Decimal('0')) + Decimal(str(cantidad))
    return fila


def _consumir_proyecto_luego_general(producto_id: int, almacen_id: int,
                                     proyecto_id: int | None, cantidad) -> str | None:
    """Descuenta `cantidad` del almacén dado aplicando la regla PROYECTO→GENERAL:
    primero del bucket del proyecto, el remanente del general. NUNCA toca el
    stock etiquetado a OTROS proyectos. Con `proyecto_id=None` sale solo del
    general. Devuelve None si ok, o un string de error si el físico no alcanza.
    Bloquea las filas con FOR UPDATE."""
    cantidad = Decimal(str(cantidad))
    if cantidad <= 0:
        return None
    fila_gen = _lock_stock_proyecto(producto_id, almacen_id, None)
    disp_gen = Decimal(str(fila_gen.cantidad or 0))
    fila_proj = None
    disp_proj = Decimal('0')
    if proyecto_id is not None:
        fila_proj = _lock_stock_proyecto(producto_id, almacen_id, proyecto_id)
        disp_proj = Decimal(str(fila_proj.cantidad or 0))
    if disp_proj + disp_gen < cantidad:
        return (
            f'requiere {cantidad}, disponible {disp_proj + disp_gen} en el almacén '
            f'(proyecto {disp_proj} + general {disp_gen})'
        )
    if proyecto_id is not None:
        take_proj = disp_proj if disp_proj < cantidad else cantidad
        fila_proj.cantidad = disp_proj - take_proj
        resto = cantidad - take_proj
        if resto > 0:
            fila_gen.cantidad = disp_gen - resto
    else:
        fila_gen.cantidad = disp_gen - cantidad
    return None


def _consumir_bucket_exacto(producto_id: int, almacen_id: int,
                            proyecto_id: int | None, cantidad) -> str | None:
    """Descuenta `cantidad` EXACTAMENTE del bucket indicado, sin fallback a
    general. Para TRASPASO/REASIGNACION, que mueven un bucket específico y deben
    dejar cuadrado el bucket destino. Devuelve None si ok o un error string."""
    cantidad = Decimal(str(cantidad))
    if cantidad <= 0:
        return None
    fila = _lock_stock_proyecto(producto_id, almacen_id, proyecto_id)
    disp = Decimal(str(fila.cantidad or 0))
    if disp < cantidad:
        etiqueta = 'general' if proyecto_id is None else f'proyecto #{proyecto_id}'
        return f'el bucket {etiqueta} solo tiene {disp} en el almacén (requiere {cantidad})'
    fila.cantidad = disp - cantidad
    return None


def _consumir_reconciliando(producto_id: int, almacen_id: int, cantidad) -> str | None:
    """Descuenta `cantidad` del almacén tomando de CUALQUIER bucket: general
    primero, luego los de proyecto (mayor cantidad primero). Se usa SOLO para
    reconciliar una TOMA física a nivel almacén: el conteo es la verdad, así que
    si físicamente hay menos se reduce de donde haya. A diferencia del consumo
    normal, NO respeta la etiqueta de proyecto (el faltante físico ya ocurrió).
    Devuelve None o un error si el total del almacén no alcanza."""
    restante = Decimal(str(cantidad))
    if restante <= 0:
        return None
    fila_gen = _lock_stock_proyecto(producto_id, almacen_id, None)
    disp_gen = Decimal(str(fila_gen.cantidad or 0))
    toma_gen = disp_gen if disp_gen < restante else restante
    if toma_gen > 0:
        fila_gen.cantidad = disp_gen - toma_gen
        restante -= toma_gen
    if restante > 0:
        filas = (
            db.session.query(StockAlmacenProyecto)
            .with_for_update(nowait=True)
            .filter(
                StockAlmacenProyecto.producto_id == producto_id,
                StockAlmacenProyecto.almacen_id == almacen_id,
                StockAlmacenProyecto.proyecto_id.isnot(None),
                StockAlmacenProyecto.cantidad > 0,
            )
            .order_by(StockAlmacenProyecto.cantidad.desc())
            .all()
        )
        for f in filas:
            if restante <= 0:
                break
            disp = Decimal(str(f.cantidad or 0))
            toma = disp if disp < restante else restante
            f.cantidad = disp - toma
            restante -= toma
    if restante > 0:
        return f'requiere {cantidad}, faltan {restante} en el almacén (todos los buckets)'
    return None


def _stock_proyecto_total(producto_id: int, proyecto_id: int | None) -> Decimal:
    """Σ cantidad del bucket (producto, proyecto|general) sobre TODOS los
    almacenes. El almacén se resuelve en la entrega, así que la disponibilidad
    a nivel de aprobación es cross-almacén (como la reserva)."""
    q = (
        db.session.query(db.func.coalesce(db.func.sum(StockAlmacenProyecto.cantidad), 0))
        .filter(StockAlmacenProyecto.producto_id == producto_id)
    )
    q = q.filter(StockAlmacenProyecto.proyecto_id.is_(None)) if proyecto_id is None \
        else q.filter(StockAlmacenProyecto.proyecto_id == proyecto_id)
    return Decimal(str(q.scalar() or 0))


def _reserva_derivada(producto_id: int, proyecto_id: int | None,
                      excluir_solicitud_id: int | None = None) -> Decimal:
    """Reserva vigente del bucket (producto, proyecto|general): suma del
    pendiente (aprobada−entregada, con fallback a solicitada) de las solicitudes
    APROBADAS ligadas a ese proyecto. Mismo criterio que `_reservas_de_solicitud`
    pero agregado en SQL. Evita GREATEST/LEAST (no existen en SQLite) con CASE."""
    base = db.case(
        (SolicitudMaterialDetalle.cantidad_aprobada > 0, SolicitudMaterialDetalle.cantidad_aprobada),
        else_=SolicitudMaterialDetalle.cantidad_solicitada,
    )
    pend = base - db.func.coalesce(SolicitudMaterialDetalle.cantidad_entregada, 0)
    reserved_row = db.case((pend > 0, pend), else_=0)
    q = (
        db.session.query(db.func.coalesce(db.func.sum(reserved_row), 0))
        .join(SolicitudMaterial, SolicitudMaterial.id == SolicitudMaterialDetalle.solicitud_id)
        .filter(
            SolicitudMaterial.estatus == 'APROBADA',
            SolicitudMaterialDetalle.producto_id == producto_id,
        )
    )
    q = q.filter(SolicitudMaterial.proyecto_id.is_(None)) if proyecto_id is None \
        else q.filter(SolicitudMaterial.proyecto_id == proyecto_id)
    if excluir_solicitud_id is not None:
        q = q.filter(SolicitudMaterial.id != excluir_solicitud_id)
    return Decimal(str(q.scalar() or 0))


def _resolver_partes(data: dict):
    """Valida y resuelve las partes (entrega/recibe) de un movimiento a partir del
    payload validado por `MovimientoCreateSchema`.

    Cada parte puede venir como trabajador del sistema (`*_trabajador_id`) o como
    nombre libre (`*_nombre`). Si viene un `*_trabajador_id` debe existir y estar
    activo (mismo criterio que la entrega directa). Devuelve una tupla
    `(campos, error)` donde `campos` es el dict listo para asignar al modelo
    (`entrega_trabajador_id`, `entrega_nombre`, `recibe_trabajador_id`,
    `recibe_nombre`) y `error` es un `Response` 422 (o None si todo ok). Ambas
    partes son OPCIONALES: si no se manda nada, `campos` queda en None."""
    campos = {
        'entrega_trabajador_id': None, 'entrega_nombre': None,
        'recibe_trabajador_id': None, 'recibe_nombre': None,
    }
    for parte, key_id, key_nombre in (
        ('Entrega', 'entrega_trabajador_id', 'entrega_nombre'),
        ('Recibe', 'recibe_trabajador_id', 'recibe_nombre'),
    ):
        trab_id = data.get(key_id)
        nombre = (data.get(key_nombre) or '').strip() or None
        if trab_id is not None:
            trab = Trabajador.query.filter(
                Trabajador.id == trab_id,
                Trabajador.activo == True,  # noqa: E712
            ).first()
            if not trab:
                return None, (jsonify({'detail': f'{parte}: trabajador #{trab_id} no existe o está inactivo'}), 422)
            # El trabajador manda: ignoramos un nombre libre redundante.
            campos[key_id] = trab.id
            campos[key_nombre] = None
        else:
            campos[key_nombre] = nombre
    return campos, None


def _ajustar_bucket(producto: Producto, almacen_id: int, proyecto_id, delta: Decimal,
                    user: User, motivo: str) -> str | None:
    """Aplica un AJUSTE de `delta` (±) al bucket (producto, almacén, proyecto|general)
    SIN commitear — el caller controla la transacción (editor de stock por bucket).

    Reusa los helpers de stock por proyecto: `_depositar` para deltas positivos y
    `_consumir_bucket_exacto` (bucket exacto, sin fallback a general) para los
    negativos, luego `_recalcular_caches`. Registra un MovimientoInventario
    tipo AJUSTE con la atribución de proyecto correspondiente. Devuelve None si ok
    o un string de error (bucket insuficiente). No valida reservas aquí: el caller
    hace el guard global tras aplicar todos los buckets."""
    delta = Decimal(str(delta))
    if delta == 0:
        return None
    if delta > 0:
        _depositar(producto.id, almacen_id, proyecto_id, delta)
        mov_proy_destino, mov_proy_origen = proyecto_id, None
    else:
        err = _consumir_bucket_exacto(producto.id, almacen_id, proyecto_id, -delta)
        if err:
            return err
        mov_proy_destino, mov_proy_origen = None, proyecto_id
    _recalcular_caches(producto, almacen_id)
    db.session.add(MovimientoInventario(
        tipo='AJUSTE',
        producto_id=producto.id,
        cantidad=delta,
        almacen_origen_id=(almacen_id if delta < 0 else None),
        almacen_destino_id=(almacen_id if delta > 0 else None),
        proyecto_origen_id=mov_proy_origen,
        proyecto_destino_id=mov_proy_destino,
        motivo=(motivo or 'Ajuste manual desde edición')[:250],
        usuario_id=user.id,
    ))
    return None


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
