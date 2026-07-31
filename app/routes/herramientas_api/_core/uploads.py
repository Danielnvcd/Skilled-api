"""Subida de fotos y evidencias de una unidad de herramienta.

La validación mira los *magic bytes*, no lo que declara el cliente: el MIME y la
extensión que manda el navegador son falsificables.
"""
import os

import filetype
from flask import current_app

UPLOAD_MAX_BYTES = 5 * 1024 * 1024  # 5 MB
ALLOWED_IMAGE_MIMES = {'image/png', 'image/jpeg', 'image/jpg', 'image/webp'}
ALLOWED_IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.webp'}


def _validar_imagen_archivo(file_storage):
    """Valida el FileStorage de un upload de imagen. Devuelve (mime, ext, size) o (None, None, error)."""
    if not file_storage or not file_storage.filename:
        return None, None, 'No se envió archivo'
    ext = os.path.splitext(file_storage.filename)[1].lower()
    if ext not in ALLOWED_IMAGE_EXTS:
        return None, None, f'Extensión no permitida (usar {", ".join(ALLOWED_IMAGE_EXTS)})'
    # Leer en memoria para medir tamaño y mime
    file_storage.stream.seek(0, os.SEEK_END)
    size = file_storage.stream.tell()
    file_storage.stream.seek(0)
    if size > UPLOAD_MAX_BYTES:
        return None, None, f'Archivo excede {UPLOAD_MAX_BYTES // (1024 * 1024)} MB'
    # El MIME declarado por el cliente (file_storage.mimetype) es falsificable.
    # Verificamos los magic bytes con `filetype` igual que allowed_image_file()
    # para fotos de perfil: así un atacante no puede subir un no-imagen con
    # extensión y Content-Type de imagen. No es RCE (el archivo nunca se ejecuta)
    # pero cierra el hueco de subir contenido arbitrario al disco del servidor.
    header = file_storage.stream.read(2048)
    file_storage.stream.seek(0)
    kind = filetype.guess(header)
    if kind is None or kind.mime not in ALLOWED_IMAGE_MIMES:
        detectado = kind.mime if kind else 'desconocido'
        return None, None, f'El archivo no es una imagen válida (detectado: {detectado})'
    return kind.mime, ext, size


def _upload_dir(unidad_id: int) -> str:
    """Ruta absoluta donde se guardan los uploads de una unidad."""
    base = current_app.config.get('UPLOAD_FOLDER', 'uploads')
    path = os.path.join(base, 'herramientas', str(unidad_id))
    os.makedirs(path, exist_ok=True)
    return path
