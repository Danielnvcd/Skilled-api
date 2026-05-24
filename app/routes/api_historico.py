"""API JSON para el módulo de Histórico de nóminas (consumido por el SPA React).

Replica `historico_nominas.py` (vista clásica Jinja) y `reportes.excel_historico`
pero responde JSON y autentica con JWT. Para el PDF de "Lista de raya" reusa
el template `recibo_proyecto_pdf.html` que ya existe.
"""
import io
import os
import traceback
from datetime import datetime

import pandas as pd
from flask import (
    Blueprint, current_app, g, jsonify, make_response, render_template, request,
)
from sqlalchemy.orm import joinedload, selectinload

from app.extensions import db, limiter
from app.models import (
    Prenomina, Proyecto, RegistroDiarioHoras, ReporteSemanal,
)
from app.routes.api_auth import jwt_required
from app.routes.reportes import _aplicar_estilos_y_retornar, _sanitize_rows

bp = Blueprint('api_historico', __name__, url_prefix='/api/historico')


def _u():
    return g._jwt_user


def _is_admin() -> bool:
    return _u().role in ('admin', 'super_admin')


def _admin_only():
    if not _is_admin():
        return jsonify({'error': 'Acceso denegado'}), 403
    return None


def _coord_dict(coord):
    if not coord:
        return None
    return {
        'id': coord.id,
        'username': coord.username,
        'full_name': coord.full_name or coord.username,
    }


@bp.route('', methods=['GET'])
@jwt_required
def listar_semanas():
    err = _admin_only()
    if err:
        return err

    page = request.args.get('page', 1, type=int)
    per_page = min(request.args.get('per_page', 20, type=int), 100)
    search_date_str = (request.args.get('search_date') or '').strip()

    query = db.session.query(Prenomina.fecha_inicio).filter_by(estado='APROBADO')
    if search_date_str:
        try:
            search_date_obj = datetime.strptime(search_date_str, '%Y-%m-%d').date()
            query = query.filter_by(fecha_inicio=search_date_obj)
        except ValueError:
            pass

    pagination = query.distinct().order_by(Prenomina.fecha_inicio.desc()).paginate(
        page=page, per_page=per_page, error_out=False,
    )
    fechas = [f[0] for f in pagination.items]

    semanas = []
    for fecha in fechas:
        reportes = ReporteSemanal.query.options(
            joinedload(ReporteSemanal.proyecto).joinedload(Proyecto.coordinador),
        ).filter_by(fecha_inicio_semana=fecha, estado='PRENOMINA_CERRADA').all()

        semanas.append({
            'fecha_inicio': fecha.isoformat(),
            'proyectos': [
                {
                    'id': r.proyecto.id,
                    'numero_proyecto': r.proyecto.numero_proyecto,
                    'nombre': r.proyecto.nombre or '',
                    'coordinador': _coord_dict(r.proyecto.coordinador),
                }
                for r in reportes if r.proyecto
            ],
        })

    return jsonify({
        'items': semanas,
        'page': pagination.page,
        'per_page': pagination.per_page,
        'total': pagination.total,
        'pages': pagination.pages,
        'has_next': pagination.has_next,
        'has_prev': pagination.has_prev,
    })


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


@bp.route('/<string:fecha_str>', methods=['GET'])
@jwt_required
def detalle_semana(fecha_str):
    err = _admin_only()
    if err:
        return err

    try:
        fecha_obj = datetime.strptime(fecha_str, '%Y-%m-%d').date()
    except ValueError:
        return jsonify({'error': 'Fecha inválida'}), 400

    reportes = ReporteSemanal.query.options(
        joinedload(ReporteSemanal.proyecto),
    ).filter_by(fecha_inicio_semana=fecha_obj, estado='PRENOMINA_CERRADA').all()
    if not reportes:
        return jsonify({'fecha': fecha_str, 'proyectos': []})

    prenominas_semana = Prenomina.query.options(
        selectinload(Prenomina.trabajador),
    ).filter_by(fecha_inicio=fecha_obj, estado='APROBADO').all()
    prenominas_dict = {p.trabajador_id: p for p in prenominas_semana}

    proyectos_out = []
    for r in reportes:
        trabajadores_in_project = db.session.query(
            RegistroDiarioHoras.trabajador_id,
        ).filter(RegistroDiarioHoras.reporte_id == r.id).distinct().all()
        t_ids = [t[0] for t in trabajadores_in_project]
        prens = [prenominas_dict[tid] for tid in t_ids if tid in prenominas_dict]

        proyectos_out.append({
            'proyecto': {
                'id': r.proyecto.id,
                'numero_proyecto': r.proyecto.numero_proyecto,
                'nombre': r.proyecto.nombre or '',
            },
            'fecha_fin': r.fecha_fin_semana.isoformat() if r.fecha_fin_semana else None,
            'prenominas': [_prenomina_to_dict(p) for p in prens],
            'total_deposit': sum(float(p.total_a_pagar or 0) for p in prens),
        })

    return jsonify({
        'fecha': fecha_str,
        'proyectos': proyectos_out,
    })


@bp.route('/<string:fecha_str>/proyecto/<int:proyecto_id>/pdf', methods=['GET'])
@jwt_required
@limiter.limit('10 per minute')
def imprimir_proyecto_pdf(fecha_str, proyecto_id):
    err = _admin_only()
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

    from io import BytesIO
    from xhtml2pdf import pisa

    logo_path = os.path.join(current_app.static_folder, 'imagenes', 'skilled_white_bg.jpg')

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
    err = _admin_only()
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
