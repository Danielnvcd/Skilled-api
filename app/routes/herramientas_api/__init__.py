"""Paquete `herramientas_api` — API JSON del módulo de Herramientas.

Hermano de `inventario_api` (materiales). Originalmente un solo archivo
de ~1600 líneas; dividido por dominios:

  _core.py         bp + schemas + serializers + helpers (QR, fotos, eventos)
  catalogo.py      catálogo de tipos + categorías + stats dashboard
  unidades.py      CRUD de unidades + eventos + QR
  asignaciones.py  asignación a trabajadores + devolución
  mantenimientos.py mantenimientos (apertura + cierre)
  incidencias.py   incidencias reportadas + atención
  bajas.py         solicitudes de baja + baja directa
  multimedia.py    fotos/evidencias adjuntas a unidades

Importar este paquete registra TODOS los endpoints en `bp` (efecto colateral
de los `@bp.route(...)` en cada submódulo). El blueprint se registra desde
`app/__init__.py` igual que antes — el contrato externo no cambió.

Nadie fuera de este paquete importa símbolos privados, así que `__all__`
solo expone `bp`.
"""
from ._core import bp

# Importar los submódulos provoca que sus @bp.route(...) registren las rutas
# en el blueprint compartido. Sin estos imports, el paquete no expondría
# ningún endpoint y la app arrancaría con un blueprint vacío.
from . import catalogo        # noqa: F401  /herramientas/*, /herramientas-categorias/*
from . import unidades        # noqa: F401  /herramientas-unidades/* (CRUD, eventos, QR)
from . import asignaciones    # noqa: F401  /asignaciones-herramienta/*
from . import mantenimientos  # noqa: F401  /mantenimientos-herramienta/*
from . import incidencias     # noqa: F401  /incidencias-herramienta/*
from . import bajas           # noqa: F401  /solicitudes-baja-herramienta/* + /dar-baja
from . import multimedia      # noqa: F401  /herramientas-unidades/<uid>/fotos, /media/*

__all__ = ['bp']
