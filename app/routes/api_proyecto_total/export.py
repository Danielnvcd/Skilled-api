"""Excel agregado de Proyecto Total."""
import io
import traceback
from datetime import datetime

import pandas as pd
from flask import current_app, jsonify

from app.extensions import db, limiter
from app.models import Prenomina, Proyecto, RegistroDiarioHoras, ReporteSemanal
from app.routes._api_helpers import _aplicar_estilos_y_retornar, _sanitize_rows, require_admin
from app.routes.api_auth import jwt_required

from ._core import bp


@bp.route('/<int:proyecto_id>/excel', methods=['GET'])
@jwt_required
@limiter.limit('10 per minute')
def exportar_excel(proyecto_id):
    err = require_admin()
    if err:
        return err

    proyecto = db.session.get(Proyecto, proyecto_id)
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
