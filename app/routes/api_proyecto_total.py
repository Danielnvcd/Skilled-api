"""API JSON para la vista de Proyecto Total (consumida por el SPA React).

Replica la lógica del blueprint clásico `proyecto_total.py` pero responde JSON y
autentica con JWT. Reusa los helpers de estilos Excel de `reportes.py` para
mantener la consistencia visual con los exports clásicos.
"""
import io
import traceback
from datetime import datetime
from decimal import Decimal

import pandas as pd
from flask import Blueprint, current_app, g, jsonify, request
from sqlalchemy.orm import joinedload, selectinload

from app.extensions import db, limiter
from app.models import Prenomina, Proyecto, RegistroDiarioHoras, ReporteSemanal
from app.routes.api_auth import jwt_required
from app.routes.reportes import _aplicar_estilos_y_retornar, _sanitize_rows
from app.utils import to_dec

bp = Blueprint('api_proyecto_total', __name__, url_prefix='/api/proyecto-total')


_MONEY_KEYS = [
    'salario_base', 'pago_viaticos', 'pago_festivos', 'depositos_otros', 'depositos_prestamos',
    'descuento_infonavit', 'ajuste_inbursa', 'descuentos_otros', 'descuento_prestamos',
    'descuento_incidencias', 'recuperacion_manual', 'total_percepciones',
    'total_deducciones', 'total_a_pagar',
]


def _u():
    return g._jwt_user


def _is_admin() -> bool:
    return _u().role in ('admin', 'super_admin')


def _admin_only():
    if not _is_admin():
        return jsonify({'error': 'Acceso denegado'}), 403
    return None


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


@bp.route('', methods=['GET'])
@jwt_required
def listar():
    err = _admin_only()
    if err:
        return err

    page = request.args.get('page', 1, type=int)
    per_page = min(request.args.get('per_page', 20, type=int), 100)
    q = (request.args.get('q') or '').strip()

    query = Proyecto.query.options(
        joinedload(Proyecto.coordinador),
        selectinload(Proyecto.participantes),
    )
    if q:
        like = f'%{q}%'
        query = query.filter(Proyecto.nombre.ilike(like) | Proyecto.numero_proyecto.ilike(like))

    pagination = query.order_by(Proyecto.numero_proyecto).paginate(
        page=page, per_page=per_page, error_out=False,
    )

    proyectos_data = []
    for proyecto in pagination.items:
        agg = _build_proyecto_data(proyecto)
        if agg:
            proyectos_data.append(agg)

    return jsonify({
        'items': proyectos_data,
        'page': pagination.page,
        'per_page': pagination.per_page,
        'total': pagination.total,
        'pages': pagination.pages,
        'has_next': pagination.has_next,
        'has_prev': pagination.has_prev,
    })


@bp.route('/<int:proyecto_id>/excel', methods=['GET'])
@jwt_required
@limiter.limit('10 per minute')
def exportar_excel(proyecto_id):
    err = _admin_only()
    if err:
        return err

    proyecto = Proyecto.query.get(proyecto_id)
    if not proyecto:
        return jsonify({'error': 'Proyecto no encontrado'}), 404

    try:
        output = io.BytesIO()
        writer = pd.ExcelWriter(output, engine='openpyxl')

        reportes = ReporteSemanal.query.filter_by(
            proyecto_id=proyecto.id, estado='PRENOMINA_CERRADA',
        ).order_by(ReporteSemanal.fecha_inicio_semana).all()

        if not reportes:
            return jsonify({'error': 'No hay semanas cerradas para este proyecto'}), 404

        data = []
        for rep in reportes:
            trabajadores_in_project = db.session.query(
                RegistroDiarioHoras.trabajador_id,
            ).filter(RegistroDiarioHoras.reporte_id == rep.id).distinct().all()
            t_ids = [t[0] for t in trabajadores_in_project]

            prenominas_rep = Prenomina.query.filter(
                Prenomina.fecha_inicio == rep.fecha_inicio_semana,
                Prenomina.estado == 'APROBADO',
                Prenomina.trabajador_id.in_(t_ids),
            ).all() if t_ids else []

            for p in prenominas_rep:
                data.append({
                    'Semana Inicio': rep.fecha_inicio_semana.strftime('%Y-%m-%d'),
                    'Semana Fin': rep.fecha_fin_semana.strftime('%Y-%m-%d'),
                    'No. Empleado': p.trabajador.no_empleado if p.trabajador else '',
                    'Nombre del Empleado': f"{p.trabajador.nombre} {p.trabajador.nombre_apellidos}" if p.trabajador else '',
                    'Salario Base': float(p.salario_base or 0),
                    'Pago Horas Extras': float(p.pago_horas_extras or 0),
                    'Pago Viáticos': float(p.pago_viaticos or 0),
                    'Pago Festivos': float(p.pago_festivos or 0),
                    'Otros Depósitos': float(p.depositos_otros or 0),
                    'Depósitos Préstamos': float(p.depositos_prestamos or 0),
                    'Total Percepciones': float(p.total_percepciones or 0),
                    'Descuento Infonavit': float(p.descuento_infonavit or 0),
                    'Ajuste Inbursa': float(p.ajuste_inbursa or 0),
                    'Otros Descuentos': float(p.descuentos_otros or 0),
                    'Abono Préstamos': float(p.descuento_prestamos or 0),
                    'Descuento Incidencias': float(p.descuento_incidencias or 0),
                    'Total Deducciones': float(p.total_deducciones or 0),
                    'TOTAL A PAGAR': float(p.total_a_pagar or 0),
                })

        if not data:
            return jsonify({'error': 'No hay datos de nómina para este proyecto'}), 404

        total_row = {
            'Semana Inicio': 'TOTAL',
            'Semana Fin': '',
            'No. Empleado': '',
            'Nombre del Empleado': '',
        }
        for k in [
            'Salario Base', 'Pago Horas Extras', 'Pago Viáticos', 'Pago Festivos',
            'Otros Depósitos', 'Depósitos Préstamos', 'Total Percepciones',
            'Descuento Infonavit', 'Ajuste Inbursa', 'Otros Descuentos',
            'Abono Préstamos', 'Descuento Incidencias', 'Total Deducciones',
            'TOTAL A PAGAR',
        ]:
            total_row[k] = sum(d[k] for d in data)
        data.append(total_row)

        df = pd.DataFrame(_sanitize_rows(data))
        df.to_excel(writer, sheet_name='Proyecto Total', index=False)

        timestamp = datetime.now().strftime('%Y%m%d_%H%M')
        filename = f"Reporte_ProyectoTotal_{proyecto.numero_proyecto}_{timestamp}.xlsx"
        return _aplicar_estilos_y_retornar(writer, output, filename)
    except Exception as e:
        current_app.logger.error('Error generando Excel ProyectoTotal API: %s\n%s', e, traceback.format_exc())
        return jsonify({'error': 'Error al generar el Excel'}), 500
