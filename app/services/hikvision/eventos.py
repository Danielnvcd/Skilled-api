"""Eventos de acceso del lector: consulta al equipo y lectura del stream.

Es la vista OPERATIVA de los eventos (la pestaña «Actividad»). La lectura de
checadas para nómina vive aparte, en `asistencia.py`.

Cómo llegan los eventos al ERP (ver `escucha.py`):

    GET /ISAPI/Event/notification/alertStream
        Conexión que el lector deja ABIERTA y por la que empuja cada evento en
        cuanto ocurre (multipart/mixed, una parte JSON por evento). En este
        firmware `alertStream/capabilities` responde `notSupport`, pero el
        stream funciona: se probó contra el equipo real. Trae `serialNo` y
        `frontSerialNo`, pero NO la ruta de la foto.

    POST /ISAPI/AccessControl/AcsEvent?format=json  con `beginSerialNo`
        «Todo lo posterior al último que guardé». Trae la ruta de la foto
        (`pictureURL`) y no necesita rango de fechas. El stream solo se usa
        como AVISO de que hay algo nuevo; los datos se toman de aquí. Así hay
        un único camino de ingesta, y un corte de conexión no pierde nada: al
        reconectar se pide desde el último serial guardado.

Verificado en un DS-K1T342MFWX-E1 (FW V4.48.40): la búsqueda por serial NO
devuelve los eventos ordenados; quien la use debe ordenarlos.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from .client import MAX_RESULTS
from .errores import ErrorConexion

MAJOR_ACCESO = 5

# Qué significa cada `minor` de major=5. Solo se listan los que se han visto en
# el equipo o son estándar en toda la gama; cualquier otro se muestra con su
# código en vez de inventarle un nombre.
#
# 104 no aparece con nombre en la documentación pública consultada. En el
# equipo real llega con el número de empleado ya identificado y SIN apertura
# de puerta, así que se clasifica como rechazo y se deja el código a la vista.
TIPOS_EVENTO = {
    1: ('permitido', 'Tarjeta válida'),
    38: ('permitido', 'Huella verificada'),
    39: ('denegado', 'Huella no reconocida'),
    75: ('permitido', 'Rostro verificado'),
    76: ('denegado', 'Rostro no reconocido'),
    104: ('denegado', 'Identificado, acceso rechazado (código 104)'),
    21: ('puerta', 'Puerta desbloqueada'),
    22: ('puerta', 'Puerta bloqueada'),
}

# Filtros de la pantalla → el `tipo` guardado que abarcan.
FILTROS = {
    'todos': None,
    'permitidos': 'permitido',
    'denegados': 'denegado',
}

# Prefijo de las capturas que guarda el equipo. `ruta_captura` solo acepta
# rutas bajo aquí: el endpoint que las sirve no debe poder usarse para bajar
# cualquier otro archivo del lector.
PREFIJO_CAPTURAS = '/LOCALS/pic/'

# Cota de lo que se trae por ronda. Si el lector acumuló más (p. ej. estuvo
# días sin escucha), la siguiente ronda sigue desde donde quedó esta.
PAGINAS_POR_RONDA = 100

# Un evento JSON pesa < 1 KB. Si el buffer del stream crece más que esto sin
# formar una parte completa, el flujo está corrupto y se reconecta.
MAX_BUFFER_STREAM = 1024 * 1024

_URL_ACSEVENT = '/ISAPI/AccessControl/AcsEvent?format=json'


def _paginar(cli, cond: dict, *, limite: int, paginas: int) -> list[dict]:
    """Recorre las páginas de AcsEvent (tope real de 30 por página)."""
    salida: list[dict] = []
    posicion = 0
    for _ in range(paginas):
        falta = limite - len(salida)
        if falta <= 0:
            break
        datos = cli.pedir_json('POST', _URL_ACSEVENT, {
            'AcsEventCond': {
                **cond,
                'searchResultPosition': posicion,
                'maxResults': min(falta, MAX_RESULTS),
            },
        })
        bloque = datos.get('AcsEvent') or {}
        lote = bloque.get('InfoList') or []
        salida.extend(lote)
        total = int(bloque.get('totalMatches') or 0)
        posicion += len(lote)
        if not lote or posicion >= total:
            break
    return salida[:limite]


def desde_serial(cli, ultimo_serial: int) -> list[dict]:
    """Eventos de acceso con `serialNo` mayor que `ultimo_serial`."""
    return _paginar(cli, {
        'searchID': f'erp-desde-{ultimo_serial}',
        'major': MAJOR_ACCESO,
        'minor': 0,
        'beginSerialNo': ultimo_serial + 1,
        'endSerialNo': 3_000_000_000,   # tope que anuncia el equipo
    }, limite=PAGINAS_POR_RONDA * MAX_RESULTS, paginas=PAGINAS_POR_RONDA)


def recientes(cli, inicio: str, fin: str, *, limite: int) -> list[dict]:
    """Hasta `limite` eventos del rango, del más reciente al más antiguo.

    Solo para la PRIMERA carga de un lector (tabla vacía): traerse el historial
    por serial desde 1 podría ser el año entero del equipo.
    """
    return _paginar(cli, {
        'searchID': 'erp-historial',
        'major': MAJOR_ACCESO,
        'minor': 0,
        'startTime': inicio,
        'endTime': fin,
        'timeReverseOrder': True,
    }, limite=limite, paginas=(limite // MAX_RESULTS) + 1)


# Eventos de la cerradura: el lector los manda por el stream al desbloquear
# (acceso reconocido, apertura remota o botón) y al volver a bloquear.
MINOR_PUERTA_ABIERTA = 21
MINOR_PUERTA_CERRADA = 22


def ultimo_estado_puerta(eventos) -> dict | None:
    """Estado de la cerradura según el evento de puerta más reciente del lote.

    `eventos` son `EventoHikvision` recién guardados. Devuelve
    {'cerradura': 'Abierta'|'Cerrada', 'hora': hora_local} o None si el lote
    no trae eventos de puerta.
    """
    de_puerta = [e for e in eventos
                 if e.minor in (MINOR_PUERTA_ABIERTA, MINOR_PUERTA_CERRADA)]
    if not de_puerta:
        return None
    ultimo = max(de_puerta, key=lambda e: e.serial_no)
    return {
        'cerradura': 'Abierta' if ultimo.minor == MINOR_PUERTA_ABIERTA else 'Cerrada',
        'hora': ultimo.hora_local,
    }


def ruta_captura(picture_url: str) -> str | None:
    """Ruta local de la foto que tomó el equipo, o None si no hay o no es válida.

    Del `pictureURL` (que el equipo reporta con su IP) solo se toma la ruta: la
    descarga siempre va contra el host configurado del lector.
    """
    if not picture_url:
        return None
    partes = urlsplit(picture_url)
    ruta = partes.path + (f'?{partes.query}' if partes.query else '')
    return ruta if es_ruta_captura_valida(ruta) else None


def es_ruta_captura_valida(ruta: str) -> bool:
    """Solo capturas del equipo: bajo `/LOCALS/pic/` y sin escapar del prefijo."""
    return bool(ruta) and ruta.startswith(PREFIJO_CAPTURAS) and '..' not in ruta


def normalizar(evento: dict) -> dict:
    """Aplana un evento crudo de AcsEvent a los campos que se guardan."""
    minor = int(evento.get('minor') or 0)
    tipo, descripcion = TIPOS_EVENTO.get(minor, ('otro', f'Evento {minor}'))
    return {
        'serial': evento.get('serialNo'),
        'fecha_hora': evento.get('time'),
        'major': int(evento.get('major') or MAJOR_ACCESO),
        'minor': minor,
        'tipo': tipo,
        'descripcion': descripcion,
        'employee_no': (evento.get('employeeNoString') or '').strip(),
        'nombre_en_equipo': evento.get('name') or '',
        'modo_verificacion': evento.get('currentVerifyMode') or '',
        'cubrebocas': evento.get('mask') == 'yes',
        'captura': ruta_captura(evento.get('pictureURL') or ''),
    }


# ── Stream ───────────────────────────────────────────────────────────────────

_FIN_CABECERA = re.compile(rb'\r?\n\r?\n')
_CONTENT_LENGTH = re.compile(rb'Content-Length:\s*(\d+)', re.IGNORECASE)
_CONTENT_TYPE = re.compile(rb'Content-Type:\s*([^\r\n;]+)', re.IGNORECASE)


def partes_multipart(trozos):
    """Parte un `multipart/mixed` que llega en trozos arbitrarios.

    Genera `(content_type, cuerpo)` por cada parte completa. Se guía por
    `Content-Length` y no por el boundary: el lector siempre lo manda, y así
    un JSON que contenga una línea en blanco no rompe el corte.

    Los trozos de red no respetan los límites de las partes (una cabecera puede
    quedar partida entre dos), de ahí el buffer.
    """
    buf = b''
    for trozo in trozos:
        buf += trozo
        while True:
            fin = _FIN_CABECERA.search(buf)
            if not fin:
                break
            cabecera = buf[:fin.start()]
            largo = _CONTENT_LENGTH.search(cabecera)
            if not largo:
                # Bloque sin longitud (el boundary suelto, un preámbulo): no es
                # una parte, se descarta hasta ahí.
                buf = buf[fin.end():]
                continue
            n = int(largo.group(1))
            if len(buf) < fin.end() + n:
                break
            tipo = _CONTENT_TYPE.search(cabecera)
            yield (
                tipo.group(1).decode('latin-1').strip().lower() if tipo else '',
                buf[fin.end():fin.end() + n],
            )
            buf = buf[fin.end() + n:]
        if len(buf) > MAX_BUFFER_STREAM:
            raise ErrorConexion(
                'El lector envió datos que no se pudieron interpretar.',
                detalle=f'buffer del alertStream excedió {MAX_BUFFER_STREAM} bytes',
            )
