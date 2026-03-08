from flask import Blueprint, render_template, flash, redirect, url_for, make_response, request
from app.utils import login_required
from app.extensions import db
from app.models import Prenomina, ReporteSemanal, RegistroDiarioHoras, Proyecto
from sqlalchemy import func
from datetime import datetime

bp = Blueprint('historico', __name__, url_prefix='/historico')

@bp.route('/')
@login_required
def index():
    page = request.args.get('page', 1, type=int)
    search_date_str = request.args.get('search_date', '')

    query = db.session.query(Prenomina.fecha_inicio).filter_by(estado='APROBADO')
    
    if search_date_str:
        try:
            search_date_obj = datetime.strptime(search_date_str, '%Y-%m-%d').date()
            query = query.filter_by(fecha_inicio=search_date_obj)
        except ValueError:
            pass

    pagination = query.distinct().order_by(Prenomina.fecha_inicio.desc()).paginate(page=page, per_page=20, error_out=False)
    fechas_list = [f[0] for f in pagination.items]
    
    semanas = []
    for f in fechas_list:
        reportes = ReporteSemanal.query.filter_by(fecha_inicio_semana=f, estado='PRENOMINA_CERRADA').all()
        semanas.append({
            'fecha_inicio': f,
            'fecha_str': f.strftime('%Y-%m-%d'),
            'proyectos': reportes
        })
        
    return render_template('historico_nominas.html', fechas=fechas_list, semanas=semanas, pagination=pagination, search_date=search_date_str)

@bp.route('/detalle/<fecha_str>')
@login_required
def detalle(fecha_str):
    try:
        fecha_obj = datetime.strptime(fecha_str, '%Y-%m-%d').date()
    except ValueError:
        flash("Fecha inválida.", "danger")
        return redirect(url_for('historico.index'))

    # Get all reports processed that week
    reportes = ReporteSemanal.query.filter_by(fecha_inicio_semana=fecha_obj, estado='PRENOMINA_CERRADA').all()
    if not reportes:
        flash("No hay proyectos cerrados para esta semana.", "warning")
        return redirect(url_for('historico.index'))

    reporte_ids = [r.id for r in reportes]
    
    # Get physical workers that have an approved global prenomina that week
    prenominas_semana = Prenomina.query.filter_by(fecha_inicio=fecha_obj, estado='APROBADO').all()
    prenominas_dict = {p.trabajador_id: p for p in prenominas_semana}

    # Structure data by project
    proyectos_data = {}
    for r in reportes:
        # Find all workers that participated in this specific project this week
        trabajadores_in_project = db.session.query(RegistroDiarioHoras.trabajador_id).filter(
            RegistroDiarioHoras.reporte_id == r.id, 
            RegistroDiarioHoras.horas_productivas > 0
        ).distinct().all()
        
        t_ids = [t[0] for t in trabajadores_in_project]
        
        # Only attach prenominas if the worker worked > 0 hrs in this project
        prens_in_project = [prenominas_dict[tid] for tid in t_ids if tid in prenominas_dict]
        
        proyectos_data[r.proyecto.id] = {
            'proyecto': r.proyecto,
            'prenominas': prens_in_project,
            'total_deposit': sum(p.total_a_pagar for p in prens_in_project)
        }

    return render_template('historico_detalle.html', fecha_obj=fecha_obj, fecha_str=fecha_str, proyectos_data=proyectos_data)

@bp.route('/imprimir_proyecto/<fecha_str>/<int:proyecto_id>')
@login_required
def imprimir_proyecto(fecha_str, proyecto_id):
    try:
        fecha_obj = datetime.strptime(fecha_str, '%Y-%m-%d').date()
    except ValueError:
        return "Fecha inválida", 400
        
    proyecto = Proyecto.query.get_or_404(proyecto_id)
    reporte = ReporteSemanal.query.filter_by(proyecto_id=proyecto.id, fecha_inicio_semana=fecha_obj, estado='PRENOMINA_CERRADA').first()
    
    if not reporte:
        return "Reporte no encontrado para este proyecto en la semana especificada.", 404
        
    # Find workers that participated in this project this week
    trabajadores_in_project = db.session.query(RegistroDiarioHoras.trabajador_id).filter(
        RegistroDiarioHoras.reporte_id == reporte.id, 
        RegistroDiarioHoras.horas_productivas > 0
    ).distinct().all()
    t_ids = [t[0] for t in trabajadores_in_project]
    
    # Get those workers' global prenominas
    prenominas = Prenomina.query.filter(
        Prenomina.fecha_inicio == fecha_obj, 
        Prenomina.estado == 'APROBADO',
        Prenomina.trabajador_id.in_(t_ids)
    ).all()
    
    from xhtml2pdf import pisa
    from io import BytesIO
    from flask import make_response, current_app
    import os
    
    logo_path = os.path.join(current_app.static_folder, 'imagenes', 'skilled_white_bg.jpg')
    
    html_salida = render_template('recibo_proyecto_pdf.html', 
                                  proyecto=proyecto, 
                                  fecha_obj=fecha_obj, 
                                  fecha_fin=reporte.fecha_fin_semana, 
                                  prenominas=prenominas,
                                  logo_path=logo_path)
    
    pdf = BytesIO()
    pisa_status = pisa.CreatePDF(BytesIO(html_salida.encode('utf-8')), dest=pdf)
    
    if pisa_status.err:
        return "Error al generar el PDF de Resumen.", 500
        
    response = make_response(pdf.getvalue())
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'inline; filename=Lista_Raya_{proyecto.numero_proyecto}_{fecha_obj.strftime("%d%m")}.pdf'
    return response
