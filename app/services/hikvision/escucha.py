"""Escucha en tiempo real de los lectores: `flask hikvision escuchar`.

Por qué un proceso aparte y no dentro de la API: cada lector necesita una
conexión abierta TODO el tiempo, y gunicorn corre varios workers que se
reciclan. Dentro de ellos habría una escucha por worker (eventos duplicados) o
ninguna tras un reinicio. Un proceso dedicado, uno solo, lo resuelve.

Flujo por lector (un hilo cada uno):

    conectar
      → verificar el equipo (¿otro número de serie? ¿firmware nuevo? ¿DHCP?)
      → ¿reinició su numeración de eventos? → época nueva
      → ponerse al día por serial → abrir alertStream
        cada parte recibida        → renueva el latido (máx. cada 20 s)
        aviso de acceso (major 5)  → si su serial es nuevo: ponerse al día,
                                     guardar, avisar al navegador por Socket.IO
        cada 5 min                 → repaso por si un aviso se perdió
        cada 30 min                → vigilar reloj y reinicio de seriales
        corte / silencio de 90 s   → marcar DESCONECTADO y reconectar con
                                     espera creciente (5 s … 60 s)
        credenciales rechazadas    → esperar mucho más, o detenerse del todo
                                     si quedan ≤ 2 intentos antes del bloqueo

El stream solo AVISA; los datos siempre se toman de AcsEvent por serial (ver
`eventos.py`). Así, reconectar nunca pierde eventos: se pide desde el último
guardado. Y el historial que el lector reenvía al reconectar no cuesta nada:
sus seriales ya son conocidos y se ignoran sin llamar al equipo.

El supervisor además:
  · toma un candado en Redis para que corra UNA sola escucha aunque por error
    se levanten dos (réplica de más, desarrollo apuntando al mismo lector);
  · alerta a los admins si un lector lleva > 5 min desconectado;
  · ejecuta las tareas de sincronización en segundo plano (`tareas.py`);
  · purga una vez al día lo que excede la retención (`retencion.py`).

El aviso al navegador viaja por Redis (`message_queue` de Flask-SocketIO), así
llega a los workers de la API aunque este proceso no atienda conexiones.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import signal
import socket
import threading
import time
from datetime import datetime, timedelta, timezone

from app.extensions import db
from app.models import DispositivoHikvision

from . import asistencia, ingesta, vigilancia
from .client import ClienteHikvision
from .errores import ErrorAutenticacion, ErrorDispositivo, ErrorHikvision

logger = logging.getLogger(__name__)

ESPERA_INICIAL_S = 5
ESPERA_MAX_S = 60
# Tras un 401 real (contraseña cambiada en el lector) NO se reintenta al ritmo
# normal: el lector bloquea la cuenta tras 5 intentos fallidos por defecto
# (`illegalLoginLock`) y la cuenta es la misma que usan las personas.
ESPERA_AUTENTICACION_S = 600
# Con tan pocos intentos restantes ya no se arriesga ninguno más: la escucha
# se detiene hasta que alguien corrija la contraseña en el ERP (el supervisor
# la reinicia al ver el cambio).
INTENTOS_MINIMOS = 2
# Cada cuánto se escribe el latido en la base. Más seguido solo es escritura
# de más: el umbral para darla por caída es de 90 s.
LATIDO_CADA_S = 20
# Repaso de seguridad aunque no llegue aviso (ver `repasar_si_toca`).
REPASO_CADA_S = 300
# Vigilancia de reloj y de reinicio de seriales (ver `vigilar_si_toca`).
VIGILANCIA_CADA_S = 1800
# Desfase de reloj a partir del cual se alerta. Un minuto ya mueve checadas.
DESFASE_ALERTA_S = 60
# Segundos sin conexión antes de alertar a los admins.
DESCONEXION_ALERTA_S = 300
# Cada cuánto el supervisor revisa altas, bajas y cambios de lectores.
REVISION_CADA_S = 30
# Al navegador se le mandan los últimos N eventos nuevos, no todos: tras una
# reconexión larga pueden ser cientos, y la pantalla igual recarga su lista.
MAX_EVENTOS_POR_AVISO = 20

ROLES_AVISO = ['admin', 'super_admin']

# Candado de instancia única (Redis). El TTL cubre varias revisiones del
# supervisor: si el proceso muere, otro puede tomarlo en ≤ 90 s.
LLAVE_LIDER = 'hikvision:escucha:lider'
TTL_LIDER_S = 90

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
        self._ultima_vigilancia = time.monotonic()
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
                    espera = self._tras_rechazo_de_credenciales(e)
                except ErrorHikvision as e:
                    if not self._detener.is_set():
                        logger.warning('Hikvision escucha disp=%s: %s', self.dispositivo_id, e.detalle)
                    self._marcar('DESCONECTADO', e.mensaje)
                except Exception:  # noqa: BLE001 — el hilo nunca debe morir en silencio
                    logger.exception('Hikvision escucha disp=%s: error inesperado', self.dispositivo_id)
                    self._marcar('DESCONECTADO', 'Error interno en la escucha; se reintentará.')
                finally:
                    db.session.remove()
            if espera is None:
                # Credenciales al borde del bloqueo: se espera a que el
                # supervisor detenga esta escucha (cambio de contraseña).
                self._detener.wait()
                break
            if self._detener.wait(espera):
                break
            if espera < ESPERA_MAX_S:
                espera = min(espera * 2, ESPERA_MAX_S)
        with self.app.app_context():
            self._marcar('DESCONECTADO', None, registrar=False)
            db.session.remove()

    def _tras_rechazo_de_credenciales(self, e: ErrorAutenticacion) -> int | None:
        """Qué hacer tras un 401 real. Devuelve la espera, o None = detenerse."""
        self._marcar('DESCONECTADO', e.mensaje, tipo_suceso='AUTENTICACION')
        try:
            d = db.session.get(DispositivoHikvision, self.dispositivo_id)
            if d is not None:
                vigilancia.alertar(
                    d, 'credenciales', 'el lector rechazó la contraseña',
                    f'{e.mensaje} La escucha en tiempo real está detenida; corrige el '
                    'usuario o la contraseña del lector en el ERP.',
                )
                db.session.commit()
        except Exception:  # noqa: BLE001
            db.session.rollback()
            logger.exception('Hikvision escucha disp=%s: no se pudo alertar', self.dispositivo_id)

        if e.bloqueado:
            espera = (e.segundos_bloqueo or 1800) + 60
            logger.warning('Hikvision escucha disp=%s: cuenta bloqueada, se reintenta en %d s',
                           self.dispositivo_id, espera)
            return espera
        if e.intentos_restantes is not None and e.intentos_restantes <= INTENTOS_MINIMOS:
            logger.error('Hikvision escucha disp=%s: quedan %s intento(s); la escucha se detiene '
                         'hasta que cambie la contraseña', self.dispositivo_id, e.intentos_restantes)
            return None
        logger.warning('Hikvision escucha disp=%s: %s — se reintenta en %d s',
                       self.dispositivo_id, e.detalle, ESPERA_AUTENTICACION_S)
        return ESPERA_AUTENTICACION_S

    # ── Una conexión ─────────────────────────────────────────────────────

    def _sesion(self) -> None:
        d = db.session.get(DispositivoHikvision, self.dispositivo_id)
        if d is None or not d.activo:
            self._detener.set()
            return

        # Dos clientes: el del stream queda bloqueado leyendo; las consultas
        # por serial van por otro para no competir por la misma conexión.
        with ClienteHikvision.desde_dispositivo(d) as cli_consultas:
            self.verificar_equipo(cli_consultas, d)
            self.verificar_reinicio(cli_consultas)
            self.max_serial = max(self.max_serial, ingesta.ultimo_serial(d.id))
            self._atender_novedades(cli_consultas)

            self._cli_stream = ClienteHikvision.desde_dispositivo(d)
            try:
                self._marcar('CONECTADO', None)
                self._avisar_reconexion()
                logger.info('Hikvision escucha disp=%s: conectado', self.dispositivo_id)
                for tipo, cuerpo in self._cli_stream.flujo_alertas():
                    if self._detener.is_set():
                        return
                    self._latido()
                    if 'json' in tipo:
                        self.atender_parte(cli_consultas, cuerpo)
                    self.repasar_si_toca(cli_consultas)
                    self.vigilar_si_toca(cli_consultas)
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
            # Lo normal: historial reenviado al reconectar, ya guardado. Pero
            # si el aviso es MÁS NUEVO que lo guardado, el lector reinició su
            # numeración: sin revisarlo aquí, sus eventos se ignorarían hasta
            # la vigilancia de 30 min.
            if self._aviso_mas_nuevo_que_lo_guardado(datos.get('dateTime')):
                if self.verificar_reinicio(cli):
                    self._atender_novedades(cli)
            return
        if self._atender_novedades(cli):
            # Aunque la consulta no haya traído nada (el aviso pudo llegar un
            # instante antes de que el evento fuera consultable), este serial
            # ya se atendió: el repaso periódico recoge lo que haya quedado.
            self.max_serial = max(self.max_serial, serial)

    def _aviso_mas_nuevo_que_lo_guardado(self, fecha_aviso: str | None) -> bool:
        try:
            aviso = datetime.fromisoformat(fecha_aviso or '')
        except ValueError:
            return False
        if aviso.tzinfo is None:
            return False
        guardada = ingesta.ultima_fecha(self.dispositivo_id)
        if guardada is None:
            return False
        if guardada.tzinfo is None:
            guardada = guardada.replace(tzinfo=timezone.utc)
        # Margen para no confundir un reenvío del mismo segundo.
        return aviso > guardada + timedelta(seconds=60)

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

    def vigilar_si_toca(self, cli) -> None:
        """Cada 30 min: reloj del lector y reinicio de seriales (2–3 consultas)."""
        ahora = time.monotonic()
        if ahora - self._ultima_vigilancia < VIGILANCIA_CADA_S:
            return
        self._ultima_vigilancia = ahora
        try:
            self.vigilar_reloj(cli)
            if self.verificar_reinicio(cli):
                self._atender_novedades(cli)
        except ErrorAutenticacion:
            raise
        except ErrorHikvision as e:
            db.session.rollback()
            logger.warning('Hikvision escucha disp=%s: vigilancia falló: %s',
                           self.dispositivo_id, e.detalle)

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
        avisar_puerta(self.dispositivo_id, nuevos)
        asistencia.avisar_cambios(checadas)
        return True

    # ── Vigilancia del equipo ────────────────────────────────────────────

    def verificar_equipo(self, cli, d: DispositivoHikvision) -> None:
        """Al conectar: ¿es el mismo equipo? ¿cambió el firmware? ¿sigue con IP fija?

        Otro número de serie en la misma dirección significa que se cambió el
        lector (o que otro equipo tomó su IP): su numeración de eventos no
        tiene nada que ver con la guardada, así que se abre época nueva.
        """
        info = cli.info_dispositivo()
        serie, firmware = info.get('numero_serie') or '', info.get('firmware') or ''

        if d.numero_serie and serie and serie != d.numero_serie:
            detalle = f'número de serie {d.numero_serie} → {serie}'
            vigilancia.registrar(d.id, 'EQUIPO_CAMBIADO', detalle)
            vigilancia.alertar(
                d, 'equipo_cambiado', 'se detectó otro equipo',
                f'En {d.host} responde un lector distinto ({detalle}). Si fue un cambio '
                'de equipo, vuelve a sincronizar a los empleados: el nuevo no los tiene.',
            )
            ingesta.abrir_epoca(d.id)
            self.max_serial = 0
        elif d.firmware and firmware and firmware != d.firmware:
            detalle = f'firmware {d.firmware} → {firmware}'
            vigilancia.registrar(d.id, 'FIRMWARE_CAMBIADO', detalle)
            vigilancia.alertar(
                d, 'firmware', 'se actualizó el firmware',
                f'El lector pasó de {detalle}. Conviene revisar que la sincronización '
                'y la actividad sigan funcionando: el ISAPI puede cambiar entre versiones.',
            )

        d.numero_serie = serie or d.numero_serie
        d.firmware = firmware or d.firmware
        d.modelo = info.get('modelo') or d.modelo

        try:
            red = cli.red()
        except ErrorDispositivo:
            red = None   # modelo sin ese endpoint: no se vigila
        if red and red.get('direccionamiento') and red['direccionamiento'] != 'static':
            vigilancia.registrar(d.id, 'IP_DINAMICA', f"direccionamiento {red['direccionamiento']}")
            vigilancia.alertar(
                d, 'ip_dinamica', 'el lector no tiene IP fija',
                f"El lector obtiene su IP por {red['direccionamiento']}. Si el router le da "
                'otra dirección, el ERP perderá la conexión. Configúrale una IP fija o una '
                'reserva DHCP.',
            )
        db.session.commit()

    def verificar_reinicio(self, cli) -> bool:
        """Si el lector reinició su numeración, abre época nueva. True si lo hizo."""
        evidencia = ingesta.detectar_reinicio(cli, self.dispositivo_id)
        if not evidencia:
            return False
        d = db.session.get(DispositivoHikvision, self.dispositivo_id)
        detalle = (f"el último evento del lector tiene serial {evidencia['serial_equipo']} "
                   f"y el último guardado {evidencia['ultimo_guardado']}")
        vigilancia.registrar(d.id, 'SERIALES_REINICIADOS', detalle)
        vigilancia.alertar(
            d, 'seriales', 'el lector reinició su numeración de eventos',
            f'Probablemente se reseteó o se borró su historial ({detalle}). El ERP abrió '
            'una numeración nueva y sigue registrando; revisa que los empleados sigan '
            'dados de alta en el lector.',
        )
        ingesta.abrir_epoca(d.id)
        db.session.commit()
        self.max_serial = 0
        return True

    def vigilar_reloj(self, cli) -> None:
        hora = cli.hora_dispositivo()
        desfase = vigilancia.desfase_segundos(hora.get('hora_local', ''), _ahora())
        if desfase is None or abs(desfase) <= DESFASE_ALERTA_S:
            return
        d = db.session.get(DispositivoHikvision, self.dispositivo_id)
        sentido = 'adelantado' if desfase > 0 else 'atrasado'
        detalle = f'{abs(desfase)} s {sentido} (modo {hora.get("modo") or "?"})'
        vigilancia.registrar(d.id, 'RELOJ_DESFASADO', detalle)
        vigilancia.alertar(
            d, 'reloj', 'el reloj del lector está desajustado',
            f'Va {detalle}. Las checadas se registran con la hora del lector: '
            'configúrale NTP o ajusta la hora desde la pestaña Equipo.',
        )
        db.session.commit()

    def _avisar_reconexion(self) -> None:
        """«Reconectado» solo si antes se avisó la desconexión."""
        try:
            if vigilancia.hubo_alerta_desconexion(self.dispositivo_id):
                d = db.session.get(DispositivoHikvision, self.dispositivo_id)
                vigilancia.alertar(d, 'reconectado', 'conexión recuperada',
                                   'La escucha en tiempo real volvió a conectarse y ya recuperó '
                                   'los eventos del tiempo sin conexión.')
                db.session.commit()
        except Exception:  # noqa: BLE001
            db.session.rollback()

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

    def _marcar(self, estado: str, error: str | None, *, registrar: bool = True,
                tipo_suceso: str | None = None) -> None:
        try:
            db.session.rollback()
            # Cada conexión se anota (es lo que cuenta las reconexiones del
            # panel de salud, aunque el proceso anterior muriera sin alcanzar a
            # escribir DESCONECTADO). La desconexión, solo si CAMBIA el estado:
            # durante una caída larga se reintenta cada minuto y cada intento
            # fallido no merece una fila.
            anterior = db.session.query(DispositivoHikvision.escucha_estado).filter(
                DispositivoHikvision.id == self.dispositivo_id,
            ).scalar()
            registrar = registrar and (tipo_suceso is not None or estado == 'CONECTADO'
                                       or anterior != estado)
            cambios = {**_SIN_TOCAR_UPDATED_AT,
                       'escucha_estado': estado, 'escucha_error': (error or None)}
            if estado == 'CONECTADO':
                cambios['escucha_latido'] = _ahora()
                self._ultimo_latido = time.monotonic()
            DispositivoHikvision.query.filter_by(id=self.dispositivo_id).update(
                cambios, synchronize_session=False,
            )
            if registrar:
                vigilancia.registrar(self.dispositivo_id, tipo_suceso or estado, error)
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


def avisar_puerta(dispositivo_id: int, eventos) -> None:
    """Empuja al navegador el estado de la cerradura si el lote lo cambió.

    Sin esto la pantalla mostraba el estado del momento en que se cargó: al
    abrir, el relé cierra solo a los pocos segundos y nadie se enteraba sin
    recargar. El lector ya reporta ambos cambios (21/22) por el stream.
    """
    from .eventos import ultimo_estado_puerta
    estado = ultimo_estado_puerta(eventos)
    if estado is None:
        return
    try:
        from app.realtime import emit_to_role
        emit_to_role(ROLES_AVISO, 'hikvision:puerta', {'dispositivo_id': dispositivo_id, **estado})
    except Exception:  # noqa: BLE001 — la pantalla se corrige con la siguiente consulta
        logger.warning('Hikvision escucha disp=%s: no se pudo avisar el estado de la puerta',
                       dispositivo_id)


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
        self._id = f'{socket.gethostname()}:{os.getpid()}'
        self._es_lider = False
        self._ultima_purga = 0.0
        self._trabajador = None

    def correr(self) -> None:
        signal.signal(signal.SIGTERM, lambda *_: self._salir.set())
        signal.signal(signal.SIGINT, lambda *_: self._salir.set())
        logger.info('Hikvision escucha: supervisor iniciado (%s)', self._id)
        from .tareas import TrabajadorTareas
        self._trabajador = TrabajadorTareas(self.app, lambda: self._es_lider)
        self._trabajador.iniciar()
        while not self._salir.is_set():
            try:
                if self.tomar_liderazgo():
                    self.revisar()
                    self.vigilar_desconexiones()
                    self.purgar_si_toca()
                elif self.escuchas:
                    logger.warning('Hikvision escucha: otra instancia tomó el control; '
                                   'se detienen las escuchas')
                    self.detener_todo()
            except Exception:  # noqa: BLE001 — una base caída no debe tumbar el supervisor
                logger.exception('Hikvision escucha: fallo al revisar los lectores')
            self._salir.wait(REVISION_CADA_S)
        self.detener_todo()
        self._trabajador.detener()
        self.soltar_liderazgo()

    # ── Instancia única ──────────────────────────────────────────────────

    def tomar_liderazgo(self) -> bool:
        """Candado en Redis: solo una escucha habla con los lectores.

        Sin Redis se degrada a «siempre líder» con un aviso: mejor dos escuchas
        (no duplican eventos, la tabla es única) que ninguna.
        """
        from app.extensions import get_redis
        r = get_redis()
        if r is None:
            if not self._es_lider:
                logger.warning('Hikvision escucha: Redis no disponible; no se puede garantizar '
                               'una sola instancia')
            self._es_lider = True
            return True
        try:
            if r.set(LLAVE_LIDER, self._id, nx=True, ex=TTL_LIDER_S):
                self._es_lider = True
            elif r.get(LLAVE_LIDER) == self._id:
                r.expire(LLAVE_LIDER, TTL_LIDER_S)
                self._es_lider = True
            else:
                if self._es_lider or self.escuchas:
                    logger.info('Hikvision escucha: en espera; la escucha activa es %s',
                                r.get(LLAVE_LIDER))
                self._es_lider = False
        except Exception:  # noqa: BLE001 — Redis intermitente: conservar lo que había
            logger.warning('Hikvision escucha: no se pudo renovar el candado en Redis')
        return self._es_lider

    def soltar_liderazgo(self) -> None:
        from app.extensions import get_redis
        r = get_redis()
        try:
            if r is not None and r.get(LLAVE_LIDER) == self._id:
                r.delete(LLAVE_LIDER)
        except Exception:  # noqa: BLE001
            pass

    # ── Escuchas ─────────────────────────────────────────────────────────

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

    def vigilar_desconexiones(self) -> None:
        """Alerta si un lector activo lleva más de 5 min sin tiempo real."""
        with self.app.app_context():
            try:
                ahora = _ahora()
                for d in DispositivoHikvision.query.filter_by(activo=True):
                    if d.estado_escucha(ahora)['en_vivo']:
                        continue
                    desde = d.escucha_latido or d.created_at
                    if desde is None:
                        continue
                    if desde.tzinfo is None:
                        desde = desde.replace(tzinfo=timezone.utc)
                    sin_conexion = (ahora - desde).total_seconds()
                    if sin_conexion < DESCONEXION_ALERTA_S:
                        continue
                    minutos = int(sin_conexion // 60)
                    vigilancia.alertar(
                        d, 'desconectado', 'sin conexión en tiempo real',
                        f'La escucha lleva {minutos} min sin conexión con el lector'
                        + (f' ({d.escucha_error}).' if d.escucha_error else '.')
                        + ' Los accesos se siguen guardando en el lector y se recuperarán al '
                          'reconectar, pero la nómina y la actividad no se actualizan.',
                    )
                db.session.commit()
            except Exception:  # noqa: BLE001
                db.session.rollback()
                logger.exception('Hikvision escucha: fallo al vigilar desconexiones')
            finally:
                db.session.remove()

    def purgar_si_toca(self) -> None:
        """Retención: una vez al día, en el primer ciclo tras cumplirse."""
        if self._ultima_purga and time.monotonic() - self._ultima_purga < 86400:
            return
        self._ultima_purga = time.monotonic()
        from .retencion import purgar
        with self.app.app_context():
            try:
                resultado = purgar()
                db.session.commit()
                if any(resultado.values()):
                    logger.info('Hikvision retención: %s', resultado)
            except Exception:  # noqa: BLE001
                db.session.rollback()
                logger.exception('Hikvision escucha: fallo al purgar')
            finally:
                db.session.remove()

    def detener_todo(self) -> None:
        for escucha, _ in self.escuchas.values():
            escucha.detener()
        for escucha, _ in self.escuchas.values():
            escucha._hilo.join(timeout=10)
        self.escuchas.clear()
        logger.info('Hikvision escucha: escuchas detenidas')
