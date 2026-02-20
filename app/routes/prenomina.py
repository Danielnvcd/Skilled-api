from flask import Blueprint, render_template, request, flash, redirect, url_for, jsonify, current_app
from datetime import datetime, timedelta
import traceback
from app.extensions import db
from app.models import ReporteSemanal, Prenomina, Trabajador, Prestamo, RegistroDiarioHoras
from app.utils import login_required, log_action

bp = Blueprint('prenomina', __name__, url_prefix='/prenomina')

@bp.route('/')
@login_required
def index():
    # Obtener reportes TERMINADOS (pendientes) o PRENOMINA_CERRADA (históricos)
    reportes = ReporteSemanal.query.filter(ReporteSemanal.estado.in_(['TERMINADO', 'PRENOMINA_CERRADA'])).order_by(ReporteSemanal.fecha_inicio_semana.desc()).all()
    # Prenóminas ya procesadas
    prenominas = Prenomina.query.all()
    
    # Mapear reportes que ya tienen prenómina iniciada/generada
    reportes_procesados_ids = {p.reporte_semanal_id for p in prenominas if p.reporte_semanal_id}
    
    return render_template('prenomina.html', reportes=reportes, reportes_procesados_ids=reportes_procesados_ids)

@bp.route('/generar/<int:reporte_id>', methods=['GET'])
@login_required
def generar(reporte_id):
    reporte = ReporteSemanal.query.get_or_404(reporte_id)
    if reporte.estado != 'TERMINADO':
        flash("Las nóminas solo se pueden calcular para semanas de trabajo CERRADAS por el coordinador.", "warning")
        return redirect(url_for('prenomina.index'))
        
    prenominas = Prenomina.query.filter_by(reporte_semanal_id=reporte.id).all()
    
    # Si aún no hay prenominas guardadas en BBDD para este reporte, generamos un preview en memoria (cálculo inicial)
    if not prenominas:
        prenominas = calcular_preview_prenomina(reporte)
        
    return render_template('prenomina_generar.html', reporte=reporte, prenominas=prenominas)

@bp.route('/imprimir/<int:reporte_id>', methods=['GET'])
@login_required
def imprimir(reporte_id):
    reporte = ReporteSemanal.query.get_or_404(reporte_id)
    if reporte.estado != 'TERMINADO':
        flash("Solo se pueden imprimir nóminas de semanas cerradas.", "warning")
        return redirect(url_for('prenomina.index'))
        
    prenominas = Prenomina.query.filter_by(reporte_semanal_id=reporte.id).all()
    if not prenominas:
        prenominas = calcular_preview_prenomina(reporte)
        
    from xhtml2pdf import pisa
    from io import BytesIO
    from flask import make_response
    
    html_salida = ""
    for idx, p in enumerate(prenominas):
        html_salida += render_template('recibo_pdf.html', reporte=reporte, p=p, loop_last=(idx == len(prenominas) - 1))
        
    pdf = BytesIO()
    pisa_status = pisa.CreatePDF(BytesIO(html_salida.encode('utf-8')), dest=pdf)
    
    if pisa_status.err:
        flash("Hubo un error interno al generar el PDF.", "danger")
        return redirect(url_for('prenomina.generar', reporte_id=reporte.id))
        
    response = make_response(pdf.getvalue())
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'inline; filename=Prenomina_P{reporte.proyecto.numero_proyecto}_{reporte.fecha_inicio_semana.strftime("%d%m")}.pdf'
    return response

@bp.route('/guardar/<int:reporte_id>', methods=['POST'])
@login_required
def guardar(reporte_id):
    try:
        reporte = ReporteSemanal.query.get_or_404(reporte_id)
        if reporte.estado != 'TERMINADO':
            return jsonify({'success': False, 'message': 'Solo se pueden guardar nóminas de semanas cerradas.'}), 400
            
        # Revisamos si ya existe una prenómina guardada
        prenominas = Prenomina.query.filter_by(reporte_semanal_id=reporte.id).all()
        if not prenominas:
            # Si no existe, calculamos y guardamos la persistencia en DB
            nuevas_prenominas = calcular_preview_prenomina(reporte)
            for p in nuevas_prenominas:
                p.estado = 'APROBADO' # Indicando que esta prenómina ha sido asentada
                db.session.add(p)
                
            reporte.estado = 'PRENOMINA_CERRADA'
            db.session.commit()
            
            log_action('crear_prenomina', f'Prenómina guardada y cerrada para el reporte ID {reporte.id}')
            return jsonify({'success': True, 'message': 'Nómina generada y guardada correctamente.'})
        else:
            return jsonify({'success': False, 'message': 'La prenómina para esta semana ya fue guardada anteriormente.'}), 400
            
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error al guardar prenómina: {traceback.format_exc()}")
        return jsonify({'success': False, 'message': 'Ocurrió un error al intentar guardar la prenómina.'}), 500

def calcular_preview_prenomina(reporte):
    """
    Función utilitaria (lógica de negocio).
    Toma un ReporteSemanal (TERMINADO) con su RegistroDiarioHoras,
    y traduce esas horas en pesos (Percepciones y Deducciones) devolviendo objetos Prenomina no persistidos aún.
    """
    preview = []
    
    # Obtenemos ids de los trabajadores involucrados en este reporte semanal
    trabajadores_ids = {reg.trabajador_id for reg in reporte.registros}
    
    for t_id in trabajadores_ids:
        trabajador = Trabajador.query.get(t_id)
        registros_trabajador = [r for r in reporte.registros if r.trabajador_id == t_id]
        
        # 1. Totalizar horas productivas
        total_horas = sum(r.horas_productivas or 0 for r in registros_trabajador)
        dias_capturados = len(registros_trabajador)
        
        # Objeto Prenomina base
        p = Prenomina(
            reporte_semanal_id=reporte.id,
            trabajador_id=trabajador.id,
            trabajador=trabajador, # Para uso en template sin hacer commit
            fecha_inicio=reporte.fecha_inicio_semana,
            fecha_fin=reporte.fecha_fin_semana,
            tipo_pago=trabajador.tipo_pago or 'EFECTIVO'
        )
        
        # 2. Reglas Financieras según Tipo de Nómina
        tipo = trabajador.tipo_nomina or 'Semanal'
        
        if tipo == 'Cuadrado':
            # Pago fijo. No extras.
            p.salario_base = trabajador.salario_real_pactado_x_sem or 0
        elif tipo == 'Por hora':
            # Salario Real * total_horas
            # asumiendo que salario_real... guarda el COSTO POR HORA para este tipo de empleado
            costo_hr = trabajador.salario_real_pactado_x_sem or 0
            p.salario_base = float(total_horas) * float(costo_hr)
        else:
            # Semanal
            p.salario_base = trabajador.salario_real_pactado_x_sem or 0
            # TODO: Add specific over-time logic for "Semanal" if it exceeds 48 hours for example.
        
        # Deducciones maestras
        p.descuento_infonavit = trabajador.infonavit or 0
        p.ajuste_inbursa = trabajador.ajuste_inbursa or 0
        
        # Calculo Viaticos (dias lab * costo viatico si lo tuviera)
        if trabajador.viaticos:
             p.pago_viaticos = float(trabajador.viaticos) * dias_capturados
             
        # Actualizamos totales provisionales
        p.total_percepciones = p.salario_base + p.pago_viaticos
        p.total_deducciones = p.descuento_infonavit + p.ajuste_inbursa
        p.total_a_pagar = p.total_percepciones - p.total_deducciones
        
        preview.append(p)
        
    return preview
