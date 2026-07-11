"""Descarga segura de imágenes externas (anti-SSRF + tope de tamaño + magic-bytes).

Se usa por el pipeline de imágenes de productos → R2. La defensa NO es confiar
en el contenido descargado: quien lo llama re-codifica el resultado a WebP con
Pillow (image_to_webp), lo que destruye cualquier payload embebido. Aquí solo
nos aseguramos de:
  - Hablar solo HTTPS.
  - No dejar que "bajar una URL" toque la red interna (SSRF): el host no puede
    resolver a IPs privadas/loopback/link-local/reservadas.
  - Re-validar el host en cada redirect.
  - Cortar por tamaño máximo mientras se descarga (no confiar en Content-Length).
  - Verificar magic-bytes: el contenido tiene que ser una imagen real.
"""
import os
import socket
import ipaddress
import logging
from urllib.parse import urlparse

import httpx
import filetype

logger = logging.getLogger(__name__)

DEFAULT_MAX_BYTES = 10 * 1024 * 1024   # 10 MB
DEFAULT_TIMEOUT = 10                   # segundos
MAX_REDIRECTS = 3

# Formatos que Pillow (+ pillow-heif) sabe abrir para re-codificar a WebP.
_ALLOWED_IMAGE_MIMES = {
    'image/jpeg', 'image/png', 'image/webp', 'image/gif',
    'image/heic', 'image/heif', 'image/bmp', 'image/tiff',
}


class ImagenDescargaError(Exception):
    """Falla controlada de descarga/validación de una imagen."""


def _host_is_safe(host: str) -> bool:
    """Resuelve `host` y devuelve False si CUALQUIER IP resuelta es no pública."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    if not infos:
        return False
    for info in infos:
        ip_str = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            return False
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return False
    return True


def _validar_url(url: str):
    parsed = urlparse(url)
    if parsed.scheme != 'https':
        raise ImagenDescargaError('solo se permiten URLs HTTPS')
    if not parsed.hostname:
        raise ImagenDescargaError('URL sin host')
    if not _host_is_safe(parsed.hostname):
        raise ImagenDescargaError('el host resuelve a una IP no permitida (posible SSRF)')


def descargar_imagen_segura(url: str, max_bytes: int = None, timeout: int = None):
    """Descarga la imagen de `url` con defensas anti-SSRF y tope de tamaño.

    Devuelve (data: bytes, mime: str). Lanza ImagenDescargaError si algo no
    cumple (no-HTTPS, IP interna, HTTP != 200, demasiado grande, no-imagen…).
    """
    max_bytes = max_bytes or int(os.environ.get('IMG_MAX_DOWNLOAD_BYTES', DEFAULT_MAX_BYTES))
    timeout = timeout or int(os.environ.get('IMG_FETCH_TIMEOUT', DEFAULT_TIMEOUT))

    current = url
    data = None
    # +1 porque el primer intento no es un redirect.
    for _ in range(MAX_REDIRECTS + 1):
        _validar_url(current)
        with httpx.Client(follow_redirects=False, timeout=timeout) as client:
            with client.stream('GET', current, headers={'User-Agent': 'SkilledBot/1.0'}) as resp:
                if resp.is_redirect:
                    loc = resp.headers.get('location')
                    if not loc:
                        raise ImagenDescargaError('redirect sin cabecera Location')
                    # Resuelve el destino contra la URL actual y re-valida en el
                    # próximo giro del loop (anti-SSRF vía redirect a IP interna).
                    current = str(httpx.URL(current).join(loc))
                    continue
                if resp.status_code != 200:
                    raise ImagenDescargaError(f'respuesta HTTP {resp.status_code}')
                clen = resp.headers.get('content-length')
                if clen and clen.isdigit() and int(clen) > max_bytes:
                    raise ImagenDescargaError('imagen demasiado grande (Content-Length)')
                chunks = []
                total = 0
                for chunk in resp.iter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise ImagenDescargaError('imagen demasiado grande')
                    chunks.append(chunk)
                data = b''.join(chunks)
                break
    else:
        raise ImagenDescargaError('demasiados redirects')

    if not data:
        raise ImagenDescargaError('respuesta vacía')

    kind = filetype.guess(data)
    if kind is None or kind.mime not in _ALLOWED_IMAGE_MIMES:
        detected = kind.mime if kind else 'desconocido'
        raise ImagenDescargaError(f'el contenido no es una imagen válida ({detected})')
    return data, kind.mime
