"""Puerta y relé del lector: parámetros, estado en vivo y apertura remota.

Qué hace que un empleado abra la puerta
---------------------------------------
No hace falta nada aparte de darlo de alta bien: `usuarios.cuerpo_usuario()`
manda `doorRight: "1"` y `RightPlan: [{doorNo: 1, planTemplateNo: "1"}]`, que es
el permiso permanente sobre la puerta 1. Con eso, al reconocer el rostro el
equipo cierra el relé durante `openDuration` segundos (5 de fábrica).

La apertura remota de aquí es lo OTRO: abrir desde el ERP sin que nadie se
identifique, para visitas o cuando alguien olvidó registrarse.

Endpoints, verificados en un DS-K1T342MFWX-E1 (FW V3.16.1):

    GET /ISAPI/AccessControl/Door/param/1        parámetros (responde XML)
    GET /ISAPI/AccessControl/AcsWorkStatus       estado en vivo (JSON)
    PUT /ISAPI/AccessControl/RemoteControl/door/1  abrir (XML, sin capabilities)

Ojo con lo que NO existe en este modelo, para no perder tiempo buscándolo:
`/AccessControl/Door/status` responde `notSupport`, y
`/RemoteControl/door/1/capabilities` responde `methodNotAllowed` — ese endpoint
solo acepta PUT. El estado se lee de `AcsWorkStatus`.
"""
from __future__ import annotations

import logging

from .client import etiqueta_local
from .errores import ErrorConfiguracion

logger = logging.getLogger(__name__)

# Órdenes que acepta RemoteControl/door. Se expone solo `open`: las demás dejan
# la puerta en un estado permanente (siempre abierta / siempre cerrada) que,
# olvidado desde un panel web, es un problema de seguridad físico y no una
# función de nómina.
CMD_ABRIR = 'open'

# Lectura de `AcsWorkStatus.doorStatus`. Los códigos son del fabricante.
_DOOR_STATUS = {
    0: 'Sin información',
    1: 'Reposo',
    2: 'Siempre abierta',
    3: 'Siempre cerrada',
    4: 'Normal',
}
_LOCK_STATUS = {0: 'Cerrada', 1: 'Abierta'}
_MAGNETIC_STATUS = {0: 'Cerrado', 1: 'Abierto'}


def _primero(lista, defecto=None):
    """Primer elemento de las listas por puerta que devuelve `AcsWorkStatus`."""
    if isinstance(lista, list) and lista:
        return lista[0]
    return defecto


def parametros(cli, puerta: int = 1) -> dict:
    """Configuración de la puerta. `openDuration` es cuánto dura el relé cerrado."""
    raiz = cli.pedir_xml('GET', f'/ISAPI/AccessControl/Door/param/{puerta}')
    campos = {etiqueta_local(h.tag): (h.text or '').strip() for h in raiz}
    return {
        'nombre': campos.get('doorName', ''),
        'segundos_apertura': _entero(campos.get('openDuration'), 5),
        'segundos_apertura_discapacidad': _entero(campos.get('disabledOpenDuration'), 0),
        'tipo_magnetico': campos.get('magneticType', ''),
        'tipo_boton': campos.get('openButtonType', ''),
    }


def _entero(valor, defecto=0) -> int:
    try:
        return int(valor)
    except (TypeError, ValueError):
        return defecto


def estado(cli) -> dict:
    """Estado en vivo: cerradura, puerta, contacto magnético y lectores.

    En este modelo es la ÚNICA vía: `/AccessControl/Door/status` responde
    `notSupport`.
    """
    datos = cli.pedir_json('GET', '/ISAPI/AccessControl/AcsWorkStatus?format=json')
    ws = datos.get('AcsWorkStatus') or {}
    cod_puerta = _primero(ws.get('doorStatus'), 0)
    cod_cerradura = _primero(ws.get('doorLockStatus'), 0)
    cod_magnetico = _primero(ws.get('magneticStatus'), 0)
    return {
        'puerta': _DOOR_STATUS.get(cod_puerta, f'Desconocido ({cod_puerta})'),
        'cerradura': _LOCK_STATUS.get(cod_cerradura, f'Desconocido ({cod_cerradura})'),
        'magnetico': _MAGNETIC_STATUS.get(cod_magnetico, f'Desconocido ({cod_magnetico})'),
        'lector_en_linea': bool(_primero(ws.get('cardReaderOnlineStatus'), 0)),
        'red': ws.get('netStatus', ''),
        'antisabotaje': ws.get('hostAntiDismantleStatus', ''),
    }


def abrir(cli, puerta: int = 1) -> None:
    """Cierra el relé de `puerta` durante su `openDuration` configurado.

    Es una acción FÍSICA: abre una puerta de verdad. Quien la llame debe
    registrarla en la bitácora — no es una consulta más.

    El cuerpo va en XML aunque el resto del módulo use JSON: este endpoint no
    acepta `?format=json` en el firmware probado.
    """
    if puerta < 1:
        raise ErrorConfiguracion('Número de puerta inválido.')

    cuerpo = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<RemoteControlDoor version="2.0" xmlns="http://www.isapi.org/ver20/XMLSchema">'
        f'<cmd>{CMD_ABRIR}</cmd>'
        '</RemoteControlDoor>'
    )
    # `pedir_xml` ya levanta el error tipado si el <ResponseStatus> no trae éxito.
    cli.pedir_xml('PUT', f'/ISAPI/AccessControl/RemoteControl/door/{puerta}', cuerpo)
