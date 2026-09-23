"""Guardar en el ERP los eventos del lector, sin duplicar.

Lo usan dos caminos que deben dar exactamente el mismo resultado:

  · el proceso de escucha (`escucha.py`), cada vez que el lector avisa;
  · el botón «Traer eventos» de la pantalla, cuando la escucha no corre.

Ambos llaman a `ponerse_al_dia()`, que pide al lector todo lo posterior al
último serial guardado. Que sea idempotente (única por dispositivo + serial)
es lo que permite llamarla de más sin miedo: el lector reenvía su historial al
reconectar, y dos avisos seguidos pueden pedir el mismo tramo.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import EventoHikvision, SyncEmpleadoHikvision

from . import eventos as svc_eventos

logger = logging.getLogger(__name__)

# Primera carga de un lector (tabla vacía): cuánto historial se trae. Más no
# aporta a la pantalla de actividad y la fase de asistencia tendrá su propio
# punto de arranque.
DIAS_HISTORIAL_INICIAL = 31
MAX_EVENTOS_HISTORIAL_INICIAL = 3000


def ultimo_serial(dispositivo_id: int) -> int:
    return db.session.query(func.max(EventoHikvision.serial_no)).filter(
        EventoHikvision.dispositivo_id == dispositivo_id,
    ).scalar() or 0


def ponerse_al_dia(cli, dispositivo_id: int) -> list[EventoHikvision]:
    """Trae del lector lo que falte y lo guarda. Devuelve SOLO lo nuevo.

    No hace commit: lo decide quien llama, para que el guardado y cualquier
    otra cosa de la misma operación (estado de la escucha, bitácora) vayan
    juntos.
    """
    ultimo = ultimo_serial(dispositivo_id)
    if ultimo:
        crudos = svc_eventos.desde_serial(cli, ultimo)
    else:
        crudos = _historial_inicial(cli)
    return guardar(dispositivo_id, crudos)


def _historial_inicial(cli) -> list[dict]:
    """Últimos días del lector, para no arrancar con la pantalla vacía.

    Las fechas se piden en la zona del EQUIPO, que se lee de su reloj.
    """
    ahora = datetime.fromisoformat(cli.hora_dispositivo()['hora_local'])
    inicio = (ahora - timedelta(days=DIAS_HISTORIAL_INICIAL)).replace(microsecond=0)
    fin = (ahora + timedelta(minutes=1)).replace(microsecond=0)
    return svc_eventos.recientes(
        cli, inicio.isoformat(), fin.isoformat(), limite=MAX_EVENTOS_HISTORIAL_INICIAL,
    )


def guardar(dispositivo_id: int, crudos: list[dict]) -> list[EventoHikvision]:
    """Inserta los eventos que aún no existan. Devuelve los insertados, en orden."""
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
            EventoHikvision.serial_no.in_(list(por_serial)),
        )
    }
    trabajadores = _trabajadores_por_numero(dispositivo_id)

    nuevos = []
    for serial in sorted(set(por_serial) - existentes):
        fecha, e = por_serial[serial]
        nuevos.append(EventoHikvision(
            dispositivo_id=dispositivo_id,
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
