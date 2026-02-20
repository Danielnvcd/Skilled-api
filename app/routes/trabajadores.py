from flask import Blueprint, render_template, request, flash, redirect, url_for, jsonify
from app.extensions import db
from app.models import Trabajador
from app.utils import login_required, log_action
import traceback

bp = Blueprint('trabajadores', __name__, url_prefix='/trabajadores')

@bp.route('/', methods=['GET'])
@login_required
def index():
    trabajadores = Trabajador.query.order_by(Trabajador.id).all()
    return render_template('trabajadores.html', trabajadores=trabajadores)

@bp.route('/agregar', methods=['POST'])
@login_required
def agregar():
    try:
        data = request.form
        
        # Basic validation
        if Trabajador.query.filter_by(no_empleado=data.get('no_empleado')).first():
            flash('Error: El Número de Empleado ya existe.', 'danger')
            return redirect(url_for('trabajadores.index'))

        nuevo_trabajador = Trabajador(
            # Identificadores
            no_empleado=data.get('no_empleado'),
            nombre_apellidos=data.get('nombre_apellidos'),
            nombre=data.get('nombre'),
            
            # Laborales
            tipo_mov=data.get('tipo_mov'),
            tipo_cont=data.get('tipo_cont'),
            area=data.get('area'),
            puesto=data.get('puesto'),
            tipo_jornada=data.get('tipo_jornada'),
            fecha_ingreso=data.get('fecha_ingreso') if data.get('fecha_ingreso') else None,
            
            # Generales
            curp=data.get('curp'),
            rfc=data.get('rfc'),
            nss=data.get('nss'),
            fecha_nacimiento=data.get('fecha_nacimiento') if data.get('fecha_nacimiento') else None,
            sexo=data.get('sexo'),
            estado_civil=data.get('estado_civil'),
            nacionalidad=data.get('nacionalidad'),
            
            # Datos de contacto
            correo=data.get('correo'),
            celular=data.get('celular'),
            
            # Médicos
            tipo_sangre=data.get('tipo_sangre'),
            alergias=data.get('alergias'),
            contacto_emergencia=data.get('contacto_emergencia'),
            numero_contacto_emerg=data.get('numero_contacto_emerg'),
            
            # Sueldos (Finanzas)
            salario_real_pactado_x_sem=data.get('salario_real_pactado_x_sem') or 0,
            tipo_pago=data.get('tipo_pago')
        )
        
        db.session.add(nuevo_trabajador)
        db.session.commit()
        log_action(f"Agregó al trabajador {nuevo_trabajador.nombre} ({nuevo_trabajador.no_empleado})")
        flash('Trabajador agregado exitosamente.', 'success')
        
    except Exception as e:
        db.session.rollback()
        print(f"Error saving worker: {e}\n{traceback.format_exc()}")
        flash('Ocurrió un error al guardar el trabajador. Verifica los datos.', 'danger')
        
    return redirect(url_for('trabajadores.index'))
