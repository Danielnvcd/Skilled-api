"""Paquete `app.utils` — re-exports planos para preservar la API original.

Antes era un solo archivo `app/utils.py` de ~540 líneas que mezclaba seguridad,
archivos, imágenes, cálculo de horas y helpers financieros. Ahora está
particionado por dominio:

  security.py       is_strong_password, _safe_log_value
  validaciones.py   TRABAJADOR_LENGTHS, validate_lengths
  audit.py          log_action
  files.py          allowed_file, STRICT_MIMETYPES, safe_excel_value
  images.py         allowed_image_file, image_to_webp, replace_ext_with_webp
  horas.py          turnos_se_traslapan, calcular_horas_productivas
  payroll.py        to_dec, recalcular_totales_prenomina

Todos los símbolos que el resto del código importaba como
`from app.utils import X` siguen funcionando — este `__init__.py` los re-exporta.
"""
from app.utils.audit import log_action
from app.utils.files import (
    STRICT_MIMETYPES,
    allowed_file,
    safe_excel_value,
)
from app.utils.horas import (
    _time_to_minutes,
    calcular_horas_productivas,
    turnos_se_traslapan,
)
from app.utils.images import (
    ALLOWED_IMAGE_EXTENSIONS,
    ALLOWED_IMAGE_MIMES,
    DEFAULT_WEBP_QUALITY,
    MAX_IMAGE_SIZE,
    allowed_image_file,
    image_to_webp,
    replace_ext_with_webp,
)
from app.utils.payroll import (
    recalcular_totales_prenomina,
    to_dec,
)
from app.utils.security import (
    _safe_log_value,
    is_strong_password,
)
from app.utils.validaciones import (
    TRABAJADOR_LENGTHS,
    validate_lengths,
)

__all__ = [
    # security
    'is_strong_password', '_safe_log_value',
    # validaciones
    'TRABAJADOR_LENGTHS', 'validate_lengths',
    # audit
    'log_action',
    # files
    'STRICT_MIMETYPES', 'allowed_file', 'safe_excel_value',
    # images
    'ALLOWED_IMAGE_EXTENSIONS', 'ALLOWED_IMAGE_MIMES', 'MAX_IMAGE_SIZE',
    'DEFAULT_WEBP_QUALITY',
    'allowed_image_file', 'image_to_webp', 'replace_ext_with_webp',
    # horas
    'turnos_se_traslapan', 'calcular_horas_productivas', '_time_to_minutes',
    # payroll
    'to_dec', 'recalcular_totales_prenomina',
]
