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
import io
import os
import socket
import ipaddress
import logging
from urllib.parse import urlparse

import httpx
import filetype
import pillow_heif
from PIL import Image

# Registrar el opener HEIF para poder leer .size de HEIC/HEIF (idempotente;
# images.py también lo registra — llamarlo dos veces no hace daño).
pillow_heif.register_heif_opener()

logger = logging.getLogger(__name__)

DEFAULT_MAX_BYTES = 10 * 1024 * 1024   # 10 MB
DEFAULT_TIMEOUT = 10                   # segundos
MAX_REDIRECTS = 3
# Anti-bomba de descompresión: tope de píxeles (ancho×alto). 50 MP ≈ 8660×5773,
# holgado para fotos de catálogo y muy por debajo de lo que revienta la RAM.
DEFAULT_MAX_PIXELS = 50_000_000

# Formatos que Pillow (+ pillow-heif) sabe abrir para re-codificar a WebP.
_ALLOWED_IMAGE_MIMES = {
    'image/jpeg', 'image/png', 'image/webp', 'image/gif',
    'image/heic', 'image/heif', 'image/bmp', 'image/tiff',
}


class ImagenDescargaError(Exception):
    """Falla controlada de descarga/validación de una imagen."""


def _ip_es_publica(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    return not (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified)


def _resolver_host_publico(host: str) -> str:
    """Resuelve `host` UNA vez; falla si cualquier IR resuelta no es pública.

    Devuelve la IP a la que se conectará. Que devuelva la IP —en vez de un
    booleano— es justo lo que cierra el agujero: ver `_destino_fijado`.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise ImagenDescargaError('no se pudo resolver el host')
    if not infos:
        raise ImagenDescargaError('el host no resolvió a ninguna dirección')

    ips = [info[4][0] for info in infos]
    # TODAS deben ser públicas: una respuesta DNS mixta (una pública y una
    # interna) no debe colar por quedarnos con la primera que nos convenga.
    for ip_str in ips:
        if not _ip_es_publica(ip_str):
            raise ImagenDescargaError('el host resuelve a una IP no permitida (posible SSRF)')
    return ips[0]


def _destino_fijado(url: str):
    """Valida la URL y devuelve a dónde conectarse con la IP ya fijada.

    ── El agujero que cierra ───────────────────────────────────────────────
    Antes se comprobaba el host con `getaddrinfo` y después se le pasaba la URL
    a httpx, que resolvía el nombre POR SU CUENTA. Entre las dos resoluciones
    hay una ventana, y el DNS del atacante la controla: basta con servir un
    registro con TTL 0 que responda una IP pública en la comprobación y
    127.0.0.1 (o 169.254.169.254, o `redis`) en la conexión. Es el rebinding de
    DNS de manual, y anula la defensa entera por muy correcta que sea la lista
    de rangos.

    El arreglo es conectarse a la IP que YA se validó, en lugar del nombre:
      · la petición va a `https://<ip>:<puerto><ruta>`,
      · `Host:` conserva el nombre para que el servidor sirva el vhost correcto,
      · `sni_hostname` conserva el nombre para el handshake TLS, así que el
        certificado se sigue validando contra el dominio y no contra la IP
        (verificado: sin esa extensión, la conexión falla por certificado).

    Devuelve (url_a_la_ip, cabecera_host, hostname_para_sni).
    """
    parsed = urlparse(url)
    if parsed.scheme != 'https':
        raise ImagenDescargaError('solo se permiten URLs HTTPS')
    host = parsed.hostname
    if not host:
        raise ImagenDescargaError('URL sin host')

    try:
        puerto = parsed.port or 443
    except ValueError:
        raise ImagenDescargaError('puerto inválido en la URL')

    ip = _resolver_host_publico(host)
    literal = f'[{ip}]' if ':' in ip else ip  # IPv6 va entre corchetes

    ruta = parsed.path or '/'
    if parsed.query:
        ruta = f'{ruta}?{parsed.query}'
    destino = f'https://{literal}:{puerto}{ruta}'
    # El puerto solo va en `Host:` cuando no es el estándar, como haría el browser.
    cabecera_host = host if puerto == 443 else f'{host}:{puerto}'
    return destino, cabecera_host, host


def _validar_dimensiones(data: bytes, max_pixels: int):
    """Anti-bomba de descompresión: lee SOLO el encabezado (Image.open no carga
    los píxeles) para conocer las dimensiones y rechaza si ancho×alto supera el
    tope, ANTES de que image_to_webp cargue la imagen completa en memoria.
    Devuelve (ancho, alto) si es aceptable."""
    try:
        with Image.open(io.BytesIO(data)) as probe:
            w, h = probe.size
    except Exception:
        raise ImagenDescargaError('no se pudieron leer las dimensiones de la imagen')
    if w <= 0 or h <= 0:
        raise ImagenDescargaError('dimensiones de imagen inválidas')
    if w * h > max_pixels:
        raise ImagenDescargaError(
            f'imagen demasiado grande ({w}x{h} px, máx {max_pixels} px)'
        )
    return w, h


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
        # `current` se mantiene siempre como la URL LÓGICA (con el nombre de
        # dominio), no la fijada a IP: es contra ella que hay que resolver un
        # redirect relativo. Cada vuelta del bucle re-resuelve y re-valida, así
        # que un redirect a IP interna se corta igual que la URL inicial.
        destino, cabecera_host, sni = _destino_fijado(current)
        with httpx.Client(follow_redirects=False, timeout=timeout) as client:
            with client.stream(
                'GET', destino,
                headers={'User-Agent': 'SkilledBot/1.0', 'Host': cabecera_host},
                extensions={'sni_hostname': sni},
            ) as resp:
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

    # Anti-bomba de descompresión: rechazar por dimensiones antes de procesar.
    max_pixels = int(os.environ.get('IMG_MAX_PIXELS', DEFAULT_MAX_PIXELS))
    _validar_dimensiones(data, max_pixels)
    return data, kind.mime
