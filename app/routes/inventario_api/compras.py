"""Solicitudes de Compra (procura) — registro persistente de la lista de compra.

A diferencia de:
  - `solicitudes.py` (SolicitudMaterial): surte material/herramienta del stock
    que YA existe.
  - `etiquetas.py` (OC express): genera un PDF desechable sin persistir nada.

aquí guardamos qué se pide comprar, a qué proveedor, si ya se ordenó y si ya
llegó (atendido). Al recibir material ligado a un producto del catálogo se
genera una ENTRADA real al almacén (sube el stock), reutilizando las mismas
primitivas de stock que el resto del módulo (`_lock_stock`,
`_recalcular_cache_stock`, `MovimientoInventario`).

Permisos: este módulo es EXCLUSIVO del rol `inventario` (más `super_admin` como
dueño global del sistema). `admin` NO entra: en este sistema `admin` es RH y no
tiene nada que ver con inventario.

Estados: PENDIENTE → ORDENADA → RECIBIDA (+ CANCELADA). El paso a RECIBIDA solo
ocurre vía el endpoint /recibir (que mueve stock), no por el PATCH de estado.
"""
import datetime
from decimal import Decimal

from flask import jsonify, request, send_file
from marshmallow import fields, validate
from sqlalchemy.orm import joinedload, selectinload

from app.extensions import db, limiter, get_real_client_ip_flask
from app.models import (
    Producto, MovimientoInventario, Almacen, Proyecto,
    SolicitudCompra, SolicitudCompraDetalle, PRIORIDADES_COMPRA,
)
from app.realtime import emit_to_role

from ._core import (
    bp,
    _es_error_de_lock,
    _require_login,
    _parse_or_422, _int_arg, _audit,
    _BaseSchema,
    _INV_ROLES,
    _almacen_default_id, _depositar, _recalcular_caches,
)
from .etiquetas import _render_oc_express_pdf, _whatsapp_link
from .solicitudes import _unidad_permite_decimales


# Roles del módulo de compras. admin (RH) queda fuera a propósito.
_COMPRA_ROLES = ['inventario', 'super_admin']


# ─── Auth: solo inventario / super_admin ──────────────────────────────────────

def _require_compras(view):
    """Lectura y escritura del módulo de compras: solo inventario y super_admin."""
    from functools import wraps
    from app.utils import log_action

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


# ─── CRUD + listado ───────────────────────────────────────────────────────────

@bp.route('/solicitudes-compra/', methods=['POST'])
@limiter.limit('15/minute', key_func=lambda: f"ip:{get_real_client_ip_flask()}")
@_require_compras
def create_solicitud_compra():
    user = request.current_user
    data, err = _parse_or_422(CompraCreateSchema(), request.get_json(silent=True))
    if err:
        return err

    # Proyecto opcional pero, si viene, debe existir.
    proyecto_id = data.get('proyecto_id')
    if proyecto_id is not None:
        if not Proyecto.query.filter(Proyecto.id == proyecto_id).first():
            return jsonify({'detail': f'Proyecto #{proyecto_id} no existe'}), 422

    # Validar líneas antes de persistir nada.
    lineas_validadas = []
    errores = []
    for idx, det in enumerate(data['detalles']):
        producto_id = det.get('producto_id')
        descripcion_libre = (det.get('descripcion_libre') or '').strip()
        unidad = (det.get('unidad') or '').strip() or None

        if producto_id:
            prod = Producto.query.filter(
                Producto.id == producto_id, Producto.activo == True  # noqa: E712
            ).first()
            if not prod:
                errores.append(f"Línea {idx+1}: producto #{producto_id} no existe o está inactivo")
                continue
            if not unidad:
                unidad = prod.unidad
            descripcion_libre = None
        elif descripcion_libre:
            producto_id = None
        else:
            errores.append(f"Línea {idx+1}: indica un producto del catálogo o una descripción")
            continue

        cant = Decimal(str(det['cantidad_solicitada']))
        # Decimales según unidad (igual que el resto del sistema): pieza/caja →
        # enteros; kg/m/litro → admiten decimales.
        if not _unidad_permite_decimales(unidad) and cant != cant.to_integral_value():
            errores.append(f"Línea {idx+1}: '{unidad or 'pza'}' se pide en cantidades enteras (sin decimales)")
            continue

        lineas_validadas.append({
            'producto_id': producto_id,
            'descripcion_libre': descripcion_libre,
            'unidad': unidad,
            'cantidad_solicitada': cant,
            'precio_estimado': (
                Decimal(str(det['precio_estimado'])) if det.get('precio_estimado') is not None else None
            ),
            'notas': (det.get('notas') or '').strip() or None,
        })

    if errores:
        return jsonify({'detail': errores}), 400

    nueva = SolicitudCompra(
        solicitado_por_id=user.id,
        proveedor_sugerido=(data.get('proveedor_sugerido') or '').strip() or None,
        proveedor_contacto=(data.get('proveedor_contacto') or '').strip() or None,
        proyecto_id=proyecto_id,
        prioridad=data.get('prioridad') or 'MEDIA',
        notas=(data.get('notas') or '').strip() or None,
        estatus='PENDIENTE',
    )
    db.session.add(nueva)
    db.session.flush()

    for ln in lineas_validadas:
        db.session.add(SolicitudCompraDetalle(solicitud_compra_id=nueva.id, **ln))

    _audit(user, f"Nueva solicitud de compra {nueva.folio} ({len(lineas_validadas)} líneas)")
    db.session.commit()

    sol = _load_compra(nueva.id)
    emit_to_role(_COMPRA_ROLES, 'compra:changed', {'id': sol.id, 'action': 'created'})
    return jsonify(_compra_to_dict(sol))


@bp.route('/solicitudes-compra/', methods=['GET'])
@_require_compras
def list_solicitudes_compra():
    skip, err = _int_arg('skip', 0, 0, 1_000_000)
    if err:
        return err
    limit, err = _int_arg('limit', 200, 0, 2000)
    if err:
        return err

    query = SolicitudCompra.query.options(
        joinedload(SolicitudCompra.solicitado_por),
        joinedload(SolicitudCompra.proyecto_ref),
        selectinload(SolicitudCompra.detalles).joinedload(SolicitudCompraDetalle.producto),
    )

    estatus = (request.args.get('estatus') or '').strip().upper()
    if estatus:
        query = query.filter(SolicitudCompra.estatus == estatus)

    proyecto_id = request.args.get('proyecto_id')
    if proyecto_id:
        try:
            query = query.filter(SolicitudCompra.proyecto_id == int(proyecto_id))
        except (TypeError, ValueError):
            return jsonify({'detail': "proyecto_id debe ser entero"}), 422

    proveedor = (request.args.get('proveedor') or '').strip()
    if proveedor:
        query = query.filter(SolicitudCompra.proveedor_sugerido.ilike(f'%{proveedor}%'))

    sols = (
        query.order_by(SolicitudCompra.fecha_creacion.desc())
        .offset(skip).limit(limit).all()
    )
    return jsonify([_compra_to_dict(s) for s in sols])


@bp.route('/solicitudes-compra/<int:sol_id>', methods=['GET'])
@_require_compras
def get_solicitud_compra(sol_id: int):
    sol = _load_compra(sol_id)
    if not sol:
        return jsonify({'detail': 'Solicitud de compra no encontrada'}), 404
    return jsonify(_compra_to_dict(sol))


@bp.route('/solicitudes-compra/<int:sol_id>/estado', methods=['PATCH'])
@_require_compras
def update_solicitud_compra_estado(sol_id: int):
    data, err = _parse_or_422(CompraEstadoSchema(), request.get_json(silent=True))
    if err:
        return err

    sol = _load_compra(sol_id)
    if not sol:
        return jsonify({'detail': 'Solicitud de compra no encontrada'}), 404

    previo = sol.estatus
    nuevo = data['estatus']

    TRANSICIONES = {
        'PENDIENTE': {'ORDENADA', 'CANCELADA'},
        'ORDENADA':  {'PENDIENTE', 'CANCELADA'},
        'RECIBIDA':  {'ORDENADA'},   # reabrir para recibir más / corregir
        'CANCELADA': {'PENDIENTE'},
    }
    if nuevo != previo and nuevo not in TRANSICIONES.get(previo, set()):
        return jsonify({
            'detail': f'Transición inválida: {previo} → {nuevo}',
            'permitidas': sorted(TRANSICIONES.get(previo, set())),
        }), 409

    sol.estatus = nuevo
    if nuevo == 'ORDENADA' and not sol.fecha_orden:
        sol.fecha_orden = datetime.datetime.now()
    if nuevo == 'CANCELADA':
        sol.fecha_cierre = datetime.datetime.now()
    elif nuevo in ('PENDIENTE', 'ORDENADA'):
        sol.fecha_cierre = None

    if previo != nuevo:
        _audit(request.current_user, f"Solicitud de compra {sol.folio}: {previo} → {nuevo}")
    db.session.commit()

    sol = _load_compra(sol_id)
    if previo != nuevo:
        emit_to_role(_COMPRA_ROLES, 'compra:changed', {'id': sol.id, 'action': f'estado:{nuevo}'})
    return jsonify(_compra_to_dict(sol))


@bp.route('/solicitudes-compra/<int:sol_id>/detalles/<int:det_id>', methods=['PATCH'])
@_require_compras
def patch_solicitud_compra_detalle(sol_id: int, det_id: int):
    """Edita cantidad/precio/notas de una línea — solo mientras está PENDIENTE."""
    data, err = _parse_or_422(CompraDetallePatchSchema(), request.get_json(silent=True))
    if err:
        return err

    sol = SolicitudCompra.query.filter(SolicitudCompra.id == sol_id).first()
    if not sol:
        return jsonify({'detail': 'Solicitud de compra no encontrada'}), 404
    if sol.estatus != 'PENDIENTE':
        return jsonify({
            'detail': f'Solo solicitudes PENDIENTES permiten editar líneas (actual: {sol.estatus})'
        }), 409

    det = SolicitudCompraDetalle.query.filter(
        SolicitudCompraDetalle.id == det_id,
        SolicitudCompraDetalle.solicitud_compra_id == sol_id,
    ).first()
    if not det:
        return jsonify({'detail': 'Línea no encontrada'}), 404

    if data.get('cantidad_solicitada') is not None:
        det.cantidad_solicitada = Decimal(str(data['cantidad_solicitada']))
    if 'precio_estimado' in (request.get_json(silent=True) or {}):
        pe = data.get('precio_estimado')
        det.precio_estimado = Decimal(str(pe)) if pe is not None else None
    if 'notas' in (request.get_json(silent=True) or {}):
        det.notas = (data.get('notas') or '').strip() or None

    db.session.commit()
    sol = _load_compra(sol_id)
    emit_to_role(_COMPRA_ROLES, 'compra:changed', {'id': sol_id, 'action': 'detalle_updated'})
    return jsonify(_compra_to_dict(sol))


@bp.route('/solicitudes-compra/<int:sol_id>', methods=['DELETE'])
@_require_compras
def cancelar_solicitud_compra(sol_id: int):
    """Cancela (soft) la solicitud de compra. No se borra el registro: queda
    CANCELADA para conservar la bitácora."""
    sol = SolicitudCompra.query.filter(SolicitudCompra.id == sol_id).first()
    if not sol:
        return jsonify({'detail': 'Solicitud de compra no encontrada'}), 404
    if sol.estatus == 'RECIBIDA':
        return jsonify({'detail': 'No se puede cancelar una solicitud ya recibida'}), 409

    previo = sol.estatus
    sol.estatus = 'CANCELADA'
    sol.fecha_cierre = datetime.datetime.now()
    _audit(request.current_user, f"Solicitud de compra {sol.folio} cancelada (era {previo})")
    db.session.commit()
    emit_to_role(_COMPRA_ROLES, 'compra:changed', {'id': sol_id, 'action': 'estado:CANCELADA'})
    return jsonify({'detail': 'Solicitud de compra cancelada', 'id': sol_id})


# ─── Recibir (atender) → ENTRADA al stock ─────────────────────────────────────

@bp.route('/solicitudes-compra/<int:sol_id>/recibir', methods=['POST'])
@limiter.limit('20/minute', key_func=lambda: f"ip:{get_real_client_ip_flask()}")
@_require_compras
def recibir_solicitud_compra(sol_id: int):
    """Recepción total o parcial. Por cada línea con producto del catálogo crea
    una ENTRADA al almacén destino (sube stock). Las líneas de texto libre solo
    acumulan `cantidad_recibida` (no hay producto que mover).

    Cuando todas las líneas con cantidad > 0 quedan completamente recibidas, la
    solicitud pasa a RECIBIDA. Si era PENDIENTE, una recepción la avanza a
    ORDENADA (compra que ya llegó sin haberse marcado como ordenada).
    """
    data, err = _parse_or_422(RecibirCompraSchema(), request.get_json(silent=True))
    if err:
        return err

    sol = (
        SolicitudCompra.query
        .options(selectinload(SolicitudCompra.detalles).joinedload(SolicitudCompraDetalle.producto))
        .filter(SolicitudCompra.id == sol_id)
        .first()
    )
    if not sol:
        return jsonify({'detail': 'Solicitud de compra no encontrada'}), 404
    if sol.estatus not in ('PENDIENTE', 'ORDENADA'):
        return jsonify({
            'detail': f'Solo solicitudes PENDIENTE u ORDENADA pueden recibirse (actual: {sol.estatus})'
        }), 409

    detalles_por_id = {d.id: d for d in (sol.detalles or [])}
    vistos: set[int] = set()
    # (det, delta) por recepción con cantidad > 0
    recepciones: list[tuple[SolicitudCompraDetalle, Decimal]] = []

    for item in data['recepciones']:
        det_id = item['detalle_id']
        if det_id in vistos:
            return jsonify({'detail': f'Línea #{det_id} duplicada en el payload'}), 422
        vistos.add(det_id)

        det = detalles_por_id.get(det_id)
        if not det:
            return jsonify({'detail': f'Línea #{det_id} no pertenece a la solicitud {sol.folio}'}), 422

        delta = Decimal(str(item['cantidad_recibida']))
        if delta < 0:
            return jsonify({'detail': f'Línea #{det_id}: cantidad_recibida no puede ser negativa'}), 422
        if delta == 0:
            continue
        unidad_det = det.unidad or (det.producto.unidad if det.producto else None)
        if not _unidad_permite_decimales(unidad_det) and delta != delta.to_integral_value():
            return jsonify({'detail': f'Línea #{det_id}: este ítem se recibe en cantidades enteras (sin decimales)'}), 422

        sol_c = Decimal(str(det.cantidad_solicitada or 0))
        rec_c = Decimal(str(det.cantidad_recibida or 0))
        pendiente = sol_c - rec_c
        if delta > pendiente:
            return jsonify({
                'detail': (
                    f'Línea #{det_id}: recibir {delta} excede el pendiente ({pendiente}). '
                    f'Solicitado {sol_c}, ya recibido {rec_c}.'
                )
            }), 422
        recepciones.append((det, delta))

    if not recepciones:
        return jsonify({'detail': 'Ninguna línea con cantidad mayor a 0 para recibir'}), 422

    # ¿Hay líneas con producto del catálogo? Solo entonces necesitamos almacén.
    hay_producto = any(det.producto_id for det, _ in recepciones)
    almacen_id = None
    if hay_producto:
        almacen_id = data.get('almacen_destino_id') or _almacen_default_id()
        if not almacen_id:
            return jsonify({'detail': 'No hay bodegas registradas para registrar la entrada'}), 400
        almacen = Almacen.query.filter(Almacen.id == almacen_id, Almacen.activo == True).first()  # noqa: E712
        if not almacen:
            return jsonify({'detail': f'Almacén #{almacen_id} no existe o está inactivo'}), 404

    # Sumar deltas por producto (una línea puede repetir producto en otra línea).
    delta_por_producto: dict[int, Decimal] = {}
    for det, delta in recepciones:
        if det.producto_id:
            delta_por_producto[det.producto_id] = (
                delta_por_producto.get(det.producto_id, Decimal('0')) + delta
            )

    user = request.current_user
    motivo_base = (data.get('motivo') or '').strip() or f'Recepción compra {sol.folio}'

    try:
        # Lock determinístico (producto id asc) + ENTRADA por producto.
        productos_locked: dict[int, Producto] = {}
        for prod_id in sorted(delta_por_producto.keys()):
            producto = (
                Producto.query.with_for_update(nowait=True)
                .filter(Producto.id == prod_id).first()
            )
            if not producto:
                db.session.rollback()
                return jsonify({'detail': f'Producto #{prod_id} no encontrado'}), 404
            productos_locked[prod_id] = producto

            cant_total = delta_por_producto[prod_id]
            # ENTRADA al bucket del proyecto de la compra (feature stock por
            # proyecto): lo recibido para un proyecto queda etiquetado a él;
            # una compra sin proyecto cae en el bucket general.
            _depositar(prod_id, almacen_id, sol.proyecto_id, cant_total)

            db.session.add(MovimientoInventario(
                tipo='ENTRADA',
                producto_id=prod_id,
                cantidad=cant_total,
                almacen_destino_id=almacen_id,
                proyecto_destino_id=sol.proyecto_id,
                motivo=motivo_base,
                usuario_id=user.id,
            ))

        for producto in productos_locked.values():
            _recalcular_caches(producto, almacen_id)

        # Acumular cantidad_recibida por línea.
        for det, delta in recepciones:
            det.cantidad_recibida = Decimal(str(det.cantidad_recibida or 0)) + delta

        # ¿Quedó completa? Toda línea con solicitada > 0 debe tener recibida >= solicitada.
        completa = True
        for d in (sol.detalles or []):
            s_c = Decimal(str(d.cantidad_solicitada or 0))
            r_c = Decimal(str(d.cantidad_recibida or 0))
            if s_c > 0 and r_c < s_c:
                completa = False
                break

        if completa:
            sol.estatus = 'RECIBIDA'
            sol.fecha_cierre = datetime.datetime.now()
        elif sol.estatus == 'PENDIENTE':
            # Recepción parcial sobre una solicitud aún no ordenada: la avanzamos.
            sol.estatus = 'ORDENADA'
            if not sol.fecha_orden:
                sol.fecha_orden = datetime.datetime.now()

        _audit(
            user,
            f"Recepción compra {sol.folio} "
            f"({len(recepciones)} líneas{f', almacén #{almacen_id}' if almacen_id else ''}) "
            f"→ {sol.estatus}",
        )
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        if _es_error_de_lock(exc):
            return jsonify({'detail': 'Stock bloqueado por otra operación, reintenta'}), 409
        raise

    sol = _load_compra(sol_id)
    emit_to_role(_COMPRA_ROLES, 'compra:changed', {
        'id': sol.id, 'action': 'recibida' if sol.estatus == 'RECIBIDA' else 'recepcion_parcial',
    })
    if hay_producto:
        # Subió stock real → refrescar catálogo / bajo-mínimo / kardex.
        emit_to_role(_INV_ROLES, 'movimiento:changed', {'origen': 'compra_recepcion', 'compra_id': sol.id})
        emit_to_role(_INV_ROLES, 'producto:changed', {'origen': 'compra_recepcion'})
    return jsonify(_compra_to_dict(sol))


# ─── Productos con compra activa (indicadores en catálogo / bajo-mínimo) ──────

@bp.route('/solicitudes-compra/productos-activos', methods=['GET'])
@_require_compras
def productos_con_compra_activa():
    """Devuelve, por producto, la compra activa (PENDIENTE u ORDENADA) que lo
    incluye. El SPA lo usa para marcar 'este producto ya tiene compra en curso'
    en el catálogo y en bajo-mínimo.

    Response: `[{producto_id, solicitud_id, folio, estatus, cantidad_solicitada,
    cantidad_recibida}]`. Si un producto está en varias compras activas se
    devuelve una fila por compra; el front se queda con la más reciente.
    """
    rows = (
        db.session.query(
            SolicitudCompraDetalle.producto_id,
            SolicitudCompra.id,
            SolicitudCompra.estatus,
            SolicitudCompraDetalle.cantidad_solicitada,
            SolicitudCompraDetalle.cantidad_recibida,
        )
        .join(SolicitudCompra, SolicitudCompra.id == SolicitudCompraDetalle.solicitud_compra_id)
        .filter(
            SolicitudCompraDetalle.producto_id.isnot(None),
            SolicitudCompra.estatus.in_(['PENDIENTE', 'ORDENADA']),
        )
        .order_by(SolicitudCompra.id.desc())
        .all()
    )
    return jsonify([
        {
            'producto_id': pid,
            'solicitud_id': sid,
            'folio': f'SC-{sid:06d}',
            'estatus': estatus,
            'cantidad_solicitada': float(c_sol or 0),
            'cantidad_recibida': float(c_rec or 0),
        }
        for (pid, sid, estatus, c_sol, c_rec) in rows
    ])


# ─── PDF de la solicitud de compra ────────────────────────────────────────────

@bp.route('/solicitudes-compra/<int:sol_id>/pdf', methods=['GET'])
@_require_compras
def imprimir_solicitud_compra(sol_id: int):
    """Genera el PDF de la orden (reutiliza la plantilla de OC express) y expone
    el link de WhatsApp en el header `X-Whatsapp-Link`."""
    sol = _load_compra(sol_id)
    if not sol:
        return jsonify({'detail': 'Solicitud de compra no encontrada'}), 404

    proveedor = (sol.proveedor_sugerido or '').strip() or 'Sin proveedor'
    contacto = (sol.proveedor_contacto or '').strip()
    fecha_str = sol.fecha_creacion.strftime('%d/%m/%Y %H:%M') if sol.fecha_creacion else ''
    solicitante = (sol.solicitado_por.full_name or sol.solicitado_por.username) if sol.solicitado_por else '—'

    def _q(v):
        v = float(v or 0)
        return int(v) if v % 1 == 0 else round(v, 2)

    items_view = []
    for d in (sol.detalles or []):
        prod = d.producto
        items_view.append({
            'codigo': (prod.codigo if prod else None) or '—',
            'descripcion': (prod.descripcion if prod else None) or d.descripcion_libre or '—',
            'unidad': d.unidad or (prod.unidad if prod else '') or '',
            'cantidad': _q(d.cantidad_solicitada),
        })

    pdf = _render_oc_express_pdf(
        folio=sol.folio, fecha_str=fecha_str,
        proveedor=proveedor, contacto=contacto,
        notas=(sol.notas or ''), solicitante=solicitante,
        items=items_view,
    )
    if pdf is None:
        return jsonify({'detail': 'Error al generar el PDF (xhtml2pdf no disponible)'}), 500

    _audit(request.current_user, f"PDF solicitud de compra {sol.folio}")
    db.session.commit()

    response = send_file(pdf, mimetype='application/pdf', as_attachment=False, download_name=f'{sol.folio}.pdf')
    wa_link = _whatsapp_link(proveedor, contacto, sol.folio, items_view)
    response.headers['X-Whatsapp-Link'] = wa_link
    response.headers['X-Folio'] = sol.folio
    response.headers['Access-Control-Expose-Headers'] = 'X-Whatsapp-Link, X-Folio'
    return response
