from flask import Blueprint, render_template, request, flash, redirect, url_for, jsonify, current_app
from app.extensions import db
from app.models import Proyecto, Trabajador, User
from app.utils import login_required, log_action
import traceback
import json

bp = Blueprint('proyectos', __name__, url_prefix='/proyectos')

@bp.route('/', methods=['GET'])
@login_required
def index():
    proyectos = Proyecto.query.all()
    # Para el modal, necesitamos todos los trabajadores
    trabajadores = Trabajador.query.filter_by(activo=True).order_by(Trabajador.nombre).all()
    coordinadores = User.query.filter(User.role.in_(['coordinador', 'admin'])).order_by(User.username).all()
    return render_template('proyectos.html', proyectos=proyectos, trabajadores=trabajadores, coordinadores=coordinadores)

@bp.route('/agregar', methods=['POST'])
@login_required
def agregar():
    try:
        data = request.form
        
        numero_proyecto = data.get('numero_proyecto', '').strip()
        nombre = data.get('nombre', '').strip()
        
        if not numero_proyecto or not nombre:
            flash('Error: Número de Proyecto y Nombre son obligatorios.', 'danger')
            return redirect(url_for('proyectos.index'))
        
        # Validar si el proyecto ya existe
        if Proyecto.query.filter_by(numero_proyecto=numero_proyecto).first():
            flash('Error: El Número de Proyecto ya existe.', 'danger')
            return redirect(url_for('proyectos.index'))
            
        nuevo_proyecto = Proyecto(
            numero_proyecto=numero_proyecto,
            nombre=nombre,
            activo=data.get('activo') == 'true' or data.get('activo') == 'on' or data.get('activo', '').lower() == 'true',
            coordinador_id=data.get('coordinador_id') if data.get('coordinador_id') else None
        )
        
        coordinador_name = None
        if nuevo_proyecto.coordinador_id:
            sup = User.query.get(nuevo_proyecto.coordinador_id)
            if sup:
                coordinador_name = sup.username
        
        # Checkbox is simple 'on' if checked, else omitted in form.
        nuevo_proyecto.activo = 'activo' in data
        
        # Relaciones Many-to-Many para Participantes
        try:
            participantes_str = data.get('participantes_json', '[]')
            participantes_ids = json.loads(participantes_str)
            for t_id in participantes_ids:
                trabajador = Trabajador.query.get(t_id)
                if trabajador:
                    nuevo_proyecto.participantes.append(trabajador)
                    # Sync worker's location fields
                    trabajador.no_proyecto = nuevo_proyecto.numero_proyecto
                    trabajador.ubicacion_actual = nuevo_proyecto.nombre
                    if coordinador_name:
                        trabajador.coord_a_cargo = coordinador_name
        except Exception as e:
            current_app.logger.error(f"Error parseando participantes: {e}")
            flash('Advertencia: Hubo problemas al agregar algunos participantes.', 'warning')
            
        db.session.add(nuevo_proyecto)
        db.session.commit()
        log_action(f"Creó el proyecto {nuevo_proyecto.numero_proyecto} - {nuevo_proyecto.nombre}")
        flash('Proyecto registrado exitosamente.', 'success')
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error creando proyecto: {e}\n{traceback.format_exc()}")
        flash('Ocurrió un error al crear el proyecto.', 'danger')
        
    return redirect(url_for('proyectos.index'))
    
@bp.route('/get/<int:id>')
@login_required
def get_proyecto(id):
    p = Proyecto.query.get_or_404(id)
    participantes_ids = [t.id for t in p.participantes]
    
    data = {
        'id': p.id,
        'numero_proyecto': p.numero_proyecto,
        'nombre': p.nombre or '',
        'activo': p.activo,
        'coordinador_id': p.coordinador_id or '',
        'participantes_ids': participantes_ids
    }
    return jsonify(data)

@bp.route('/editar/<int:id>', methods=['POST'])
@login_required
def editar(id):
    try:
        p = Proyecto.query.get_or_404(id)
        data = request.form
        
        # Check uniqueness of no_proyecto if it changed
        new_no = data.get('numero_proyecto')
        if new_no != p.numero_proyecto and Proyecto.query.filter_by(numero_proyecto=new_no).first():
            flash('Error: El Número de Proyecto ya existe.', 'danger')
            return redirect(url_for('proyectos.index'))
            
        # Guardar valor original ANTES de mutar
        old_numero_proyecto = p.numero_proyecto
        
        p.numero_proyecto = new_no
        p.nombre = data.get('nombre')
        p.activo = 'activo' in data
        p.coordinador_id = data.get('coordinador_id') if data.get('coordinador_id') else None
        
        coordinador_name = None
        if p.coordinador_id:
            sup = User.query.get(p.coordinador_id)
            if sup:
                coordinador_name = sup.username
        
        # Participantes
        try:
            participantes_str = data.get('participantes_json', '[]')
            participantes_ids = json.loads(participantes_str)
            
            # Find users who were removed from the project and clear their data
            current_participants_ids = [str(t.id) for t in p.participantes]
            new_participants_ids_str = [str(t_id) for t_id in participantes_ids]
            
            for past_worker in p.participantes:
                if str(past_worker.id) not in new_participants_ids_str:
                    # Comparar contra el número VIEJO del proyecto
                    if past_worker.no_proyecto == old_numero_proyecto:
                        past_worker.no_proyecto = None
                        past_worker.ubicacion_actual = None
                        past_worker.coord_a_cargo = None
            
            p.participantes = [] # Clear current
            
            for t_id in participantes_ids:
                trabajador = Trabajador.query.get(t_id)
                if trabajador:
                    p.participantes.append(trabajador)
                    # Sync info
                    trabajador.no_proyecto = p.numero_proyecto
                    trabajador.ubicacion_actual = p.nombre
                    if coordinador_name:
                        trabajador.coord_a_cargo = coordinador_name
                    
        except Exception as e:
            current_app.logger.error(f"Error parseando participantes (edicion): {e}")
            flash('Advertencia: Hubo un problema al actualizar los participantes.', 'warning')
            
        db.session.commit()
        log_action(f"Actualizó el proyecto {p.numero_proyecto} - {p.nombre}")
        flash('Proyecto modificado exitosamente.', 'success')
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error actualizando proyecto: {e}\n{traceback.format_exc()}")
        flash('Falló la modificación del proyecto.', 'danger')
        
    return redirect(url_for('proyectos.index'))
