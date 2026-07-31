"""Reglas de negocio del catálogo de productos.

Puras: validan y normalizan datos de entrada, sin tocar la base ni la request.
"""
from decimal import Decimal

from .._core import CABLE_UNIDAD, _es_categoria_cable, _unidad_permite_decimales


def _validar_normalizar_cable(categoria, unidad, cable_tipo, cable_calibre):
    """Reglas de negocio de los productos de CABLE.

    - Si la categoría es de cable: `cable_tipo` y `cable_calibre` son
      obligatorios (no vacíos) y la unidad se FUERZA a 'M' (metros), sin
      importar lo que venga del cliente.
    - Si NO es cable: los campos de cable se limpian a None para no arrastrar
      datos huérfanos al cambiar de categoría.

    Devuelve (unidad_final, cable_tipo_final, cable_calibre_final, error) donde
    `error` es un mensaje str si falta algún dato obligatorio, o None si todo ok.
    """
    tipo = (cable_tipo or '').strip() or None
    calibre = (cable_calibre or '').strip() or None
    if _es_categoria_cable(categoria):
        if not tipo or not calibre:
            return None, None, None, 'Los productos de cable requieren Tipo y Tamaño (mm²/AWG)'
        return CABLE_UNIDAD, tipo, calibre, None
    return unidad, None, None, None


def _validar_stock_entero(unidad, *valores):
    """Si la unidad es por pieza (no admite decimales), exige que los valores de
    stock dados sean enteros. Devuelve un mensaje de error o None. Replica la
    regla `_unidad_permite_decimales` usada en solicitudes/compras — así el
    catálogo funciona igual que el resto del sistema."""
    if _unidad_permite_decimales(unidad):
        return None
    for v in valores:
        if v is None:
            continue
        d = Decimal(str(v))
        if d != d.to_integral_value():
            return (f"La unidad '{unidad or 'pza'}' maneja cantidades enteras: "
                    f"el stock no puede tener decimales")
    return None
