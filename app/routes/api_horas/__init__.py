"""Paquete `api_horas` — API JSON del módulo de Horas (consumida por el SPA React).

Espejo del blueprint clásico `horas.py` pero protegido por JWT. Reusa
`calcular_horas_productivas` y `turnos_se_traslapan` de `app.utils`.

Originalmente un archivo de ~1080 líneas; ahora dividido por dominios:

  _core.py     bp + constantes + helpers de acceso + serializers
  reportes.py  /reportes (listar, abrir, detalle, cerrar) + /proyectos-disponibles
  registros.py /reportes/<id>/registros (POST + bulk) + /registros/<id> (PUT/DELETE)
  movil.py     /movil/resumen + /qr-check (kiosko coordinador)
  rfid_qr.py   /qr/* y /rfid/* (gestión de credenciales + kiosko RFID)

Importar este paquete registra TODOS los endpoints en `bp` (efecto colateral
de los `@bp.route(...)` en cada submódulo). El blueprint se registra desde
`app/__init__.py` igual que antes — el contrato externo no cambió.

Re-exports: `_puede_acceder_proyecto` lo usa `app.realtime` para filtrar
emits a coordinadores con scope sobre el proyecto.
"""
from ._core import bp, _puede_acceder_proyecto

# Importar los submódulos provoca que sus @bp.route(...) registren las rutas
# en el blueprint compartido. Sin estos imports, el paquete no expondría
# ningún endpoint y la app arrancaría con un blueprint vacío.
from . import reportes   # noqa: F401  /reportes, /proyectos-disponibles
from . import registros  # noqa: F401  /reportes/<id>/registros*, /registros/<id>
from . import movil      # noqa: F401  /movil/resumen, /qr-check
from . import rfid_qr    # noqa: F401  /qr/*, /rfid/*

__all__ = ['bp', '_puede_acceder_proyecto']
