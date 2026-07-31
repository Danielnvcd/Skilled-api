"""Plan de materiales por proyecto (Inventario → Proyectos).

  _core.py     alcance por dueño (coordinador) y consumo acumulado
  consulta.py  listado, detalle, historial, pedidos y existencias del proyecto
  plan.py      upsert y borrado de líneas del plan

Importar el paquete registra las rutas de todos los submódulos en `bp`.
"""
from . import consulta  # noqa: F401  /proyectos-materiales/* (GET)
from . import plan      # noqa: F401  /proyectos-materiales/<id>/plan
