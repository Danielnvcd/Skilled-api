"""Piezas compartidas por la asignación de material a proyectos.

Conversión tolerante de números (la plantilla de Excel trae de todo) y el
consumo de bucket que aborta el lote cuando el stock cambió a media aplicación.
"""
from decimal import Decimal, InvalidOperation

from app.extensions import db
from app.models import StockAlmacenProyecto

from .._core import ErrorDeNegocio, _consumir_bucket_exacto


def _dec(v) -> Decimal:
    try:
        return Decimal(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal('0')


def _consumir_o_abortar(producto_id: int, almacen_id: int, proyecto_id: int | None,
                        cantidad: Decimal, sku: str):
    """Consume un bucket exacto y aborta el lote entero si no alcanza.

    La previsualización ya dijo que alcanzaba: si aquí no, alguien movió stock
    en medio y los números que vio el usuario ya no son ciertos, así que no se
    aplica nada y se le pide volver a previsualizar.
    """
    err = _consumir_bucket_exacto(producto_id, almacen_id, proyecto_id, cantidad)
    if err:
        raise ErrorDeNegocio(
            f'El stock cambió mientras se aplicaba ({sku}): {err}. '
            f'Vuelve a previsualizar.', 409,
        )


def _f(v) -> float:
    return float(Decimal(str(v or 0)))


def _bucket(producto_id: int, almacen_id: int, proyecto_id: int | None) -> Decimal:
    """Existencia actual de un bucket, sin bloquear (solo lectura)."""
    q = db.session.query(StockAlmacenProyecto.cantidad).filter(
        StockAlmacenProyecto.producto_id == producto_id,
        StockAlmacenProyecto.almacen_id == almacen_id,
    )
    q = q.filter(StockAlmacenProyecto.proyecto_id.is_(None)) if proyecto_id is None \
        else q.filter(StockAlmacenProyecto.proyecto_id == proyecto_id)
    return Decimal(str(q.scalar() or 0))
