"""Validación estricta de imágenes (perfil/documentos) y conversión a WebP."""
import io
import logging

import filetype
import pillow_heif
from PIL import Image, ImageOps


# Register HEIF opener al importar
pillow_heif.register_heif_opener()


# ──────────────────────────────────────────────
# Validación estricta para fotos de perfil (solo imágenes reales)
# ──────────────────────────────────────────────
ALLOWED_IMAGE_EXTENSIONS = {'jpg', 'jpeg', 'png'}
ALLOWED_IMAGE_MIMES = {'image/jpeg', 'image/png'}
MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5 MB máximo para fotos de perfil


def allowed_image_file(file_storage):
    """Valida que el archivo sea una imagen real (JPG/PNG) de máximo 5MB.

    A diferencia de allowed_file(), esta función:
    - Solo acepta extensiones de imagen (jpg, jpeg, png)
    - Valida magic bytes con filetype
    - Rechaza archivos > 5MB
    - Rechaza cualquier otro tipo aunque tenga extensión de imagen

    Usar para: fotos de perfil de trabajadores y usuarios.
    """
    filename = file_storage.filename
    if not filename or '.' not in filename:
        return False

    ext = filename.rsplit('.', 1)[1].lower()
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        logging.warning(f"Foto rechazada: extensión '{ext}' no permitida para {filename}")
        return False

    # Verificar tamaño: leer todo el contenido para medir, luego resetear
    file_storage.seek(0, 2)  # Ir al final del archivo
    file_size = file_storage.tell()
    file_storage.seek(0)  # Resetear al inicio

    if file_size > MAX_IMAGE_SIZE:
        logging.warning(f"Foto rechazada: {filename} excede el límite de {MAX_IMAGE_SIZE // (1024*1024)}MB ({file_size} bytes)")
        return False

    # Leer primeros bytes para verificar magic number
    header = file_storage.read(2048)
    file_storage.seek(0)  # IMPORTANTE: resetear posición del stream

    kind = filetype.guess(header)
    if kind is None:
        logging.warning(f"Foto rechazada: no se pudo determinar el tipo de {filename}")
        return False

    if kind.mime not in ALLOWED_IMAGE_MIMES:
        logging.warning(
            f"Security Alert: Foto rechazada por MIME inválido para {filename}. "
            f"Extensión: {ext}, MIME detectado: {kind.mime}"
        )
        return False

    return True


# ──────────────────────────────────────────────
# Conversión a WebP
# ──────────────────────────────────────────────
# Todas las imágenes que entran al sistema (fotos de perfil, documentos imagen)
# se reencoden a WebP: mejora compresión 25–35 % vs JPEG con la misma calidad
# percibida, y unifica el formato en disco. Pillow + pillow_heif soportan
# leer JPG/PNG/HEIC; la salida siempre es WebP.

# Calidad por defecto para WebP (escala 1–100). 85 es el sweet spot percibido
# similar a JPEG-90 con archivos notablemente más chicos.
DEFAULT_WEBP_QUALITY = 85


def image_to_webp(file_storage, quality=DEFAULT_WEBP_QUALITY, max_dim=None):
    """Convierte un FileStorage de imagen a WebP en memoria.

    - Aplica EXIF orientation antes de guardar (iPhone suele rotar vía metadata
      y sin esto la foto sale acostada).
    - Si la imagen viene con paleta o modo raro, la normaliza a RGB/RGBA.
    - Si `max_dim` se especifica, redimensiona a ese máximo conservando ratio.
    - Devuelve un BytesIO posicionado al inicio.

    El stream original se rebobina al inicio al terminar para que el caller
    pueda re-leerlo si lo necesita (p.ej. para magic-bytes posteriores).
    """
    file_storage.seek(0)
    try:
        with Image.open(file_storage) as img:
            img = ImageOps.exif_transpose(img)
            if img.mode == 'P':
                img = img.convert('RGBA')
            elif img.mode not in ('RGB', 'RGBA'):
                img = img.convert('RGBA' if 'A' in img.mode else 'RGB')
            if max_dim:
                img.thumbnail((max_dim, max_dim), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format='WEBP', quality=quality, method=6)
            buf.seek(0)
    finally:
        file_storage.seek(0)
    return buf


def replace_ext_with_webp(filename):
    """Devuelve `filename` con su extensión cambiada a .webp.

    Si no tiene extensión, le añade .webp al final.
    """
    if not filename:
        return 'imagen.webp'
    base = filename.rsplit('.', 1)[0] if '.' in filename else filename
    return f"{base}.webp"
