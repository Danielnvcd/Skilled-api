"""Lectura de checadas del lector Hikvision. ESQUELETO — fase 2.

Esta fase deja lista la consulta de eventos, pero NADA de aquí escribe todavía
en el ERP. La conexión con nómina se hace en la siguiente entrega, y cuando se
haga será reutilizando lo que YA existe, sin tocar el cálculo de pagos:

    AcsEvent (checkIn/checkOut)
        ↓  dedupe por (dispositivo, serialNo)
    hikvision_evento_asistencia            ← tabla de la fase 2
        ↓  emparejar entrada/salida por (trabajador, fecha)
    RegistroDiarioHoras                    ← YA EXISTE, es donde escribe el kiosko RFID
        ↓  calcular_horas_productivas()    ← YA EXISTE, app/utils/horas.py
    Prenomina                              ← YA EXISTE, INTACTA

El lector se vuelve una tercera fuente de checadas junto al kiosko RFID y la
captura manual. No se crea ningún sistema de salarios ni de pagos.

Punto abierto para esa fase: los empleados de oficina no pertenecen a ningún
`Proyecto`, y `ReporteSemanal.proyecto_id` es NOT NULL. La salida limpia es un
proyecto "OFICINA" al que se les asigne, porque entonces prenómina funciona
exactamente igual que hoy sin cambiar una línea.

Endpoint (verificado en `GET /ISAPI/AccessControl/AcsEvent/capabilities`):

    POST /ISAPI/AccessControl/AcsEvent?format=json
      major        5  = evento de control de acceso
      minor        la lista soportada por ESTE equipo incluye 75 y 76, pero varía
                   entre modelos: por eso `consultar_eventos` NO filtra por minor
                   y deja que el ERP discrimine por `attendanceStatus`.
      maxResults   tope real 30
"""
from __future__ import annotations

import logging

from .client import MAX_RESULTS, es_fin_de_paginacion

logger = logging.getLogger(__name__)

# major=5 es "control de acceso" en toda la gama. Es el único filtro seguro de
# aplicar sin conocer el modelo.
MAJOR_ACCESO = 5

# Valores de `attendanceStatus` que el equipo reporta. Se filtra por esto —y no
# por un `minor` adivinado— porque `minor` cambia entre modelos y firmwares.
ESTADOS_ASISTENCIA = ('checkIn', 'checkOut', 'breakIn', 'breakOut')


def consultar_eventos(cli, inicio: str, fin: str, *, limite_paginas: int = 500) -> list[dict]:
    """Eventos de acceso entre `inicio` y `fin` (ISO 8601 con offset).

    Las marcas de tiempo salen del reloj del equipo, así que ese reloj tiene que
    estar en hora: con la zona mal puesta las checadas se guardan corridas y la
    nómina sale mal. `ClienteHikvision.hora_dispositivo()` sirve para vigilarlo.

    Pagina de 30 en 30 (tope del firmware) con una cota dura de páginas: sin
    ella, un equipo que nunca marque el fin dejaría el worker girando.
    """
    salida: list[dict] = []
    posicion = 0

    for _ in range(limite_paginas):
        datos = cli.pedir_json('POST', '/ISAPI/AccessControl/AcsEvent?format=json', {
            'AcsEventCond': {
                'searchID': 'erp-asistencia',
                'searchResultPosition': posicion,
                'maxResults': MAX_RESULTS,
                'major': MAJOR_ACCESO,
                'minor': 0,          # 0 = todos los minor de ese major
                'startTime': inicio,
                'endTime': fin,
            },
        })
        bloque = datos.get('AcsEvent') or {}
        lote = bloque.get('InfoList') or []
        salida.extend(lote)
        if es_fin_de_paginacion(bloque) or not lote:
            break
        posicion += len(lote)

    return salida


def normalizar_evento(evento: dict) -> dict:
    """Aplana un evento crudo del equipo a lo que al ERP le importa.

    Se conserva `serialNo`: es lo que permite no contar dos veces la misma
    checada cuando una consulta se solapa con la anterior.
    """
    return {
        'serial_no': evento.get('serialNo'),
        'employee_no': (evento.get('employeeNoString') or '').strip(),
        'fecha_hora': evento.get('time'),
        'tipo': evento.get('attendanceStatus') or '',
        'major': evento.get('major'),
        'minor': evento.get('minor'),
        'modo_verificacion': evento.get('currentVerifyMode') or '',
        'nombre_en_equipo': evento.get('name') or '',
    }


def es_checada_de_persona(evento: dict) -> bool:
    """¿El evento corresponde a una persona identificada marcando asistencia?

    Descarta el ruido: aperturas manuales, eventos de puerta y todo lo que no
    trae número de empleado ni estado de asistencia reconocible.
    """
    return bool(evento.get('employee_no')) and evento.get('tipo') in ESTADOS_ASISTENCIA
