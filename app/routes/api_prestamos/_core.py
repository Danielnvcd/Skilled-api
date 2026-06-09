"""Núcleo del paquete `api_prestamos`.

Define el blueprint, serializers y el helper de recálculo de prenóminas
abiertas que disparan los flujos de crear / editar / abonar / liquidar.
"""
from flask import Blueprint

from app.extensions import db
from app.models import Prenomina, Prestamo
from app.utils import recalcular_totales_prenomina, to_dec


bp = Blueprint('api_prestamos', __name__, url_prefix='/api/prestamos')


def _recalcular_prenominas_abiertas(trabajador_id):
    """Recalcula todas las prenóminas ABIERTAS de un trabajador usando la lógica compartida."""
    prenominas_abiertas = Prenomina.query.filter_by(
        trabajador_id=trabajador_id,
        estado='ABIERTA'
    ).all()
    if not prenominas_abiertas:
        return
    # Cargar préstamos una sola vez para todos (mismo trabajador_id)
    prestamos_activos = Prestamo.query.filter_by(trabajador_id=trabajador_id, estado='ACTIVO').all()
    for p in prenominas_abiertas:
        recalcular_totales_prenomina(p, prestamos_activos=prestamos_activos)
    db.session.commit()


def _num(v) -> float:
    return float(to_dec(v)) if v is not None else 0.0


def _prestamo_row(p: Prestamo) -> dict:
    t = p.trabajador
    return {
        'id': p.id,
        'trabajador_id': p.trabajador_id,
        'trabajador': {
            'id': t.id,
            'no_empleado': t.no_empleado,
            'nombre': t.nombre,
            'nombre_apellidos': t.nombre_apellidos,
            'nombre_completo': t.nombre_completo,
        } if t else None,
        'monto_total': _num(p.monto_total),
        'monto_restante': _num(p.monto_restante),
        'plazo_semanas': p.plazo_semanas,
        'descuento_semanal': _num(p.descuento_semanal),
        'motivo': p.motivo or '',
        'frecuencia': p.frecuencia or 'semanal',
        'fecha_inicio': p.fecha_inicio.isoformat() if p.fecha_inicio else None,
        'estado': p.estado,
        'activo': bool(p.activo),
        'creado_en': p.creado_en.isoformat() if p.creado_en else None,
    }
