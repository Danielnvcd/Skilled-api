"""Entrega total o parcial de una solicitud APROBADA (Pausa 8b)."""
import datetime
from decimal import Decimal
from typing import NamedTuple

from flask import jsonify, request
from sqlalchemy.orm import selectinload

from app.extensions import db, limiter, get_real_client_ip_flask
from app.models import (
    AsignacionHerramienta, Herramienta, HerramientaUnidad, MovimientoInventario,
    Producto, SolicitudMaterial, SolicitudMaterialDetalle, Trabajador,
    crear_evento_herramienta,
)
from app.realtime import emit_to_role

from .._core import (
    bp,
    ErrorDeNegocio, transaccion_de_stock,
    _require_inventario_admin,
    _parse_or_422,
    EntregarSolicitudSchema,
    _solicitud_to_dict,
    _audit,
    _recalcular_caches, _consumir_proyecto_luego_general,
    _reservas_de_solicitud, _liberar_reservas,
    _unidad_permite_decimales, _es_entero,
    resolver_almacen_activo,
    _INV_ROLES, _SOL_ROLES,
)
from ._comun import _descontar_celda, _lock_producto_o_404


# ─── Pausa 8b: entrega total o parcial ───────────────────────────────────────
#
# El flujo se reparte en tres fases, cada una en su función:
#   1. PLANEAR  — `_planear_entregas` valida el payload contra las líneas de la
#      solicitud y devuelve qué se va a entregar (sin tocar nada).
#   2. RESERVAR — se toman los locks: productos (`_lock_producto_o_404`) y
#      unidades de herramienta (`_reservar_unidades_herramienta`).
#   3. APLICAR  — se mueven stock, líneas, movimientos y asignaciones.

class _LineaAEntregar(NamedTuple):
    """Una línea que sí se va a entregar en esta llamada.

    `baseline` es la cantidad aprobada de referencia: la de la línea o, en
    solicitudes anteriores a la Pausa 8b que se aprobaron sin sembrarla, la
    solicitada. `cantidad` es el delta a entregar ahora (entero en herramientas).
    """
    detalle: SolicitudMaterialDetalle
    cantidad: Decimal
    baseline: Decimal


def _validar_linea_herramienta(det, delta: Decimal) -> _LineaAEntregar:
    """Valida una línea HERRAMIENTA del payload. Las herramientas son enteras:
    1 unidad física = 1 asignación."""
    if not det.herramienta_id:
        raise ErrorDeNegocio(f'Detalle #{det.id}: línea HERRAMIENTA sin herramienta_id', 422)
    if delta != delta.to_integral_value():
        raise ErrorDeNegocio(f'Detalle #{det.id}: cantidad de herramientas debe ser entera', 422)

    aprobada = int(det.cantidad_aprobada or 0)
    entregada = int(det.cantidad_entregada or 0)
    baseline = aprobada if aprobada > 0 else int(det.cantidad_solicitada or 0)
    pendiente = baseline - entregada
    cantidad = int(delta)
    if cantidad > pendiente:
        raise ErrorDeNegocio(
            f'Detalle #{det.id}: cantidad_entregada ({cantidad}) excede el '
            f'pendiente ({pendiente}). Aprobada: {baseline}, '
            f'ya entregada: {entregada}.', 422,
        )
    return _LineaAEntregar(det, cantidad, baseline)


def _validar_linea_material(det, delta: Decimal) -> _LineaAEntregar:
    """Valida una línea MATERIAL del payload contra lo aprobado y pendiente."""
    if not det.producto_id:
        raise ErrorDeNegocio(f'Detalle #{det.id}: línea MATERIAL sin producto_id', 422)

    unidad = det.producto.unidad if det.producto else None
    if not _unidad_permite_decimales(unidad) and not _es_entero(delta):
        raise ErrorDeNegocio(
            f'Detalle #{det.id}: este material se entrega en cantidades enteras '
            f'(unidad: {unidad or "pieza"})', 422,
        )

    aprobada = Decimal(str(det.cantidad_aprobada or 0))
    entregada = Decimal(str(det.cantidad_entregada or 0))
    # Compat con solicitudes pre-8b que aprobaron sin sembrar cantidad_aprobada.
    baseline = aprobada if aprobada > 0 else Decimal(str(det.cantidad_solicitada or 0))
    pendiente = baseline - entregada
    if delta > pendiente:
        raise ErrorDeNegocio(
            f'Detalle #{det.id}: cantidad_entregada ({delta}) excede el pendiente '
            f'({pendiente}). Aprobada: {baseline}, ya entregada: {entregada}.', 422,
        )
    return _LineaAEntregar(det, delta, baseline)


def _planear_entregas(sol: SolicitudMaterial, entregas: list[dict]):
    """Valida el payload de entrega contra las líneas de la solicitud.

    Devuelve `(materiales, herramientas)` con las líneas que sí se entregan
    (cantidad > 0). No toca la base: si algo no cuadra levanta `ErrorDeNegocio`
    antes de que se haya modificado nada.
    """
    detalles_por_id = {d.id: d for d in (sol.detalles or [])}
    materiales: list[_LineaAEntregar] = []
    herramientas: list[_LineaAEntregar] = []
    vistos: set[int] = set()

    for item in entregas:
        det_id = item['detalle_id']
        if det_id in vistos:
            raise ErrorDeNegocio(f'Detalle #{det_id} duplicado en el payload', 422)
        vistos.add(det_id)

        det = detalles_por_id.get(det_id)
        if not det:
            raise ErrorDeNegocio(
                f'Detalle #{det_id} no pertenece a la solicitud #{sol.id}', 422,
            )

        delta = Decimal(str(item['cantidad_entregada']))
        if delta < 0:
            raise ErrorDeNegocio(
                f'Detalle #{det_id}: cantidad_entregada no puede ser negativa', 422,
            )
        if delta == 0:
            continue  # el front puede mandar 0 para "no entregar esta línea ahora"

        if (det.tipo_item or 'MATERIAL').upper() == 'HERRAMIENTA':
            herramientas.append(_validar_linea_herramienta(det, delta))
        else:
            materiales.append(_validar_linea_material(det, delta))

    if not materiales and not herramientas:
        raise ErrorDeNegocio('Ninguna línea con cantidad mayor a 0 para entregar', 422)
    return materiales, herramientas


def _trabajador_para_herramientas(sol: SolicitudMaterial) -> Trabajador:
    """Trabajador al que se asignan las herramientas: el ligado a la cuenta del
    solicitante. Las asignaciones son a un Trabajador, no a un User, así que sin
    ese vínculo no se puede entregar herramienta."""
    if not sol.solicitante or not sol.solicitante.trabajador_id:
        raise ErrorDeNegocio(
            'El solicitante no tiene un trabajador asociado. '
            'Liga la cuenta a un trabajador desde Usuarios para poder '
            'entregar las herramientas, o asígnalas manualmente desde '
            'Asignaciones de Herramienta.', 400,
        )
    trab = Trabajador.query.filter(
        Trabajador.id == sol.solicitante.trabajador_id,
        Trabajador.activo == True,  # noqa: E712
    ).first()
    if not trab:
        raise ErrorDeNegocio(
            f'El trabajador #{sol.solicitante.trabajador_id} asociado al '
            f'solicitante no existe o está inactivo.', 400,
        )
    return trab


def _totalizar(lineas: list[_LineaAEntregar], clave) -> dict:
    """Suma las cantidades de las líneas agrupando por `clave(detalle)` — un
    mismo producto/herramienta puede venir en varias líneas y se valida y
    descuenta una sola vez."""
    total: dict = {}
    for linea in lineas:
        k = clave(linea.detalle)
        total[k] = total.get(k, 0) + linea.cantidad
    return total


def _reservar_unidades_herramienta(por_herramienta: dict[int, int]):
    """Toma (FOR UPDATE, id asc anti-deadlock) exactamente las unidades
    DISPONIBLES que pide cada herramienta. Si no alcanzan, 409."""
    pools: dict[int, list[HerramientaUnidad]] = {}
    for h_id in sorted(por_herramienta):
        requeridas = por_herramienta[h_id]
        unidades = (
            HerramientaUnidad.query
            .with_for_update(nowait=True)
            .filter(
                HerramientaUnidad.herramienta_id == h_id,
                HerramientaUnidad.estado == 'DISPONIBLE',
            )
            .order_by(HerramientaUnidad.id.asc())
            .limit(requeridas)
            .all()
        )
        if len(unidades) < requeridas:
            herr = db.session.get(Herramienta, h_id)
            nombre = herr.descripcion if herr else f'#{h_id}'
            raise ErrorDeNegocio(
                f'No hay unidades suficientes DISPONIBLES de "{nombre}": '
                f'requiere {requeridas}, disponibles {len(unidades)}.', 409,
            )
        pools[h_id] = unidades
    return pools


def _descontar_stock_de_entrega(por_producto: dict[int, Decimal], almacen_id: int,
                                proyecto_id: int | None) -> dict[int, Producto]:
    """Lock determinístico (id asc) + consumo físico + liberación de la reserva
    equivalente, por cada producto de la entrega. Devuelve los productos
    bloqueados para poder recalcular sus caches después."""
    productos: dict[int, Producto] = {}
    for prod_id in sorted(por_producto):
        producto = _lock_producto_o_404(prod_id)
        productos[prod_id] = producto
        cantidad = por_producto[prod_id]

        # Consumo proyecto→general en el almacén de origen (feature stock por
        # proyecto): descuenta primero del bucket del proyecto de la solicitud
        # y el remanente del general; nunca de otros proyectos.
        err = _consumir_proyecto_luego_general(prod_id, almacen_id, proyecto_id, cantidad)
        if err:
            raise ErrorDeNegocio(
                f'Stock insuficiente en bodega #{almacen_id} para {producto.codigo}: {err}.', 409,
            )

        # Liberar la reserva equivalente (cache global; clamp a 0 por seguridad).
        restante = Decimal(str(producto.stock_reservado or 0)) - cantidad
        producto.stock_reservado = restante if restante > 0 else Decimal('0')
    return productos


def _aplicar_entregas_material(lineas: list[_LineaAEntregar], sol: SolicitudMaterial,
                               almacen_id: int, estante_por_detalle: dict,
                               motivo: str, user):
    """Suma lo entregado a cada línea y registra su movimiento SALIDA."""
    for det, cantidad, baseline in lineas:
        # Si la línea era pre-8b (cant_aprob=0), formalizar la aprobación con
        # el baseline para que la lógica de completa/reservas sea consistente.
        if Decimal(str(det.cantidad_aprobada or 0)) == 0 and baseline > 0:
            det.cantidad_aprobada = baseline

        det.cantidad_entregada = Decimal(str(det.cantidad_entregada or 0)) + cantidad
        db.session.add(MovimientoInventario(
            tipo='SALIDA',
            producto_id=det.producto_id,
            cantidad=cantidad,
            almacen_origen_id=almacen_id,
            proyecto_origen_id=sol.proyecto_id,
            motivo=motivo,
            usuario_id=user.id,
        ))
        _descontar_celda(det.producto_id, estante_por_detalle.get(det.id), almacen_id, cantidad)


def _aplicar_entregas_herramienta(lineas: list[_LineaAEntregar], sol: SolicitudMaterial,
                                  pools: dict[int, list[HerramientaUnidad]],
                                  trabajador: Trabajador, fecha_devolucion,
                                  motivo: str, user) -> int:
    """Crea una AsignacionHerramienta por unidad entregada, marca la unidad como
    ASIGNADA y registra el evento. Devuelve cuántas asignaciones se crearon."""
    asignaciones = 0
    for det, cantidad, baseline in lineas:
        if int(det.cantidad_aprobada or 0) == 0 and baseline > 0:
            det.cantidad_aprobada = baseline

        # Consumimos del pool de unidades reservado para esta herramienta.
        pool = pools[det.herramienta_id]
        for _ in range(cantidad):
            unidad = pool.pop(0)
            estado_anterior = unidad.estado
            asig = AsignacionHerramienta(
                unidad_id=unidad.id,
                trabajador_id=trabajador.id,
                solicitud_id=sol.id,
                proyecto=(sol.proyecto or None),
                fecha_entrega=datetime.datetime.utcnow(),
                fecha_devolucion_prevista=fecha_devolucion,
                estado='ACTIVA',
                condicion_entrega='BUENA',
                observaciones_entrega=motivo,
                entregado_por_id=user.id,
            )
            db.session.add(asig)
            db.session.flush()  # para tener asig.id en el evento

            unidad.estado = 'ASIGNADA'
            unidad.asignado_trabajador_id = trabajador.id
            crear_evento_herramienta(
                unidad, 'ASIGNACION', user,
                observaciones=f'Entrega solicitud #{sol.id} → {trabajador.nombre_completo}',
                estado_anterior=estado_anterior, estado_nuevo='ASIGNADA',
                referencia_id=asig.id, referencia_tipo='asignacion',
            )
            asignaciones += 1

        det.cantidad_entregada = int(det.cantidad_entregada or 0) + cantidad
    return asignaciones


def _entrega_completa(sol: SolicitudMaterial) -> bool:
    """¿Ya se surtió todo lo aprobado? Considera AMBOS tipos de línea: cada una
    debe tener entregada ≥ aprobada (cuando aprobada > 0)."""
    for d in (sol.detalles or []):
        aprobada = Decimal(str(d.cantidad_aprobada or 0))
        entregada = Decimal(str(d.cantidad_entregada or 0))
        if aprobada > 0 and entregada < aprobada:
            return False
    return True


@bp.route('/solicitudes/<int:sol_id>/entregar', methods=['POST'])
@limiter.limit(
    "20/minute",
    key_func=lambda: f"ip:{get_real_client_ip_flask()}",
)
@_require_inventario_admin
@transaccion_de_stock
def entregar_solicitud(sol_id: int):
    """Entrega total o parcial de una solicitud APROBADA (Pausa 8b).

    Body: `{ almacen_origen_id?, motivo?, entregas: [{detalle_id, cantidad_entregada}, ...] }`.

    Crea SALIDA por cada línea MATERIAL con cantidad > 0, libera la porción de
    `stock_reservado` correspondiente y descuenta el stock físico del almacén.
    Las líneas HERRAMIENTA generan asignaciones al trabajador del solicitante.

    Si tras la entrega todas las líneas tienen cantidad_entregada ==
    cantidad_aprobada, la solicitud queda ENTREGADA; si no, sigue APROBADA
    (entrega parcial).
    """
    data, err = _parse_or_422(EntregarSolicitudSchema(), request.get_json(silent=True))
    if err: return err

    sol = (
        SolicitudMaterial.query
        .options(selectinload(SolicitudMaterial.detalles))
        .filter(SolicitudMaterial.id == sol_id)
        .first()
    )
    if not sol:
        return jsonify({'detail': 'Solicitud no encontrada'}), 404
    if sol.estatus != 'APROBADA':
        return jsonify({
            'detail': f'Solo solicitudes APROBADAS pueden entregarse (actual: {sol.estatus})'
        }), 409

    # ── 1. Planear: qué se entrega de cada línea (aún no se toca nada) ──
    materiales, herramientas = _planear_entregas(sol, data['entregas'])

    # Si hay líneas HERRAMIENTA, el solicitante DEBE tener un trabajador asociado.
    trabajador = _trabajador_para_herramientas(sol) if herramientas else None

    # El almacén solo hace falta si hay MATERIAL; con solo herramientas no se
    # exige bodega.
    almacen_id = None
    if materiales:
        almacen_id = resolver_almacen_activo(data.get('almacen_origen_id')).id

    user = request.current_user
    motivo_base = (data.get('motivo') or '').strip() or f'Entrega solicitud #{sol_id}'
    # Pausa 11: de qué estante (celda) surte cada línea, si el almacenista lo eligió.
    estante_por_detalle = {item['detalle_id']: item.get('estante_id') for item in data['entregas']}

    # ── 2. Reservar: locks de stock y de unidades de herramienta ──
    productos = _descontar_stock_de_entrega(
        _totalizar(materiales, lambda d: d.producto_id), almacen_id, sol.proyecto_id,
    )
    pools = _reservar_unidades_herramienta(_totalizar(herramientas, lambda d: d.herramienta_id))

    # ── 3. Aplicar: líneas, movimientos, caches y asignaciones ──
    _aplicar_entregas_material(
        materiales, sol, almacen_id, estante_por_detalle, motivo_base, user,
    )
    for producto in productos.values():
        _recalcular_caches(producto, almacen_id)

    asignaciones = _aplicar_entregas_herramienta(
        herramientas, sol, pools, trabajador,
        data.get('fecha_devolucion_prevista'), motivo_base, user,
    )

    completa = _entrega_completa(sol)
    if completa:
        sol.estatus = 'ENTREGADA'
        sol.fecha_cierre = datetime.datetime.now()
        sol.entregada_por_id = user.id
        # Por seguridad liberamos cualquier reserva sobrante (debería ser 0).
        restos = _reservas_de_solicitud(sol)
        if restos:
            _liberar_reservas(restos)

    _audit(
        user,
        f"Solicitud #{sol_id} {'ENTREGADA' if completa else 'entrega parcial'} "
        f"({len(materiales) + len(herramientas)} líneas: {len(materiales)} mat, "
        f"{asignaciones} herr asignadas"
        f"{f', almacén #{almacen_id}' if almacen_id else ''})",
    )
    db.session.commit()

    db.session.refresh(sol)
    _ = list(sol.detalles)
    emit_to_role(_SOL_ROLES, 'solicitud:changed', {
        'id': sol.id, 'action': 'entregada' if sol.estatus == 'ENTREGADA' else 'entrega_parcial',
    })
    # Una entrega real genera SALIDAs de material → notificar también que
    # cambió stock para que el catalogo/bajo-minimo refresquen.
    if materiales:
        emit_to_role(_INV_ROLES, 'movimiento:changed', {
            'origen': 'solicitud_entrega', 'solicitud_id': sol.id,
        })
    return jsonify(_solicitud_to_dict(sol))
