"""Sincronizaciones de empleados en segundo plano.

La API crea la tarea y responde al instante (202); el trabajador de este
módulo, que vive dentro del proceso de escucha (`flask hikvision escuchar`),
la ejecuta con la misma lógica que la API usa para tandas chicas
(`sincronizar.sincronizar_lote`) y avisa el avance por Socket.IO:

    hikvision:tarea  {id, dispositivo_id, estado, procesados, total}

Si el proceso muere a media tarea, al volver la marca como ERROR
(«interrumpida») en vez de dejarla EN_CURSO para siempre. Lo que alcanzó a
sincronizarse ya quedó guardado en `hikvision_sync_empleado`, así que
relanzarla es seguro: sincronizar es idempotente (el usuario existente se
actualiza, no se duplica).
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone

from app.extensions import db
from app.models import AuditLog, DispositivoHikvision, TareaHikvision, User

from .errores import ErrorHikvision

logger = logging.getLogger(__name__)

# Cada cuánto el trabajador busca tareas nuevas en la base. Es una consulta
# barata a Postgres, no al lector.
REVISAR_CADA_S = 2
# Una tarea EN_CURSO se da por abandonada si lleva más de esto, o de
# `SEGUNDOS_POR_EMPLEADO` × su tamaño si es mayor (una tanda de 500 tarda
# legítimamente ~30 min a ~3.6 s por persona).
INTERRUMPIDA_TRAS = timedelta(minutes=30)
SEGUNDOS_POR_EMPLEADO = 15
# Cada cuánto se buscan tareas abandonadas.
RECUPERAR_CADA_S = 60


def _ahora():
    return datetime.now(timezone.utc)


def encolar(dispositivo, trabajador_ids, *, creado_por_id=None) -> TareaHikvision:
    """Crea la tarea PENDIENTE. No hace commit."""
    ids = sorted({int(i) for i in trabajador_ids})
    tarea = TareaHikvision(
        dispositivo_id=dispositivo.id, tipo='SINCRONIZAR', trabajador_ids=ids,
        estado='PENDIENTE', total=len(ids), procesados=0, creado_por_id=creado_por_id,
    )
    db.session.add(tarea)
    db.session.flush()
    return tarea


def avisar(tarea: TareaHikvision) -> None:
    try:
        from app.realtime import emit_to_role
        emit_to_role(['admin', 'super_admin'], 'hikvision:tarea', {
            'id': tarea.id, 'dispositivo_id': tarea.dispositivo_id, 'estado': tarea.estado,
            'procesados': tarea.procesados, 'total': tarea.total,
        })
    except Exception:  # noqa: BLE001 — el progreso es cosmético; el resultado queda en la base
        logger.warning('Hikvision tareas: no se pudo avisar el avance de %s', tarea.id)


def recuperar_interrumpidas() -> int:
    """Marca como ERROR las tareas EN_CURSO abandonadas. No hace commit."""
    ahora = _ahora()
    n = 0
    for t in TareaHikvision.query.filter_by(estado='EN_CURSO').all():
        iniciada = t.iniciada_en
        if iniciada is not None and iniciada.tzinfo is None:
            iniciada = iniciada.replace(tzinfo=timezone.utc)
        margen = max(INTERRUMPIDA_TRAS, timedelta(seconds=SEGUNDOS_POR_EMPLEADO * (t.total or 0)))
        if iniciada is None or iniciada < ahora - margen:
            t.estado = 'ERROR'
            t.error = ('Interrumpida: el servicio de lectores se reinició a media tarea. '
                       'Lo sincronizado hasta ese momento quedó guardado; puedes relanzarla.')
            t.terminada_en = _ahora()
            n += 1
    return n


def reclamar_siguiente() -> TareaHikvision | None:
    """Toma la tarea PENDIENTE más antigua y la marca EN_CURSO. Hace commit.

    `SKIP LOCKED` evita que dos trabajadores tomen la misma (en Postgres; en
    SQLite, el de las pruebas, se ignora y no hace falta).
    """
    tarea = (TareaHikvision.query.filter_by(estado='PENDIENTE')
             .order_by(TareaHikvision.id)
             .with_for_update(skip_locked=True)
             .first())
    if tarea is None:
        db.session.rollback()
        return None
    tarea.estado = 'EN_CURSO'
    tarea.iniciada_en = _ahora()
    db.session.commit()
    return tarea


def ejecutar(tarea: TareaHikvision) -> None:
    """Corre la sincronización de la tarea y guarda el resultado. Hace commit."""
    from .sincronizar import sincronizar_lote

    d = db.session.get(DispositivoHikvision, tarea.dispositivo_id)
    if d is None or not d.activo:
        _terminar(tarea, 'ERROR', error='El lector ya no existe o está desactivado.')
        return

    def al_avanzar(procesados, total):
        # Se guarda el avance Y lo sincronizado hasta aquí: si el proceso muere
        # a media tarea, lo hecho no se pierde.
        tarea.procesados = procesados
        db.session.commit()
        avisar(tarea)

    try:
        respuesta = sincronizar_lote(d, tarea.trabajador_ids, al_avanzar=al_avanzar)
    except ErrorHikvision as e:
        db.session.rollback()
        logger.warning('Hikvision tarea %s: %s', tarea.id, e.detalle)
        d.ultimo_estado = 'ERROR'
        d.ultimo_error = e.mensaje
        _terminar(tarea, 'ERROR', error=e.mensaje)
        return

    d.ultimo_estado = 'OK'
    d.ultimo_error = None
    tarea.resultados = respuesta['resultados']
    tarea.procesados = tarea.total
    _auditar(tarea, d, respuesta['resumen'])
    _terminar(tarea, 'TERMINADA')
    try:
        from app.realtime import emit_to_role
        emit_to_role(['admin', 'super_admin'], 'hikvision:changed', {
            'dispositivo_id': d.id, 'action': 'sincronizado',
        })
    except Exception:  # noqa: BLE001
        pass


def _terminar(tarea, estado, *, error=None) -> None:
    tarea.estado = estado
    tarea.error = (error or '')[:500] or None
    tarea.terminada_en = _ahora()
    db.session.commit()
    avisar(tarea)


def _auditar(tarea, d, resumen) -> None:
    """Bitácora con el usuario que pidió la tarea.

    `log_action` saca el usuario y la IP de la petición HTTP, que aquí no
    existe: se escribe directo, sin IP, con el usuario que la encoló.
    """
    usuario = db.session.get(User, tarea.creado_por_id) if tarea.creado_por_id else None
    db.session.add(AuditLog(
        user=(usuario.username if usuario else 'sistema')[:80],
        action=(f'Sincronizó {resumen["sincronizados"]} empleado(s) con el lector "{d.nombre}" '
                f'({d.host}:{d.puerto}); {resumen["fallidos"]} sin sincronizar '
                f'(tarea {tarea.id})')[:200],
        ip=None,
    ))


class TrabajadorTareas:
    """Hilo que ejecuta las tareas pendientes, una a la vez.

    Solo trabaja mientras su proceso tenga el candado de escucha
    (`es_lider()`): así, si por error corren dos procesos, uno solo toca los
    lectores.
    """

    def __init__(self, app, es_lider):
        self.app = app
        self.es_lider = es_lider
        self._detener = threading.Event()
        self._hilo = threading.Thread(target=self._correr, name='hikvision-tareas', daemon=True)

    def iniciar(self) -> None:
        self._hilo.start()

    def detener(self) -> None:
        self._detener.set()
        self._hilo.join(timeout=5)

    def _recuperar(self) -> None:
        with self.app.app_context():
            try:
                if recuperar_interrumpidas():
                    db.session.commit()
            except Exception:  # noqa: BLE001
                db.session.rollback()
                logger.exception('Hikvision tareas: no se pudieron recuperar las interrumpidas')
            finally:
                db.session.remove()

    def _correr(self) -> None:
        ultima_recuperacion = 0.0
        while not self._detener.wait(REVISAR_CADA_S):
            if not self.es_lider():
                continue
            if time.monotonic() - ultima_recuperacion >= RECUPERAR_CADA_S:
                ultima_recuperacion = time.monotonic()
                self._recuperar()
            with self.app.app_context():
                tarea_id = None
                try:
                    tarea = reclamar_siguiente()
                    if tarea is not None:
                        tarea_id = tarea.id
                        logger.info('Hikvision tareas: ejecutando %s (%d empleados)',
                                    tarea.id, tarea.total)
                        avisar(tarea)
                        ejecutar(tarea)
                except Exception:  # noqa: BLE001 — una tarea rota no detiene a las demás
                    db.session.rollback()
                    logger.exception('Hikvision tareas: error inesperado en la tarea %s', tarea_id)
                    self._marcar_rota(tarea_id)
                finally:
                    db.session.remove()

    @staticmethod
    def _marcar_rota(tarea_id) -> None:
        """Que un error inesperado no deje la tarea EN_CURSO para siempre."""
        if tarea_id is None:
            return
        try:
            tarea = db.session.get(TareaHikvision, tarea_id)
            if tarea is not None and tarea.estado == 'EN_CURSO':
                _terminar(tarea, 'ERROR', error='Error interno al sincronizar; revisa el log.')
        except Exception:  # noqa: BLE001
            db.session.rollback()
