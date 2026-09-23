"""Checadas del lector → registros de horas → prenómina (fase 2).

El lector se vuelve una fuente más de checadas, junto al kiosko RFID, el QR
del móvil y la captura manual. Escribe en `RegistroDiarioHoras` igual que
ellas, así que la prenómina y `calcular_horas_productivas()` NO cambian:

    EventoHikvision (acceso permitido)          ← lo guarda la escucha
        ↓  primer acceso del día = entrada, último = salida
        ↓  redondeo a 30 min, igual que el QR
    RegistroDiarioHoras  (origen='LECTOR')      ← en el reporte OFICINA
        ↓  el admin cierra el reporte (TERMINADO), como cualquier proyecto
    Prenomina                                   ← intacta

Reglas (acordadas con el usuario):

  · Se pasa por el lector al entrar Y al salir. Un solo acceso en el día deja
    solo la entrada; la salida se captura a mano o llega con el siguiente.
  · El personal de oficina no pertenece a ningún proyecto y `ReporteSemanal`
    exige uno: se usa un proyecto «OFICINA» que se crea solo, y su reporte de
    la semana (martes a lunes) se abre con la primera checada.
  · Automático y en tiempo real, pero lo manual manda: si el registro del día
    ya existía por otra vía, o alguien cambió a mano la hora del lector
    (`origen='LECTOR_EDITADO'`), el lector no lo toca.
  · Nunca se escribe en una semana cuya prenómina ya se guardó o cerró.

Cada día se RECALCULA desde todos sus eventos guardados, no se va acumulando:
así es idempotente (reprocesar no duplica nada) y el orden en que lleguen los
eventos no importa.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta

from app.extensions import db
from app.models import (
    EventoHikvision,
    Prenomina,
    Proyecto,
    RegistroDiarioHoras,
    ReporteSemanal,
    SyncEmpleadoHikvision,
    Trabajador,
)
from app.utils import calcular_horas_productivas

logger = logging.getLogger(__name__)

NUMERO_PROYECTO_OFICINA = 'OFICINA'
NOMBRE_PROYECTO_OFICINA = 'Personal de oficina (lector biométrico)'

# Mismo redondeo que el QR del móvil (`api_horas/movil.py`): la nómina ya se
# paga así y el lector no debe cambiar las reglas.
REDONDEO_MIN = 30

# Solo cuentan los accesos PERMITIDOS: un rechazo no es una checada.
TIPO_CHECADA = 'permitido'

ORIGEN_LECTOR = 'LECTOR'


# ── Calendario ──────────────────────────────────────────────────────────────

def semana_de(fecha: date) -> tuple[date, date]:
    """(martes, lunes) de la semana de nómina que contiene `fecha`."""
    inicio = fecha - timedelta(days=(fecha.weekday() - 1) % 7)
    return inicio, inicio + timedelta(days=6)


def redondear(hora: time) -> time:
    """Redondeo a `REDONDEO_MIN`, IDÉNTICO al del QR (`api_horas/movil.py`).

    Idéntico a propósito, rarezas incluidas, para que QR y lector den la misma
    hora a la misma persona: ignora los segundos y usa `round()` de Python, que
    en el empate exacto redondea al par (8:15 → 8:00, pero 8:45 → 9:00). Si se
    corrige, hay que corregirlo en los dos lados a la vez.
    """
    total = hora.hour * 60 + hora.minute
    r = round(total / REDONDEO_MIN) * REDONDEO_MIN
    return time((r // 60) % 24, r % 60)


# ── Proyecto y reporte OFICINA ──────────────────────────────────────────────

def proyecto_oficina() -> Proyecto:
    p = Proyecto.query.filter_by(numero_proyecto=NUMERO_PROYECTO_OFICINA).first()
    if p is None:
        p = Proyecto(numero_proyecto=NUMERO_PROYECTO_OFICINA,
                     nombre=NOMBRE_PROYECTO_OFICINA, activo=True)
        db.session.add(p)
        db.session.flush()
        logger.info('Hikvision asistencia: se creó el proyecto %s', NUMERO_PROYECTO_OFICINA)
    return p


def _motivo_semana_bloqueada(inicio: date, fin: date) -> str | None:
    """Por qué NO se puede escribir en esa semana, o None si se puede."""
    if Prenomina.query.filter_by(fecha_inicio=inicio).first():
        return 'la prenómina de esa semana ya se guardó'
    cerrada = ReporteSemanal.query.filter(
        ReporteSemanal.estado == 'PRENOMINA_CERRADA',
        ReporteSemanal.fecha_inicio_semana <= fin,
        ReporteSemanal.fecha_fin_semana >= inicio,
    ).first()
    if cerrada:
        return 'la prenómina de esa semana ya está cerrada'
    return None


def reporte_oficina(fecha: date) -> tuple[ReporteSemanal | None, str, bool]:
    """(reporte BORRADOR donde escribir, motivo si no hay, ¿se creó ahora?)."""
    proyecto = proyecto_oficina()
    inicio, fin = semana_de(fecha)
    reporte = ReporteSemanal.query.filter(
        ReporteSemanal.proyecto_id == proyecto.id,
        ReporteSemanal.fecha_inicio_semana <= fecha,
        ReporteSemanal.fecha_fin_semana >= fecha,
    ).first()
    if reporte is not None:
        if reporte.estado != 'BORRADOR':
            return None, 'el reporte OFICINA de esa semana ya está cerrado', False
        return reporte, '', False

    motivo = _motivo_semana_bloqueada(inicio, fin)
    if motivo:
        return None, motivo, False

    reporte = ReporteSemanal(
        proyecto_id=proyecto.id,
        fecha_inicio_semana=inicio,
        fecha_fin_semana=fin,
        estado='BORRADOR',
        creado_por_id=None,   # lo abrió el lector, no una persona
    )
    db.session.add(reporte)
    db.session.flush()
    logger.info('Hikvision asistencia: reporte OFICINA abierto %s → %s', inicio, fin)
    return reporte, '', True


# ── Un trabajador, un día ───────────────────────────────────────────────────

def checadas_del_dia(trabajador_id: int, fecha: date) -> list[time]:
    """Horas (de la oficina) de los accesos permitidos del trabajador ese día.

    Se busca por `trabajador_id` y también por el número de empleado con que
    está en cada lector: alguien sincronizado DESPUÉS de sus primeros accesos
    tiene eventos sin `trabajador_id` resuelto.
    """
    numeros = [
        n for (n,) in db.session.query(SyncEmpleadoHikvision.employee_no_remoto)
        .filter(SyncEmpleadoHikvision.trabajador_id == trabajador_id)
    ]
    condicion = EventoHikvision.trabajador_id == trabajador_id
    if numeros:
        condicion = condicion | EventoHikvision.employee_no.in_(numeros)

    # El rango en UTC acota con el índice; el filtro exacto es el día LOCAL
    # que viene en `hora_local` (la oficina, no el servidor).
    desde = datetime.combine(fecha - timedelta(days=1), time.min)
    hasta = datetime.combine(fecha + timedelta(days=2), time.min)
    prefijo = fecha.isoformat()
    horas = []
    for (hora_local,) in db.session.query(EventoHikvision.hora_local).filter(
        condicion,
        EventoHikvision.tipo == TIPO_CHECADA,
        EventoHikvision.fecha_hora >= desde,
        EventoHikvision.fecha_hora < hasta,
    ):
        if hora_local.startswith(prefijo):
            try:
                horas.append(time.fromisoformat(hora_local[11:19]))
            except ValueError:
                continue
    return sorted(horas)


def aplicar_dia(trabajador: Trabajador, fecha: date) -> dict:
    """Recalcula entrada/salida del día desde los eventos. No hace commit.

    Devuelve {'accion': creado|actualizado|sin_cambios|omitido, 'motivo',
    'reporte_id', 'reporte_nuevo'}.
    """
    resultado = {'trabajador_id': trabajador.id, 'fecha': fecha.isoformat(),
                 'accion': 'omitido', 'motivo': '', 'reporte_id': None, 'reporte_nuevo': False}

    horas = checadas_del_dia(trabajador.id, fecha)
    if not horas:
        resultado['motivo'] = 'sin accesos permitidos ese día'
        return resultado

    entrada = redondear(horas[0])
    salida = redondear(horas[-1]) if len(horas) > 1 else None
    if salida == entrada:
        # Dos pasadas seguidas (entrar y volver a pasar al minuto) no son una
        # jornada: se queda solo la entrada.
        salida = None

    reporte, motivo, nuevo = reporte_oficina(fecha)
    if reporte is None:
        resultado['motivo'] = motivo
        return resultado
    resultado['reporte_id'] = reporte.id
    resultado['reporte_nuevo'] = nuevo

    if trabajador not in reporte.proyecto.participantes:
        reporte.proyecto.participantes.append(trabajador)

    reg = RegistroDiarioHoras.query.filter_by(
        reporte_id=reporte.id, trabajador_id=trabajador.id, fecha=fecha,
    ).first()
    if reg is not None and reg.origen != ORIGEN_LECTOR:
        resultado['motivo'] = (
            'editado a mano' if reg.origen == 'LECTOR_EDITADO' else 'capturado por otra vía'
        )
        return resultado

    if reg is None:
        reg = RegistroDiarioHoras(
            reporte_id=reporte.id,
            trabajador_id=trabajador.id,
            fecha=fecha,
            tipo_nomina=trabajador.tipo_nomina or 'Semanal',
            tomo_comida=False,
            origen=ORIGEN_LECTOR,
            # Único y estable: si el kiosko u otra vía reintenta con este id,
            # la idempotencia existente lo reconoce como el mismo registro.
            client_record_id=f'hik:{trabajador.id}:{fecha.isoformat()}',
        )
        db.session.add(reg)
        resultado['accion'] = 'creado'
    elif (reg.hora_entrada, reg.hora_salida) == (entrada, salida):
        resultado['accion'] = 'sin_cambios'
        return resultado
    else:
        resultado['accion'] = 'actualizado'

    reg.hora_entrada = entrada
    reg.hora_salida = salida
    reg.horas_productivas = (
        calcular_horas_productivas(
            entrada, salida,
            tipo_nomina=reg.tipo_nomina or 'Semanal',
            tomo_comida=bool(reg.tomo_comida),
        )
        if salida else None
    )
    return resultado


# ── Lote ────────────────────────────────────────────────────────────────────

def _dias_afectados(eventos) -> set[tuple[int, date]]:
    """(trabajador_id, día local) que tocan estos eventos."""
    sin_resolver = {e.employee_no for e in eventos
                    if e.tipo == TIPO_CHECADA and not e.trabajador_id and e.employee_no}
    por_numero = {}
    if sin_resolver:
        por_numero = {
            n: t for n, t in db.session.query(
                SyncEmpleadoHikvision.employee_no_remoto, SyncEmpleadoHikvision.trabajador_id,
            ).filter(SyncEmpleadoHikvision.employee_no_remoto.in_(sin_resolver))
        }
    dias = set()
    for e in eventos:
        if e.tipo != TIPO_CHECADA:
            continue
        trabajador_id = e.trabajador_id or por_numero.get(e.employee_no)
        try:
            dia = date.fromisoformat(e.hora_local[:10])
        except (TypeError, ValueError):
            continue
        if trabajador_id:
            dias.add((trabajador_id, dia))
    return dias


def aplicar_eventos(eventos) -> list[dict]:
    """Pasa a registros de horas los días que tocan estos eventos nuevos.

    Cada día va en su propio savepoint: que uno falle (un dato raro, una
    semana bloqueada) no impide los demás ni la ingesta de los eventos, que ya
    quedaron guardados. No hace commit.
    """
    resultados = []
    for trabajador_id, dia in sorted(_dias_afectados(eventos)):
        trabajador = db.session.get(Trabajador, trabajador_id)
        if trabajador is None or not trabajador.activo:
            continue
        try:
            with db.session.begin_nested():
                resultados.append(aplicar_dia(trabajador, dia))
        except Exception:  # noqa: BLE001 — una checada mala no tumba la ingesta
            logger.exception('Hikvision asistencia: no se pudo aplicar trab=%s día=%s',
                             trabajador_id, dia)
    return resultados


def recalcular(desde: date, hasta: date) -> list[dict]:
    """Reprocesa todos los eventos guardados entre dos días (ambos incluidos).

    Para arrancar la fase 2 con los eventos que ya estaban en la base, o tras
    corregir algo. Idempotente.
    """
    eventos = EventoHikvision.query.filter(
        EventoHikvision.tipo == TIPO_CHECADA,
        EventoHikvision.fecha_hora >= datetime.combine(desde - timedelta(days=1), time.min),
        EventoHikvision.fecha_hora < datetime.combine(hasta + timedelta(days=2), time.min),
    ).all()
    dentro = [e for e in eventos if desde.isoformat() <= (e.hora_local or '')[:10] <= hasta.isoformat()]
    return aplicar_eventos(dentro)


def avisar_cambios(resultados: list[dict]) -> None:
    """Refresca en vivo la lista de reportes y la captura abierta. Tras el commit."""
    cambiados = {r['reporte_id'] for r in resultados
                 if r['accion'] in ('creado', 'actualizado') and r['reporte_id']}
    if not cambiados:
        return
    try:
        from app.realtime import emit_to_role
        for reporte_id in cambiados:
            emit_to_role(['admin', 'super_admin', 'coordinador'], 'reporte:lista_changed', {
                'id': reporte_id, 'action': 'registro_lector',
            })
    except Exception:  # noqa: BLE001 — el registro ya quedó guardado
        logger.warning('Hikvision asistencia: no se pudo avisar por Socket.IO')
