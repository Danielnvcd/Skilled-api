"""Cliente HTTP para lectores Hikvision vía ISAPI.

Es la ÚNICA pieza que habla con el equipo. Todo lo demás (usuarios, fotos,
asistencia) se construye encima, y las rutas Flask no tocan httpx jamás.

Decisiones que vienen de probar contra el hardware real (DS-K1T342MFWX-E1,
firmware V3.16.1), no del manual:

  · El firmware devuelve `responseStatusStrg` — así, truncado — donde la
    documentación dice `responseStatusStrings`. Un paginador que busque la
    llave documentada nunca encuentra el fin de página y gira para siempre.
  · El valor de fin sin resultados es `"NO MATCH"`, con espacio, no
    `"NO_MATCHES"` con guión bajo.
  · `maxResults` tope real: 30. Pedir más hace que el equipo rechace.
  · Un 401 puede ser credenciales malas O la cuenta bloqueada por intentos
    fallidos (~30 min). Por eso NUNCA se reintenta un 401.

Seguridad
---------
La contraseña no aparece en ningún mensaje de error, log ni excepción. Las
excepciones de httpx se atrapan y se re-lanzan como `ErrorConexion`, porque la
excepción cruda incluye la URL —y con ella el usuario— en su representación.
"""
from __future__ import annotations

import ipaddress
import logging
import socket
import xml.etree.ElementTree as ET
from datetime import datetime
from xml.sax.saxutils import escape

import httpx

from .errores import (
    ErrorAutenticacion,
    ErrorConexion,
    ErrorConfiguracion,
    ErrorDispositivo,
    ErrorFoto,
    traducir_sub_status,
)

logger = logging.getLogger(__name__)

# Timeouts separados: conectar a un equipo apagado falla rápido, pero modelar un
# rostro tarda varios segundos y no queremos cortarlo a media faena.
TIMEOUT_CONEXION = 5.0
TIMEOUT_LECTURA = 20.0
# Subir y modelar un rostro es la operación más lenta del equipo.
TIMEOUT_LECTURA_FOTO = 45.0
# Silencio máximo en el alertStream. El lector manda señal de vida cada ~30 s,
# así que 90 tolera perder dos antes de reconectar.
TIMEOUT_STREAM = 90.0

# Tope real del equipo para UserInfo/Search y AcsEvent.
MAX_RESULTS = 30

# `statusCode: 1` es el único éxito en las respuestas JSON de ISAPI.
STATUS_OK = 1

# Marcas de fin de paginación. Se comparan normalizadas (mayúsculas, sin
# espacios ni guiones) porque el firmware usa "NO MATCH" y el manual "NO_MATCHES".
_FIN_PAGINACION = {'OK', 'NOMATCH', 'NOMATCHES'}


def validar_host(host: str) -> str:
    """Valida que `host` apunte a la red local y lo devuelve normalizado.

    Por qué existe: el host lo captura un administrador desde un formulario, y
    sin esta validación el ERP se convertiría en un proxy — alguien podría
    apuntar un "lector" a un servicio interno o a una URL de metadatos de nube y
    usar el botón "Probar conexión" para sondear la red desde el servidor.

    Es el espejo de `app/utils/image_fetch.py`, que hace lo contrario: allá se
    EXIGE IP pública porque se bajan imágenes de internet; aquí se exige privada
    porque el lector vive en la LAN.
    """
    host = (host or '').strip()
    if not host:
        raise ErrorConfiguracion('Falta la dirección IP del lector.')

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        # No es IP literal: se resuelve y se exige que TODO lo resuelto sea privado.
        try:
            infos = socket.getaddrinfo(host, None)
        except socket.gaierror as e:
            raise ErrorConfiguracion(
                f'No se pudo resolver el nombre "{host}".',
                detalle=f'getaddrinfo falló para {host}: {e}',
            ) from None
        direcciones = {info[4][0] for info in infos}
        if not direcciones:
            raise ErrorConfiguracion(f'El nombre "{host}" no resolvió a ninguna dirección.')
        for dir_ in direcciones:
            if not _es_privada(ipaddress.ip_address(dir_)):
                raise ErrorConfiguracion(
                    f'"{host}" resuelve a una dirección fuera de la red local. '
                    'El lector debe estar en la LAN.',
                    detalle=f'{host} resolvió a {dir_}, que no es privada',
                )
        return host

    if not _es_privada(ip):
        raise ErrorConfiguracion(
            f'{host} no es una dirección de red local. El lector debe estar en la LAN.',
            detalle=f'host {host} fuera de rangos privados',
        )
    return host


def _es_privada(ip) -> bool:
    """True si la IP es de red local y se puede usar como destino de un lector.

    Link-local (169.254.0.0/16, fe80::/10) queda FUERA a propósito aunque
    técnicamente sea "local": ahí vive 169.254.169.254, el servicio de metadatos
    de AWS/GCP/Azure, y esta API corre en un VPS. Un lector instalado en serio
    tiene IP fija de LAN; uno en APIPA significa que se quedó sin DHCP y no es un
    destino que valga la pena permitir a cambio de dejar esa puerta abierta.
    """
    if ip.is_link_local:
        return False
    return bool(ip.is_private or ip.is_loopback)


def validar_puerto(puerto) -> int:
    try:
        p = int(puerto)
    except (TypeError, ValueError):
        raise ErrorConfiguracion('El puerto debe ser un número.') from None
    if not 1 <= p <= 65535:
        raise ErrorConfiguracion('El puerto debe estar entre 1 y 65535.')
    return p


class _DigestSinReuso(httpx.DigestAuth):
    """Digest que pide un reto nuevo en CADA petición.

    `httpx.DigestAuth` guarda el nonce del primer reto y lo reenvía de entrada
    en las peticiones siguientes. Este lector lo invalida a los pocos segundos
    (medido: a los 30 s ya lo rechaza) y responde 401 SIN mandar un reto nuevo,
    así que httpx no puede recuperarse y el 401 llega como si la contraseña
    fuera mala.

    Era lo que cortaba la escucha en tiempo real: el aviso del stream llegaba
    al instante, pero la consulta que trae el evento daba 401, la sesión se
    caía y el evento se guardaba hasta la reconexión (20–60 s tarde). Peor aún,
    cada uno de esos 401 es un intento de autenticación rechazado, y el
    lector bloquea la cuenta tras varios (5 por defecto, `illegalLoginLock`).

    Olvidar el reto antes de cada petición cuesta un viaje más en la LAN y
    garantiza que nunca se mandan credenciales con un nonce vencido. El primer
    401 de cada petición va SIN credenciales, así que no cuenta como intento
    fallido.
    """

    def auth_flow(self, request):
        self._last_challenge = None
        yield from super().auth_flow(request)


class ClienteHikvision:
    """Sesión contra UN lector. Usar como context manager para cerrar el socket.

        with ClienteHikvision.desde_dispositivo(disp) as cli:
            info = cli.info_dispositivo()
    """

    def __init__(self, host: str, puerto: int, usuario: str, password: str):
        self.host = validar_host(host)
        self.puerto = validar_puerto(puerto)
        if not usuario or not password:
            raise ErrorConfiguracion('El lector necesita usuario y contraseña.')
        self._usuario = usuario
        # Se guarda solo para construir la autenticación. No se expone en repr,
        # no se loggea y no entra en ningún mensaje de error.
        self._password = password
        self._cliente: httpx.Client | None = None

    def __repr__(self) -> str:
        # Explícito: sin este repr, un traceback de httpx podría imprimir el
        # objeto con todos sus atributos, contraseña incluida.
        return f'<ClienteHikvision {self.host}:{self.puerto} usuario={self._usuario!r}>'

    @classmethod
    def desde_dispositivo(cls, dispositivo) -> 'ClienteHikvision':
        """Construye el cliente desde una fila `DispositivoHikvision`."""
        return cls(
            host=dispositivo.host,
            puerto=dispositivo.puerto,
            usuario=dispositivo.usuario,
            password=dispositivo.password,
        )

    # ── Ciclo de vida ────────────────────────────────────────────────────────

    def _http(self) -> httpx.Client:
        if self._cliente is None:
            self._cliente = httpx.Client(
                base_url=f'http://{self.host}:{self.puerto}',
                auth=_DigestSinReuso(self._usuario, self._password),
                timeout=httpx.Timeout(TIMEOUT_LECTURA, connect=TIMEOUT_CONEXION),
                # Sin redirecciones: ISAPI no las usa, y seguirlas ciegamente
                # mandaría las credenciales a donde diga el equipo.
                follow_redirects=False,
            )
        return self._cliente

    def cerrar(self) -> None:
        if self._cliente is not None:
            self._cliente.close()
            self._cliente = None

    def __enter__(self) -> 'ClienteHikvision':
        return self

    def __exit__(self, *exc) -> None:
        self.cerrar()

    # ── Petición base ────────────────────────────────────────────────────────

    def _peticion(self, metodo: str, ruta: str, *, timeout=None, **kw) -> httpx.Response:
        """Ejecuta la petición y normaliza los fallos de transporte y el 401."""
        try:
            resp = self._http().request(metodo, ruta, timeout=timeout, **kw)
        except httpx.TimeoutException as e:
            raise ErrorConexion(
                'El lector no respondió a tiempo.',
                detalle=f'timeout en {metodo} {ruta}: {type(e).__name__}',
            ) from None
        except httpx.HTTPError as e:
            # Se corta la cadena con `from None`: la excepción original de httpx
            # lleva la URL, y la URL puede arrastrar credenciales al log.
            raise ErrorConexion(
                detalle=f'fallo de transporte en {metodo} {ruta}: {type(e).__name__}',
            ) from None

        if resp.status_code == 401:
            raise ErrorAutenticacion(
                detalle=f'401 en {metodo} {ruta} (credenciales inválidas o cuenta bloqueada)',
            )
        # A propósito NO se aborta aquí ante un 4xx/5xx. ISAPI responde los
        # errores de negocio con HTTP 400 y un cuerpo JSON que SÍ dice qué pasó
        # (p. ej. 400 + subStatusCode=SubpicAnalysisModelingError cuando el
        # rostro de la foto no se puede modelar). Cortar por el código HTTP
        # tiraría justo el diagnóstico que el usuario necesita leer. Quien
        # interpreta el cuerpo — `pedir_json`, `enviar_multipart`, `pedir_xml` —
        # es el que decide el error tipado.
        return resp

    # ── JSON ─────────────────────────────────────────────────────────────────

    def pedir_json(self, metodo: str, ruta: str, cuerpo: dict | None = None,
                   *, timeout=None) -> dict:
        """Petición JSON que valida el `statusCode` de ISAPI.

        Un HTTP 200 NO significa éxito: ISAPI devuelve 200 con
        `statusCode != 1` para casi todos sus fallos de negocio.
        """
        kw = {}
        if cuerpo is not None:
            kw['json'] = cuerpo
        resp = self._peticion(metodo, ruta, timeout=timeout, **kw)

        datos = self._json_o_error(resp, f'{metodo} {ruta}')
        self._verificar_status(datos, f'{metodo} {ruta}')
        return datos

    @staticmethod
    def _json_o_error(resp: httpx.Response, contexto: str) -> dict:
        """Cuerpo JSON de la respuesta, o `ErrorDispositivo` si no se puede leer.

        Cuando el cuerpo no es JSON el código HTTP es lo único que queda para
        explicar el fallo, así que ahí sí se reporta.
        """
        try:
            return resp.json()
        except ValueError:
            raise ErrorDispositivo(
                f'El lector respondió con un error (HTTP {resp.status_code}).'
                if resp.status_code >= 400
                else 'El lector devolvió una respuesta que no se pudo interpretar.',
                detalle=f'HTTP {resp.status_code}, cuerpo no-JSON en {contexto}: {resp.text[:200]!r}',
            ) from None

    @staticmethod
    def _verificar_status(datos: dict, contexto: str) -> None:
        """Levanta el error tipado que corresponda si `statusCode` no es éxito."""
        status = datos.get('statusCode')
        # Las respuestas de consulta (Search) no siempre traen statusCode;
        # su ausencia no es un fallo.
        if status is None or status == STATUS_OK:
            return

        sub = str(datos.get('subStatusCode') or '')
        detalle = (
            f'{contexto} → statusCode={status} '
            f'statusString={datos.get("statusString")!r} subStatusCode={sub!r}'
        )
        amigable = traducir_sub_status(sub)

        if sub == 'SubpicAnalysisModelingError' or 'FacePicQuality' in sub:
            raise ErrorFoto(amigable, detalle=detalle)
        raise ErrorDispositivo(amigable, detalle=detalle, sub_status=sub)

    def enviar_multipart(self, ruta: str, partes: dict, *, metodo: str = 'POST',
                         timeout=None) -> dict:
        """Multipart/form-data que valida el `statusCode` de ISAPI.

        Existe aparte de `pedir_json` porque `FDSetUp` no manda un cuerpo
        JSON sino dos partes: la metadata y la imagen. `partes` usa el formato de
        httpx — {nombre: (archivo_o_None, contenido, content_type)} — y su ORDEN
        se respeta, cosa que el equipo exige.
        """
        resp = self._peticion(metodo, ruta, files=partes, timeout=timeout)
        datos = self._json_o_error(resp, f'{metodo} {ruta}')
        self._verificar_status(datos, f'{metodo} {ruta}')
        return datos

    # ── Stream de eventos ────────────────────────────────────────────────────

    def flujo_alertas(self):
        """Partes del `alertStream`: `(content_type, cuerpo)` según llegan.

        La conexión queda abierta indefinidamente; el lector manda una señal de
        vida cada ~30 s aunque no pase nada. Si en `TIMEOUT_STREAM` no llega
        NADA, la conexión se da por muerta (`ErrorConexion`) y quien escucha
        reconecta.

        Para cortarla desde otro hilo basta `cerrar()`: la lectura en curso
        falla y el generador termina con `ErrorConexion`.
        """
        from .eventos import partes_multipart

        ruta = '/ISAPI/Event/notification/alertStream'
        try:
            with self._http().stream(
                'GET', ruta,
                timeout=httpx.Timeout(TIMEOUT_STREAM, connect=TIMEOUT_CONEXION),
            ) as resp:
                if resp.status_code == 401:
                    raise ErrorAutenticacion(
                        detalle=f'401 en GET {ruta} (credenciales inválidas o cuenta bloqueada)',
                    )
                if resp.status_code != 200:
                    raise ErrorDispositivo(
                        'El lector rechazó la conexión de eventos en tiempo real.',
                        detalle=f'HTTP {resp.status_code} en GET {ruta}',
                    )
                yield from partes_multipart(resp.iter_bytes())
        except httpx.TimeoutException as e:
            raise ErrorConexion(
                'El lector dejó de enviar señales de vida.',
                detalle=f'timeout en GET {ruta}: {type(e).__name__}',
            ) from None
        except httpx.HTTPError as e:
            # Igual que en `_peticion`: se corta la cadena para no arrastrar la
            # URL (y con ella el usuario) al log.
            raise ErrorConexion(
                detalle=f'fallo de transporte en GET {ruta}: {type(e).__name__}',
            ) from None

    # ── Binario ──────────────────────────────────────────────────────────────

    def descargar(self, ruta: str, *, max_bytes: int = 2 * 1024 * 1024) -> bytes:
        """Bytes de un archivo del equipo (p. ej. el `faceURL` de un usuario).

        `ruta` es SOLO la ruta: la petición siempre va contra el host de este
        cliente, así que una URL que el equipo reporte no puede desviarla a otra
        máquina. El tope de bytes evita que una respuesta anómala se cargue
        entera en memoria.
        """
        resp = self._peticion('GET', ruta)
        if resp.status_code != 200:
            raise ErrorDispositivo(
                'El lector no entregó la imagen solicitada.',
                detalle=f'HTTP {resp.status_code} en GET {ruta}',
            )
        if len(resp.content) > max_bytes:
            raise ErrorDispositivo(
                'La imagen del lector excede el tamaño esperado.',
                detalle=f'{len(resp.content)} bytes en GET {ruta}',
            )
        return resp.content

    # ── XML (deviceInfo y time siguen siendo XML en este firmware) ───────────

    def pedir_xml(self, metodo: str, ruta: str, cuerpo: str | None = None) -> ET.Element:
        kw = {}
        if cuerpo is not None:
            kw['content'] = cuerpo.encode('utf-8')
            kw['headers'] = {'Content-Type': 'application/xml'}
        resp = self._peticion(metodo, ruta, **kw)
        try:
            raiz = ET.fromstring(resp.text)
        except ET.ParseError as e:
            raise ErrorDispositivo(
                f'El lector respondió con un error (HTTP {resp.status_code}).'
                if resp.status_code >= 400
                else 'El lector devolvió una respuesta que no se pudo interpretar.',
                detalle=f'HTTP {resp.status_code}, XML inválido en {metodo} {ruta}: {e}',
            ) from None

        # Los fallos de los endpoints XML llegan como <ResponseStatus> con su
        # propio statusCode, igual que en JSON pero en otro envoltorio.
        if etiqueta_local(raiz.tag) == 'ResponseStatus':
            campos = {etiqueta_local(h.tag): (h.text or '').strip() for h in raiz}
            if campos.get('statusCode') and campos['statusCode'] != str(STATUS_OK):
                self._verificar_status({
                    'statusCode': int(campos['statusCode']),
                    'statusString': campos.get('statusString'),
                    'subStatusCode': campos.get('subStatusCode'),
                }, f'{metodo} {ruta}')
        return raiz

    # ── Operaciones de alto nivel ────────────────────────────────────────────

    def info_dispositivo(self) -> dict:
        """`GET /ISAPI/System/deviceInfo` — el "Probar conexión" del formulario.

        Es la llamada más barata que exige autenticación correcta, así que sirve
        de prueba de vida y de credenciales a la vez.
        """
        raiz = self.pedir_xml('GET', '/ISAPI/System/deviceInfo')
        # El XML viene con namespace; se compara por el nombre local de la etiqueta.
        campos = {etiqueta_local(hijo.tag): (hijo.text or '').strip() for hijo in raiz}
        return {
            'modelo': campos.get('model', ''),
            'numero_serie': campos.get('serialNumber', ''),
            'firmware': campos.get('firmwareVersion', ''),
            'nombre_dispositivo': campos.get('deviceName', ''),
            'mac': campos.get('macAddress', ''),
            'tipo': campos.get('deviceType', ''),
        }

    def capacidades_usuario(self) -> dict:
        """Límites REALES del equipo para `employeeNo` y nombre.

        Se consultan en vez de asumirse: cambian entre modelos y entre versiones
        de firmware, y mandar un `employeeNo` más largo del permitido se
        traduce en un error opaco del equipo.
        """
        datos = self.pedir_json('GET', '/ISAPI/AccessControl/UserInfo/capabilities?format=json')
        info = datos.get('UserInfo') or {}
        return {
            'employee_no_max': int((info.get('employeeNo') or {}).get('@max', 32)),
            'nombre_max': int((info.get('name') or {}).get('@max', 128)),
        }

    def hora_dispositivo(self) -> dict:
        """`GET /ISAPI/System/time`. Un reloj desfasado hace inservibles las checadas."""
        raiz = self.pedir_xml('GET', '/ISAPI/System/time')
        campos = {etiqueta_local(h.tag): (h.text or '').strip() for h in raiz}
        return {
            'modo': campos.get('timeMode', ''),
            'hora_local': campos.get('localTime', ''),
            'zona': campos.get('timeZone', ''),
        }

    def ajustar_hora(self, ahora_utc: datetime) -> dict:
        """Pone el reloj del equipo en `ahora_utc`, conservando SU zona horaria.

        La hora se manda en el desfase que el equipo ya usa (el de su
        `localTime`), no en el del servidor: el VPS corre en UTC y mandarla así
        corregiría el instante pero movería todas las horas que muestra la
        pantalla del lector. `timeZone` se reenvía tal cual por lo mismo.

        Devuelve la hora que reporta el equipo DESPUÉS del cambio, para
        confirmar que lo aplicó.
        """
        actual = self.hora_dispositivo()
        try:
            zona = datetime.fromisoformat(actual['hora_local']).tzinfo
        except ValueError:
            zona = None
        if zona is None:
            raise ErrorDispositivo(
                'El lector no informó su zona horaria; no se puede ajustar la hora con seguridad.',
                detalle=f'localTime sin desfase: {actual["hora_local"]!r}',
            )
        local = ahora_utc.astimezone(zona).replace(microsecond=0)
        cuerpo = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Time version="2.0" xmlns="http://www.isapi.org/ver20/XMLSchema">'
            '<timeMode>manual</timeMode>'
            f'<localTime>{local.isoformat()}</localTime>'
            f'<timeZone>{escape(actual["zona"])}</timeZone>'
            '</Time>'
        )
        self.pedir_xml('PUT', '/ISAPI/System/time', cuerpo)
        return self.hora_dispositivo()

    def capacidad(self) -> dict:
        """Usuarios y rostros registrados contra el máximo que admite el equipo.

        Los máximos se leen de las capabilities (`maxRecordNum` de UserInfo y
        `FDRecordDataMaxNum` de FDLib) en vez de suponerlos: cambian por modelo.
        """
        conteo = self.pedir_json('GET', '/ISAPI/AccessControl/UserInfo/Count?format=json')
        conteo = conteo.get('UserInfoCount') or {}
        caps_usr = self.pedir_json('GET', '/ISAPI/AccessControl/UserInfo/capabilities?format=json')
        caps_fd = self.pedir_json('GET', '/ISAPI/Intelligent/FDLib/capabilities?format=json')
        return {
            'usuarios': int(conteo.get('userNumber') or 0),
            'con_rostro': int(conteo.get('bindFaceUserNumber') or 0),
            'max_usuarios': int((caps_usr.get('UserInfo') or {}).get('maxRecordNum') or 0),
            'max_rostros': int(caps_fd.get('FDRecordDataMaxNum') or 0),
        }


def etiqueta_local(tag: str) -> str:
    """Nombre de la etiqueta sin el namespace: `{...}model` → `model`."""
    return tag.rsplit('}', 1)[-1]


def estado_pagina(bloque: dict) -> str:
    """Estado de paginación normalizado a MAYÚSCULAS y sin separadores.

    Absorbe las dos formas que se han visto en firmware real:
    `responseStatusStrg` (truncado) y `responseStatusStrings` (documentado), con
    valores `"NO MATCH"` o `"NO_MATCHES"`.
    """
    crudo = bloque.get('responseStatusStrg') or bloque.get('responseStatusStrings') or ''
    return str(crudo).upper().replace('_', '').replace(' ', '').replace('-', '')


def es_fin_de_paginacion(bloque: dict) -> bool:
    """True si esta página es la última (o no hubo resultados)."""
    return estado_pagina(bloque) in _FIN_PAGINACION
