"""Núcleo compartido del paquete `herramientas_api`.

Define el blueprint `bp`, las constantes de upload, los schemas Marshmallow,
los serializers comunes y los helpers de QR/eventos. Cada submódulo del
paquete importa lo que necesita desde aquí.

No registres rutas en este archivo. Las rutas viven en los submódulos por
dominio: catalogo.py, unidades.py, asignaciones.py, mantenimientos.py,
incidencias.py, bajas.py, multimedia.py.
"""
import os
import logging

import filetype
from flask import Blueprint, current_app
from marshmallow import Schema, fields, validate, EXCLUDE
from sqlalchemy import func

from app.extensions import db
from app.models import (
    User,
    Herramienta, HerramientaUnidad, HerramientaCategoria,
    AsignacionHerramienta, MantenimientoHerramienta,
    IncidenciaHerramienta, SolicitudBajaHerramienta,
    EventoHerramienta, MediaHerramienta,
    ESTADOS_UNIDAD, USO_HERRAMIENTA,
    TIPO_INCIDENCIA, TIPO_MANTENIMIENTO, CONDICION_HERRAMIENTA,
)
from app.routes.inventario_api import (
    _require_login, _require_inventario, _require_inventario_admin,
    _parse_or_422, _int_arg, _audit, CODIGO_REGEX, _IMAGEN_URL_REGEX,
)

logger = logging.getLogger(__name__)

bp = Blueprint('herramientas_api', __name__, url_prefix='/api/v1')


# Roles que ven herramientas (catalogo + unidades + asignaciones + incidencias).
# coordinador es relevante porque puede recibir asignaciones; solicitante_material
# las pide vía solicitudes. Mantengo simétrico con _INV_ROLES.
_HERR_ROLES = ['admin', 'super_admin', 'inventario', 'coordinador']


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
        'unidad_descripcion': (a.unidad.herramienta.descripcion if (a.unidad and a.unidad.herramienta) else None),
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
    # El MIME declarado por el cliente (file_storage.mimetype) es falsificable.
    # Verificamos los magic bytes con `filetype` igual que allowed_image_file()
    # para fotos de perfil: así un atacante no puede subir un no-imagen con
    # extensión y Content-Type de imagen. No es RCE (el archivo nunca se ejecuta)
    # pero cierra el hueco de subir contenido arbitrario al disco del servidor.
    header = file_storage.stream.read(2048)
    file_storage.stream.seek(0)
    kind = filetype.guess(header)
    if kind is None or kind.mime not in ALLOWED_IMAGE_MIMES:
        detectado = kind.mime if kind else 'desconocido'
        return None, None, f'El archivo no es una imagen válida (detectado: {detectado})'
    return kind.mime, ext, size


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
