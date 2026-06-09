"""Validación de archivos subidos (extensión + magic bytes) y anti-formula injection en Excel."""
import logging

import filetype
from flask import current_app


# Strict MIME Type Whitelist
STRICT_MIMETYPES = {
    'pdf': ['application/pdf'],
    'doc': ['application/msword', 'application/x-msi', 'application/vnd.ms-office'],
    'docx': ['application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'application/zip'],
    'ppt': ['application/vnd.ms-powerpoint', 'application/vnd.ms-office'],
    'pptx': ['application/vnd.openxmlformats-officedocument.presentationml.presentation', 'application/zip'],
    'xls': ['application/vnd.ms-excel', 'application/vnd.ms-office'],
    'xlsx': ['application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'application/zip'],
    'xlsm': ['application/vnd.ms-excel.sheet.macroEnabled.12', 'application/zip'],
    'jpg': ['image/jpeg'],
    'jpeg': ['image/jpeg'],
    'png': ['image/png'],
    'heic': ['image/heic', 'image/heif'],
    'mp4': ['video/mp4'],
    'mp3': ['audio/mpeg'],
    'wav': ['audio/wav', 'audio/x-wav'],
}


def allowed_file(file_storage, allowed_exts=None):
    """Valida extensión + magic bytes de un archivo subido.

    `allowed_exts` permite restringir el set por contexto (p.ej. para
    documentos de trabajador solo aceptamos PDF/JPG/PNG/HEIC, no audio/video).
    Si es None usa `current_app.config['ALLOWED_EXTENSIONS']`.
    """
    filename = file_storage.filename
    if '.' not in filename:
        return False

    ext = filename.rsplit('.', 1)[1].lower()
    allowed = allowed_exts if allowed_exts is not None else current_app.config['ALLOWED_EXTENSIONS']
    if ext not in allowed:
        return False

    # Read first 2048 bytes for magic number check
    header = file_storage.read(2048)
    file_storage.seek(0)  # IMPORTANT: Reset stream position

    kind = filetype.guess(header)

    if kind is None:
        logging.warning(f"File validation failed: could not determine type for {filename}")
        return False

    detected_ext = kind.extension
    detected_mime = kind.mime

    if detected_ext == 'jpeg':
        detected_ext = 'jpg'

    if ext in STRICT_MIMETYPES:
        if detected_mime not in STRICT_MIMETYPES[ext]:
            logging.warning(f"Security Alert: MIME mismatch for {filename}. Declared: {ext}, Detected MIME: {detected_mime}")
            return False
    else:
        if detected_ext != ext:
            if not (ext in ['docx', 'xlsx', 'pptx'] and detected_ext == 'zip'):
                logging.warning(f"Security Alert: Extension mismatch for {filename}. Declared: {ext}, Detected: {detected_ext}")
                return False

    return True


# ──────────────────────────────────────────────
# Anti-inyección de fórmulas en exports Excel
# ──────────────────────────────────────────────
# Excel/LibreOffice ejecutan como fórmula cualquier celda que empiece con uno de estos
# caracteres. Un usuario que controla campos como "nombre", "motivo" o "notas" podría
# inyectar p. ej. `=cmd|'/C calc'!A1` y al abrir el .xlsx el admin dispararía el cálculo.
# Mitigación recomendada por OWASP: anteponer una comilla simple ('), que Excel trata
# como prefijo de texto literal (no se muestra en la celda pero desactiva la fórmula).
_EXCEL_DANGEROUS_PREFIXES = ('=', '+', '-', '@', '\t', '\r')


def safe_excel_value(value):
    """Sanitiza un valor antes de escribirlo en una celda Excel.

    - Strings que empiezan con =, +, -, @, TAB o CR se prefijan con comilla simple
      para evitar que Excel los interprete como fórmulas.
    - Otros tipos (int, float, datetime, None, etc.) se devuelven sin cambios.
    """
    if isinstance(value, str) and value and value[0] in _EXCEL_DANGEROUS_PREFIXES:
        return "'" + value
    return value
