"""Núcleo del paquete `api_historico`. Blueprint + serializers."""
from flask import Blueprint

from app.models import Prenomina


bp = Blueprint('api_historico', __name__, url_prefix='/api/historico')


def _coord_dict(coord):
    if not coord:
        return None
    return {
        'id': coord.id,
        'username': coord.username,
        'full_name': coord.full_name or coord.username,
    }


def _prenomina_to_dict(p: Prenomina) -> dict:
    t = p.trabajador
    return {
        'id': p.id,
        'trabajador': {
            'id': t.id if t else None,
            'no_empleado': t.no_empleado if t else '',
            'nombre': t.nombre if t else '',
            'nombre_apellidos': t.nombre_apellidos if t else '',
            'nombre_completo': f"{t.nombre} {t.nombre_apellidos}".strip() if t else '',
            'tipo_jornada': t.tipo_jornada if t else '',
        } if t else None,
        'tipo_pago': p.tipo_pago or '',
        'salario_base': float(p.salario_base or 0),
        'pago_viaticos': float(p.pago_viaticos or 0),
        'pago_festivos': float(p.pago_festivos or 0),
        'depositos_otros': float(p.depositos_otros or 0),
        'depositos_prestamos': float(p.depositos_prestamos or 0),
        'descuento_infonavit': float(p.descuento_infonavit or 0),
        'ajuste_inbursa': float(p.ajuste_inbursa or 0),
        'descuentos_otros': float(p.descuentos_otros or 0),
        'descuento_prestamos': float(p.descuento_prestamos or 0),
        'descuento_incidencias': float(p.descuento_incidencias or 0),
        'recuperacion_manual': float(p.recuperacion_manual or 0),
        'total_percepciones': float(p.total_percepciones or 0),
        'total_deducciones': float(p.total_deducciones or 0),
        'total_a_pagar': float(p.total_a_pagar or 0),
    }
