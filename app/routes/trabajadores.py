from flask import Blueprint, render_template, request, flash, redirect, url_for, jsonify, current_app, send_from_directory
from app.extensions import db
from app.models import Trabajador, CredencialPlanta, DocumentoTrabajador
from app.utils import login_required, log_action
from werkzeug.utils import secure_filename
import traceback
import json
import os
from datetime import datetime as dt

bp = Blueprint('trabajadores', __name__, url_prefix='/trabajadores')

def _parse_date(value):
    """Convierte string YYYY-MM-DD a date. Retorna None si está vacío o es inválido."""
    if not value:
        return None
    try:
        return dt.strptime(value, '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return None

@bp.route('/', methods=['GET'])
@login_required
def index():
    trabajadores = Trabajador.query.filter_by(activo=True).order_by(Trabajador.id).all()
    return render_template('trabajadores.html', trabajadores=trabajadores)

@bp.route('/bajas', methods=['GET'])
@login_required
def bajas():
    trabajadores = Trabajador.query.filter_by(activo=False).order_by(Trabajador.fecha_baja.desc().nulls_last(), Trabajador.id).all()
    return render_template('trabajadores_bajas.html', trabajadores=trabajadores)

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
            fecha_ingreso=_parse_date(data.get('fecha_ingreso')),
            descripcion_servicio=data.get('descripcion_servicio'),
            inicio=_parse_date(data.get('inicio')),
            termino_prueba=_parse_date(data.get('termino_prueba')),
            fecha_baja=_parse_date(data.get('fecha_baja')),
            
            # Generales
            curp=data.get('curp'),
            rfc=data.get('rfc'),
            nss=data.get('nss'),
            fecha_nacimiento=_parse_date(data.get('fecha_nacimiento')),
            sexo=data.get('sexo'),
            estado_civil=data.get('estado_civil'),
            nacionalidad=data.get('nacionalidad'),
            edad=data.get('edad') or None,
            domicilio=data.get('domicilio'),
            
            # Datos de contacto
            correo=data.get('correo'),
            celular=data.get('celular'),
            
            # Médicos
            tipo_sangre=data.get('tipo_sangre'),
            alergias=data.get('alergias'),
            enfermedades_cronicas=data.get('enfermedades_cronicas'),
            contacto_emergencia=data.get('contacto_emergencia'),
            parentesco_contacto=data.get('parentesco_contacto'),
            numero_contacto_emerg=data.get('numero_contacto_emerg'),
            lentes=data.get('lentes'),
            licencia_conducir=data.get('licencia_conducir'),
            estatura=data.get('estatura'),
            
            # Sueldos (Finanzas)
            salario_real_pactado_x_sem=data.get('salario_real_pactado_x_sem') or 0,
            tipo_pago=data.get('tipo_pago'),
            tipo_nomina=data.get('tipo_nomina'),
            sb=data.get('sb') or 0,
            sdi=data.get('sdi') or 0,
            letra=data.get('letra'),
            hr_extra=data.get('hr_extra') or 0,
            infonavit=data.get('infonavit') or 0,
            ajuste_inbursa=data.get('ajuste_inbursa') or 0,
            caja_ahorro=data.get('caja_ahorro') or 0,
            viaticos=data.get('viaticos') or 0,
            pago_dia_festivo=data.get('pago_dia_festivo') or 0,
            pagos_efectivo=data.get('pagos_efectivo') or 0,
            folio_mov_idse=data.get('folio_mov_idse'),
            
            # Ubicación y Operación (no_proyecto, ubicacion_actual, coord_a_cargo se manejan en Proyectos)
            observaciones=data.get('observaciones')
        )
        
        # Handle Profile Picture
        if 'foto_perfil' in request.files:
            file = request.files['foto_perfil']
            if file and file.filename != '':
                filename = secure_filename(file.filename)
                import time
                unique_filename = f"pp_{int(time.time())}_{filename}"
                pp_folder = os.path.join(current_app.config['UPLOAD_FOLDER'], 'perfiles')
                os.makedirs(pp_folder, exist_ok=True)
                file_path = os.path.join(pp_folder, unique_filename)
                file.save(file_path)
                nuevo_trabajador.foto_perfil = f"perfiles/{unique_filename}"
        
        # Parse Credentials JSON
        try:
            credenciales_str = data.get('credenciales_json', '[]')
            credenciales_data = json.loads(credenciales_str)
            for c_data in credenciales_data:
                nueva_credencial = CredencialPlanta(
                    planta=c_data.get('planta', '').upper(),
                    credencial_id=c_data.get('credencial_id', '')[:40]
                )
                nuevo_trabajador.credenciales.append(nueva_credencial)
        except Exception as json_err:
            print(f"Error parsing credentials: {json_err}")
            # we continue even if credentials fail parsing, though we can flash a warning
            flash('Hubo un problema guardando algunas credenciales de planta.', 'warning')
        
        db.session.add(nuevo_trabajador)
        db.session.commit()
        log_action(f"Agregó al trabajador {nuevo_trabajador.nombre} ({nuevo_trabajador.no_empleado})")
        flash('Trabajador agregado exitosamente.', 'success')
        
    except Exception as e:
        db.session.rollback()
        print(f"Error saving worker: {e}\n{traceback.format_exc()}")
        flash('Ocurrió un error al guardar el trabajador. Verifica los datos.', 'danger')
        
    return redirect(url_for('trabajadores.index'))

@bp.route('/get/<int:id>')
@login_required
def get_trabajador(id):
    t = Trabajador.query.get_or_404(id)
    credenciales = [{'planta': c.planta, 'credencial_id': c.credencial_id} for c in t.credenciales]
    documentos = [d.to_dict() for d in t.documentos]
    
    # Extraer coordinadores de los proyectos activos asignados
    coordinadores_set = set()
    for p in t.proyectos:
        if p.activo and p.coordinador:
            coordinadores_set.add(p.coordinador.full_name or p.coordinador.username)
            
    coordinadores_asignados = ", ".join(coordinadores_set) if coordinadores_set else "Ninguno asignado"
    
    data = {
        'id': t.id,
        'no_empleado': t.no_empleado,
        'nombre_apellidos': t.nombre_apellidos,
        'nombre': t.nombre,
        
        # Laborales
        'tipo_mov': t.tipo_mov or '',
        'tipo_cont': t.tipo_cont or '',
        'area': t.area or '',
        'puesto': t.puesto or '',
        'tipo_jornada': t.tipo_jornada or '',
        'fecha_ingreso': t.fecha_ingreso.isoformat() if t.fecha_ingreso else '',
        'descripcion_servicio': t.descripcion_servicio or '',
        'inicio': t.inicio.isoformat() if t.inicio else '',
        'termino_prueba': t.termino_prueba.isoformat() if t.termino_prueba else '',
        'fecha_baja': t.fecha_baja.isoformat() if t.fecha_baja else '',
        
        # Generales
        'curp': t.curp or '',
        'rfc': t.rfc or '',
        'nss': t.nss or '',
        'fecha_nacimiento': t.fecha_nacimiento.isoformat() if t.fecha_nacimiento else '',
        'sexo': t.sexo or '',
        'estado_civil': t.estado_civil or '',
        'nacionalidad': t.nacionalidad or '',
        'edad': t.edad or '',
        'domicilio': t.domicilio or '',
        
        # Contacto
        'correo': t.correo or '',
        'celular': t.celular or '',
        
        # Medicos
        'tipo_sangre': t.tipo_sangre or '',
        'alergias': t.alergias or '',
        'enfermedades_cronicas': t.enfermedades_cronicas or '',
        'contacto_emergencia': t.contacto_emergencia or '',
        'parentesco_contacto': t.parentesco_contacto or '',
        'numero_contacto_emerg': t.numero_contacto_emerg or '',
        'lentes': t.lentes or '',
        'licencia_conducir': t.licencia_conducir or '',
        'estatura': t.estatura or '',
        
        # Finanzas
        'salario_real_pactado_x_sem': str(t.salario_real_pactado_x_sem) if t.salario_real_pactado_x_sem else '',
        'tipo_pago': t.tipo_pago or '',
        'tipo_nomina': t.tipo_nomina or '',
        'sb': str(t.sb) if t.sb else '',
        'sdi': str(t.sdi) if t.sdi else '',
        'letra': t.letra or '',
        'hr_extra': str(t.hr_extra) if t.hr_extra else '',
        'infonavit': str(t.infonavit) if t.infonavit else '',
        'ajuste_inbursa': str(t.ajuste_inbursa) if t.ajuste_inbursa else '',
        'caja_ahorro': str(t.caja_ahorro) if t.caja_ahorro else '',
        'viaticos': str(t.viaticos) if t.viaticos else '',
        'pago_dia_festivo': str(t.pago_dia_festivo) if t.pago_dia_festivo else '',
        'pagos_efectivo': str(t.pagos_efectivo) if t.pagos_efectivo else '',
        'folio_mov_idse': t.folio_mov_idse or '',
        
        # Operacion
        'ubicacion_actual': t.ubicacion_actual or '',
        'coordinadores_actuales': coordinadores_asignados,
        'no_proyecto': t.no_proyecto or '',
        'observaciones': t.observaciones or '',
        
        'foto_perfil': t.foto_perfil or '',
        'credenciales': credenciales,
        'documentos': documentos
    }
    return jsonify(data)

@bp.route('/editar/<int:id>', methods=['POST'])
@login_required
def editar(id):
    try:
        t = Trabajador.query.get_or_404(id)
        data = request.form
        
        # Check uniqueness of no_empleado if it changed
        new_no = data.get('no_empleado')
        if new_no != t.no_empleado and Trabajador.query.filter_by(no_empleado=new_no).first():
            flash('Error: El Número de Empleado ya existe.', 'danger')
            return redirect(url_for('trabajadores.index'))
            
        t.no_empleado = new_no
        t.nombre_apellidos = data.get('nombre_apellidos')
        t.nombre = data.get('nombre')
        
        # Laborales
        t.tipo_mov = data.get('tipo_mov')
        t.tipo_cont = data.get('tipo_cont')
        t.area = data.get('area')
        t.puesto = data.get('puesto')
        t.tipo_jornada = data.get('tipo_jornada')
        t.fecha_ingreso = _parse_date(data.get('fecha_ingreso'))
        t.descripcion_servicio = data.get('descripcion_servicio')
        t.inicio = _parse_date(data.get('inicio'))
        t.termino_prueba = _parse_date(data.get('termino_prueba'))
        t.fecha_baja = _parse_date(data.get('fecha_baja'))
        
        # Generales
        t.curp = data.get('curp')
        t.rfc = data.get('rfc')
        t.nss = data.get('nss')
        t.fecha_nacimiento = _parse_date(data.get('fecha_nacimiento'))
        t.sexo = data.get('sexo')
        t.estado_civil = data.get('estado_civil')
        t.nacionalidad = data.get('nacionalidad')
        t.edad = data.get('edad') or None
        t.domicilio = data.get('domicilio')
        
        # Contacto
        t.correo = data.get('correo')
        t.celular = data.get('celular')
        
        # Medicos
        t.tipo_sangre = data.get('tipo_sangre')
        t.alergias = data.get('alergias')
        t.enfermedades_cronicas = data.get('enfermedades_cronicas')
        t.contacto_emergencia = data.get('contacto_emergencia')
        t.parentesco_contacto = data.get('parentesco_contacto')
        t.numero_contacto_emerg = data.get('numero_contacto_emerg')
        t.lentes = data.get('lentes')
        t.licencia_conducir = data.get('licencia_conducir')
        t.estatura = data.get('estatura')
        
        # Finanzas
        t.salario_real_pactado_x_sem = data.get('salario_real_pactado_x_sem') or 0
        t.tipo_pago = data.get('tipo_pago')
        t.tipo_nomina = data.get('tipo_nomina')
        t.sb = data.get('sb') or 0
        t.sdi = data.get('sdi') or 0
        t.letra = data.get('letra')
        t.hr_extra = data.get('hr_extra') or 0
        t.infonavit = data.get('infonavit') or 0
        t.ajuste_inbursa = data.get('ajuste_inbursa') or 0
        t.caja_ahorro = data.get('caja_ahorro') or 0
        t.viaticos = data.get('viaticos') or 0
        t.pago_dia_festivo = data.get('pago_dia_festivo') or 0
        t.pagos_efectivo = data.get('pagos_efectivo') or 0
        t.folio_mov_idse = data.get('folio_mov_idse')
        
        # Operacion (no_proyecto, ubicacion_actual, coord_a_cargo se manejan en Proyectos)
        t.observaciones = data.get('observaciones')
        
        # Handle Profile Picture Edit
        if 'foto_perfil' in request.files:
            file = request.files['foto_perfil']
            if file and file.filename != '':
                # Delete old profile picture if exists
                if t.foto_perfil:
                    old_pp_path = os.path.join(current_app.config['UPLOAD_FOLDER'], t.foto_perfil)
                    try:
                        if os.path.exists(old_pp_path):
                            os.remove(old_pp_path)
                    except Exception as e:
                        print(f"Error deleting old profile pic: {e}")
                
                filename = secure_filename(file.filename)
                import time
                unique_filename = f"pp_{int(time.time())}_{filename}"
                pp_folder = os.path.join(current_app.config['UPLOAD_FOLDER'], 'perfiles')
                os.makedirs(pp_folder, exist_ok=True)
                file_path = os.path.join(pp_folder, unique_filename)
                file.save(file_path)
                t.foto_perfil = f"perfiles/{unique_filename}"
        
        # Update credentials
        try:
            credenciales_str = data.get('credenciales_json', '[]')
            credenciales_data = json.loads(credenciales_str)
            # Remove old credentials
            t.credenciales = []
            
            for c_data in credenciales_data:
                nueva_credencial = CredencialPlanta(
                    planta=c_data.get('planta', '').upper(),
                    credencial_id=c_data.get('credencial_id', '')[:40]
                )
                t.credenciales.append(nueva_credencial)
        except Exception as json_err:
            print(f"Error parsing credentials on edit: {json_err}")
            flash('Hubo un problema actualizando algunas credenciales.', 'warning')
            
        db.session.commit()
        log_action(f"Actualizó al trabajador {t.nombre} ({t.no_empleado})")
        flash('Trabajador actualizado exitosamente.', 'success')
        
    except Exception as e:
        db.session.rollback()
        print(f"Error updating worker: {e}\n{traceback.format_exc()}")
        flash('Ocurrió un error al actualizar el trabajador.', 'danger')
        
    return redirect(url_for('trabajadores.index'))

@bp.route('/credenciales/<int:id>', methods=['POST'])
@login_required
def guardar_credenciales(id):
    try:
        t = Trabajador.query.get_or_404(id)
        data = request.form
        
        # Update observaciones
        if 'observaciones' in data:
            t.observaciones = data.get('observaciones')

        # Update credentials
        credenciales_str = data.get('credenciales_json', '[]')
        credenciales_data = json.loads(credenciales_str)
        
        # Remove old credentials
        t.credenciales = []
        
        for c_data in credenciales_data:
            nueva_credencial = CredencialPlanta(
                planta=c_data.get('planta', '').upper(),
                credencial_id=c_data.get('credencial_id', '')[:40]
            )
            t.credenciales.append(nueva_credencial)
            
        db.session.commit()
        log_action(f"Actualizó las credenciales del trabajador {t.nombre} ({t.no_empleado})")
        return jsonify({'success': True, 'message': 'Credenciales actualizadas correctamente.'})
    except Exception as e:
        db.session.rollback()
        print(f"Error updating credentials: {e}\n{traceback.format_exc()}")
        return jsonify({'success': False, 'error': str(e)}), 500

@bp.route('/eliminar/<int:id>', methods=['POST'])
@login_required
def eliminar(id):
    try:
        from datetime import date
        t = Trabajador.query.get_or_404(id)
        nombre_trabajador = t.nombre
        no_emp = t.no_empleado
        
        # Soft delete
        t.activo = False
        t.fecha_baja = date.today()
        
        db.session.commit()
        log_action(f"Dio de baja al trabajador {nombre_trabajador} ({no_emp})")
        flash('Trabajador dado de baja exitosamente.', 'success')
    except Exception as e:
        db.session.rollback()
        print(f"Error inactivating worker: {e}\n{traceback.format_exc()}")
        flash('Ocurrió un error al dar de baja el trabajador.', 'danger')
        
    return redirect(url_for('trabajadores.index'))

@bp.route('/reactivar/<int:id>', methods=['POST'])
@login_required
def reactivar(id):
    try:
        t = Trabajador.query.get_or_404(id)
        nombre_trabajador = t.nombre
        no_emp = t.no_empleado
        
        t.activo = True
        t.fecha_baja = None
        
        db.session.commit()
        log_action(f"Reactivó al trabajador {nombre_trabajador} ({no_emp})")
        flash('Trabajador reactivado exitosamente.', 'success')
    except Exception as e:
        db.session.rollback()
        print(f"Error reactivating worker: {e}\n{traceback.format_exc()}")
        flash('Ocurrió un error al reactivar el trabajador.', 'danger')
        
    return redirect(url_for('trabajadores.bajas'))

@bp.route('/<int:id>/documentos', methods=['POST'])
@login_required
def upload_documento(id):
    t = Trabajador.query.get_or_404(id)
    
    if 'documento' not in request.files:
        return jsonify({'error': 'No file part'}), 400
        
    file = request.files['documento']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
        
    if file:
        filename = secure_filename(file.filename)
        # Create a unique filename to prevent overwrites
        import time
        unique_filename = f"{int(time.time())}_{filename}"
        
        # Save path: uploads/trabajadores/<id>/
        upload_folder = os.path.join(current_app.config['UPLOAD_FOLDER'], 'trabajadores', str(t.id))
        os.makedirs(upload_folder, exist_ok=True)
        
        file_path = os.path.join(upload_folder, unique_filename)
        file.save(file_path)
        
        # Relate to DB path
        db_path = f"trabajadores/{t.id}/{unique_filename}"
        
        nuevo_doc = DocumentoTrabajador(
            trabajador_id=t.id,
            nombre_archivo=filename,
            ruta_archivo=db_path
        )
        db.session.add(nuevo_doc)
        db.session.commit()
        log_action(f"Subió documento {filename} para {t.nombre} ({t.no_empleado})")
        
        return jsonify(nuevo_doc.to_dict()), 201

@bp.route('/documento/<int:doc_id>', methods=['GET'])
@login_required
def get_documento(doc_id):
    doc = DocumentoTrabajador.query.get_or_404(doc_id)
    return send_from_directory(current_app.config['UPLOAD_FOLDER'], doc.ruta_archivo)

@bp.route('/foto/<int:id>', methods=['GET'])
@login_required
def get_foto_perfil(id):
    t = Trabajador.query.get_or_404(id)
    if not t.foto_perfil:
        return "Not found", 404
    return send_from_directory(current_app.config['UPLOAD_FOLDER'], t.foto_perfil)
    
@bp.route('/documento/<int:doc_id>', methods=['DELETE'])
@login_required
def delete_documento(doc_id):
    doc = DocumentoTrabajador.query.get_or_404(doc_id)
    file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], doc.ruta_archivo)
    
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
    except Exception as e:
        print(f"Error physically deleting file {file_path}: {e}")
        
    log_action(f"Eliminó documento {doc.nombre_archivo} del trabajador ID {doc.trabajador_id}")
    db.session.delete(doc)
    db.session.commit()
    
    return jsonify({'success': True}), 200
