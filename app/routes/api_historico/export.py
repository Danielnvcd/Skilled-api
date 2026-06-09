"""Exportaciones del histórico: PDF de Lista de raya por proyecto y Excel
agregado por semana."""
import io
import os
import traceback
from datetime import datetime
from io import BytesIO

import pandas as pd
from flask import current_app, jsonify, make_response, render_template
from xhtml2pdf import pisa

from app.extensions import db, limiter
from app.models import Prenomina, Proyecto, RegistroDiarioHoras, ReporteSemanal
from app.routes._api_helpers import _aplicar_estilos_y_retornar, _sanitize_rows, require_admin
from app.routes.api_auth import jwt_required

from ._core import bp


@bp.route('/<string:fecha_str>/proyecto/<int:proyecto_id>/pdf', methods=['GET'])
@jwt_required
@limiter.limit('10 per minute')
def imprimir_proyecto_pdf(fecha_str, proyecto_id):
    err = require_admin()
    if err:
        return err
    try:
        fecha_obj = datetime.strptime(fecha_str, '%Y-%m-%d').date()
    except ValueError:
        return jsonify({'error': 'Fecha inválida'}), 400

    proyecto = Proyecto.query.get(proyecto_id)
    if not proyecto:
        return jsonify({'error': 'Proyecto no encontrado'}), 404

    reporte = ReporteSemanal.query.filter_by(
        proyecto_id=proyecto.id,
        fecha_inicio_semana=fecha_obj,
        estado='PRENOMINA_CERRADA',
    ).first()
    if not reporte:
        return jsonify({'error': 'Reporte no encontrado para este proyecto en la semana'}), 404

    trabajadores_in_project = db.session.query(
        RegistroDiarioHoras.trabajador_id,
    ).filter(RegistroDiarioHoras.reporte_id == reporte.id).distinct().all()
    t_ids = [t[0] for t in trabajadores_in_project]

    prenominas = Prenomina.query.filter(
        Prenomina.fecha_inicio == fecha_obj,
        Prenomina.estado == 'APROBADO',
        Prenomina.trabajador_id.in_(t_ids),
    ).all() if t_ids else []

    # API-only: la app se construye con `static_folder=None`. Resolvemos el
    # logo contra BASE_DIR; si no existe, degradamos a None.
    base_dir = current_app.config.get('BASE_DIR') or os.path.dirname(current_app.root_path)
    logo_path = os.path.join(base_dir, 'static', 'imagenes', 'skilled_white_bg.jpg')
    if not os.path.exists(logo_path):
        logo_path = None

    html_salida = render_template(
        'recibo_proyecto_pdf.html',
        proyecto=proyecto,
        fecha_obj=fecha_obj,
        fecha_fin=reporte.fecha_fin_semana,
        prenominas=prenominas,
        logo_path=logo_path,
    )

    pdf = BytesIO()
    pisa_status = pisa.CreatePDF(BytesIO(html_salida.encode('utf-8')), dest=pdf)
    if pisa_status.err:
        return jsonify({'error': 'Error al generar el PDF'}), 500

    response = make_response(pdf.getvalue())
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = (
        f'inline; filename=Lista_Raya_{proyecto.numero_proyecto}_{fecha_obj.strftime("%d%m")}.pdf'
    )
    return response


@bp.route('/<string:fecha_str>/excel', methods=['GET'])
@jwt_required
@limiter.limit('10 per minute')
def exportar_excel(fecha_str):
    err = require_admin()
    if err:
        return err
    try:
        fecha_obj = datetime.strptime(fecha_str, '%Y-%m-%d').date()
    except ValueError:
        return jsonify({'error': 'Fecha inválida'}), 400

    try:
        output = io.BytesIO()
        writer = pd.ExcelWriter(output, engine='openpyxl')

        prenominas = Prenomina.query.filter(
            Prenomina.fecha_inicio == fecha_obj,
            Prenomina.estado == 'APROBADO',
        ).order_by(Prenomina.trabajador_id).all()

        if not prenominas:
            return jsonify({'error': 'No se encontraron prenóminas aprobadas para esta semana'}), 404

        data = []
        for p in prenominas:
            data.append({
                'Semana Inicio': p.fecha_inicio.strftime('%Y-%m-%d') if p.fecha_inicio else '',
                'Semana Fin': p.fecha_fin.strftime('%Y-%m-%d') if p.fecha_fin else '',
                'No. Empleado': p.trabajador.no_empleado if p.trabajador else '',
                'Nombre del Empleado': f"{p.trabajador.nombre} {p.trabajador.nombre_apellidos}" if p.trabajador else '',
                'Total Percepciones': float(p.total_percepciones or 0),
                'Total Deducciones': float(p.total_deducciones or 0),
                'TOTAL PAGADO': float(p.total_a_pagar or 0),
                'Método de Pago': p.tipo_pago or '',
            })

        total_row = {
            'Semana Inicio': 'TOTAL',
            'Semana Fin': '',
            'No. Empleado': '',
            'Nombre del Empleado': '',
            'Total Percepciones': sum(d['Total Percepciones'] for d in data),
            'Total Deducciones': sum(d['Total Deducciones'] for d in data),
            'TOTAL PAGADO': sum(d['TOTAL PAGADO'] for d in data),
            'Método de Pago': '',
        }
        data.append(total_row)

        df = pd.DataFrame(_sanitize_rows(data))
        df.to_excel(writer, sheet_name='Histórico', index=False)

        timestamp = datetime.now().strftime('%Y%m%d_%H%M')
        return _aplicar_estilos_y_retornar(writer, output, f"Reporte_Historico_{fecha_str}_{timestamp}.xlsx")
    except Exception as e:
        current_app.logger.error('Error generando Excel histórico API: %s\n%s', e, traceback.format_exc())
        return jsonify({'error': 'Error al generar el Excel'}), 500
