"""Reservas de stock por solicitudes aprobadas (Pausa 2-bis del plan).

`Producto.stock_reservado` es un cache GLOBAL por producto de lo apartado por
solicitudes APROBADAS. Disponible = stock_actual − stock_reservado.

Nota de diseño (feature stock por proyecto): la reserva/aprobación es GLOBAL por
producto — permisiva a propósito, igual que el resto del sistema. El "candado"
por proyecto (no consumir stock etiquetado a OTRO proyecto) se aplica en el
consumo físico de la ENTREGA/SALIDA vía `_consumir_proyecto_luego_general`.
"""
from decimal import Decimal

from app.extensions import db
from app.models import Producto, SolicitudMaterial, SolicitudMaterialDetalle


def _pendiente_de_linea(cantidad_aprobada, cantidad_solicitada, cantidad_entregada) -> Decimal:
    """Cantidad que una línea debe tener APARTADA: aprobada − entregada.

    Si `cantidad_aprobada` es 0 (solicitud recién aprobada sin tocar líneas, o
    anterior a la Pausa 8b) cae al fallback `cantidad_solicitada`, porque esas
    se aprobaban "tal cual se solicitó". Nunca negativa.
    """
    aprobada = Decimal(str(cantidad_aprobada or 0))
    base = aprobada if aprobada > 0 else Decimal(str(cantidad_solicitada or 0))
    pendiente = base - Decimal(str(cantidad_entregada or 0))
    return pendiente if pendiente > 0 else Decimal('0')


def _reservas_de_solicitud(sol: SolicitudMaterial) -> dict[int, Decimal]:
    """Suma por producto la cantidad que esta solicitud debe tener APARTADA.

    Solo cuenta detalles MATERIAL (no HERRAMIENTAS). Una solicitud puede repetir
    el mismo producto en varias líneas — los agrupamos para hacer un solo update
    por producto.
    """
    out: dict[int, Decimal] = {}
    for d in (sol.detalles or []):
        if d.producto_id is None:
            continue
        reserva = _pendiente_de_linea(
            d.cantidad_aprobada, d.cantidad_solicitada, d.cantidad_entregada,
        )
        if reserva > 0:
            out[d.producto_id] = out.get(d.producto_id, Decimal('0')) + reserva
    return out


def _lock_producto(producto_id: int) -> Producto | None:
    """SELECT ... FOR UPDATE NOWAIT del producto, para tocar `stock_reservado`
    sin carreras. Levanta si otra transacción lo tiene tomado."""
    return (
        Producto.query
        .with_for_update(nowait=True)
        .filter(Producto.id == producto_id)
        .first()
    )


def _intentar_reservar(reservas: dict[int, Decimal]) -> list[str]:
    """Locks + valida disponibilidad GLOBAL. Aplica las reservas si TODOS los
    productos alcanzan. Si alguno no, NO aplica nada (caller debe hacer rollback)
    y devuelve lista de errores legibles para el SPA.
    Disponible = stock_actual − stock_reservado.

    La disponibilidad POR proyecto se expone aparte, solo informativa, en
    `GET /productos/<id>/disponibilidad?proyecto_id=`."""
    if not reservas:
        return []
    errores = []
    a_aplicar = []
    for prod_id, cant in reservas.items():
        prod = _lock_producto(prod_id)
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
        prod = _lock_producto(prod_id)
        if not prod:
            continue
        nuevo = Decimal(str(prod.stock_reservado or 0)) - cant
        prod.stock_reservado = nuevo if nuevo > 0 else Decimal('0')


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
