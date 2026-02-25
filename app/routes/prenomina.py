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
    
    semanas = {}
    for r in reportes:
        fecha_str = r.fecha_inicio_semana.strftime('%Y-%m-%d')
        if fecha_str not in semanas:
            semanas[fecha_str] = {
                'fecha_inicio_semana': r.fecha_inicio_semana,
                'fecha_fin_semana': r.fecha_fin_semana,
                'fecha_str': fecha_str,
                'proyectos': [],
                'estado': 'PRENOMINA_CERRADA'
            }
        semanas[fecha_str]['proyectos'].append(r.proyecto)
        if r.estado != 'PRENOMINA_CERRADA':
            semanas[fecha_str]['estado'] = 'TERMINADO'
            
    # Prenóminas ya procesadas (solo extraemos las fechas de inicio que ya están en DB)
    prenominas_fechas = { p.fecha_inicio.strftime('%Y-%m-%d') for p in Prenomina.query.all() }
    
    semanas_list = sorted(semanas.values(), key=lambda x: x['fecha_inicio_semana'], reverse=True)
    
    return render_template('prenomina.html', semanas=semanas_list, prenominas_fechas=prenominas_fechas)

@bp.route('/generar/<fecha_str>', methods=['GET'])
@login_required
def generar(fecha_str):
    try:
        fecha_obj = datetime.strptime(fecha_str, '%Y-%m-%d').date()
    except ValueError:
        flash("Formato de fecha inválido.", "danger")
        return redirect(url_for('prenomina.index'))
        
    reportes = ReporteSemanal.query.filter(
        ReporteSemanal.fecha_inicio_semana == fecha_obj,
        ReporteSemanal.estado.in_(['TERMINADO', 'PRENOMINA_CERRADA'])
    ).all()
    
    if not reportes:
        flash("Las nóminas solo se pueden calcular para semanas de trabajo CERRADAS.", "warning")
        return redirect(url_for('prenomina.index'))
        
    prenominas = Prenomina.query.filter_by(fecha_inicio=fecha_obj).all()
    
    ya_guardada = len(prenominas) > 0

    # Si aún no hay prenominas guardadas en BBDD para esta semana consolida, generamos el preview
    if not prenominas:
        prenominas = calcular_preview_prenomina(fecha_obj, reportes)
        
    proyectos_involucrados = [r.proyecto for r in reportes]
        
    return render_template('prenomina_generar.html', fecha_inicio=fecha_obj, fecha_fin=reportes[0].fecha_fin_semana, fecha_str=fecha_str, prenominas=prenominas, proyectos_involucrados=proyectos_involucrados, ya_guardada=ya_guardada)

@bp.route('/imprimir/<fecha_str>', methods=['GET'])
@login_required
def imprimir(fecha_str):
    try:
        fecha_obj = datetime.strptime(fecha_str, '%Y-%m-%d').date()
    except ValueError:
        return "Fecha inválida", 400
        
    reportes = ReporteSemanal.query.filter(
        ReporteSemanal.fecha_inicio_semana == fecha_obj,
        ReporteSemanal.estado.in_(['TERMINADO', 'PRENOMINA_CERRADA'])
    ).all()
    
    if not reportes:
        flash("Solo se pueden imprimir nóminas de semanas cerradas.", "warning")
        return redirect(url_for('prenomina.index'))
        
    prenominas = Prenomina.query.filter_by(fecha_inicio=fecha_obj).all()
    if not prenominas:
        prenominas = calcular_preview_prenomina(fecha_obj, reportes)
        
    from xhtml2pdf import pisa
    from io import BytesIO
    from flask import make_response, current_app
    import os
    
    recibos_data = []
    reporte_ids = [r.id for r in reportes]
    # Usamos el primer reporte_id temporalmente solo para info genérica (el UI será refactorizado)
    reporte_generico = reportes[0]
    
    for p in prenominas:
        # Obtener los registros consolidados del trabajador (todos los proyectos de esta semana)
        registros_trabajador = RegistroDiarioHoras.query.filter(
            RegistroDiarioHoras.reporte_id.in_(reporte_ids), 
            RegistroDiarioHoras.trabajador_id == p.trabajador_id
        ).order_by(RegistroDiarioHoras.fecha).all()
        
        total_hrs = sum(r.horas_productivas or 0 for r in registros_trabajador)
        
        recibos_data.append({
            'p': p,
            'registros_trabajador': registros_trabajador,
            'total_hrs': total_hrs
        })
        
    logo_path = os.path.join(current_app.static_folder, 'imagenes', 'skilled_white_bg.jpg')
    html_salida = render_template('recibo_pdf.html', reporte=reporte_generico, recibos_data=recibos_data, logo_path=logo_path)
        
    pdf = BytesIO()
    pisa_status = pisa.CreatePDF(BytesIO(html_salida.encode('utf-8')), dest=pdf)
    
    if pisa_status.err:
        flash("Hubo un error interno al generar el PDF.", "danger")
        return redirect(url_for('prenomina.generar', fecha_str=fecha_str))
        
    response = make_response(pdf.getvalue())
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'inline; filename=Prenomina_Consolidada_{fecha_obj.strftime("%d%m")}.pdf'
    return response

@bp.route('/imprimir_individual/<fecha_str>/<int:trabajador_id>', methods=['GET'])
@login_required
def imprimir_individual(fecha_str, trabajador_id):
    try:
        fecha_obj = datetime.strptime(fecha_str, '%Y-%m-%d').date()
    except ValueError:
        return "Fecha inválida", 400
        
    reportes = ReporteSemanal.query.filter(
        ReporteSemanal.fecha_inicio_semana == fecha_obj,
        ReporteSemanal.estado.in_(['TERMINADO', 'PRENOMINA_CERRADA'])
    ).all()
    
    if not reportes:
        flash("Solo se pueden imprimir nóminas de semanas cerradas.", "warning")
        return redirect(url_for('prenomina.index'))
        
    # See if it's already saved
    prenomina = Prenomina.query.filter_by(fecha_inicio=fecha_obj, trabajador_id=trabajador_id).first()
    
    # If not saved, generate it from the preview function
    if not prenomina:
        all_prenominas = calcular_preview_prenomina(fecha_obj, reportes)
        prenomina = next((p for p in all_prenominas if p.trabajador_id == trabajador_id), None)
        
    if not prenomina:
        return "Trabajador no encontrado en esta nómina.", 404
        
    from xhtml2pdf import pisa
    from io import BytesIO
    from flask import make_response, current_app
    import os
    
    reporte_ids = [r.id for r in reportes]
    reporte_generico = reportes[0]
    
    registros_trabajador = RegistroDiarioHoras.query.filter(
        RegistroDiarioHoras.reporte_id.in_(reporte_ids), 
        RegistroDiarioHoras.trabajador_id == prenomina.trabajador_id
    ).order_by(RegistroDiarioHoras.fecha).all()
    
    total_hrs = sum(r.horas_productivas or 0 for r in registros_trabajador)
    
    logo_path = os.path.join(current_app.static_folder, 'imagenes', 'skilled_white_bg.jpg')
    html_salida = render_template('recibo_pdf.html', reporte=reporte_generico, p=prenomina, 
                                   registros_trabajador=registros_trabajador, 
                                   total_hrs=total_hrs,
                                   loop_last=True,
                                   logo_path=logo_path)
                                   
    pdf = BytesIO()
    pisa_status = pisa.CreatePDF(BytesIO(html_salida.encode('utf-8')), dest=pdf)
    
    if pisa_status.err:
        flash("Hubo un error interno al generar el PDF.", "danger")
        return redirect(url_for('prenomina.generar', fecha_str=fecha_str))
        
    response = make_response(pdf.getvalue())
    response.headers['Content-Type'] = 'application/pdf'
    
    # Nombre del archivo con Nombre del Trabajador, Fecha y Hora exacta de descarga
    ahora = datetime.now()
    nombre_limpio = prenomina.trabajador.nombre_apellidos.replace(' ', '_')
    timestamp = ahora.strftime("%Y-%m-%d_%H-%M-%S")
    nombre_archivo = f'Recibo_{nombre_limpio}_{timestamp}.pdf'
    
    response.headers['Content-Disposition'] = f'inline; filename={nombre_archivo}'
    return response

@bp.route('/guardar/<fecha_str>', methods=['POST'])
@login_required
def guardar(fecha_str):
    try:
        fecha_obj = datetime.strptime(fecha_str, '%Y-%m-%d').date()
        reportes = ReporteSemanal.query.filter(
            ReporteSemanal.fecha_inicio_semana == fecha_obj,
            ReporteSemanal.estado.in_(['TERMINADO', 'PRENOMINA_CERRADA'])
        ).all()
        
        if not reportes:
             return jsonify({'success': False, 'message': 'Solo se pueden guardar nóminas de semanas cerradas o ya está guardada.'}), 400
             
        # Revisamos si ya existe una prenómina guardada
        prenominas = Prenomina.query.filter_by(fecha_inicio=fecha_obj).all()
        if not prenominas:
            nuevas_prenominas = calcular_preview_prenomina(fecha_obj, reportes)
            for p in nuevas_prenominas:
                p.reporte_semanal_id = None # Es global, no atado a un reporte individual
                p.estado = 'APROBADO'
                db.session.add(p)
                
            for r in reportes:
                r.estado = 'PRENOMINA_CERRADA'
            db.session.commit()
            
            log_action('crear_prenomina', f'Prenómina guardada y cerrada globalmente para la semana {fecha_str}')
            return jsonify({'success': True, 'message': 'Nómina global generada y guardada correctamente.'})
        else:
            return jsonify({'success': False, 'message': 'La prenómina para esta semana ya fue guardada anteriormente.'}), 400
            
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error al guardar prenómina global: {traceback.format_exc()}")
        return jsonify({'success': False, 'message': 'Ocurrió un error al intentar guardar la prenómina.'}), 500

def calcular_preview_prenomina(fecha_obj, reportes):
    """
    Función utilitaria (lógica de negocio).
    Toma una lista de ReporteSemanal de todos los proyectos de ESA semana,
    y traduce esas horas sumadas para cada trabajador devolviendo Prenominas globales simuladas.
    """
    preview = []
    
    # Obtenemos ids únicos de los trabajadores involucrados en esta semana
    trabajadores_ids = set()
    for r in reportes:
        for reg in r.registros:
            trabajadores_ids.add(reg.trabajador_id)
            
    fecha_fin_semana = reportes[0].fecha_fin_semana if reportes else None
    
    for t_id in trabajadores_ids:
        trabajador = Trabajador.query.get(t_id)
        
        # Juntar todos los registros del trabajador en los múltiples reportes
        registros_trabajador = []
        for r in reportes:
            registros_trabajador.extend([reg for reg in r.registros if reg.trabajador_id == t_id])
        
        # 1. Totalizar horas productivas consolidando proyectos
        total_horas = sum(r.horas_productivas or 0 for r in registros_trabajador)
        
        # Viáticos: asumiremos que cada día laborado con cualquier proyecto cuenta como 1 día (fechas únicas)
        fechas_laboradas = set(r.fecha for r in registros_trabajador if r.horas_productivas and r.horas_productivas > 0)
        dias_capturados = len(fechas_laboradas)
        
        p = Prenomina(
            reporte_semanal_id=None,
            trabajador_id=trabajador.id,
            trabajador=trabajador,
            fecha_inicio=fecha_obj,
            fecha_fin=fecha_fin_semana,
            tipo_pago=trabajador.tipo_pago or 'EFECTIVO',
            pago_festivos=0.0,
            depositos_otros=0.0,
            depositos_prestamos=0.0,
            descuentos_otros=0.0,
            descuento_prestamos=0.0,
            descuento_incidencias=0.0,
            recuperacion_manual=0.0
        )
        
        tipo = trabajador.tipo_nomina or 'Semanal'
        p.salario_base = 0.0
        p.pago_horas_extras = 0.0
        
        salario_pactado = float(trabajador.salario_real_pactado_x_sem or 0)
        
        if tipo == 'Por hora':
            p.salario_base = float(total_horas) * salario_pactado
        elif tipo == 'Cuadrado':
            p.salario_base = salario_pactado
        else: # Semanal
            p.salario_base = salario_pactado
            if total_horas > 50:
                horas_extras = float(total_horas) - 50.0
                costo_hr_extra = float(trabajador.hr_extra or 0)
                p.pago_horas_extras = horas_extras * costo_hr_extra
        
        # Deducciones maestras
        p.descuento_infonavit = float(trabajador.infonavit or 0)
        p.ajuste_inbursa = float(trabajador.ajuste_inbursa or 0)
        
        # Cálculo de Incidencias Consolidadas
        total_descuento_incidencias = 0.0
        if tipo in ['Semanal', 'Cuadrado']:
            horas_ausentes_incidencia = 0.0
            incidencias_descontables = ['Falta', 'Retardo', 'Falta checada de entrada', 'Falta checada de salida', 'Permiso', 'Luto', 'Casamiento']
            
            for reg in registros_trabajador:
                if reg.incidencia in incidencias_descontables:
                    # En lugar de castigar doble si el empleado faltó por error a 2 proyectos en el mismo día
                    # es más seguro calcular 10hrs por día que se declaró una falta (pero podría no ser exacto en combinaciones).
                    # De momento mantenemos el sumatorio de incidencias tal cual:
                    if not reg.horas_productivas or reg.horas_productivas == 0:
                        horas_ausentes_incidencia += 10.0
                        
            if horas_ausentes_incidencia > 0:
                 costo_hora_ord = salario_pactado / 50.0
                 total_descuento_incidencias = horas_ausentes_incidencia * costo_hora_ord
                 p.descuento_incidencias = total_descuento_incidencias
                 
        if trabajador.viaticos:
             p.pago_viaticos = float(trabajador.viaticos) * dias_capturados
        else:
             p.pago_viaticos = 0.0
             
        p.total_percepciones = p.salario_base + p.pago_horas_extras + p.pago_viaticos
        p.total_deducciones = p.descuento_infonavit + p.ajuste_inbursa + float(p.descuento_incidencias or 0)
        p.total_a_pagar = p.total_percepciones - p.total_deducciones
        
        preview.append(p)
        
    return preview
