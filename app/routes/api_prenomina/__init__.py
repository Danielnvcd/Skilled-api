"""Paquete `api_prenomina` — API JSON para Prenómina (SPA React).

Espejo del blueprint clásico `prenomina.py` pero protegido por JWT. Reusa:
- `calcular_preview_prenomina` (lógica de cálculo) del módulo clásico
- `recalcular_totales_prenomina` de utils
- Template Jinja `recibo_pdf.html` para los recibos PDF y correos
  (la apariencia del recibo queda idéntica a la vista clásica)

Originalmente un archivo de ~1175 líneas; ahora dividido por dominios:

  _core.py    bp + helpers (_parse_fecha, _reportes_de_semana, _prenomina_dict,
              _build_recibos_data, _render_recibos_pdf)
  semanas.py  índice, preview, guardar, editor, cerrar (flujo principal)
  ajustes.py  descuentos / depósitos / viáticos / festivos (mutaciones manuales)
  envio.py    PDFs, correo (individual + bulk + todos), Excel

Importar este paquete registra TODOS los endpoints en `bp` (efecto colateral
de los `@bp.route(...)` en cada submódulo). El blueprint se registra desde
`app/__init__.py` igual que antes — el contrato externo no cambió.
"""
from ._core import bp

# Importar los submódulos provoca que sus @bp.route(...) registren las rutas
# en el blueprint compartido. Sin estos imports, el paquete no expondría
# ningún endpoint y la app arrancaría con un blueprint vacío.
from . import semanas  # noqa: F401  /semanas, /semanas/<fecha>/preview|guardar|editar|cerrar
from . import ajustes  # noqa: F401  /descuentos, /depositos, /viaticos, /festivos
from . import envio    # noqa: F401  /semanas/<fecha>/imprimir|correo|excel

__all__ = ['bp']
