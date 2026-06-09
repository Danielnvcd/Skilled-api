"""Núcleo del paquete `api_proyecto_total`. Blueprint + agregador por proyecto."""
from decimal import Decimal

from flask import Blueprint
from sqlalchemy.orm import joinedload

from app.extensions import db
from app.models import Prenomina, Proyecto, RegistroDiarioHoras, ReporteSemanal
from app.utils import to_dec


bp = Blueprint('api_proyecto_total', __name__, url_prefix='/api/proyecto-total')


_MONEY_KEYS = [
    'salario_base', 'pago_viaticos', 'pago_festivos', 'depositos_otros', 'depositos_prestamos',
    'descuento_infonavit', 'ajuste_inbursa', 'descuentos_otros', 'descuento_prestamos',
    'descuento_incidencias', 'recuperacion_manual', 'total_percepciones',
    'total_deducciones', 'total_a_pagar',
]


def _coordinador_dict(coord):
    if not coord:
        return None
    return {
        'id': coord.id,
        'username': coord.username,
        'full_name': coord.full_name or coord.username,
    }


def _build_proyecto_data(proyecto: Proyecto) -> dict | None:
    """Construye el agregado por proyecto: semanas + grand totals. Devuelve None
    si el proyecto no tiene semanas cerradas con prenóminas aprobadas."""
    reportes = ReporteSemanal.query.filter_by(
        proyecto_id=proyecto.id,
        estado='PRENOMINA_CERRADA',
    ).order_by(ReporteSemanal.fecha_inicio_semana).all()

    if not reportes:
        return None

    semanas = []
    grand = {k: Decimal('0') for k in _MONEY_KEYS}
    grand['trabajadores_count'] = 0

    for rep in reportes:
        trabajadores_in_project = db.session.query(
            RegistroDiarioHoras.trabajador_id,
        ).filter(RegistroDiarioHoras.reporte_id == rep.id).distinct().all()
        t_ids = [t[0] for t in trabajadores_in_project]

        prenominas = Prenomina.query.filter(
            Prenomina.fecha_inicio == rep.fecha_inicio_semana,
            Prenomina.estado == 'APROBADO',
            Prenomina.trabajador_id.in_(t_ids),
        ).all() if t_ids else []

        if not prenominas:
            continue

        week_dec = {
            k: sum((to_dec(getattr(p, k)) for p in prenominas), Decimal('0'))
            for k in _MONEY_KEYS
        }

        semanas.append({
            'fecha_inicio': rep.fecha_inicio_semana.isoformat(),
            'fecha_fin': rep.fecha_fin_semana.isoformat() if rep.fecha_fin_semana else None,
            'num_trabajadores': len(prenominas),
            **{k: float(week_dec[k]) for k in _MONEY_KEYS},
        })

        for k in _MONEY_KEYS:
            grand[k] += week_dec[k]
        grand['trabajadores_count'] += len(prenominas)

    if not semanas:
        return None

    grand_out = {k: float(v) if isinstance(v, Decimal) else v for k, v in grand.items()}
    return {
        'proyecto': {
            'id': proyecto.id,
            'numero_proyecto': proyecto.numero_proyecto,
            'nombre': proyecto.nombre or '',
            'coordinador': _coordinador_dict(proyecto.coordinador),
        },
        'num_semanas': len(semanas),
        'semanas': semanas,
        'grand': grand_out,
    }
