"""Escaneo de archivos subidos con ClamAV (demonio `clamd`).

Por qué existe
--------------
Todo lo que es imagen se re-encodea a WebP antes de guardarse, y ese re-render
destruye cualquier payload embebido. Los PDF son la excepción: convertirlos
destruiría el documento, así que se almacenan intactos. Este módulo cubre ese
hueco — es la única capa que mira el CONTENIDO y no solo la forma del archivo.

Se habla con el demonio `clamd`, no con el binario `clamscan`: `clamscan` carga
~1 GB de firmas en CADA invocación (segundos por archivo), mientras que el
demonio las tiene ya en memoria y responde en milisegundos.

Config (ver .env.example y docs/ANTIVIRUS_CLAMAV.md):
  CLAMAV_SOCKET       ruta del socket unix (p.ej. /var/run/clamav/clamd.ctl)
  CLAMAV_HOST/PORT    alternativa por TCP si clamd corre en otra máquina
  CLAMAV_FAIL_CLOSED  'true' (por defecto) rechaza la subida si el antivirus
                      no responde; 'false' la deja pasar y solo lo registra
  CLAMAV_TIMEOUT      segundos de espera (por defecto 30)

Si no hay ni socket ni host, el módulo queda APAGADO y las subidas se comportan
exactamente como antes. Igual que el gate de `archivos.py`: mientras esté
apagado nadie llama al resto.
"""
import io
import os
import logging

logger = logging.getLogger(__name__)


class AntivirusNoDisponible(RuntimeError):
    """El demonio no respondió. NO significa que el archivo esté limpio."""


def _socket() -> str:
    return os.environ.get('CLAMAV_SOCKET', '').strip()


def _host() -> str:
    return os.environ.get('CLAMAV_HOST', '').strip()


def _timeout() -> int:
    try:
        return int(os.environ.get('CLAMAV_TIMEOUT', '30'))
    except (TypeError, ValueError):
        return 30


def habilitado() -> bool:
    """True si hay un clamd configurado (socket unix o TCP)."""
    return bool(_socket() or _host())


def fail_closed() -> bool:
    """¿Rechazar la subida si el antivirus no responde?

    Por defecto SÍ: si te tomaste el trabajo de instalar el antivirus, un
    escaneo que no ocurre no debería pasar por bueno en silencio. Ponerlo en
    'false' prioriza que la gente pueda seguir subiendo documentos aunque clamd
    esté caído — decisión legítima, pero que hay que tomar a conciencia."""
    return os.environ.get('CLAMAV_FAIL_CLOSED', 'true').strip().lower() != 'false'


def _cliente():
    """Cliente clamd (unix o TCP). Import perezoso: la librería solo hace falta
    donde el antivirus está activo, y en Windows/local no se instala."""
    import clamd
    if _socket():
        return clamd.ClamdUnixSocket(path=_socket(), timeout=_timeout())
    try:
        puerto = int(os.environ.get('CLAMAV_PORT', '3310'))
    except (TypeError, ValueError):
        puerto = 3310
    return clamd.ClamdNetworkSocket(host=_host(), port=puerto, timeout=_timeout())


def escanear(datos: bytes) -> str | None:
    """Escanea `datos`. Devuelve None si está limpio, o el nombre de la amenaza.

    Lanza `AntivirusNoDisponible` si el demonio no contesta — el caller decide
    qué hacer según `fail_closed()`. Nunca devuelve None por un fallo de
    conexión: confundir "no pude revisar" con "está limpio" es justo el error
    que vuelve inútil a un antivirus.
    """
    if not habilitado():
        return None
    try:
        respuesta = _cliente().instream(io.BytesIO(datos))
    except Exception as e:
        raise AntivirusNoDisponible(str(e)) from e

    # Formato de python-clamd: {'stream': ('OK', None)}
    #                          {'stream': ('FOUND', 'Eicar-Test-Signature')}
    estado, detalle = (respuesta or {}).get('stream', ('OK', None))
    if estado == 'FOUND':
        return detalle or 'amenaza desconocida'
    if estado == 'ERROR':
        raise AntivirusNoDisponible(detalle or 'clamd devolvió ERROR')
    return None


def ping() -> str | None:
    """None si clamd responde; si no, el motivo. Para el panel de sistemas."""
    if not habilitado():
        return None
    try:
        _cliente().ping()
        return None
    except Exception as e:
        return f'{type(e).__name__}: {e}'[:200]


def version() -> str | None:
    """Versión del motor y firmas, o None si no está disponible.

    Es SOLO cosmético (se muestra en el panel); la señal de salud es `ping()`.
    Muchas instalaciones traen el comando VERSION deshabilitado en clamd.conf,
    en cuyo caso clamd contesta un error en vez de la versión: si la respuesta
    no parece una versión de ClamAV, se devuelve None y el panel muestra su
    texto por defecto en lugar de un 'UNKNOWN COMMAND' desconcertante."""
    if not habilitado():
        return None
    try:
        texto = str(_cliente().version())[:200].strip()
    except Exception:
        return None
    return texto if 'clamav' in texto.lower() else None
