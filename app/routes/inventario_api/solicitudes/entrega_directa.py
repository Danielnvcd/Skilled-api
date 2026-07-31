"""Entrega directa de mostrador: el almacenista surte en el acto.

No pasa por el ciclo de aprobación: crea una SolicitudMaterial ya ENTREGADA y
descuenta stock físico directo, sin reservas.
"""
import datetime
from decimal import Decimal

from flask import jsonify, request

from app.extensions import db, limiter, get_real_client_ip_flask
from app.models import (
    MovimientoInventario, Producto, SolicitudMaterial, SolicitudMaterialDetalle,
)
from app.realtime import emit_to_role

from .._core import (
    bp,
    ErrorDeNegocio, transaccion_de_stock,
    _require_inventario_admin,
    _parse_or_422,
    EntregaDirectaSchema,
    _solicitud_to_dict,
    _audit,
    _recalcular_caches, _consumir_proyecto_luego_general,
    _unidad_permite_decimales, _es_entero,
    resolver_almacen_activo, resolver_proyecto, resolver_trabajador_activo,
    _INV_ROLES, _SOL_ROLES,
)
from ._comun import _descontar_celda, _lock_producto_o_404


def _resolver_solicitante_entrega(data: dict):
    """Quién recibe el material en una entrega directa: un trabajador del sistema
    o un nombre libre. Al menos uno es obligatorio. Devuelve `(trabajador, nombre)`
    donde solo uno de los dos viene informado."""
    trab_id = data.get('solicitante_trabajador_id')
    nombre_libre = (data.get('solicitante_nombre') or '').strip() or None
    trabajador = resolver_trabajador_activo(trab_id) if trab_id is not None else None
    if trabajador is None and not nombre_libre:
        raise ErrorDeNegocio(
            'Indica quién recibe el material: elige un trabajador o escribe un nombre.', 422,
        )
    return trabajador, nombre_libre


def _agrupar_lineas_entrega_directa(detalles: list[dict]):
    """Valida las líneas de una entrega directa (producto activo, cantidad > 0 y
    regla de decimales por unidad) y las agrupa por producto.

    Un mismo producto puede venir en varias líneas: se suman para validar y
    descontar stock una sola vez. Devuelve `(cantidad_por_producto,
    estante_por_producto)`; de venir varias líneas del mismo producto con
    distinto estante gana el último (best-effort para el sub-libro de celdas).
    """
    cantidad_por_producto: dict[int, Decimal] = {}
    estante_por_producto: dict[int, int] = {}
    productos: dict[int, Producto] = {}
    errores = []

    for idx, det in enumerate(detalles):
        etiqueta = f"Línea {idx + 1}"
        pid = det['producto_id']
        prod = productos.get(pid)
        if prod is None:
            prod = Producto.query.filter(
                Producto.id == pid,
                Producto.activo == True,  # noqa: E712
            ).first()
            if not prod:
                errores.append(f"{etiqueta}: producto #{pid} no existe o inactivo")
                continue
            productos[pid] = prod

        cantidad = Decimal(str(det['cantidad']))
        if cantidad <= 0:
            errores.append(f"{etiqueta}: la cantidad debe ser mayor a 0")
            continue
        if not _unidad_permite_decimales(prod.unidad) and not _es_entero(cantidad):
            errores.append(
                f"{etiqueta}: '{prod.descripcion}' se entrega en cantidades enteras "
                f"(unidad: {prod.unidad or 'pieza'})"
            )
            continue

        cantidad_por_producto[pid] = cantidad_por_producto.get(pid, Decimal('0')) + cantidad
        if det.get('estante_id'):
            estante_por_producto[pid] = det['estante_id']

    if errores:
        raise ErrorDeNegocio(errores, 422)
    if not cantidad_por_producto:
        raise ErrorDeNegocio('Ninguna línea con cantidad mayor a 0', 422)
    return cantidad_por_producto, estante_por_producto


@bp.route('/solicitudes/entrega-directa', methods=['POST'])
@limiter.limit(
    "20/minute",
    key_func=lambda: f"ip:{get_real_client_ip_flask()}",
)
@_require_inventario_admin
@transaccion_de_stock
def create_entrega_directa():
    """Entrega directa de mostrador (solo inventario/admin).

    El almacenista surte material para un proyecto en el acto, sin solicitud
    previa del trabajador. Crea una SolicitudMaterial ya ENTREGADA + un
    movimiento SALIDA por línea, descontando del almacén origen. El solicitante
    real (trabajador o nombre libre) se guarda aparte del capturista, y el PDF
    lo muestra.

    Body: `{proyecto, proyecto_id?, solicitante_trabajador_id?,
             solicitante_nombre?, almacen_origen_id?, notas?, motivo?,
             detalles: [{producto_id, cantidad, estante_id?}]}`.

    Solo MATERIAL: no maneja herramientas (esas siguen por solicitud normal).
    No usa reservas: descuenta stock físico directo.
    """
    user = request.current_user

    data, err = _parse_or_422(EntregaDirectaSchema(), request.get_json(silent=True))
    if err: return err

    proyecto = (data.get('proyecto') or '').strip()
    if not proyecto:
        return jsonify({'detail': 'Debes seleccionar un proyecto'}), 422

    trabajador, nombre_libre = _resolver_solicitante_entrega(data)
    proy = resolver_proyecto(data.get('proyecto_id'), proyecto)
    proyecto_id = proy.id if proy else None
    almacen = resolver_almacen_activo(data.get('almacen_origen_id'))

    delta_por_producto, estante_por_producto = _agrupar_lineas_entrega_directa(data['detalles'])
    motivo_base = (data.get('motivo') or '').strip() or f'Entrega directa — {proyecto}'

    # Lock determinístico (id asc) sobre Producto + buckets, validar stock en el
    # almacén y descontar. Sin reservas: es entrega inmediata.
    productos: dict[int, Producto] = {}
    for pid in sorted(delta_por_producto):
        producto = _lock_producto_o_404(pid)
        productos[pid] = producto
        # Consumo proyecto→general en el almacén de origen (feature stock por
        # proyecto): descuenta del bucket del proyecto de la entrega y el
        # remanente del general; nunca de otros proyectos.
        err = _consumir_proyecto_luego_general(
            pid, almacen.id, proyecto_id, delta_por_producto[pid],
        )
        if err:
            raise ErrorDeNegocio(
                f'Stock insuficiente en {almacen.nombre} para {producto.codigo}: {err}.', 409,
            )

    # Crear la solicitud ENTREGADA + sus líneas + las SALIDAs.
    nueva = SolicitudMaterial(
        solicitante_id=user.id,
        entrega_directa=True,
        solicitante_trabajador_id=(trabajador.id if trabajador else None),
        solicitante_nombre=(None if trabajador else nombre_libre),
        proyecto=proyecto,
        proyecto_id=proyecto_id,
        notas=data.get('notas'),
        estatus='ENTREGADA',
        fecha_cierre=datetime.datetime.now(),
        entregada_por_id=user.id,
    )
    db.session.add(nueva)
    db.session.flush()

    for pid in sorted(delta_por_producto):
        cant_total = delta_por_producto[pid]
        db.session.add(SolicitudMaterialDetalle(
            solicitud_id=nueva.id,
            tipo_item='MATERIAL',
            producto_id=pid,
            cantidad_solicitada=cant_total,
            cantidad_aprobada=cant_total,
            cantidad_entregada=cant_total,
        ))
        db.session.add(MovimientoInventario(
            tipo='SALIDA',
            producto_id=pid,
            cantidad=cant_total,
            almacen_origen_id=almacen.id,
            proyecto_origen_id=proyecto_id,
            motivo=motivo_base,
            usuario_id=user.id,
        ))
        _descontar_celda(pid, estante_por_producto.get(pid), almacen.id, cant_total)
        _recalcular_caches(productos[pid], almacen.id)

    quien = trabajador.nombre_completo if trabajador else nombre_libre
    _audit(
        user,
        f"Entrega directa #{nueva.id} → {quien} — proyecto: {proyecto} "
        f"({len(delta_por_producto)} productos, almacén #{almacen.id})",
    )
    db.session.commit()

    db.session.refresh(nueva)
    _ = list(nueva.detalles)
    emit_to_role(_SOL_ROLES, 'solicitud:changed', {
        'id': nueva.id, 'action': 'entrega_directa',
    })
    emit_to_role(_INV_ROLES, 'movimiento:changed', {
        'origen': 'entrega_directa', 'solicitud_id': nueva.id,
    })
    return jsonify(_solicitud_to_dict(nueva))
