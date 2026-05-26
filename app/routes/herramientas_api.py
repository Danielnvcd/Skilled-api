"""Endpoints JSON del módulo de Herramientas.

Hermano del blueprint `inventario_api` (materiales). Reusa los decoradores
de auth y el patrón Marshmallow + 422 + with_for_update + AuditLog +
EventoHerramienta (bitácora dedicada).

Reglas de oro:
  - Baja siempre lógica (cambio de estado a DADA_DE_BAJA + fecha_baja).
  - Toda transición de estado de unidad genera EventoHerramienta.
  - `with_for_update(nowait=True)` en la unidad para evitar dobles asignaciones.
  - Solicitante: ve solo lo suyo. Inventario/admin: ve todo y autoriza.
"""
import io
import os
import uuid
import logging
import datetime
from decimal import Decimal
from functools import wraps

import qrcode
from flask import Blueprint, jsonify, request, Response, send_file, current_app, g
from marshmallow import Schema, fields, validate, ValidationError, EXCLUDE
from sqlalchemy import or_, and_, func
from sqlalchemy.orm import joinedload, selectinload

from app.extensions import db, limiter, get_real_client_ip_flask
from app.models import (
    User, AuditLog, Trabajador, Almacen, Estante,
    Herramienta, HerramientaUnidad, HerramientaCategoria,
    AsignacionHerramienta, MantenimientoHerramienta,
    IncidenciaHerramienta, SolicitudBajaHerramienta,
    EventoHerramienta, MediaHerramienta,
    SolicitudMaterial,
    ESTADOS_UNIDAD, ESTADOS_ASIGNACION, ESTADOS_MANTENIMIENTO,
    ESTADOS_INCIDENCIA, ESTADOS_SOLICITUD_BAJA, USO_HERRAMIENTA,
    TIPO_INCIDENCIA, TIPO_MANTENIMIENTO, CONDICION_HERRAMIENTA,
    crear_evento_herramienta, crear_notif_inventario, Notificacion,
)
from app.routes.inventario_api import (
    _require_login, _require_inventario, _require_inventario_admin,
    _parse_or_422, _int_arg, _audit, CODIGO_REGEX, _IMAGEN_URL_REGEX,
)

logger = logging.getLogger(__name__)

bp = Blueprint('herramientas_api', __name__, url_prefix='/api/v1')


# ─── Constantes ──────────────────────────────────────────────────────────────

UPLOAD_MAX_BYTES = 5 * 1024 * 1024  # 5 MB
ALLOWED_IMAGE_MIMES = {'image/png', 'image/jpeg', 'image/jpg', 'image/webp'}
ALLOWED_IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.webp'}


# ─── Schemas Marshmallow ─────────────────────────────────────────────────────

class _BaseSchema(Schema):
    class Meta:
        unknown = EXCLUDE


class HerramientaCreateSchema(_BaseSchema):
    sku = fields.Str(required=True, validate=[validate.Length(min=1, max=50),
                                              validate.Regexp(CODIGO_REGEX)])
    descripcion = fields.Str(required=True, validate=validate.Length(min=1, max=250))
    clasificacion = fields.Str(required=True, validate=validate.Length(min=1, max=100))
    marca = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=100))
    modelo = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=100))
    uso = fields.Str(load_default='OTRO', validate=validate.OneOf(USO_HERRAMIENTA))
    unidad = fields.Str(required=True, validate=validate.Length(min=1, max=50))
    piezas = fields.Int(load_default=1, validate=validate.Range(min=1, max=1000))
    serializada = fields.Bool(load_default=True)
    imagen_url = fields.Str(load_default=None, allow_none=True, validate=[
        validate.Length(max=500), validate.Regexp(_IMAGEN_URL_REGEX),
    ])


class HerramientaUpdateSchema(_BaseSchema):
    sku = fields.Str(load_default=None, allow_none=True, validate=[validate.Length(min=1, max=50),
                                                                    validate.Regexp(CODIGO_REGEX)])
    descripcion = fields.Str(load_default=None, allow_none=True, validate=validate.Length(min=1, max=250))
    clasificacion = fields.Str(load_default=None, allow_none=True, validate=validate.Length(min=1, max=100))
    marca = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=100))
    modelo = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=100))
    uso = fields.Str(load_default=None, allow_none=True, validate=validate.OneOf(USO_HERRAMIENTA))
    unidad = fields.Str(load_default=None, allow_none=True, validate=validate.Length(min=1, max=50))
    piezas = fields.Int(load_default=None, allow_none=True, validate=validate.Range(min=1, max=1000))
    imagen_url = fields.Str(load_default=None, allow_none=True, validate=[
        validate.Length(max=500), validate.Regexp(_IMAGEN_URL_REGEX),
    ])


class UnidadCreateSchema(_BaseSchema):
    herramienta_id = fields.Int(required=True)
    no_serie = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=100))
    almacen_id = fields.Int(load_default=None, allow_none=True)
    estante_id = fields.Int(load_default=None, allow_none=True)
    cantidad = fields.Float(load_default=1.0, validate=validate.Range(min=0.01, max=1_000_000))
    complementos = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=500))
    fecha_adquisicion = fields.Date(load_default=None, allow_none=True)
    costo_adquisicion = fields.Float(load_default=None, allow_none=True, validate=validate.Range(min=0, max=1_000_000))
    vida_util_meses = fields.Int(load_default=None, allow_none=True, validate=validate.Range(min=0, max=600))
    observaciones = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=1000))


class UnidadUpdateSchema(_BaseSchema):
    no_serie = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=100))
    almacen_id = fields.Int(load_default=None, allow_none=True)
    estante_id = fields.Int(load_default=None, allow_none=True)
    cantidad = fields.Float(load_default=None, allow_none=True, validate=validate.Range(min=0.01, max=1_000_000))
    complementos = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=500))
    fecha_adquisicion = fields.Date(load_default=None, allow_none=True)
    costo_adquisicion = fields.Float(load_default=None, allow_none=True, validate=validate.Range(min=0, max=1_000_000))
    vida_util_meses = fields.Int(load_default=None, allow_none=True, validate=validate.Range(min=0, max=600))
    observaciones = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=1000))


class AsignacionCreateSchema(_BaseSchema):
    unidad_id = fields.Int(required=True)
    trabajador_id = fields.Int(required=True)
    solicitud_id = fields.Int(load_default=None, allow_none=True)
    proyecto = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=200))
    fecha_devolucion_prevista = fields.DateTime(load_default=None, allow_none=True)
    condicion_entrega = fields.Str(load_default='BUENA', validate=validate.OneOf(CONDICION_HERRAMIENTA))
    observaciones_entrega = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=1000))


class DevolucionSchema(_BaseSchema):
    condicion_devolucion = fields.Str(required=True, validate=validate.OneOf(CONDICION_HERRAMIENTA))
    observaciones_devolucion = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=1000))
    nuevo_estado_unidad = fields.Str(load_default='DISPONIBLE',
                                       validate=validate.OneOf(['DISPONIBLE', 'DAÑADA', 'EXTRAVIADA']))


class MantenimientoCreateSchema(_BaseSchema):
    unidad_id = fields.Int(required=True)
    tipo = fields.Str(required=True, validate=validate.OneOf(TIPO_MANTENIMIENTO))
    motivo = fields.Str(required=True, validate=validate.Length(min=3, max=250))
    proveedor = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=150))
    costo = fields.Float(load_default=None, allow_none=True, validate=validate.Range(min=0, max=1_000_000))
    observaciones = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=1000))


class MantenimientoCierreSchema(_BaseSchema):
    estado_final_unidad = fields.Str(required=True,
                                       validate=validate.OneOf(['DISPONIBLE', 'DAÑADA', 'DADA_DE_BAJA']))
    costo_real = fields.Float(load_default=None, allow_none=True, validate=validate.Range(min=0, max=1_000_000))
    observaciones = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=1000))


class IncidenciaCreateSchema(_BaseSchema):
    unidad_id = fields.Int(required=True)
    tipo = fields.Str(required=True, validate=validate.OneOf(TIPO_INCIDENCIA))
    descripcion = fields.Str(required=True, validate=validate.Length(min=5, max=2000))


class IncidenciaAtenderSchema(_BaseSchema):
    estado = fields.Str(required=True, validate=validate.OneOf(['REVISION', 'RESUELTA', 'RECHAZADA']))
    resolucion = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=2000))


class SolicitudBajaCreateSchema(_BaseSchema):
    unidad_id = fields.Int(required=True)
    motivo = fields.Str(required=True, validate=validate.Length(min=10, max=2000))


class SolicitudBajaAutorizarSchema(_BaseSchema):
    observaciones = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=1000))


class CategoriaUpsertSchema(_BaseSchema):
    imagen_url = fields.Str(load_default=None, allow_none=True, validate=[
        validate.Length(max=500), validate.Regexp(_IMAGEN_URL_REGEX),
    ])
    icono = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=50))
    color = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=20))


# ─── Serializadores ──────────────────────────────────────────────────────────

def _herramienta_to_dict(h: Herramienta, *, incluir_stats=False) -> dict:
    base = {
        'id': h.id,
        'sku': h.sku,
        'descripcion': h.descripcion,
        'clasificacion': h.clasificacion,
        'categoria_id': h.categoria_id,
        'marca': h.marca,
        'modelo': h.modelo,
        'uso': h.uso,
        'unidad': h.unidad,
        'piezas': h.piezas,
        'serializada': bool(h.serializada),
        'imagen_url': h.imagen_url,
        'activo': bool(h.activo),
        'created_at': h.created_at.isoformat() if h.created_at else None,
        'updated_at': h.updated_at.isoformat() if h.updated_at else None,
    }
    if incluir_stats:
        stats = {e: 0 for e in ESTADOS_UNIDAD}
        for u in h.unidades:
            stats[u.estado] = stats.get(u.estado, 0) + 1
        base['stats_estados'] = stats
        base['total_unidades'] = sum(stats.values())
    return base


def _unidad_to_dict(u: HerramientaUnidad, *, incluir_relacion=False) -> dict:
    base = {
        'id': u.id,
        'herramienta_id': u.herramienta_id,
        'no_serie': u.no_serie,
        'codigo_interno': u.codigo_interno,
        'qr_code': u.qr_code,
        'estado': u.estado,
        'almacen_id': u.almacen_id,
        'estante_id': u.estante_id,
        'asignado_trabajador_id': u.asignado_trabajador_id,
        'cantidad': float(u.cantidad or 1),
        'complementos': u.complementos,
        'fecha_adquisicion': u.fecha_adquisicion.isoformat() if u.fecha_adquisicion else None,
        'costo_adquisicion': float(u.costo_adquisicion) if u.costo_adquisicion is not None else None,
        'vida_util_meses': u.vida_util_meses,
        'observaciones': u.observaciones,
        'fecha_baja': u.fecha_baja.isoformat() if u.fecha_baja else None,
        'motivo_baja': u.motivo_baja,
        'created_at': u.created_at.isoformat() if u.created_at else None,
    }
    if incluir_relacion:
        base['herramienta'] = {
            'id': u.herramienta.id,
            'sku': u.herramienta.sku,
            'descripcion': u.herramienta.descripcion,
            'clasificacion': u.herramienta.clasificacion,
            'marca': u.herramienta.marca,
            'modelo': u.herramienta.modelo,
            'imagen_url': u.herramienta.imagen_url,
        } if u.herramienta else None
        base['almacen_nombre'] = u.almacen.nombre if u.almacen else None
        base['estante_nombre'] = u.estante.nombre if u.estante else None
        base['trabajador_nombre'] = u.asignado_trabajador.nombre_completo if u.asignado_trabajador else None
        foto_principal = next((m for m in u.media if m.tipo == 'FOTO_HERRAMIENTA'), None)
        base['foto_principal_id'] = foto_principal.id if foto_principal else None
    return base


def _asignacion_to_dict(a: AsignacionHerramienta) -> dict:
    return {
        'id': a.id,
        'unidad_id': a.unidad_id,
        'trabajador_id': a.trabajador_id,
        'trabajador_nombre': a.trabajador.nombre_completo if a.trabajador else None,
        'unidad_codigo': a.unidad.codigo_interno if a.unidad else None,
        'unidad_no_serie': a.unidad.no_serie if a.unidad else None,
        'solicitud_id': a.solicitud_id,
        'proyecto': a.proyecto,
        'fecha_entrega': a.fecha_entrega.isoformat() if a.fecha_entrega else None,
        'fecha_devolucion_prevista': a.fecha_devolucion_prevista.isoformat() if a.fecha_devolucion_prevista else None,
        'fecha_devolucion_real': a.fecha_devolucion_real.isoformat() if a.fecha_devolucion_real else None,
        'estado': a.estado,
        'condicion_entrega': a.condicion_entrega,
        'condicion_devolucion': a.condicion_devolucion,
        'observaciones_entrega': a.observaciones_entrega,
        'observaciones_devolucion': a.observaciones_devolucion,
        'entregado_por_id': a.entregado_por_id,
        'entregado_por_username': a.entregado_por.username if a.entregado_por else None,
        'recibido_por_id': a.recibido_por_id,
    }


def _mantenimiento_to_dict(m: MantenimientoHerramienta) -> dict:
    return {
        'id': m.id,
        'unidad_id': m.unidad_id,
        'tipo': m.tipo,
        'motivo': m.motivo,
        'proveedor': m.proveedor,
        'fecha_inicio': m.fecha_inicio.isoformat() if m.fecha_inicio else None,
        'fecha_fin': m.fecha_fin.isoformat() if m.fecha_fin else None,
        'costo': float(m.costo) if m.costo is not None else None,
        'observaciones': m.observaciones,
        'estado_final_unidad': m.estado_final_unidad,
        'estado': m.estado,
        'abierto_por_id': m.abierto_por_id,
        'cerrado_por_id': m.cerrado_por_id,
    }


def _incidencia_to_dict(i: IncidenciaHerramienta) -> dict:
    return {
        'id': i.id,
        'unidad_id': i.unidad_id,
        'reportado_por_id': i.reportado_por_id,
        'reportado_por_username': i.reportado_por.username if i.reportado_por else None,
        'tipo': i.tipo,
        'descripcion': i.descripcion,
        'estado': i.estado,
        'fecha_reporte': i.fecha_reporte.isoformat() if i.fecha_reporte else None,
        'atendido_por_id': i.atendido_por_id,
        'resolucion': i.resolucion,
        'fecha_cierre': i.fecha_cierre.isoformat() if i.fecha_cierre else None,
    }


def _solicitud_baja_to_dict(s: SolicitudBajaHerramienta) -> dict:
    return {
        'id': s.id,
        'unidad_id': s.unidad_id,
        'solicitante_id': s.solicitante_id,
        'solicitante_username': s.solicitante.username if s.solicitante else None,
        'motivo': s.motivo,
        'estado': s.estado,
        'autorizado_por_id': s.autorizado_por_id,
        'ejecutado_por_id': s.ejecutado_por_id,
        'fecha_solicitud': s.fecha_solicitud.isoformat() if s.fecha_solicitud else None,
        'fecha_autorizacion': s.fecha_autorizacion.isoformat() if s.fecha_autorizacion else None,
        'fecha_ejecucion': s.fecha_ejecucion.isoformat() if s.fecha_ejecucion else None,
        'observaciones': s.observaciones,
    }


def _evento_to_dict(e: EventoHerramienta) -> dict:
    return {
        'id': e.id,
        'tipo_evento': e.tipo_evento,
        'estado_anterior': e.estado_anterior,
        'estado_nuevo': e.estado_nuevo,
        'usuario_id': e.usuario_id,
        'usuario_username': e.usuario.username if e.usuario else None,
        'observaciones': e.observaciones,
        'referencia_id': e.referencia_id,
        'referencia_tipo': e.referencia_tipo,
        'fecha': e.fecha.isoformat() if e.fecha else None,
    }


def _media_to_dict(m: MediaHerramienta) -> dict:
    return {
        'id': m.id,
        'unidad_id': m.unidad_id,
        'evento_id': m.evento_id,
        'tipo': m.tipo,
        'ruta_archivo': m.ruta_archivo,
        'url': f"/api/v1/herramientas-unidades/{m.unidad_id}/media/{m.id}",
        'nombre_original': m.nombre_original,
        'mime': m.mime,
        'tamano_bytes': m.tamano_bytes,
        'subido_por_id': m.subido_por_id,
        'created_at': m.created_at.isoformat() if m.created_at else None,
    }


def _categoria_to_dict(c: HerramientaCategoria) -> dict:
    return {
        'id': c.id,
        'nombre': c.nombre,
        'imagen_url': c.imagen_url,
        'icono': c.icono,
        'color': c.color,
    }


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _next_codigo_interno() -> str:
    """Genera el siguiente codigo_interno tipo HRR-000123. Bloquea contra
    duplicados consultando el max actual y sumando 1 (suficiente con la
    constraint UNIQUE que falla cualquier colisión por race condition)."""
    last = (db.session.query(func.max(HerramientaUnidad.id)).scalar() or 0) + 1
    return f"HRR-{last:06d}"


def _validar_imagen_archivo(file_storage):
    """Valida el FileStorage de un upload de imagen. Devuelve (mime, ext, size) o (None, None, error)."""
    if not file_storage or not file_storage.filename:
        return None, None, 'No se envió archivo'
    ext = os.path.splitext(file_storage.filename)[1].lower()
    if ext not in ALLOWED_IMAGE_EXTS:
        return None, None, f'Extensión no permitida (usar {", ".join(ALLOWED_IMAGE_EXTS)})'
    # Leer en memoria para medir tamaño y mime
    file_storage.stream.seek(0, os.SEEK_END)
    size = file_storage.stream.tell()
    file_storage.stream.seek(0)
    if size > UPLOAD_MAX_BYTES:
        return None, None, f'Archivo excede {UPLOAD_MAX_BYTES // (1024 * 1024)} MB'
    mime = file_storage.mimetype or ''
    if mime not in ALLOWED_IMAGE_MIMES:
        return None, None, f'MIME no permitido ({mime})'
    return mime, ext, size


def _upload_dir(unidad_id: int) -> str:
    """Ruta absoluta donde se guardan los uploads de una unidad."""
    base = current_app.config.get('UPLOAD_FOLDER', 'uploads')
    path = os.path.join(base, 'herramientas', str(unidad_id))
    os.makedirs(path, exist_ok=True)
    return path


def _puede_ver_unidad(user: User, unidad: HerramientaUnidad) -> bool:
    """Inventario y admin ven todo. Solicitante solo ve unidades asignadas a su trabajador."""
    if user.role in ('inventario', 'admin', 'super_admin'):
        return True
    if user.role == 'solicitante_material' and user.trabajador_id:
        return unidad.asignado_trabajador_id == user.trabajador_id
    return False


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


# ─── Unidades ────────────────────────────────────────────────────────────────

@bp.route('/herramientas-unidades/', methods=['GET'])
@_require_login
def list_unidades():
    user = request.current_user
    if user.role not in ('inventario', 'admin', 'super_admin', 'solicitante_material'):
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

    if user.role == 'solicitante_material':
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
    return jsonify([_unidad_to_dict(u, incluir_relacion=True) for u in unidades])


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

    data = _unidad_to_dict(u, incluir_relacion=True)
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
    return jsonify(_unidad_to_dict(nueva, incluir_relacion=True)), 201


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

    # Validar coherencia almacén / estante antes de persistir.
    nuevo_almacen_id = data.get('almacen_id') if data.get('almacen_id') is not None else u.almacen_id
    nuevo_estante_id = data.get('estante_id') if data.get('estante_id') is not None else u.estante_id
    if data.get('almacen_id'):
        if not Almacen.query.filter(Almacen.id == data['almacen_id']).first():
            return jsonify({'detail': 'almacen_id no existe'}), 404
    if data.get('estante_id'):
        est = Estante.query.filter(Estante.id == data['estante_id']).first()
        if not est:
            return jsonify({'detail': 'estante_id no existe'}), 404
        if nuevo_almacen_id and est.almacen_id != nuevo_almacen_id:
            return jsonify({'detail': 'estante_id no pertenece al almacen_id indicado'}), 422

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
    return jsonify(_unidad_to_dict(u, incluir_relacion=True))


# ─── Asignaciones ────────────────────────────────────────────────────────────

@bp.route('/asignaciones-herramienta/', methods=['POST'])
@limiter.limit('30/minute', key_func=lambda: f"ip:{get_real_client_ip_flask()}")
@_require_inventario_admin
def crear_asignacion():
    data, err = _parse_or_422(AsignacionCreateSchema(), request.get_json(silent=True))
    if err: return err

    # Bloquear la unidad contra race condition: dos requests no pueden asignar la misma a la vez.
    unidad = (
        HerramientaUnidad.query
        .with_for_update(nowait=True)
        .filter(HerramientaUnidad.id == data['unidad_id']).first()
    )
    if not unidad:
        return jsonify({'detail': 'Unidad no encontrada'}), 404
    if unidad.estado != 'DISPONIBLE':
        db.session.rollback()
        return jsonify({'detail': f'Unidad en estado {unidad.estado}, no disponible'}), 409

    trab = Trabajador.query.filter(Trabajador.id == data['trabajador_id'],
                                     Trabajador.activo == True).first()
    if not trab:
        return jsonify({'detail': 'Trabajador no encontrado o inactivo'}), 404

    if data.get('solicitud_id'):
        sol = SolicitudMaterial.query.filter(SolicitudMaterial.id == data['solicitud_id']).first()
        if not sol:
            return jsonify({'detail': 'Solicitud no encontrada'}), 404

    user = request.current_user
    estado_anterior = unidad.estado
    asig = AsignacionHerramienta(
        unidad_id=unidad.id,
        trabajador_id=trab.id,
        solicitud_id=data.get('solicitud_id'),
        proyecto=data.get('proyecto'),
        fecha_entrega=datetime.datetime.utcnow(),
        fecha_devolucion_prevista=data.get('fecha_devolucion_prevista'),
        estado='ACTIVA',
        condicion_entrega=data.get('condicion_entrega', 'BUENA'),
        observaciones_entrega=data.get('observaciones_entrega'),
        entregado_por_id=user.id,
    )
    db.session.add(asig)
    db.session.flush()

    unidad.estado = 'ASIGNADA'
    unidad.asignado_trabajador_id = trab.id
    crear_evento_herramienta(
        unidad, 'ASIGNACION', user,
        observaciones=f"Asignada a {trab.nombre_completo} (proyecto: {data.get('proyecto') or 'N/A'})",
        estado_anterior=estado_anterior, estado_nuevo='ASIGNADA',
        referencia_id=asig.id, referencia_tipo='asignacion',
    )
    _audit(user, f"Asignación herramienta #{asig.id}: unidad {unidad.codigo_interno} → {trab.nombre_completo}")
    db.session.commit()
    db.session.refresh(asig)
    return jsonify(_asignacion_to_dict(asig)), 201


@bp.route('/asignaciones-herramienta/', methods=['GET'])
@_require_login
def list_asignaciones():
    user = request.current_user
    if user.role not in ('inventario', 'admin', 'super_admin', 'solicitante_material'):
        return jsonify({'detail': 'Forbidden'}), 403

    estado = request.args.get('estado', type=str)
    trabajador_id = request.args.get('trabajador_id', type=int)
    unidad_id = request.args.get('unidad_id', type=int)
    skip, err = _int_arg('skip', 0, 0, 1_000_000)
    if err: return err
    limit, err = _int_arg('limit', 200, 0, 500)
    if err: return err

    query = AsignacionHerramienta.query.options(
        joinedload(AsignacionHerramienta.unidad),
        joinedload(AsignacionHerramienta.trabajador),
        joinedload(AsignacionHerramienta.entregado_por),
    )
    if user.role == 'solicitante_material':
        if not user.trabajador_id:
            return jsonify([])
        query = query.filter(AsignacionHerramienta.trabajador_id == user.trabajador_id)
    if estado:
        if estado not in ESTADOS_ASIGNACION:
            return jsonify({'detail': 'estado inválido'}), 422
        query = query.filter(AsignacionHerramienta.estado == estado)
    if trabajador_id:
        query = query.filter(AsignacionHerramienta.trabajador_id == trabajador_id)
    if unidad_id:
        query = query.filter(AsignacionHerramienta.unidad_id == unidad_id)

    asigs = query.order_by(AsignacionHerramienta.fecha_entrega.desc()).offset(skip).limit(limit).all()
    return jsonify([_asignacion_to_dict(a) for a in asigs])


@bp.route('/asignaciones-herramienta/<int:aid>/devolver', methods=['PATCH'])
@limiter.limit('30/minute', key_func=lambda: f"ip:{get_real_client_ip_flask()}")
@_require_inventario_admin
def devolver_asignacion(aid: int):
    data, err = _parse_or_422(DevolucionSchema(), request.get_json(silent=True))
    if err: return err

    asig = AsignacionHerramienta.query.filter(AsignacionHerramienta.id == aid).first()
    if not asig:
        return jsonify({'detail': 'Asignación no encontrada'}), 404
    if asig.estado != 'ACTIVA':
        return jsonify({'detail': f'Asignación en estado {asig.estado}, no se puede devolver'}), 409

    unidad = (
        HerramientaUnidad.query.with_for_update(nowait=True)
        .filter(HerramientaUnidad.id == asig.unidad_id).first()
    )
    if not unidad:
        return jsonify({'detail': 'Unidad no encontrada'}), 404

    user = request.current_user
    estado_anterior = unidad.estado
    nuevo_estado = data['nuevo_estado_unidad']

    asig.estado = 'DEVUELTA'
    asig.fecha_devolucion_real = datetime.datetime.utcnow()
    asig.condicion_devolucion = data['condicion_devolucion']
    asig.observaciones_devolucion = data.get('observaciones_devolucion')
    asig.recibido_por_id = user.id

    unidad.estado = nuevo_estado
    unidad.asignado_trabajador_id = None

    crear_evento_herramienta(
        unidad, 'DEVOLUCION', user,
        observaciones=f"Devolución condición: {data['condicion_devolucion']}",
        estado_anterior=estado_anterior, estado_nuevo=nuevo_estado,
        referencia_id=asig.id, referencia_tipo='asignacion',
    )
    _audit(user, f"Devolución asignación #{aid}: {estado_anterior} → {nuevo_estado}")
    db.session.commit()
    db.session.refresh(asig)
    return jsonify(_asignacion_to_dict(asig))


# ─── Mantenimientos ──────────────────────────────────────────────────────────

@bp.route('/mantenimientos-herramienta/', methods=['POST'])
@limiter.limit('30/minute', key_func=lambda: f"ip:{get_real_client_ip_flask()}")
@_require_inventario_admin
def crear_mantenimiento():
    data, err = _parse_or_422(MantenimientoCreateSchema(), request.get_json(silent=True))
    if err: return err

    unidad = HerramientaUnidad.query.with_for_update(nowait=True).filter(
        HerramientaUnidad.id == data['unidad_id']
    ).first()
    if not unidad:
        return jsonify({'detail': 'Unidad no encontrada'}), 404
    if unidad.estado not in ('DISPONIBLE', 'ASIGNADA', 'DAÑADA'):
        return jsonify({'detail': f'Unidad en estado {unidad.estado}, no apta para mantenimiento'}), 409

    user = request.current_user
    estado_anterior = unidad.estado

    # Si está asignada, cerramos la asignación como VENCIDA
    asig_activa = next((a for a in unidad.asignaciones if a.estado == 'ACTIVA'), None)
    if asig_activa:
        asig_activa.estado = 'VENCIDA'
        asig_activa.fecha_devolucion_real = datetime.datetime.utcnow()
        asig_activa.observaciones_devolucion = '(Cerrada automáticamente por envío a mantenimiento)'
        asig_activa.recibido_por_id = user.id

    mant = MantenimientoHerramienta(
        unidad_id=unidad.id,
        tipo=data['tipo'],
        motivo=data['motivo'],
        proveedor=data.get('proveedor'),
        fecha_inicio=datetime.datetime.utcnow(),
        costo=Decimal(str(data['costo'])) if data.get('costo') is not None else None,
        observaciones=data.get('observaciones'),
        estado='ABIERTO',
        abierto_por_id=user.id,
    )
    db.session.add(mant)
    db.session.flush()

    unidad.estado = 'EN_MANTENIMIENTO'
    unidad.asignado_trabajador_id = None
    crear_evento_herramienta(
        unidad, 'MANTENIMIENTO_IN', user,
        observaciones=f"{data['tipo']}: {data['motivo']}",
        estado_anterior=estado_anterior, estado_nuevo='EN_MANTENIMIENTO',
        referencia_id=mant.id, referencia_tipo='mantenimiento',
    )
    _audit(user, f"Mantenimiento #{mant.id} abierto en unidad {unidad.codigo_interno}")
    db.session.commit()
    db.session.refresh(mant)
    return jsonify(_mantenimiento_to_dict(mant)), 201


@bp.route('/mantenimientos-herramienta/', methods=['GET'])
@_require_login
def list_mantenimientos():
    user = request.current_user
    if user.role not in ('inventario', 'admin', 'super_admin', 'solicitante_material'):
        return jsonify({'detail': 'Forbidden'}), 403

    estado = request.args.get('estado', type=str)
    unidad_id = request.args.get('unidad_id', type=int)
    query = MantenimientoHerramienta.query

    # Solicitante solo puede ver mantenimientos de unidades que tiene/tuvo asignadas.
    if user.role == 'solicitante_material':
        if not user.trabajador_id:
            return jsonify([])
        unidad_ids_visibles = [
            u.id for u in HerramientaUnidad.query.filter(
                HerramientaUnidad.asignado_trabajador_id == user.trabajador_id
            ).all()
        ]
        if not unidad_ids_visibles:
            return jsonify([])
        query = query.filter(MantenimientoHerramienta.unidad_id.in_(unidad_ids_visibles))

    if estado:
        if estado not in ESTADOS_MANTENIMIENTO:
            return jsonify({'detail': 'estado inválido'}), 422
        query = query.filter(MantenimientoHerramienta.estado == estado)
    if unidad_id:
        query = query.filter(MantenimientoHerramienta.unidad_id == unidad_id)
    mants = query.order_by(MantenimientoHerramienta.fecha_inicio.desc()).limit(500).all()
    return jsonify([_mantenimiento_to_dict(m) for m in mants])


@bp.route('/mantenimientos-herramienta/<int:mid>/cerrar', methods=['PATCH'])
@limiter.limit('30/minute', key_func=lambda: f"ip:{get_real_client_ip_flask()}")
@_require_inventario_admin
def cerrar_mantenimiento(mid: int):
    data, err = _parse_or_422(MantenimientoCierreSchema(), request.get_json(silent=True))
    if err: return err

    mant = MantenimientoHerramienta.query.filter(MantenimientoHerramienta.id == mid).first()
    if not mant:
        return jsonify({'detail': 'Mantenimiento no encontrado'}), 404
    if mant.estado == 'CERRADO':
        return jsonify({'detail': 'Mantenimiento ya cerrado'}), 409

    unidad = HerramientaUnidad.query.with_for_update(nowait=True).filter(
        HerramientaUnidad.id == mant.unidad_id
    ).first()
    user = request.current_user
    estado_anterior = unidad.estado
    nuevo_estado = data['estado_final_unidad']

    mant.estado = 'CERRADO'
    mant.fecha_fin = datetime.datetime.utcnow()
    mant.cerrado_por_id = user.id
    mant.estado_final_unidad = nuevo_estado
    if data.get('costo_real') is not None:
        mant.costo = Decimal(str(data['costo_real']))
    if data.get('observaciones'):
        mant.observaciones = (mant.observaciones or '') + f"\n[Cierre] {data['observaciones']}"

    unidad.estado = nuevo_estado
    if nuevo_estado == 'DADA_DE_BAJA':
        unidad.fecha_baja = datetime.datetime.utcnow()
        unidad.motivo_baja = f"Mantenimiento #{mid}: irrecuperable"

    crear_evento_herramienta(
        unidad, 'MANTENIMIENTO_OUT', user,
        observaciones=f"Cierre mantenimiento (estado final: {nuevo_estado})",
        estado_anterior=estado_anterior, estado_nuevo=nuevo_estado,
        referencia_id=mant.id, referencia_tipo='mantenimiento',
    )
    _audit(user, f"Mantenimiento #{mid} cerrado: unidad → {nuevo_estado}")
    db.session.commit()
    db.session.refresh(mant)
    return jsonify(_mantenimiento_to_dict(mant))


# ─── Incidencias ─────────────────────────────────────────────────────────────

@bp.route('/incidencias-herramienta/', methods=['POST'])
@limiter.limit('20/minute', key_func=lambda: f"ip:{get_real_client_ip_flask()}")
@_require_login
def crear_incidencia():
    user = request.current_user
    if user.role not in ('solicitante_material', 'inventario', 'admin', 'super_admin'):
        return jsonify({'detail': 'Forbidden'}), 403
    data, err = _parse_or_422(IncidenciaCreateSchema(), request.get_json(silent=True))
    if err: return err

    unidad = HerramientaUnidad.query.filter(HerramientaUnidad.id == data['unidad_id']).first()
    if not unidad:
        return jsonify({'detail': 'Unidad no encontrada'}), 404
    if not _puede_ver_unidad(user, unidad):
        return jsonify({'detail': 'Forbidden'}), 403

    inc = IncidenciaHerramienta(
        unidad_id=unidad.id,
        reportado_por_id=user.id,
        tipo=data['tipo'],
        descripcion=data['descripcion'],
        estado='ABIERTA',
        fecha_reporte=datetime.datetime.utcnow(),
    )
    db.session.add(inc)
    db.session.flush()
    crear_evento_herramienta(
        unidad, 'INCIDENCIA', user,
        observaciones=f"{data['tipo']}: {data['descripcion'][:120]}",
        referencia_id=inc.id, referencia_tipo='incidencia',
    )
    crear_notif_inventario(
        'HERRAMIENTA_INCIDENCIA',
        f"Incidencia en {unidad.codigo_interno}",
        f"{user.username} reportó {data['tipo']} en {unidad.codigo_interno}",
        url=f"/inventario/herramientas/unidades/{unidad.id}",
    )
    _audit(user, f"Incidencia #{inc.id} reportada en unidad #{unidad.id}")
    db.session.commit()
    db.session.refresh(inc)
    return jsonify(_incidencia_to_dict(inc)), 201


@bp.route('/incidencias-herramienta/', methods=['GET'])
@_require_login
def list_incidencias():
    user = request.current_user
    if user.role not in ('solicitante_material', 'inventario', 'admin', 'super_admin'):
        return jsonify({'detail': 'Forbidden'}), 403
    estado = request.args.get('estado', type=str)
    unidad_id = request.args.get('unidad_id', type=int)
    query = IncidenciaHerramienta.query.options(joinedload(IncidenciaHerramienta.reportado_por))
    if user.role == 'solicitante_material':
        query = query.filter(IncidenciaHerramienta.reportado_por_id == user.id)
    if estado:
        if estado not in ESTADOS_INCIDENCIA:
            return jsonify({'detail': 'estado inválido'}), 422
        query = query.filter(IncidenciaHerramienta.estado == estado)
    if unidad_id:
        query = query.filter(IncidenciaHerramienta.unidad_id == unidad_id)
    incs = query.order_by(IncidenciaHerramienta.fecha_reporte.desc()).limit(500).all()
    return jsonify([_incidencia_to_dict(i) for i in incs])


@bp.route('/incidencias-herramienta/<int:iid>/atender', methods=['PATCH'])
@limiter.limit('30/minute', key_func=lambda: f"ip:{get_real_client_ip_flask()}")
@_require_inventario_admin
def atender_incidencia(iid: int):
    data, err = _parse_or_422(IncidenciaAtenderSchema(), request.get_json(silent=True))
    if err: return err
    inc = IncidenciaHerramienta.query.filter(IncidenciaHerramienta.id == iid).first()
    if not inc:
        return jsonify({'detail': 'Incidencia no encontrada'}), 404
    if inc.estado in ('RESUELTA', 'RECHAZADA'):
        return jsonify({'detail': 'Incidencia ya cerrada'}), 409

    user = request.current_user
    inc.estado = data['estado']
    inc.atendido_por_id = user.id
    if data.get('resolucion'):
        inc.resolucion = data['resolucion']
    if data['estado'] in ('RESUELTA', 'RECHAZADA'):
        inc.fecha_cierre = datetime.datetime.utcnow()
    _audit(user, f"Incidencia #{iid} → {data['estado']}")
    db.session.commit()
    db.session.refresh(inc)
    return jsonify(_incidencia_to_dict(inc))


# ─── Solicitudes de baja ─────────────────────────────────────────────────────

@bp.route('/solicitudes-baja-herramienta/', methods=['POST'])
@limiter.limit('10/minute', key_func=lambda: f"ip:{get_real_client_ip_flask()}")
@_require_login
def crear_solicitud_baja():
    user = request.current_user
    if user.role not in ('solicitante_material', 'inventario', 'admin', 'super_admin'):
        return jsonify({'detail': 'Forbidden'}), 403
    data, err = _parse_or_422(SolicitudBajaCreateSchema(), request.get_json(silent=True))
    if err: return err

    unidad = HerramientaUnidad.query.filter(HerramientaUnidad.id == data['unidad_id']).first()
    if not unidad:
        return jsonify({'detail': 'Unidad no encontrada'}), 404
    if not _puede_ver_unidad(user, unidad):
        return jsonify({'detail': 'Forbidden'}), 403
    if unidad.estado == 'DADA_DE_BAJA':
        return jsonify({'detail': 'Unidad ya está dada de baja'}), 400

    # Evitar duplicados: no permitir abrir una segunda solicitud PENDIENTE/APROBADA
    # para la misma unidad mientras la anterior siga abierta.
    pendiente_previa = SolicitudBajaHerramienta.query.filter(
        SolicitudBajaHerramienta.unidad_id == unidad.id,
        SolicitudBajaHerramienta.estado.in_(('PENDIENTE', 'APROBADA')),
    ).first()
    if pendiente_previa:
        return jsonify({
            'detail': 'Ya existe una solicitud de baja activa para esta unidad',
            'solicitud_id': pendiente_previa.id,
            'estado': pendiente_previa.estado,
        }), 409

    sol = SolicitudBajaHerramienta(
        unidad_id=unidad.id,
        solicitante_id=user.id,
        motivo=data['motivo'],
        estado='PENDIENTE',
        fecha_solicitud=datetime.datetime.utcnow(),
    )
    db.session.add(sol)
    db.session.flush()
    crear_evento_herramienta(
        unidad, 'BAJA_SOLICITUD', user,
        observaciones=f"Solicitud de baja: {data['motivo'][:120]}",
        referencia_id=sol.id, referencia_tipo='solicitud_baja',
    )
    crear_notif_inventario(
        'HERRAMIENTA_BAJA_SOLICITUD',
        f"Solicitud de baja {unidad.codigo_interno}",
        f"{user.username} solicitó baja de {unidad.codigo_interno}",
        url=f"/inventario/herramientas/incidencias",
    )
    _audit(user, f"Solicitud de baja #{sol.id} para unidad #{unidad.id}")
    db.session.commit()
    db.session.refresh(sol)
    return jsonify(_solicitud_baja_to_dict(sol)), 201


@bp.route('/solicitudes-baja-herramienta/', methods=['GET'])
@_require_login
def list_solicitudes_baja():
    user = request.current_user
    if user.role not in ('solicitante_material', 'inventario', 'admin', 'super_admin'):
        return jsonify({'detail': 'Forbidden'}), 403
    estado = request.args.get('estado', type=str)
    query = SolicitudBajaHerramienta.query.options(joinedload(SolicitudBajaHerramienta.solicitante))
    if user.role == 'solicitante_material':
        query = query.filter(SolicitudBajaHerramienta.solicitante_id == user.id)
    if estado:
        if estado not in ESTADOS_SOLICITUD_BAJA:
            return jsonify({'detail': 'estado inválido'}), 422
        query = query.filter(SolicitudBajaHerramienta.estado == estado)
    sols = query.order_by(SolicitudBajaHerramienta.fecha_solicitud.desc()).limit(500).all()
    return jsonify([_solicitud_baja_to_dict(s) for s in sols])


@bp.route('/solicitudes-baja-herramienta/<int:sid>/autorizar', methods=['PATCH'])
@limiter.limit('20/minute', key_func=lambda: f"ip:{get_real_client_ip_flask()}")
@_require_inventario_admin
def autorizar_baja(sid: int):
    data, err = _parse_or_422(SolicitudBajaAutorizarSchema(), request.get_json(silent=True))
    if err: return err
    sol = SolicitudBajaHerramienta.query.filter(SolicitudBajaHerramienta.id == sid).first()
    if not sol:
        return jsonify({'detail': 'Solicitud no encontrada'}), 404
    if sol.estado != 'PENDIENTE':
        return jsonify({'detail': 'Solo se pueden autorizar solicitudes PENDIENTES'}), 409

    user = request.current_user
    sol.estado = 'APROBADA'
    sol.autorizado_por_id = user.id
    sol.fecha_autorizacion = datetime.datetime.utcnow()
    if data.get('observaciones'):
        sol.observaciones = data['observaciones']
    unidad = HerramientaUnidad.query.filter(HerramientaUnidad.id == sol.unidad_id).first()
    if unidad:
        crear_evento_herramienta(
            unidad, 'BAJA_APROBADA', user,
            observaciones='Baja autorizada',
            referencia_id=sol.id, referencia_tipo='solicitud_baja',
        )
    _audit(user, f"Solicitud baja #{sid} autorizada")
    db.session.commit()
    db.session.refresh(sol)
    return jsonify(_solicitud_baja_to_dict(sol))


@bp.route('/solicitudes-baja-herramienta/<int:sid>/rechazar', methods=['PATCH'])
@limiter.limit('20/minute', key_func=lambda: f"ip:{get_real_client_ip_flask()}")
@_require_inventario_admin
def rechazar_baja(sid: int):
    data, err = _parse_or_422(SolicitudBajaAutorizarSchema(), request.get_json(silent=True))
    if err: return err
    sol = SolicitudBajaHerramienta.query.filter(SolicitudBajaHerramienta.id == sid).first()
    if not sol:
        return jsonify({'detail': 'Solicitud no encontrada'}), 404
    if sol.estado != 'PENDIENTE':
        return jsonify({'detail': 'Solo se pueden rechazar solicitudes PENDIENTES'}), 409

    user = request.current_user
    sol.estado = 'RECHAZADA'
    sol.autorizado_por_id = user.id
    sol.fecha_autorizacion = datetime.datetime.utcnow()
    if data.get('observaciones'):
        sol.observaciones = data['observaciones']
    unidad = HerramientaUnidad.query.filter(HerramientaUnidad.id == sol.unidad_id).first()
    if unidad:
        crear_evento_herramienta(
            unidad, 'BAJA_RECHAZADA', user,
            observaciones=f"Rechazo: {(data.get('observaciones') or '')[:120]}",
            referencia_id=sol.id, referencia_tipo='solicitud_baja',
        )
    _audit(user, f"Solicitud baja #{sid} rechazada")
    db.session.commit()
    db.session.refresh(sol)
    return jsonify(_solicitud_baja_to_dict(sol))


@bp.route('/solicitudes-baja-herramienta/<int:sid>/ejecutar', methods=['POST'])
@limiter.limit('20/minute', key_func=lambda: f"ip:{get_real_client_ip_flask()}")
@_require_inventario_admin
def ejecutar_baja(sid: int):
    sol = SolicitudBajaHerramienta.query.filter(SolicitudBajaHerramienta.id == sid).first()
    if not sol:
        return jsonify({'detail': 'Solicitud no encontrada'}), 404
    if sol.estado != 'APROBADA':
        return jsonify({'detail': 'Solo se ejecutan solicitudes APROBADAS'}), 409

    unidad = HerramientaUnidad.query.with_for_update(nowait=True).filter(
        HerramientaUnidad.id == sol.unidad_id
    ).first()
    if not unidad:
        return jsonify({'detail': 'Unidad no encontrada'}), 404
    if unidad.estado == 'DADA_DE_BAJA':
        return jsonify({'detail': 'Unidad ya dada de baja'}), 400

    user = request.current_user
    estado_anterior = unidad.estado
    unidad.estado = 'DADA_DE_BAJA'
    unidad.fecha_baja = datetime.datetime.utcnow()
    unidad.motivo_baja = (sol.motivo or '')[:250]
    unidad.asignado_trabajador_id = None

    # Cerrar asignación activa si existiera
    asig = next((a for a in unidad.asignaciones if a.estado == 'ACTIVA'), None)
    if asig:
        asig.estado = 'VENCIDA'
        asig.fecha_devolucion_real = datetime.datetime.utcnow()
        asig.observaciones_devolucion = '(Cerrada automáticamente por baja)'
        asig.recibido_por_id = user.id

    sol.estado = 'EJECUTADA'
    sol.ejecutado_por_id = user.id
    sol.fecha_ejecucion = datetime.datetime.utcnow()

    crear_evento_herramienta(
        unidad, 'BAJA_EJECUTADA', user,
        observaciones=f"Baja ejecutada (motivo: {(sol.motivo or '')[:120]})",
        estado_anterior=estado_anterior, estado_nuevo='DADA_DE_BAJA',
        referencia_id=sol.id, referencia_tipo='solicitud_baja',
    )
    _audit(user, f"Baja ejecutada #{sid}: unidad #{unidad.id}")
    db.session.commit()
    db.session.refresh(sol)
    return jsonify(_solicitud_baja_to_dict(sol))


# ─── Baja directa (atajo admin) ─────────────────────────────────────────────

@bp.route('/herramientas-unidades/<int:uid>/dar-baja', methods=['POST'])
@limiter.limit('10/minute', key_func=lambda: f"ip:{get_real_client_ip_flask()}")
@_require_inventario_admin
def baja_directa(uid: int):
    """Atajo para admin/inventario: crea solicitud APROBADA y la ejecuta de inmediato."""
    payload = request.get_json(silent=True) or {}
    motivo = (payload.get('motivo') or '').strip()
    if len(motivo) < 10:
        return jsonify({'detail': 'Motivo debe tener al menos 10 caracteres'}), 422

    unidad = HerramientaUnidad.query.with_for_update(nowait=True).filter(
        HerramientaUnidad.id == uid
    ).first()
    if not unidad:
        return jsonify({'detail': 'Unidad no encontrada'}), 404
    if unidad.estado == 'DADA_DE_BAJA':
        return jsonify({'detail': 'Unidad ya dada de baja'}), 400

    user = request.current_user
    sol = SolicitudBajaHerramienta(
        unidad_id=unidad.id,
        solicitante_id=user.id,
        motivo=motivo,
        estado='EJECUTADA',
        autorizado_por_id=user.id,
        ejecutado_por_id=user.id,
        fecha_solicitud=datetime.datetime.utcnow(),
        fecha_autorizacion=datetime.datetime.utcnow(),
        fecha_ejecucion=datetime.datetime.utcnow(),
        observaciones='Baja directa por inventario/admin',
    )
    db.session.add(sol)
    db.session.flush()

    estado_anterior = unidad.estado
    unidad.estado = 'DADA_DE_BAJA'
    unidad.fecha_baja = datetime.datetime.utcnow()
    unidad.motivo_baja = motivo[:250]
    unidad.asignado_trabajador_id = None

    crear_evento_herramienta(
        unidad, 'BAJA_EJECUTADA', user,
        observaciones=f"Baja directa: {motivo[:120]}",
        estado_anterior=estado_anterior, estado_nuevo='DADA_DE_BAJA',
        referencia_id=sol.id, referencia_tipo='solicitud_baja',
    )
    _audit(user, f"Baja directa unidad #{uid}")
    db.session.commit()
    db.session.refresh(sol)
    return jsonify(_solicitud_baja_to_dict(sol)), 201


# ─── Media / fotos ───────────────────────────────────────────────────────────

@bp.route('/herramientas-unidades/<int:uid>/fotos', methods=['POST'])
@limiter.limit('15/minute', key_func=lambda: f"ip:{get_real_client_ip_flask()}")
@_require_inventario
def subir_foto_unidad(uid: int):
    unidad = HerramientaUnidad.query.filter(HerramientaUnidad.id == uid).first()
    if not unidad:
        return jsonify({'detail': 'Unidad no encontrada'}), 404

    # IDOR fix: un solicitante_material solo puede subir foto a unidades que
    # tiene asignadas. Inventario/admin/super_admin ven todo.
    if not _puede_ver_unidad(request.current_user, unidad):
        return jsonify({'detail': 'Forbidden'}), 403

    file = request.files.get('foto') or request.files.get('archivo')
    mime, ext, info = _validar_imagen_archivo(file)
    if mime is None:
        return jsonify({'detail': info}), 422

    tipo = (request.form.get('tipo') or 'FOTO_HERRAMIENTA').upper()
    if tipo not in ('FOTO_HERRAMIENTA', 'EVIDENCIA_EVENTO'):
        return jsonify({'detail': 'tipo inválido'}), 422
    evento_id = request.form.get('evento_id', type=int)

    # IDOR fix: evento_id debe pertenecer a la misma unidad para evitar adjuntar
    # evidencia a eventos de otras unidades.
    if evento_id is not None:
        evento = EventoHerramienta.query.filter(
            EventoHerramienta.id == evento_id,
            EventoHerramienta.unidad_id == uid,
        ).first()
        if not evento:
            return jsonify({'detail': 'evento_id no pertenece a esta unidad'}), 422

    folder = _upload_dir(uid)
    fname = f"{uuid.uuid4().hex}{ext}"
    fpath = os.path.join(folder, fname)
    file.save(fpath)
    rel_path = os.path.relpath(fpath, current_app.config.get('UPLOAD_FOLDER', 'uploads'))

    user = request.current_user
    media = MediaHerramienta(
        unidad_id=uid,
        evento_id=evento_id,
        tipo=tipo,
        ruta_archivo=rel_path.replace('\\', '/'),
        nombre_original=file.filename[:250],
        mime=mime,
        tamano_bytes=info,  # size returned in info on success
        subido_por_id=user.id,
    )
    db.session.add(media)
    _audit(user, f"Foto subida a unidad #{uid}")
    db.session.commit()
    db.session.refresh(media)
    return jsonify(_media_to_dict(media)), 201


@bp.route('/herramientas-unidades/<int:uid>/media/<int:media_id>', methods=['GET'])
@_require_login
def get_media_file(uid: int, media_id: int):
    unidad = HerramientaUnidad.query.filter(HerramientaUnidad.id == uid).first()
    if not unidad:
        return jsonify({'detail': 'Unidad no encontrada'}), 404
    if not _puede_ver_unidad(request.current_user, unidad):
        return jsonify({'detail': 'Forbidden'}), 403

    media = MediaHerramienta.query.filter(MediaHerramienta.id == media_id,
                                           MediaHerramienta.unidad_id == uid).first()
    if not media:
        return jsonify({'detail': 'Archivo no encontrado'}), 404
    base = current_app.config.get('UPLOAD_FOLDER', 'uploads')
    full = os.path.join(base, media.ruta_archivo)
    if not os.path.exists(full):
        return jsonify({'detail': 'Archivo perdido en disco'}), 404
    return send_file(full, mimetype=media.mime or 'image/jpeg')


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
