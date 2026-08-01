"""Catálogo de productos: consulta, escritura, existencias y kardex.

  reglas.py     reglas puras de unidad/cable que aplican al alta y edición
  consultas.py  búsqueda, filtros, paginado, unidades y bajo mínimo
  escritura.py  alta, edición y baja (soft delete) de productos
  stock.py      ajuste de buckets, desglose por bodega y disponibilidad
  kardex.py     histórico de movimientos con saldo corrido
  minimos.py    stock mínimo en masa: sugerencia por consumo y aplicación

Importar el paquete registra las rutas de todos los submódulos en `bp`.
"""
from . import consultas   # noqa: F401  /productos/ (GET), paginado, bajo-minimo
from . import escritura   # noqa: F401  /productos/ (POST, PUT, DELETE)
from . import stock       # noqa: F401  /productos/<id>/{stocks,disponibilidad,ajustar-buckets}
from . import kardex      # noqa: F401  /productos/<id>/kardex
from . import minimos     # noqa: F401  /productos/minimos[/sugerencia]
