"""Núcleo compartido del paquete `api_horas`.

Define el blueprint `bp`, constantes (DIAS_SEMANA, INCIDENCIAS), helpers de
acceso (_is_coordinador, _puede_acceder_proyecto) y serializers comunes.

No registres rutas en este archivo. Las rutas viven en los submódulos por
dominio: reportes.py, registros.py, movil.py, rfid_qr.py.
"""
from datetime import date, datetime, timedelta

from flask import Blueprint

from app.models import Proyecto, RegistroDiarioHoras, ReporteSemanal, Trabajador
from app.routes._api_helpers import current_user, is_admin

bp = Blueprint('api_horas', __name__, url_prefix='/api/horas')

DIAS_SEMANA = ['Lun', 'Mar', 'Mié', 'Jue', 'Vie', 'Sáb', 'Dom']

INCIDENCIAS = [
    'Casamiento', 'Descanso', 'Falta', 'Incapacidad', 'Luto', 'Paternidad',
    'Permiso', 'Time x Time', 'Vacaciones', 'Viaje de Ida', 'Viaje de vuelta a Pue.',
    'Retardo', 'Levantamiento en campo', 'Falta checada de entrada', 'Falta checada de salida',
]


# ── Helpers ────────────────────────────────────────────────────────────────────

def _is_coordinador() -> bool:
    return current_user().role == 'coordinador'


def _puede_acceder_proyecto(proyecto: Proyecto) -> bool:
    """Coordinadores solo a sus proyectos; admin/super_admin a todos."""
    if is_admin():
        return True
    if _is_coordinador():
        return proyecto.coordinador_id == current_user().id
    return False


def _hora_a_str(t) -> str:
    return t.strftime('%H:%M') if t else ''


def _parse_time(s: str):
    if not s:
        return None
    return datetime.strptime(s, '%H:%M').time()


def _reporte_row(r: ReporteSemanal) -> dict:
    """Resumen para la tabla principal."""
    p = r.proyecto
    return {
        'id': r.id,
        'estado': r.estado,
        'fecha_inicio': r.fecha_inicio_semana.isoformat(),
        'fecha_fin': r.fecha_fin_semana.isoformat(),
        'proyecto': {
            'id': p.id,
            'numero_proyecto': p.numero_proyecto,
            'nombre': p.nombre or '',
        } if p else None,
        'registros_count': len(r.registros),
        'created_at': r.created_at.isoformat() if r.created_at else None,
    }


def _registro_dict(reg: RegistroDiarioHoras) -> dict:
    return {
        'id': reg.id,
        'reporte_id': reg.reporte_id,
        'trabajador_id': reg.trabajador_id,
        'fecha': reg.fecha.isoformat(),
        'hora_entrada': _hora_a_str(reg.hora_entrada),
        'hora_salida': _hora_a_str(reg.hora_salida),
        'tomo_comida': bool(reg.tomo_comida),
        'aplica_viaticos': bool(reg.aplica_viaticos),
        'monto_viaticos_manual': float(reg.monto_viaticos_manual) if reg.monto_viaticos_manual is not None else None,
        'viaticos_modo': 'manual' if reg.monto_viaticos_manual is not None else 'perfil',
        'aplica_dia_festivo': bool(reg.aplica_dia_festivo),
        'incidencia': reg.incidencia or '',
        'horas_productivas': float(reg.horas_productivas) if reg.horas_productivas is not None else 0.0,
        'client_record_id': reg.client_record_id,
        'modificado_en': reg.modificado_en.isoformat() if reg.modificado_en else None,
    }


def _trabajador_row(t: Trabajador) -> dict:
    return {
        'id': t.id,
        'no_empleado': t.no_empleado,
        'nombre': t.nombre,
        'tipo_nomina': t.tipo_nomina or 'Semanal',
        'viaticos': float(t.viaticos) if t.viaticos is not None else 0.0,
        'pago_dia_festivo': float(t.pago_dia_festivo) if t.pago_dia_festivo is not None else 0.0,
    }


def _semana_fechas(inicio: date, fin: date) -> list[dict]:
    out = []
    d = inicio
    while d <= fin:
        out.append({
            'fecha': d.isoformat(),
            'label': d.strftime('%d/%m'),
            'dia': DIAS_SEMANA[d.weekday()],
            'fin_semana': d.weekday() >= 5,
        })
        d += timedelta(days=1)
    return out
