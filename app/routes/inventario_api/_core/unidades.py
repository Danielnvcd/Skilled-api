"""Reglas de dominio sobre unidades de medida y categorías.

Reglas puras (sin DB, sin Flask) que comparten catálogo, solicitudes, compras e
importación. Vivían dentro de `solicitudes.py`, lo que obligaba a `productos.py`
y `compras.py` a importar de un módulo de rutas hermano (`from .solicitudes
import _unidad_permite_decimales`). Al vivir aquí, cada módulo depende del
núcleo y no de otro módulo de rutas.
"""
import unicodedata
from decimal import Decimal

# Unidades de medida "continuas" que admiten decimales (kg, metros, litros…).
# El resto (pieza, unidad, caja, par, rollo…) se piden en enteros. Las
# herramientas SIEMPRE son enteras, sin importar su unidad.
_UNIDADES_DECIMALES = {
    'kg', 'kgs', 'kilo', 'kilos', 'kilogramo', 'kilogramos',
    'g', 'gr', 'grs', 'gramo', 'gramos', 'mg',
    'ton', 'tonelada', 'toneladas',
    'l', 'lt', 'lts', 'litro', 'litros', 'ml', 'mililitro', 'mililitros',
    'gal', 'galon', 'galones',
    'm', 'mt', 'mts', 'metro', 'metros',
    'cm', 'centimetro', 'centimetros', 'mm', 'milimetro', 'milimetros',
    'km', 'kilometro', 'kilometros', 'm2', 'm3',
    'pulgada', 'pulgadas', 'in', 'ft', 'pie', 'pies', 'yarda', 'yardas',
    'oz', 'onza', 'onzas', 'lb', 'lbs', 'libra', 'libras',
}

# Unidad forzada para productos de cable (se pide/consume por metros).
CABLE_UNIDAD = 'M'


def sin_acentos(texto) -> str:
    """Minúsculas sin acentos ni espacios extremos. Base de las comparaciones
    de unidad/categoría, que llegan escritas por humanos ('Metros', 'metro')."""
    norm = unicodedata.normalize('NFD', str(texto or '').strip().lower())
    return ''.join(c for c in norm if unicodedata.category(c) != 'Mn')


def _unidad_permite_decimales(unidad) -> bool:
    """¿La unidad admite cantidades fraccionarias? (kg, m, l… sí; pza, caja… no)."""
    u = sin_acentos(unidad)
    u = u.replace('²', '2').replace('³', '3').replace('.', '').replace(' ', '')
    return u in _UNIDADES_DECIMALES


def _es_categoria_cable(categoria) -> bool:
    """¿La categoría del producto es de cable? Detección por texto (contiene
    'cable', case-insensitive, sin acentos): 'Cable', 'Cables', 'Cable THHN'…
    Debe coincidir con esCategoriaCable() del frontend (utils/cable.js)."""
    return 'cable' in sin_acentos(categoria)


def _es_entero(d: Decimal) -> bool:
    return d == d.to_integral_value()
