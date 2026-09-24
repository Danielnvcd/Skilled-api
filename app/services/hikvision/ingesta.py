"""Guardar en el ERP los eventos del lector, sin duplicar.

Lo usan dos caminos que deben dar exactamente el mismo resultado:

  · el proceso de escucha (`escucha.py`), cada vez que el lector avisa;
  · el botón «Traer eventos» de la pantalla, cuando la escucha no corre.

Ambos llaman a `ponerse_al_dia()`, que pide al lector todo lo posterior al
último serial guardado. Que sea idempotente (única por dispositivo + época +
serial) es lo que permite llamarla de más sin miedo: el lector reenvía su
historial al reconectar, y dos avisos seguidos pueden pedir el mismo tramo.

La época existe porque el serial del lector NO es eterno: vuelve a 1 tras un
reset de fábrica, un borrado del historial o un cambio de equipo. Ver
`detectar_reinicio()` y `abrir_epoca()`.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import DispositivoHikvision, EventoHikvision, SyncEmpleadoHikvision

from . import eventos as svc_eventos

logger = logging.getLogger(__name__)

# Primera carga de un lector (tabla vacía): cuánto historial se trae. Más no
# aporta a la pantalla de actividad y la fase de asistencia tendrá su propio
# punto de arranque.
DIAS_HISTORIAL_INICIAL = 31
MAX_EVENTOS_HISTORIAL_INICIAL = 3000


def epoca_actual(dispositivo_id: int) -> int:
    return db.session.query(DispositivoHikvision.epoca_eventos).filter(
        DispositivoHikvision.id == dispositivo_id,
    ).scalar() or 1


def ultimo_serial(dispositivo_id: int, epoca: int | None = None) -> int:
    """Mayor serial guardado en la época (la actual si no se indica)."""
    epoca = epoca or epoca_actual(dispositivo_id)
    return db.session.query(func.max(EventoHikvision.serial_no)).filter(
        EventoHikvision.dispositivo_id == dispositivo_id,
        EventoHikvision.epoca == epoca,
    ).scalar() or 0


def ultima_fecha(dispositivo_id: int):
    """Instante del evento más reciente guardado (de cualquier época), o None."""
    return db.session.query(func.max(EventoHikvision.fecha_hora)).filter(
        EventoHikvision.dispositivo_id == dispositivo_id,
    ).scalar()


def ponerse_al_dia(cli, dispositivo_id: int) -> list[EventoHikvision]:
    """Trae del lector lo que falte y lo guarda. Devuelve SOLO lo nuevo.

    No hace commit: lo decide quien llama, para que el guardado y cualquier
    otra cosa de la misma operación (estado de la escucha, bitácora) vayan
    juntos.
    """
    epoca = epoca_actual(dispositivo_id)
    ultimo = ultimo_serial(dispositivo_id, epoca)
    if ultimo:
        crudos = svc_eventos.desde_serial(cli, ultimo)
    else:
        # Época nueva (primera carga, o el lector reinició su numeración):
        # se trae por fechas, y solo lo posterior a lo ya guardado para no
        # duplicar eventos de la época anterior con serial distinto.
        crudos = _historial_inicial(cli, despues_de=ultima_fecha(dispositivo_id))
    return guardar(dispositivo_id, crudos, epoca=epoca)


def _historial_inicial(cli, despues_de=None) -> list[dict]:
    """Últimos días del lector (o lo posterior a `despues_de`).

    Las fechas se piden en la zona del EQUIPO, que se lee de su reloj.
    """
    ahora = datetime.fromisoformat(cli.hora_dispositivo()['hora_local'])
    inicio = (ahora - timedelta(days=DIAS_HISTORIAL_INICIAL)).replace(microsecond=0)
    if despues_de is not None:
        if despues_de.tzinfo is None:
            despues_de = despues_de.replace(tzinfo=timezone.utc)
        inicio = max(inicio, (despues_de + timedelta(seconds=1)).astimezone(ahora.tzinfo))
    fin = (ahora + timedelta(minutes=1)).replace(microsecond=0)
    return svc_eventos.recientes(
        cli, inicio.replace(microsecond=0).isoformat(), fin.isoformat(),
        limite=MAX_EVENTOS_HISTORIAL_INICIAL,
    )


def detectar_reinicio(cli, dispositivo_id: int) -> dict | None:
    """¿El lector reinició su numeración de eventos? Devuelve la evidencia o None.

    Se reinicia si se resetea de fábrica, se borra su historial o se cambia el
    equipo. Síntoma: su evento MÁS RECIENTE es posterior al último guardado,
    pero con un serial MENOR. Si nada más se revisara el serial, un lector sin
    eventos nuevos parecería reiniciado; por eso se exige que sea más nuevo.

    Cuesta dos consultas al equipo (hora + último evento): se llama al
    conectar y cada 30 min, no en cada evento.
    """
    ultimo = ultimo_serial(dispositivo_id)
    guardada = ultima_fecha(dispositivo_id)
    if not ultimo or guardada is None:
        return None
    if guardada.tzinfo is None:
        guardada = guardada.replace(tzinfo=timezone.utc)

    ahora = datetime.fromisoformat(cli.hora_dispositivo()['hora_local'])
    inicio = (ahora - timedelta(days=DIAS_HISTORIAL_INICIAL)).replace(microsecond=0)
    fin = (ahora + timedelta(minutes=1)).replace(microsecond=0)
    recientes = svc_eventos.recientes(cli, inicio.isoformat(), fin.isoformat(), limite=1)
    if not recientes:
        return None
    del_equipo = recientes[0]
    try:
        serial = int(del_equipo.get('serialNo') or 0)
        cuando = datetime.fromisoformat(del_equipo.get('time') or '')
    except (TypeError, ValueError):
        return None
    if cuando.tzinfo is None:
        return None
    if serial < ultimo and cuando > guardada:
        return {'serial_equipo': serial, 'ultimo_guardado': ultimo,
                'hora_equipo': cuando.isoformat()}
    return None


def abrir_epoca(dispositivo_id: int) -> int:
    """Empieza una época nueva de seriales. Devuelve su número. No hace commit."""
    d = db.session.get(DispositivoHikvision, dispositivo_id)
    d.epoca_eventos = (d.epoca_eventos or 1) + 1
    db.session.flush()
    logger.warning('Hikvision ingesta disp=%s: época de seriales %s', dispositivo_id, d.epoca_eventos)
    return d.epoca_eventos


def guardar(dispositivo_id: int, crudos: list[dict], *, epoca: int | None = None) -> list[EventoHikvision]:
    """Inserta los eventos que aún no existan. Devuelve los insertados, en orden."""
    epoca = epoca or epoca_actual(dispositivo_id)
    normalizados = []
    for crudo in crudos:
        e = svc_eventos.normalizar(crudo)
        fecha = _fecha(e['fecha_hora'])
        if e['serial'] is None or fecha is None:
            # Sin serial no se puede deduplicar y sin fecha no se puede
            # mostrar ni contar: se descarta en vez de guardar basura.
            logger.warning('Hikvision: evento sin serial o fecha descartado: %r', crudo)
            continue
        normalizados.append((int(e['serial']), fecha, e))
    if not normalizados:
        return []

    # Un mismo tramo puede venir repetido (el lector no ordena, dos avisos
    # piden lo mismo): se deduplica dentro del lote y contra la tabla.
    por_serial = {s: (f, e) for s, f, e in normalizados}
    existentes = {
        s for (s,) in db.session.query(EventoHikvision.serial_no).filter(
            EventoHikvision.dispositivo_id == dispositivo_id,
            EventoHikvision.epoca == epoca,
            EventoHikvision.serial_no.in_(list(por_serial)),
        )
    }
    trabajadores = _trabajadores_por_numero(dispositivo_id)

    nuevos = []
    for serial in sorted(set(por_serial) - existentes):
        fecha, e = por_serial[serial]
        nuevos.append(EventoHikvision(
            dispositivo_id=dispositivo_id,
            epoca=epoca,
            serial_no=serial,
            fecha_hora=fecha,
            hora_local=e['fecha_hora'][:32],
            major=e['major'],
            minor=e['minor'],
            tipo=e['tipo'],
            employee_no=e['employee_no'] or None,
            nombre_en_equipo=(e['nombre_en_equipo'] or None),
            trabajador_id=trabajadores.get(e['employee_no']),
            modo_verificacion=(e['modo_verificacion'] or None),
            cubrebocas=e['cubrebocas'],
            captura=e['captura'],
        ))
    if not nuevos:
        return []

    # Savepoint: si otro proceso insertó el mismo tramo entre la consulta y
    # aquí (la escucha y el botón manual a la vez), se cae solo este bloque y
    # se reintenta uno por uno, sin tumbar la transacción de quien llama.
    try:
        with db.session.begin_nested():
            db.session.add_all(nuevos)
    except IntegrityError:
        guardados = []
        for ev in nuevos:
            try:
                with db.session.begin_nested():
                    db.session.add(ev)
                guardados.append(ev)
            except IntegrityError:
                pass
        return guardados
    return nuevos


def _fecha(valor: str | None):
    try:
        fecha = datetime.fromisoformat(valor or '')
    except ValueError:
        return None
    # Sin desfase no se sabe qué instante es: se descarta antes que adivinar.
    # Se guarda en UTC: Postgres lo haría igual, pero SQLite (tests) descarta
    # la zona y compararía horas de la oficina contra horas UTC.
    return fecha.astimezone(timezone.utc) if fecha.tzinfo else None


def _trabajadores_por_numero(dispositivo_id: int) -> dict[str, int]:
    return {
        f.employee_no_remoto: f.trabajador_id
        for f in SyncEmpleadoHikvision.query.filter_by(dispositivo_id=dispositivo_id)
    }
