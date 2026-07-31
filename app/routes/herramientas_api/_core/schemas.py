"""Schemas Marshmallow de entrada del módulo de Herramientas.

Solo validación de forma (tipos, rangos, longitudes, catálogos de valores). Las
reglas que dependen del estado en base (que la unidad esté DISPONIBLE, que la
solicitud de baja no esté ya autorizada…) se validan en la vista.
"""
from marshmallow import EXCLUDE, Schema, fields, validate

from app.models import (
    CONDICION_HERRAMIENTA, TIPO_INCIDENCIA, TIPO_MANTENIMIENTO, USO_HERRAMIENTA,
)

from .permisos import CODIGO_REGEX, _IMAGEN_URL_REGEX


class _BaseSchema(Schema):
    class Meta:
        unknown = EXCLUDE


def _campo_imagen_url():
    """Campo `imagen_url` opcional con la validación anti-XSS/SSRF compartida con
    Inventario. Fábrica (no constante) porque un `fields.Str` no puede
    compartirse entre schemas."""
    return fields.Str(load_default=None, allow_none=True, validate=[
        validate.Length(max=500), validate.Regexp(_IMAGEN_URL_REGEX),
    ])


# ─── Catálogo de herramientas ────────────────────────────────────────────────

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
    imagen_url = _campo_imagen_url()


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
    imagen_url = _campo_imagen_url()


class CategoriaUpsertSchema(_BaseSchema):
    imagen_url = _campo_imagen_url()
    icono = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=50))
    color = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=20))


# ─── Unidades físicas ────────────────────────────────────────────────────────

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


# ─── Asignación y devolución ─────────────────────────────────────────────────

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


# ─── Mantenimientos ──────────────────────────────────────────────────────────

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


# ─── Incidencias ─────────────────────────────────────────────────────────────

class IncidenciaCreateSchema(_BaseSchema):
    unidad_id = fields.Int(required=True)
    tipo = fields.Str(required=True, validate=validate.OneOf(TIPO_INCIDENCIA))
    descripcion = fields.Str(required=True, validate=validate.Length(min=5, max=2000))


class IncidenciaAtenderSchema(_BaseSchema):
    estado = fields.Str(required=True, validate=validate.OneOf(['REVISION', 'RESUELTA', 'RECHAZADA']))
    resolucion = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=2000))


# ─── Bajas ───────────────────────────────────────────────────────────────────

class SolicitudBajaCreateSchema(_BaseSchema):
    unidad_id = fields.Int(required=True)
    motivo = fields.Str(required=True, validate=validate.Length(min=10, max=2000))


class SolicitudBajaAutorizarSchema(_BaseSchema):
    observaciones = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=1000))
