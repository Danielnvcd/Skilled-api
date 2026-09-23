"""Escucha en tiempo real de los lectores: `flask hikvision escuchar`.

Por qué un proceso aparte y no dentro de la API: cada lector necesita una
conexión abierta TODO el tiempo, y gunicorn corre varios workers que se
reciclan. Dentro de ellos habría una escucha por worker (eventos duplicados) o
ninguna tras un reinicio. Un proceso dedicado, uno solo, lo resuelve.

Flujo por lector (un hilo cada uno):

    conectar → ponerse al día por serial → abrir alertStream
        cada parte recibida        → renueva el latido (máx. cada 20 s)
        aviso de acceso (major 5)  → si su serial es nuevo: ponerse al día,
                                     guardar, avisar al navegador por Socket.IO
        corte / silencio de 90 s   → marcar DESCONECTADO y reconectar con
                                     espera creciente (5 s … 60 s)

El stream solo AVISA; los datos siempre se toman de AcsEvent por serial (ver
`eventos.py`). Así, reconectar nunca pierde eventos: se pide desde el último
guardado. Y el historial que el lector reenvía al reconectar no cuesta nada:
sus seriales ya son conocidos y se ignoran sin llamar al equipo.

El aviso al navegador viaja por Redis (`message_queue` de Flask-SocketIO), así
llega a los workers de la API aunque este proceso no atienda conexiones.
"""
from __future__ import annotations

import hashlib
import json
import logging
import signal
import threading
import time
from datetime import datetime, timezone

from app.extensions import db
from app.models import DispositivoHikvision

from . import asistencia, ingesta
from .client import ClienteHikvision
from .errores import ErrorAutenticacion, ErrorHikvision

logger = logging.getLogger(__name__)

ESPERA_INICIAL_S = 5
ESPERA_MAX_S = 60
# Tras un 401 real (contraseña cambiada en el lector) NO se reintenta al ritmo
# normal: el lector bloquea la cuenta tras 5 intentos fallidos por defecto
# (`illegalLoginLock`) y la cuenta es la misma que usan las personas.
ESPERA_AUTENTICACION_S = 600
# Cada cuánto se escribe el latido en la base. Más seguido solo es escritura
# de más: el umbral para darla por caída es de 90 s.
LATIDO_CADA_S = 20
# Repaso de seguridad aunque no llegue aviso (ver `repasar_si_toca`).
REPASO_CADA_S = 300
# Cada cuánto el supervisor revisa altas, bajas y cambios de lectores.
REVISION_CADA_S = 30
# Al navegador se le mandan los últimos N eventos nuevos, no todos: tras una
# reconexión larga pueden ser cientos, y la pantalla igual recarga su lista.
MAX_EVENTOS_POR_AVISO = 20

ROLES_AVISO = ['admin', 'super_admin']

# La escucha escribe su estado con UPDATE directos, que aplicarían el
# `onupdate` de `updated_at` cada 20 s. Esa columna dice cuándo un ADMIN editó
# el lector; el latido no es una edición.
_SIN_TOCAR_UPDATED_AT = {'updated_at': DispositivoHikvision.updated_at}


def _ahora():
    return datetime.now(timezone.utc)


class EscuchaLector:
    """La escucha de UN lector, en su propio hilo."""

    def __init__(self, app, dispositivo_id: int):
        self.app = app
        self.dispositivo_id = dispositivo_id
        self._detener = threading.Event()
        self._cli_stream: ClienteHikvision | None = None
        self._hilo = threading.Thread(
            target=self._correr, name=f'hikvision-escucha-{dispositivo_id}', daemon=True,
        )
        # Mayor serial ya atendido. Vive en memoria: tras reiniciar se
        # recalcula desde la base al ponerse al día.
        self.max_serial = 0
        self._ultimo_latido = 0.0
        self._ultimo_repaso = time.monotonic()
        # Hubo un aviso cuya consulta falló: reintentar en la próxima parte.
        self._pendiente = False

    # ── Ciclo de vida ────────────────────────────────────────────────────

    def iniciar(self) -> None:
        self._hilo.start()

    def detener(self) -> None:
        """Pide terminar y corta la conexión abierta para no esperar al timeout."""
        self._detener.set()
        cli = self._cli_stream
        if cli is not None:
            try:
                cli.cerrar()
            except Exception:  # noqa: BLE001 — cerrar es best-effort
                pass

    def vivo(self) -> bool:
        return self._hilo.is_alive()

    def _correr(self) -> None:
        espera = ESPERA_INICIAL_S
        while not self._detener.is_set():
            with self.app.app_context():
                try:
                    self._sesion()
                    espera = ESPERA_INICIAL_S
                except ErrorAutenticacion as e:
                    logger.warning('Hikvision escucha disp=%s: %s — se reintenta en %d s',
                                   self.dispositivo_id, e.detalle, ESPERA_AUTENTICACION_S)
                    self._marcar('DESCONECTADO', e.mensaje)
                    espera = ESPERA_AUTENTICACION_S
                except ErrorHikvision as e:
                    if not self._detener.is_set():
                        logger.warning('Hikvision escucha disp=%s: %s', self.dispositivo_id, e.detalle)
                    self._marcar('DESCONECTADO', e.mensaje)
                except Exception:  # noqa: BLE001 — el hilo nunca debe morir en silencio
                    logger.exception('Hikvision escucha disp=%s: error inesperado', self.dispositivo_id)
                    self._marcar('DESCONECTADO', 'Error interno en la escucha; se reintentará.')
                finally:
                    db.session.remove()
            if self._detener.wait(espera):
                break
            if espera < ESPERA_MAX_S:
                espera = min(espera * 2, ESPERA_MAX_S)
        with self.app.app_context():
            self._marcar('DESCONECTADO', None)
            db.session.remove()

    # ── Una conexión ─────────────────────────────────────────────────────

    def _sesion(self) -> None:
        d = db.session.get(DispositivoHikvision, self.dispositivo_id)
        if d is None or not d.activo:
            self._detener.set()
            return

        # Dos clientes: el del stream queda bloqueado leyendo; las consultas
        # por serial van por otro para no competir por la misma conexión.
        with ClienteHikvision.desde_dispositivo(d) as cli_consultas:
            self.max_serial = max(self.max_serial, ingesta.ultimo_serial(d.id))
            self._atender_novedades(cli_consultas)

            self._cli_stream = ClienteHikvision.desde_dispositivo(d)
            try:
                self._marcar('CONECTADO', None)
                logger.info('Hikvision escucha disp=%s: conectado', self.dispositivo_id)
                for tipo, cuerpo in self._cli_stream.flujo_alertas():
                    if self._detener.is_set():
                        return
                    self._latido()
                    if 'json' in tipo:
                        self.atender_parte(cli_consultas, cuerpo)
                    self.repasar_si_toca(cli_consultas)
            finally:
                cli, self._cli_stream = self._cli_stream, None
                cli.cerrar()

    def atender_parte(self, cli, cuerpo: bytes) -> None:
        """Una parte JSON del stream. Solo los accesos nuevos cuestan una consulta."""
        try:
            datos = json.loads(cuerpo)
        except ValueError:
            logger.warning('Hikvision escucha disp=%s: parte no JSON ignorada', self.dispositivo_id)
            return
        acceso = datos.get('AccessControllerEvent')
        if datos.get('eventType') != 'AccessControllerEvent' or not isinstance(acceso, dict):
            return   # p. ej. `videoloss`: es la señal de vida, ya contada
        if acceso.get('majorEventType') != 5:
            return   # operaciones/alarmas del equipo: no son accesos
        try:
            serial = int(acceso.get('serialNo') or 0)
        except (TypeError, ValueError):
            return
        if serial and serial <= self.max_serial:
            return   # historial reenviado al reconectar: ya está guardado
        if self._atender_novedades(cli):
            # Aunque la consulta no haya traído nada (el aviso pudo llegar un
            # instante antes de que el evento fuera consultable), este serial
            # ya se atendió: el repaso periódico recoge lo que haya quedado.
            self.max_serial = max(self.max_serial, serial)

    def repasar_si_toca(self, cli) -> None:
        """Red de seguridad: cada `REPASO_CADA_S` se pide lo nuevo aunque no
        haya llegado aviso. Una consulta cada 5 min, no un sondeo.

        Si la última consulta falló (`_pendiente`), se reintenta con la
        siguiente parte que llegue del stream — la señal de vida basta — en vez
        de esperar al repaso."""
        ahora = time.monotonic()
        if self._pendiente or ahora - self._ultimo_repaso >= REPASO_CADA_S:
            self._ultimo_repaso = ahora
            self._atender_novedades(cli)

    def _atender_novedades(self, cli) -> bool:
        """Guarda lo nuevo y avisa. False si la consulta al lector falló."""
        try:
            nuevos = ingesta.ponerse_al_dia(cli, self.dispositivo_id)
        except ErrorAutenticacion:
            db.session.rollback()
            raise   # contraseña mala de verdad: que `_correr` espere lo largo
        except ErrorHikvision as e:
            # Una consulta fallida NO debe tumbar el stream: el aviso ya llegó
            # y la conexión sigue sana. Lo pendiente se recoge en el siguiente
            # aviso o en el repaso periódico, que piden desde el último serial.
            db.session.rollback()
            logger.warning('Hikvision escucha disp=%s: no se pudo traer lo nuevo: %s',
                           self.dispositivo_id, e.detalle)
            self._pendiente = True
            return False
        self._pendiente = False
        if not nuevos:
            db.session.commit()
            return True
        self.max_serial = max(self.max_serial, max(e.serial_no for e in nuevos))
        cargas = [e.to_dict() for e in nuevos[-MAX_EVENTOS_POR_AVISO:]]
        # Fase 2: los accesos pasan a los registros de horas en la MISMA
        # transacción que los eventos. `aplicar_eventos` aísla cada día en su
        # propio savepoint, así que una checada rara no pierde los eventos.
        checadas = asistencia.aplicar_eventos(nuevos)
        db.session.commit()
        logger.info('Hikvision escucha disp=%s: %d evento(s) nuevo(s)', self.dispositivo_id, len(nuevos))
        _avisar(self.dispositivo_id, cargas, len(nuevos))
        asistencia.avisar_cambios(checadas)
        return True

    # ── Estado en la base ────────────────────────────────────────────────

    def _latido(self) -> None:
        ahora = time.monotonic()
        if ahora - self._ultimo_latido < LATIDO_CADA_S:
            return
        self._ultimo_latido = ahora
        # El estado se reafirma con cada latido: si el supervisor reinició esta
        # escucha, el hilo anterior pudo escribir DESCONECTADO después de que
        # este escribiera CONECTADO.
        DispositivoHikvision.query.filter_by(id=self.dispositivo_id).update(
            {**_SIN_TOCAR_UPDATED_AT, 'escucha_latido': _ahora(), 'escucha_estado': 'CONECTADO'},
            synchronize_session=False,
        )
        db.session.commit()

    def _marcar(self, estado: str, error: str | None) -> None:
        try:
            db.session.rollback()
            cambios = {**_SIN_TOCAR_UPDATED_AT,
                       'escucha_estado': estado, 'escucha_error': (error or None)}
            if estado == 'CONECTADO':
                cambios['escucha_latido'] = _ahora()
                self._ultimo_latido = time.monotonic()
            DispositivoHikvision.query.filter_by(id=self.dispositivo_id).update(
                cambios, synchronize_session=False,
            )
            db.session.commit()
        except Exception:  # noqa: BLE001 — si la base no está, el próximo intento lo reescribe
            logger.exception('Hikvision escucha disp=%s: no se pudo guardar el estado', self.dispositivo_id)
            db.session.rollback()
        try:
            from app.realtime import emit_to_role
            emit_to_role(ROLES_AVISO, 'hikvision:changed', {
                'dispositivo_id': self.dispositivo_id, 'action': f'escucha_{estado.lower()}',
            })
        except Exception:  # noqa: BLE001
            pass


def _avisar(dispositivo_id: int, eventos: list[dict], total: int) -> None:
    try:
        from app.realtime import emit_to_role
        emit_to_role(ROLES_AVISO, 'hikvision:evento', {
            'dispositivo_id': dispositivo_id, 'total': total, 'eventos': eventos,
        })
    except Exception:  # noqa: BLE001 — sin navegador avisado, el evento ya quedó guardado
        logger.warning('Hikvision escucha disp=%s: no se pudo avisar por Socket.IO', dispositivo_id)


def _firma(d: DispositivoHikvision) -> tuple:
    """Lo que obliga a reconectar si cambia: dirección o credenciales.

    No se usa `updated_at`: cambia con cualquier escritura de la fila (el
    estado de la última prueba, el firmware cacheado…) y reiniciaría la
    escucha sin motivo. La contraseña entra como hash para no conservar otra
    copia en claro en memoria.
    """
    return (d.host, d.puerto, d.usuario,
            hashlib.sha256((d.password or '').encode('utf-8')).hexdigest())


class Supervisor:
    """Arranca, reinicia y detiene una `EscuchaLector` por lector activo."""

    def __init__(self, app):
        self.app = app
        self.escuchas: dict[int, tuple[EscuchaLector, tuple]] = {}
        self._salir = threading.Event()

    def correr(self) -> None:
        signal.signal(signal.SIGTERM, lambda *_: self._salir.set())
        signal.signal(signal.SIGINT, lambda *_: self._salir.set())
        logger.info('Hikvision escucha: supervisor iniciado')
        while not self._salir.is_set():
            try:
                self.revisar()
            except Exception:  # noqa: BLE001 — una base caída no debe tumbar el supervisor
                logger.exception('Hikvision escucha: fallo al revisar los lectores')
            self._salir.wait(REVISION_CADA_S)
        self.detener_todo()

    def revisar(self) -> None:
        with self.app.app_context():
            try:
                activos = {d.id: _firma(d) for d in DispositivoHikvision.query.filter_by(activo=True)}
            finally:
                db.session.remove()

        for disp_id, (escucha, firma) in list(self.escuchas.items()):
            if disp_id not in activos or activos[disp_id] != firma or not escucha.vivo():
                escucha.detener()
                del self.escuchas[disp_id]

        for disp_id, firma in activos.items():
            if disp_id not in self.escuchas:
                escucha = EscuchaLector(self.app, disp_id)
                escucha.iniciar()
                self.escuchas[disp_id] = (escucha, firma)

    def detener_todo(self) -> None:
        for escucha, _ in self.escuchas.values():
            escucha.detener()
        for escucha, _ in self.escuchas.values():
            escucha._hilo.join(timeout=10)
        self.escuchas.clear()
        logger.info('Hikvision escucha: supervisor detenido')
