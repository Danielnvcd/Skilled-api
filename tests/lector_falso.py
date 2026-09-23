"""Simulador de un lector Hikvision (ISAPI) para las pruebas.

Imita lo que se midió contra el DS-K1T342MFWX-E1 real, para poder probar los
fallos sin tocar el equipo de la oficina:

  · Digest con nonce de UN solo uso: reusarlo da 401 SIN reto nuevo (así se
    comporta el equipo real, y es lo que cortaba la escucha).
  · Bloqueo tras `max_intentos` credenciales malas, con `retryTimes`,
    `resLockTime` y `lockStatus` en la respuesta.
  · AcsEvent por serial (`beginSerialNo`) y por fechas (`timeReverseOrder`).
  · alertStream multipart que entrega lo encolado y se «corta» con `cortar()`.
  · Reinicio de seriales, cambio de equipo, de firmware, de reloj y de red.

Se conecta con `ClienteHikvision.transporte = lector.transporte()`.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import queue
import re
from datetime import datetime, timedelta, timezone

import httpx

CDMX = timezone(timedelta(hours=-6))
REALM = 'DS-falso'


def _md5(texto: str) -> str:
    return hashlib.md5(texto.encode()).hexdigest()


class LectorFalso:
    def __init__(self, *, usuario='admin', password='clave-correcta', serie='SN-UNO',
                 firmware='V4.48.40', max_intentos=5):
        self.usuario = usuario
        self.password = password
        self.serie = serie
        self.firmware = firmware
        self.max_intentos = max_intentos
        self.direccionamiento = 'static'
        self.desfase = timedelta(0)          # reloj del equipo respecto al real
        self.modo_hora = 'manual'

        self.eventos: list[dict] = []
        self._serial = itertools.count(1)
        self._nonces = itertools.count(1)
        self._vigentes: set[str] = set()
        self._stream: queue.Queue = queue.Queue()

        self.fallos = 0
        self.bloqueado = False
        self.nonces_vencidos = 0           # credenciales enviadas con nonce viejo
        self.peticiones: list[tuple[str, str]] = []
        self.escrituras: dict[str, str] = {}   # ruta → último cuerpo recibido por PUT

    # ── Control desde la prueba ──────────────────────────────────────────

    def transporte(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._manejar)

    def ahora(self) -> datetime:
        return (datetime.now(timezone.utc) + self.desfase).astimezone(CDMX).replace(microsecond=0)

    def agregar_evento(self, *, minor=75, emp='OF1', cuando: datetime | None = None,
                       avisar=True) -> dict:
        """Un acceso en el equipo. Con `avisar`, también sale por el stream."""
        cuando = (cuando or self.ahora()).astimezone(CDMX).replace(microsecond=0)
        ev = {
            'major': 5, 'minor': minor, 'time': cuando.isoformat(),
            'serialNo': next(self._serial), 'employeeNoString': emp,
            'name': 'Ana' if emp else '', 'currentVerifyMode': 'cardOrFaceOrFp',
            'pictureURL': f'http://10.0.0.9/LOCALS/pic/acsLinkCap/{emp or "x"}.jpeg' if emp else '',
        }
        self.eventos.append(ev)
        if avisar:
            self.enviar_parte({
                'eventType': 'AccessControllerEvent', 'dateTime': ev['time'],
                'AccessControllerEvent': {'majorEventType': 5, 'subEventType': minor,
                                          'serialNo': ev['serialNo']},
            })
        return ev

    def latido(self) -> None:
        self.enviar_parte({'eventType': 'videoloss', 'eventState': 'inactive',
                           'dateTime': self.ahora().isoformat()})

    def enviar_parte(self, obj: dict) -> None:
        self._stream.put(obj)

    def cortar(self) -> None:
        """El stream termina (el equipo cerró la conexión)."""
        self._stream.put(None)

    def reiniciar_seriales(self) -> None:
        """Reset de fábrica / historial borrado: la numeración vuelve a 1."""
        self.eventos = []
        self._serial = itertools.count(1)

    # ── Transporte ───────────────────────────────────────────────────────

    def _manejar(self, request: httpx.Request) -> httpx.Response:
        ruta = request.url.raw_path.decode()
        rechazo = self._autenticar(request)
        if rechazo is not None:
            return rechazo
        # Solo las autenticadas: cada operación son dos peticiones HTTP (el reto
        # digest y la buena) y aquí interesan las operaciones.
        self.peticiones.append((request.method, ruta))

        base = ruta.split('?')[0]
        if base == '/ISAPI/System/deviceInfo':
            return self._xml('DeviceInfo', model='DS-K1T342MFWX-E1', serialNumber=self.serie,
                             firmwareVersion=self.firmware, deviceName='Access Controller',
                             macAddress='00:11:22:33:44:55', deviceType='ACS')
        if base == '/ISAPI/System/time' and request.method == 'GET':
            return self._xml('Time', timeMode=self.modo_hora, localTime=self.ahora().isoformat(),
                             timeZone='CST+6:00:00')
        if base == '/ISAPI/System/Network/interfaces/1/ipAddress':
            return self._xml('IPAddress', ipVersion='v4', addressingType=self.direccionamiento,
                             ipAddress='10.0.0.9')
        if request.method == 'PUT' and base in ('/ISAPI/System/time',
                                                '/ISAPI/System/time/ntpServers/1'):
            self.escrituras[base] = request.content.decode()
            if base == '/ISAPI/System/time' and '<timeMode>NTP</timeMode>' in self.escrituras[base]:
                self.modo_hora = 'NTP'
            return self._xml('ResponseStatus', statusCode='1', statusString='OK')
        if base == '/ISAPI/System/time/ntpServers':
            return httpx.Response(200, text=(
                '<NTPServerList xmlns="http://www.isapi.org/ver20/XMLSchema"><NTPServer><id>1</id>'
                '<addressingFormatType>hostname</addressingFormatType><hostName></hostName>'
                '<synchronizeInterval>0</synchronizeInterval></NTPServer></NTPServerList>'))
        if base == '/ISAPI/AccessControl/AcsEvent':
            return self._acs_event(json.loads(request.content)['AcsEventCond'])
        if base == '/ISAPI/Event/notification/alertStream':
            return httpx.Response(
                200, headers={'Content-Type': 'multipart/mixed; boundary=MIME_boundary'},
                content=self._partes_stream(),
            )
        return httpx.Response(404, json={'statusCode': 4, 'subStatusCode': 'notSupport'})

    def _autenticar(self, request) -> httpx.Response | None:
        auth = request.headers.get('Authorization', '')
        if not auth:
            nonce = f'nonce-{next(self._nonces)}'
            self._vigentes.add(nonce)
            return httpx.Response(401, headers={
                'WWW-Authenticate': f'Digest qop="auth", realm="{REALM}", nonce="{nonce}", stale="false"',
            })
        campos = dict(re.findall(r'(\w+)="?([^",]+)"?', auth[len('Digest '):]))
        if campos.get('nonce') not in self._vigentes:
            # Como el equipo real: nonce vencido → 401 sin reto nuevo.
            self.nonces_vencidos += 1
            return httpx.Response(401, text='<userCheck><statusValue>401</statusValue></userCheck>')
        self._vigentes.discard(campos['nonce'])

        if self.bloqueado:
            return self._rechazo(0, bloqueado=True)
        ha1 = _md5(f"{self.usuario}:{REALM}:{self.password}")
        ha2 = _md5(f"{request.method}:{campos.get('uri')}")
        esperado = _md5(f"{ha1}:{campos['nonce']}:{campos.get('nc')}:{campos.get('cnonce')}:"
                        f"{campos.get('qop')}:{ha2}")
        if campos.get('username') != self.usuario or campos.get('response') != esperado:
            self.fallos += 1
            restantes = self.max_intentos - self.fallos
            if restantes <= 0:
                self.bloqueado = True
                return self._rechazo(0, bloqueado=True)
            return self._rechazo(restantes)
        return None

    def _rechazo(self, restantes: int, *, bloqueado=False) -> httpx.Response:
        return httpx.Response(401, text=(
            '<ResponseStatus version="1.0"><statusCode>4</statusCode>'
            '<subStatusCode>invalidOperation</subStatusCode>'
            f'<lockStatus>{"locked" if bloqueado else "unlock"}</lockStatus>'
            f'<retryTimes>{restantes}</retryTimes>'
            f'<resLockTime>{1800 if bloqueado else 0}</resLockTime></ResponseStatus>'))

    def _acs_event(self, cond: dict) -> httpx.Response:
        lista = list(self.eventos)
        if cond.get('beginSerialNo'):
            lista = [e for e in lista if cond['beginSerialNo'] <= e['serialNo'] <= cond['endSerialNo']]
        else:
            inicio = datetime.fromisoformat(cond['startTime'])
            fin = datetime.fromisoformat(cond['endTime'])
            lista = [e for e in lista if inicio <= datetime.fromisoformat(e['time']) <= fin]
            if cond.get('timeReverseOrder'):
                lista.sort(key=lambda e: e['time'], reverse=True)
        pos, n = cond.get('searchResultPosition', 0), cond.get('maxResults', 30)
        pagina = lista[pos:pos + n]
        return httpx.Response(200, json={'AcsEvent': {
            'searchID': cond.get('searchID'), 'totalMatches': len(lista),
            'numOfMatches': len(pagina),
            'responseStatusStrg': 'MORE' if pos + n < len(lista) else 'OK',
            'InfoList': pagina,
        }})

    def _partes_stream(self):
        while True:
            obj = self._stream.get(timeout=5)
            if obj is None:
                return
            cuerpo = json.dumps(obj).encode()
            yield (b'--MIME_boundary\r\nContent-Type: application/json; charset="UTF-8"\r\n'
                   b'Content-Length: ' + str(len(cuerpo)).encode() + b'\r\n\r\n' + cuerpo + b'\r\n')

    @staticmethod
    def _xml(raiz: str, **campos) -> httpx.Response:
        cuerpo = ''.join(f'<{k}>{v}</{k}>' for k, v in campos.items())
        return httpx.Response(200, text=(
            f'<?xml version="1.0" encoding="UTF-8"?><{raiz} version="2.0" '
            f'xmlns="http://www.isapi.org/ver20/XMLSchema">{cuerpo}</{raiz}>'))
