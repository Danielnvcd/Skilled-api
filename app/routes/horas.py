from flask import Blueprint, render_template, request, flash, redirect, url_for, jsonify, session
from app.extensions import db
from app.models import Proyecto, ReporteSemanal, RegistroDiarioHoras, Trabajador
from app.utils import login_required, log_action
import traceback
from datetime import datetime, timedelta

bp = Blueprint('horas', __name__, url_prefix='/horas')

@bp.route('/', methods=['GET'])
@login_required
def index():
    user_id = session.get('user_id')
    user_role = session.get('role', 'user')

    if user_role == 'coordinador':
        proyectos = Proyecto.query.filter_by(activo=True, coordinador_id=user_id).all()
        proyecto_ids = [p.id for p in proyectos] if proyectos else []
        reportes = ReporteSemanal.query.filter(ReporteSemanal.proyecto_id.in_(proyecto_ids)).order_by(ReporteSemanal.created_at.desc()).all() if proyecto_ids else []
    else:
        reportes = ReporteSemanal.query.order_by(ReporteSemanal.created_at.desc()).all()
        proyectos = Proyecto.query.filter_by(activo=True).all()

    return render_template('horas.html', reportes=reportes, proyectos=proyectos)

@bp.route('/crear_reporte', methods=['POST'])
@login_required
def crear_reporte():
    try:
        data = request.form
        proyecto_id = data.get('proyecto_id')
        fecha_inicio_str = data.get('fecha_inicio_semana')
        fecha_fin_str = data.get('fecha_fin_semana')
        
        if not proyecto_id or not fecha_inicio_str or not fecha_fin_str:
            flash("Todos los campos son requeridos para abrir un reporte.", "warning")
            return redirect(url_for('horas.index'))

        fecha_inicio = datetime.strptime(fecha_inicio_str, '%Y-%m-%d').date()
        fecha_fin = datetime.strptime(fecha_fin_str, '%Y-%m-%d').date()

        nuevo_reporte = ReporteSemanal(
            proyecto_id=proyecto_id,
            fecha_inicio_semana=fecha_inicio,
            fecha_fin_semana=fecha_fin,
            estado='BORRADOR'
        )
        db.session.add(nuevo_reporte)
        db.session.commit()
        log_action(f"Abrió nuevo reporte semanal para el proyecto ID: {proyecto_id}")
        flash('Reporte Semanal abierto exitosamente. Ahora puedes capturar horas.', 'success')
        
    except Exception as e:
        db.session.rollback()
        print(f"Error creating report: {e}\n{traceback.format_exc()}")
        flash('Ocurrió un error al abrir el reporte semanal.', 'danger')
        
    return redirect(url_for('horas.index'))

@bp.route('/capturar/<int:reporte_id>', methods=['GET'])
@login_required
def capturar(reporte_id):
    reporte = ReporteSemanal.query.get_or_404(reporte_id)
    
    user_id = session.get('user_id')
    user_role = session.get('role', 'user')
    
    if user_role == 'coordinador' and reporte.proyecto.coordinador_id != user_id:
        flash("Acceso denegado. No eres coordinador de este proyecto.", "danger")
        return redirect(url_for('horas.index'))
        
    trabajadores = Trabajador.query.order_by(Trabajador.nombre).all()
    
    # Generate 30-min intervals for dropdowns
    horas_dropdown = []
    for h in range(0, 24):
        horas_dropdown.append(f"{h:02d}:00")
        horas_dropdown.append(f"{h:02d}:30")
        
    incidencias_lista = [
        "Casamiento", "Descanso", "Falta", "Incapacidad", "Luto", "Paternidad", 
        "Permiso", "Time x Time", "Vacaciones", "Viaje de Ida", "Viaje de vuelta a Pue.", 
        "Retardo", "Levantamiento en campo", "Falta checada de entrada", "Falta checada de salida"
    ]
    
    return render_template(
        'horas_captura.html', 
        reporte=reporte, 
        trabajadores=trabajadores,
        horas_dropdown=horas_dropdown,
        incidencias_lista=incidencias_lista
    )

@bp.route('/guardar_registro/<int:reporte_id>', methods=['POST'])
@login_required
def guardar_registro(reporte_id):
    reporte = ReporteSemanal.query.get_or_404(reporte_id)
    if reporte.estado != 'BORRADOR':
        flash("Este reporte ya está cerrado.", "warning")
        return redirect(url_for('horas.capturar', reporte_id=reporte.id))

    try:
        data = request.form
        trabajador_id = data.get('trabajador_id')
        fecha_str = data.get('fecha')
        hora_entrada_str = data.get('hora_entrada')
        hora_salida_str = data.get('hora_salida')
        tomo_comida = bool(data.get('tomo_comida'))
        incidencia = data.get('incidencia')
        
        if not trabajador_id or not fecha_str:
            flash("Faltan datos obligatorios.", "danger")
            return redirect(url_for('horas.capturar', reporte_id=reporte.id))
            
        fecha = datetime.strptime(fecha_str, '%Y-%m-%d').date()
        trabajador = Trabajador.query.get(trabajador_id)
        
        hora_entrada = None
        hora_salida = None
        horas_productivas = 0.0

        if hora_entrada_str and hora_salida_str:
            hora_entrada = datetime.strptime(hora_entrada_str, '%H:%M').time()
            hora_salida = datetime.strptime(hora_salida_str, '%H:%M').time()
            
            # Importar cálculo
            from app.utils import calcular_horas_productivas
            horas_productivas = calcular_horas_productivas(
                hora_entrada, 
                hora_salida, 
                tipo_nomina=trabajador.tipo_nomina or 'Semanal', 
                tomo_comida=tomo_comida
            )

        nuevo_registro = RegistroDiarioHoras(
            reporte_id=reporte.id,
            trabajador_id=trabajador.id,
            fecha=fecha,
            hora_entrada=hora_entrada,
            hora_salida=hora_salida,
            tomo_comida=tomo_comida,
            incidencia=incidencia if incidencia else None,
            tipo_nomina=trabajador.tipo_nomina or 'Semanal',
            horas_productivas=horas_productivas
        )
        
        db.session.add(nuevo_registro)
        db.session.commit()
        flash("Registro guardado existosamente.", "success")
        
    except Exception as e:
        db.session.rollback()
        print(f"Error guardando registro: {e}\n{traceback.format_exc()}")
        flash("Error interno al procesar registro de horas.", "danger")

    return redirect(url_for('horas.capturar', reporte_id=reporte.id))

@bp.route('/eliminar_registro/<int:registro_id>', methods=['POST'])
@login_required
def eliminar_registro(registro_id):
    registro = RegistroDiarioHoras.query.get_or_404(registro_id)
    reporte_id = registro.reporte_id
    
    if registro.reporte.estado != 'BORRADOR':
        flash("No se pueden eliminar registros de un reporte cerrado.", "warning")
        return redirect(url_for('horas.capturar', reporte_id=reporte_id))
        
    try:
        db.session.delete(registro)
        db.session.commit()
        flash("Registro eliminado.", "info")
    except Exception as e:
        db.session.rollback()
        flash("Error al eliminar el registro.", "danger")
        
    return redirect(url_for('horas.capturar', reporte_id=reporte_id))

@bp.route('/cerrar_reporte/<int:reporte_id>', methods=['POST'])
@login_required
def cerrar_reporte(reporte_id):
    reporte = ReporteSemanal.query.get_or_404(reporte_id)
    if reporte.estado == 'BORRADOR':
        reporte.estado = 'TERMINADO'
        db.session.commit()
        log_action(f"Cerró reporte semanal ID: {reporte.id} Proyecto: {reporte.proyecto.numero_proyecto}")
        flash("El reporte semanal ha sido CERRADO. Ya no se pueden agregar más horas y está listo para Prenómina.", "success")
        
    return redirect(url_for('horas.capturar', reporte_id=reporte.id))
