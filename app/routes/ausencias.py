from flask import Blueprint, render_template, request, flash, redirect, url_for, jsonify, current_app
from datetime import datetime, date
from app.extensions import db, limiter
from app.models import Ausencia, SaldoVacaciones, Trabajador
from app.utils import login_required, log_action
import traceback
from sqlalchemy import or_, and_

bp = Blueprint('ausencias', __name__, url_prefix='/ausencias')

@bp.route('/')
@login_required
def index():
    hoy = date.today()
    
    # Auto-crear saldos para trabajadores activos que aún no tengan registro (optimizado)
    trabajadores_sin_saldo = db.session.query(Trabajador.id).outerjoin(
        SaldoVacaciones, Trabajador.id == SaldoVacaciones.trabajador_id
    ).filter(
        Trabajador.activo == True,
        SaldoVacaciones.id == None
    ).all()
    
    if trabajadores_sin_saldo:
        for t in trabajadores_sin_saldo:
            db.session.add(SaldoVacaciones(trabajador_id=t[0], dias_totales_asignados=0, dias_disfrutados=0))
        db.session.commit()
    
    # 1. Vacaciones pendientes por gozar (Resumen global)
    saldos = SaldoVacaciones.query.join(Trabajador).filter(Trabajador.activo == True).all()
    
    # 2. Vacaciones en curso al día
    vacaciones_en_curso = Ausencia.query.filter(
        Ausencia.tipo_ausencia == 'VACACIONES',
        Ausencia.estado == 'EN_CURSO',
        Ausencia.fecha_inicio <= hoy,
        Ausencia.fecha_fin >= hoy
    ).all()
    
    # 3. Incapacidades vigentes
    incapacidades_vigentes = Ausencia.query.filter(
        Ausencia.tipo_ausencia.ilike('INCAPACIDAD%'),
        Ausencia.estado == 'EN_CURSO',
        Ausencia.fecha_inicio <= hoy,
        Ausencia.fecha_fin >= hoy
    ).all()
    
    # 4. Permisos en curso al día u otras incidencias
    permisos_vigentes = Ausencia.query.filter(
        Ausencia.tipo_ausencia.ilike('PERMISO%'),
        Ausencia.estado == 'EN_CURSO',
        Ausencia.fecha_inicio <= hoy,
        Ausencia.fecha_fin >= hoy
    ).all()
    
    # Próximas ausencias (opcional, útil para el tablero)
    proximas_ausencias = Ausencia.query.filter(
        Ausencia.estado == 'PROGRAMADA',
        Ausencia.fecha_inicio > hoy
    ).order_by(Ausencia.fecha_inicio.asc()).limit(10).all()
    
    # Historial de ausencias completadas recientes
    historial_ausencias = Ausencia.query.filter(
        Ausencia.estado.in_(['FINALIZADA', 'RECHAZADA', 'CANCELADA'])
    ).order_by(Ausencia.fecha_inicio.desc()).limit(20).all()
    
    # Listado para el modal
    trabajadores = Trabajador.query.filter_by(activo=True).order_by(Trabajador.nombre).all()

    return render_template(
        'ausencias.html', 
        saldos=saldos,
        vacaciones_en_curso=vacaciones_en_curso,
        incapacidades_vigentes=incapacidades_vigentes,
        permisos_vigentes=permisos_vigentes,
        proximas_ausencias=proximas_ausencias,
        historial_ausencias=historial_ausencias,
        trabajadores=trabajadores,
        hoy=hoy
    )

@bp.route('/balance/<int:trabajador_id>', methods=['GET'])
@login_required
def obtener_balance(trabajador_id):
    """Devuelve el balance actual del trabajador."""
    saldo = SaldoVacaciones.query.filter_by(trabajador_id=trabajador_id).first()
    if not saldo:
        # Si no existe, lo creamos con 0
        saldo = SaldoVacaciones(trabajador_id=trabajador_id, dias_totales_asignados=0, dias_disfrutados=0)
        db.session.add(saldo)
        db.session.commit()
    
    return jsonify({
        'success': True,
        'dias_asignados': saldo.dias_totales_asignados,
        'dias_disfrutados': saldo.dias_disfrutados,
        'dias_pendientes': saldo.dias_totales_asignados - saldo.dias_disfrutados
    })

@bp.route('/solicitar', methods=['POST'])
@login_required
@limiter.limit("10 per minute")
def solicitar_ausencia():
    """Registra una nueva ausencia (vacación, incapacidad, permiso)."""
    try:
        from flask import session
        
        data = request.form
        trabajador_id = data.get('trabajador_id')
        tipo_ausencia = data.get('tipo_ausencia')
        fecha_inicio_str = data.get('fecha_inicio')
        fecha_fin_str = data.get('fecha_fin')
        motivo = data.get('motivo', '')
        
        # Validación extra: si se requiere descuento de saldo, comprobar si hay fondo
        dias_solicitados_str = data.get('dias_solicitados', '1')
        try:
            dias_solicitados = int(dias_solicitados_str)
        except ValueError:
            dias_solicitados = 1
            
        if not all([trabajador_id, tipo_ausencia, fecha_inicio_str, fecha_fin_str]):
            flash('Faltan datos obligatorios.', 'danger')
            return redirect(url_for('ausencias.index'))
            
        fecha_inicio = datetime.strptime(fecha_inicio_str, '%Y-%m-%d').date()
        fecha_fin = datetime.strptime(fecha_fin_str, '%Y-%m-%d').date()
        hoy = date.today()
        
        if fecha_fin < fecha_inicio:
            flash('La fecha de fin no puede ser anterior a la fecha de inicio.', 'danger')
            return redirect(url_for('ausencias.index'))
            
        # Si es VACACIONES, comprobar saldo
        if tipo_ausencia == 'VACACIONES':
            saldo = SaldoVacaciones.query.filter_by(trabajador_id=trabajador_id).first()
            if not saldo:
                saldo = SaldoVacaciones(trabajador_id=trabajador_id, dias_totales_asignados=0, dias_disfrutados=0)
                db.session.add(saldo)
                
            pendientes = saldo.dias_totales_asignados - saldo.dias_disfrutados
            if dias_solicitados > pendientes:
                flash(f'El trabajador no tiene suficientes días de vacaciones (Pendientes: {pendientes}, Solicitados: {dias_solicitados}).', 'warning')
                return redirect(url_for('ausencias.index'))
        
        estado = 'PROGRAMADA'
        if fecha_inicio <= hoy and fecha_fin >= hoy:
            estado = 'EN_CURSO'
        elif fecha_fin < hoy:
            estado = 'FINALIZADA'
            
        ausencia = Ausencia(
            trabajador_id=trabajador_id,
            tipo_ausencia=tipo_ausencia,
            fecha_inicio=fecha_inicio,
            fecha_fin=fecha_fin,
            dias_solicitados=dias_solicitados,
            estado=estado,
            motivo=motivo,
            creado_por_id=session.get('user_id')
        )
        
        db.session.add(ausencia)
        
        # Descontar días solo si la ausencia entra en curso o finaliza automáticamente,
        # Si está programada a futuro, se asume aprobada y devengará en su momento.
        # Por seguridad y control, descontaremos los días desde el momento del registro.
        if tipo_ausencia == 'VACACIONES':
            saldo.dias_disfrutados += dias_solicitados
            
        db.session.commit()
        log_action(f"registrar_ausencia: Trabajador {trabajador_id}, Tipo {tipo_ausencia}, {dias_solicitados} días")
        flash('Registro de ausencia/vacaciones creado exitosamente.', 'success')
        return redirect(url_for('ausencias.index'))
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error al registrar ausencia: {traceback.format_exc()}")
        flash('Ocurrió un error al registrar la ausencia.', 'danger')
        return redirect(url_for('ausencias.index'))

@bp.route('/cancelar/<int:id>', methods=['POST'])
@login_required
@limiter.limit("10 per minute")
def cancelar_ausencia(id):
    """Cancela una ausencia y restaura el saldo de vacaciones si aplica."""
    try:
        ausencia = Ausencia.query.get_or_404(id)
        
        if ausencia.estado in ['CANCELADA']:
            flash('La ausencia ya estaba cancelada.', 'warning')
            return redirect(url_for('ausencias.index'))
            
        if ausencia.tipo_ausencia == 'VACACIONES':
            # Restaurar saldo
            saldo = SaldoVacaciones.query.filter_by(trabajador_id=ausencia.trabajador_id).first()
            if saldo:
                saldo.dias_disfrutados -= ausencia.dias_solicitados
                # Validación de seguridad para que no quede negativo (aunque puede ocurrir raras veces)
                if saldo.dias_disfrutados < 0:
                    saldo.dias_disfrutados = 0
                    
        ausencia.estado = 'CANCELADA'
        db.session.commit()
        log_action(f"cancelar_ausencia: Ausencia ID {id} cancelada.")
        flash('La ausencia fue cancelada y los días fueron restaurados.', 'success')
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error al cancelar ausencia: {traceback.format_exc()}")
        flash('Ocurrió un error interno al intentar cancelar la acción.', 'danger')
        
    return redirect(url_for('ausencias.index'))

@bp.route('/actualizar_saldo/<int:trabajador_id>', methods=['POST'])
@login_required
@limiter.limit("10 per minute")
def actualizar_saldo(trabajador_id):
    """Permite al administrador asignar más días de vacaciones (ajuste manual)."""
    try:
        dias_asignados = request.form.get('dias_asignados', type=int)
        dias_disfrutados = request.form.get('dias_disfrutados', type=int)
        
        if dias_asignados is None or dias_disfrutados is None:
            flash('Datos inválidos.', 'danger')
            return redirect(url_for('ausencias.index'))
            
        saldo = SaldoVacaciones.query.filter_by(trabajador_id=trabajador_id).first()
        if not saldo:
            saldo = SaldoVacaciones(trabajador_id=trabajador_id, dias_totales_asignados=dias_asignados, dias_disfrutados=dias_disfrutados)
            db.session.add(saldo)
        else:
            saldo.dias_totales_asignados = dias_asignados
            saldo.dias_disfrutados = dias_disfrutados
            
        db.session.commit()
        log_action(f"actualizar_saldo_vacaciones: Trabajador {trabajador_id}, Asignados {dias_asignados}, Disfrutados {dias_disfrutados}")
        flash('Saldo de vacaciones actualizado.', 'success')
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error al actualizar saldo: {traceback.format_exc()}")
        flash('Ocurrió un error al actualizar el saldo de vacaciones.', 'danger')
        
    return redirect(url_for('ausencias.index'))
