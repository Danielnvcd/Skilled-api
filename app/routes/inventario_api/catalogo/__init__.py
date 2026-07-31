"""Catálogo: proyectos, categorías e importación/exportación masiva.

  proyectos.py   selector de proyectos activos
  categorias.py  categorías del catálogo + metadatos visuales (CategoriaConfig)
  plantilla.py   columnas y armado del Excel (compartido por importar y exportar)
  importar.py    endpoints de plantilla, exportación e importación masiva

Importar el paquete registra las rutas de todos los submódulos en `bp`.
"""
from . import proyectos   # noqa: F401  /proyectos/
from . import categorias  # noqa: F401  /categorias/*, /categorias-config/*
from . import importar    # noqa: F401  /productos/{plantilla-importar,exportar,importar}
