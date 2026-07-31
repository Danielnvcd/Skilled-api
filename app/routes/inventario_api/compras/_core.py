"""Núcleo del módulo de compras: guard de rol, schemas y serializers.

Compras es un módulo hermano de inventario con su propio recorte de roles
(admin/RH queda fuera a propósito), así que define lo suyo en vez de reusar los
guards generales del inventario.
"""
from functools import wraps

from flask import jsonify, request
from marshmallow import fields, validate
from sqlalchemy.orm import joinedload, selectinload

from app.models import (
    PRIORIDADES_COMPRA, SolicitudCompra, SolicitudCompraDetalle,
)
from app.utils import log_action

from .._core import _BaseSchema, _require_login


_COMPRA_ROLES = ['inventario', 'super_admin']


# ─── Auth: solo inventario / super_admin ──────────────────────────────────────

def _require_compras(view):
    """Lectura y escritura del módulo de compras: solo inventario y super_admin."""
    @wraps(view)
    @_require_login
    def wrapper(*args, **kwargs):
        if request.current_user.role not in _COMPRA_ROLES:
            log_action(f"API 403 compras '{request.path}' (rol: {request.current_user.role})")
            return jsonify({'detail': 'Solo el rol de inventario puede usar Solicitudes de compra'}), 403
        return view(*args, **kwargs)
    return wrapper


# ─── Schemas ──────────────────────────────────────────────────────────────────

class _CompraDetalleCreateSchema(_BaseSchema):
    # Una línea es producto del catálogo (producto_id) o ítem de texto libre
    # (descripcion_libre). Validación XOR a nivel app.
    producto_id = fields.Int(load_default=None, allow_none=True)
    descripcion_libre = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=250))
    unidad = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=50))
    cantidad_solicitada = fields.Float(required=True, validate=validate.Range(min=0.01, max=1_000_000))
    precio_estimado = fields.Float(load_default=None, allow_none=True, validate=validate.Range(min=0, max=100_000_000))
    notas = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=500))


class CompraCreateSchema(_BaseSchema):
    proveedor_sugerido = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=150))
    proveedor_contacto = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=150))
    proyecto_id = fields.Int(load_default=None, allow_none=True)
    prioridad = fields.Str(load_default='MEDIA', validate=validate.OneOf(PRIORIDADES_COMPRA))
    notas = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=2000))
    detalles = fields.List(
        fields.Nested(_CompraDetalleCreateSchema),
        required=True,
        validate=validate.Length(min=1, max=500),
    )


class CompraEstadoSchema(_BaseSchema):
    # RECIBIDA no se setea aquí: se llega vía /recibir (que mueve stock).
    estatus = fields.Str(required=True, validate=validate.OneOf(['PENDIENTE', 'ORDENADA', 'CANCELADA']))


class CompraDetallePatchSchema(_BaseSchema):
    cantidad_solicitada = fields.Float(load_default=None, allow_none=True, validate=validate.Range(min=0.01, max=1_000_000))
    precio_estimado = fields.Float(load_default=None, allow_none=True, validate=validate.Range(min=0, max=100_000_000))
    notas = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=500))


class _RecepcionItemSchema(_BaseSchema):
    detalle_id = fields.Int(required=True)
    cantidad_recibida = fields.Float(required=True, validate=validate.Range(min=0, max=1_000_000))


class RecibirCompraSchema(_BaseSchema):
    almacen_destino_id = fields.Int(load_default=None, allow_none=True)
    motivo = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=250))
    recepciones = fields.List(
        fields.Nested(_RecepcionItemSchema),
        required=True,
        validate=validate.Length(min=1, max=200),
    )


# ─── Serializers ──────────────────────────────────────────────────────────────

def _compra_detalle_to_dict(d: SolicitudCompraDetalle) -> dict:
    prod = d.producto
    es_libre = d.producto_id is None
    sol = float(d.cantidad_solicitada or 0)
    rec = float(d.cantidad_recibida or 0)
    descripcion = (prod.descripcion if prod else None) or d.descripcion_libre or 'Producto eliminado'
    return {
        'id': d.id,
        'producto_id': d.producto_id,
        'es_libre': es_libre,
        'descripcion': descripcion,
        'codigo': prod.codigo if prod else None,
        'unidad': d.unidad or (prod.unidad if prod else None),
        'cantidad_solicitada': sol,
        'cantidad_recibida': rec,
        'cantidad_pendiente': max(0.0, sol - rec),
        'precio_estimado': float(d.precio_estimado) if d.precio_estimado is not None else None,
        'notas': d.notas,
    }


def _compra_to_dict(s: SolicitudCompra) -> dict:
    detalles = [_compra_detalle_to_dict(d) for d in (s.detalles or [])]
    total_estimado = sum(
        (dd['precio_estimado'] or 0) * dd['cantidad_solicitada'] for dd in detalles
    )
    proy = s.proyecto_ref
    return {
        'id': s.id,
        'folio': s.folio,
        'solicitado_por_id': s.solicitado_por_id,
        'solicitado_por_nombre': (
            s.solicitado_por.full_name or s.solicitado_por.username
        ) if s.solicitado_por else 'Desconocido',
        'proveedor_sugerido': s.proveedor_sugerido,
        'proveedor_contacto': s.proveedor_contacto,
        'proyecto_id': s.proyecto_id,
        'proyecto': proy.numero_proyecto if proy else None,
        'proyecto_nombre': proy.nombre if proy else None,
        'prioridad': s.prioridad,
        'estatus': s.estatus,
        'notas': s.notas,
        'fecha_creacion': s.fecha_creacion.isoformat() if s.fecha_creacion else None,
        'fecha_orden': s.fecha_orden.isoformat() if s.fecha_orden else None,
        'fecha_cierre': s.fecha_cierre.isoformat() if s.fecha_cierre else None,
        'total_estimado': round(total_estimado, 2),
        'detalles': detalles,
    }


def _load_compra(sol_id: int) -> SolicitudCompra | None:
    return (
        SolicitudCompra.query
        .options(
            joinedload(SolicitudCompra.solicitado_por),
            joinedload(SolicitudCompra.proyecto_ref),
            selectinload(SolicitudCompra.detalles).joinedload(SolicitudCompraDetalle.producto),
        )
        .filter(SolicitudCompra.id == sol_id)
        .first()
    )
