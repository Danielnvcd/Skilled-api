"""Piezas que comparten la entrega directa y la entrega por solicitud."""
from decimal import Decimal

from app.models import Estante, Producto, ProductoEstante

from .._core import ErrorDeNegocio


def _lock_producto_o_404(producto_id: int) -> Producto:
    """SELECT ... FOR UPDATE NOWAIT del producto antes de mover su stock. El lock
    se toma en orden de id ascendente en los llamadores para evitar deadlocks."""
    producto = (
        Producto.query
        .with_for_update(nowait=True)
        .filter(Producto.id == producto_id)
        .first()
    )
    if not producto:
        raise ErrorDeNegocio(f'Producto #{producto_id} no encontrado', 404)
    return producto


def _descontar_celda(producto_id: int, estante_id: int | None,
                     almacen_id: int, cantidad: Decimal):
    """Descuenta del sub-libro de celdas (Pausa 11) la cantidad surtida desde un
    estante concreto.

    Best-effort a propósito: el stock autoritativo ya bajó de los buckets; esto
    solo mantiene al día dónde está físicamente el material. Si no se indicó
    estante, si el estante no pertenece al almacén de origen o si no hay celda
    registrada, no hace nada. Clamp a 0 para no dejar celdas negativas.
    """
    if not estante_id:
        return
    estante = Estante.query.filter(Estante.id == estante_id).first()
    if not estante or estante.almacen_id != almacen_id:
        return
    celda = ProductoEstante.query.filter_by(
        producto_id=producto_id, estante_id=estante_id,
    ).first()
    if celda is None:
        return
    restante = Decimal(str(celda.cantidad or 0)) - cantidad
    celda.cantidad = restante if restante > 0 else Decimal('0')
