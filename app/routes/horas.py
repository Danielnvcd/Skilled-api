from flask import Blueprint, render_template, request, flash, redirect, url_for, jsonify, session, current_app, send_file
from app.extensions import db, limiter
from app.models import Proyecto, ReporteSemanal, RegistroDiarioHoras, Trabajador
from app.utils import login_required, log_action
import traceback
from datetime import datetime, timedelta, date

bp = Blueprint('horas', __name__, url_prefix='/horas')

def _reg_dict(reg):
    return {
        'id': reg.id,
        'trabajador_id': reg.trabajador_id,
        'nombre_completo': reg.trabajador.nombre_completo,
        'no_empleado': reg.trabajador.no_empleado,
        'fecha': reg.fecha.strftime('%Y-%m-%d'),
        'hora_entrada': reg.hora_entrada.strftime('%H:%M') if reg.hora_entrada else '',
        'hora_salida': reg.hora_salida.strftime('%H:%M') if reg.hora_salida else '',
        'tomo_comida': bool(reg.tomo_comida),
        'aplica_viaticos': bool(reg.aplica_viaticos),
        'monto_viaticos_manual': float(reg.monto_viaticos_manual) if reg.monto_viaticos_manual else None,
        'aplica_dia_festivo': bool(reg.aplica_dia_festivo),
        'incidencia': reg.incidencia or '',
        'horas_productivas': float(reg.horas_productivas) if reg.horas_productivas else 0.0,
    }

def _verificar_ownership_proyecto(proyecto):
    """Verifica que un coordinador sea dueño del proyecto. Admins pasan siempre."""
    role = session.get('role', 'user')
    if role == 'coordinador' and proyecto.coordinador_id != session.get('user_id'):
        return False
    return True

@bp.route('/', methods=['GET'])
@login_required
def index():
    user_id = session.get('user_id')
    user_role = session.get('role', 'user')

    if user_role == 'coordinador' and not request.args.get('desktop'):
        is_mobile = request.user_agent.platform in ['android', 'iphone', 'ipad'] or 'mobi' in request.user_agent.string.lower()
        if is_mobile:
            return redirect(url_for('horas.movil'))

    page = request.args.get('page', 1, type=int)
    q = request.args.get('q', '').strip()

    if user_role == 'coordinador':
        proyectos = Proyecto.query.filter_by(activo=True, coordinador_id=user_id).all()
        proyecto_ids = [p.id for p in proyectos] if proyectos else []
        query = ReporteSemanal.query.filter(ReporteSemanal.proyecto_id.in_(proyecto_ids))
    else:
        query = ReporteSemanal.query
        proyectos = Proyecto.query.filter_by(activo=True).all()

    if q:
        query = query.join(Proyecto).filter(
            db.or_(
                Proyecto.nombre.ilike(f'%{q}%'),
                Proyecto.numero_proyecto.ilike(f'%{q}%')
            )
        )

    pagination = query.order_by(ReporteSemanal.created_at.desc()).paginate(page=page, per_page=20, error_out=False)
    reportes = pagination.items

    return render_template('horas.html', reportes=reportes, proyectos=proyectos, pagination=pagination, q=q)

@bp.route('/crear_reporte', methods=['POST'])
@login_required
@limiter.limit("10 per minute")
def crear_reporte():
    try:
        data = request.form
        proyecto_id = data.get('proyecto_id')
        fecha_inicio_str = data.get('fecha_inicio_semana')
        fecha_fin_str = data.get('fecha_fin_semana')
        
        if not proyecto_id or not fecha_inicio_str or not fecha_fin_str:
            flash("Todos los campos son requeridos para abrir un reporte.", "warning")
            return redirect(url_for('horas.index'))

        # Validar ownership del proyecto
        proyecto = Proyecto.query.get_or_404(proyecto_id)
        if not _verificar_ownership_proyecto(proyecto):
            flash("Acceso denegado. No eres coordinador de este proyecto.", "danger")
            return redirect(url_for('horas.index'))

        fecha_inicio = datetime.strptime(fecha_inicio_str, '%Y-%m-%d').date()
        fecha_fin = datetime.strptime(fecha_fin_str, '%Y-%m-%d').date()
        
        if fecha_inicio >= fecha_fin:
            flash("Error: La fecha de inicio debe ser anterior a la fecha de fin.", "danger")
            return redirect(url_for('horas.index'))

        # Validación 1: ¿La semana ya fue cerrada globalmente? (Verifica si hay solapamiento de fechas)
        semana_cerrada = ReporteSemanal.query.filter(
            ReporteSemanal.estado == 'PRENOMINA_CERRADA',
            ReporteSemanal.fecha_inicio_semana <= fecha_fin,
            ReporteSemanal.fecha_fin_semana >= fecha_inicio
        ).first()

        if semana_cerrada:
            flash(f"La prenómina de la semana del {fecha_inicio_str} al {fecha_fin_str} ya fue CERRADA. No se pueden abrir nuevos reportes.", "danger")
            return redirect(url_for('horas.index'))

        # Validación 2: ¿Este proyecto ya tiene un reporte en esa semana?
        overlapping_report = ReporteSemanal.query.filter(
            ReporteSemanal.proyecto_id == proyecto_id,
            ReporteSemanal.fecha_inicio_semana <= fecha_fin,
            ReporteSemanal.fecha_fin_semana >= fecha_inicio
        ).first()

        if overlapping_report:
            flash(f"Este proyecto ya tiene un reporte abierto o cerrado para esta semana.", "warning")
            return redirect(url_for('horas.index'))

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
        current_app.logger.error(f"Error creating report: {traceback.format_exc()}")
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

    # Only load workers assigned to this project
    trabajadores = sorted(reporte.proyecto.participantes, key=lambda t: t.nombre)

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

    # Build weekly date list and grid lookup for the weekly view
    from datetime import timedelta
    semana_fechas = []
    d = reporte.fecha_inicio_semana
    while d <= reporte.fecha_fin_semana:
        semana_fechas.append(d)
        d += timedelta(days=1)

    grid_registros = {}
    for reg in reporte.registros:
        grid_registros.setdefault(reg.trabajador_id, {})[reg.fecha] = reg

    registros_json = [
        {
            'id': reg.id,
            'trabajador_id': reg.trabajador_id,
            'trabajador_nombre': reg.trabajador.nombre_completo,
            'fecha': reg.fecha.strftime('%Y-%m-%d'),
            'fecha_label': reg.fecha.strftime('%d/%m/%Y'),
            'hora_entrada': reg.hora_entrada.strftime('%H:%M') if reg.hora_entrada else '',
            'hora_salida': reg.hora_salida.strftime('%H:%M') if reg.hora_salida else '',
            'tomo_comida': bool(reg.tomo_comida),
            'aplica_viaticos': bool(reg.aplica_viaticos),
            'monto_viaticos_manual': float(reg.monto_viaticos_manual) if reg.monto_viaticos_manual else None,
            'viaticos_modo': 'manual' if reg.monto_viaticos_manual else 'perfil',
            'aplica_dia_festivo': bool(reg.aplica_dia_festivo),
            'incidencia': reg.incidencia or '',
            'horas_productivas': float(reg.horas_productivas) if reg.horas_productivas else 0.0,
        }
        for reg in reporte.registros
    ]

    semana_fechas_json = [
        {
            'fecha': d.strftime('%Y-%m-%d'),
            'label': d.strftime('%d/%m'),
            'dia': ['Lun', 'Mar', 'Mié', 'Jue', 'Vie', 'Sáb', 'Dom'][d.weekday()],
            'fin_semana': d.weekday() >= 5,
        }
        for d in semana_fechas
    ]

    return render_template(
        'horas_captura.html',
        reporte=reporte,
        trabajadores=trabajadores,
        horas_dropdown=horas_dropdown,
        incidencias_lista=incidencias_lista,
        semana_fechas=semana_fechas,
        grid_registros=grid_registros,
        registros_json=registros_json,
        semana_fechas_json=semana_fechas_json,
    )

@bp.route('/guardar_registro/<int:reporte_id>', methods=['POST'])
@login_required
@limiter.limit("60 per minute")
def guardar_registro(reporte_id):
    reporte = ReporteSemanal.query.get_or_404(reporte_id)
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

    def err(msg, status=400):
        if is_ajax:
            return jsonify({'ok': False, 'error': msg}), status
        flash(msg, 'danger')
        return redirect(url_for('horas.capturar', reporte_id=reporte.id))

    if not _verificar_ownership_proyecto(reporte.proyecto):
        return err("Acceso denegado. No eres coordinador de este proyecto.", 403)

    if reporte.estado != 'BORRADOR':
        return err("Este reporte ya está cerrado.")

    try:
        data = request.form
        trabajador_id = data.get('trabajador_id')
        fecha_str = data.get('fecha')
        hora_entrada_str = data.get('hora_entrada')
        hora_salida_str = data.get('hora_salida')
        tomo_comida = bool(data.get('tomo_comida'))
        aplica_viaticos = bool(data.get('aplica_viaticos'))
        viaticos_modo = data.get('viaticos_modo', 'perfil')
        monto_viaticos_manual_str = data.get('monto_viaticos_manual', '').strip()
        aplica_dia_festivo = bool(data.get('aplica_dia_festivo'))
        incidencia = data.get('incidencia')

        if not trabajador_id or not fecha_str:
            return err("Faltan datos obligatorios.")

        if (not hora_entrada_str or not hora_salida_str) and not incidencia:
            return err("Debes registrar hora de entrada y salida, o seleccionar una incidencia.")

        try:
            fecha = datetime.strptime(fecha_str, '%Y-%m-%d').date()
        except ValueError:
            return err("Error: El formato de fecha ingresado es inválido.")

        trabajador = Trabajador.query.get(trabajador_id)
        if not trabajador:
            return err("Trabajador no encontrado.")

        if trabajador not in reporte.proyecto.participantes:
            return err("Este trabajador no está asignado al proyecto.")

        monto_viaticos_manual = None
        if aplica_viaticos:
            if viaticos_modo == 'manual':
                if not monto_viaticos_manual_str:
                    return err("Error: Debes ingresar un monto para los viáticos manuales.")
                try:
                    monto_viaticos_manual = float(monto_viaticos_manual_str)
                except (ValueError, TypeError):
                    return err("Error: El monto de viáticos ingresado no es un número válido.")
                if monto_viaticos_manual <= 0:
                    return err("Error: El monto manual de viáticos debe ser mayor a $0.00.")
            else:
                if trabajador.viaticos is None or trabajador.viaticos <= 0:
                    return err(f"Error: No se pueden habilitar viáticos para {trabajador.nombre}. Su perfil tiene $0.00 asignados de viáticos.")

        if aplica_dia_festivo and (trabajador.pago_dia_festivo is None or trabajador.pago_dia_festivo <= 0):
            return err(f"Error: No se puede habilitar pago por día festivo para {trabajador.nombre}. Su perfil tiene $0.00 asignados de día festivo.")

        hora_entrada = None
        hora_salida = None
        horas_productivas = 0.0

        if hora_entrada_str and hora_salida_str:
            try:
                hora_entrada = datetime.strptime(hora_entrada_str, '%H:%M').time()
                hora_salida = datetime.strptime(hora_salida_str, '%H:%M').time()
            except ValueError:
                return err("Error: El formato de hora ingresado es inválido.")

            if hora_entrada == hora_salida:
                return err("La hora de salida debe ser distinta a la hora de entrada.")

            registros_existentes = RegistroDiarioHoras.query.join(ReporteSemanal).filter(
                RegistroDiarioHoras.trabajador_id == trabajador.id,
                RegistroDiarioHoras.fecha == fecha,
                ReporteSemanal.estado == 'BORRADOR'
            ).all()

            for reg in registros_existentes:
                if reg.hora_entrada and reg.hora_salida:
                    from app.utils import turnos_se_traslapan
                    if turnos_se_traslapan(hora_entrada, hora_salida, reg.hora_entrada, reg.hora_salida):
                        return err(f"Error: El horario ({hora_entrada_str} a {hora_salida_str}) choca con un registro existente en el proyecto '{reg.reporte.proyecto.nombre}' ({reg.hora_entrada.strftime('%H:%M')} a {reg.hora_salida.strftime('%H:%M')}).")

            from app.utils import calcular_horas_productivas
            horas_productivas = calcular_horas_productivas(
                hora_entrada, hora_salida,
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
            aplica_viaticos=aplica_viaticos,
            monto_viaticos_manual=monto_viaticos_manual,
            aplica_dia_festivo=aplica_dia_festivo,
            incidencia=incidencia if incidencia else None,
            tipo_nomina=trabajador.tipo_nomina or 'Semanal',
            horas_productivas=horas_productivas
        )

        db.session.add(nuevo_registro)
        db.session.commit()

        if is_ajax:
            return jsonify({'ok': True, 'registro': _reg_dict(nuevo_registro)})
        flash("Registro guardado exitosamente.", "success")

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error guardando registro: {traceback.format_exc()}")
        return err("Error interno al procesar registro de horas.")

    return redirect(url_for('horas.capturar', reporte_id=reporte.id))

@bp.route('/eliminar_registro/<int:registro_id>', methods=['POST'])
@login_required
@limiter.limit("30 per minute")
def eliminar_registro(registro_id):
    registro = RegistroDiarioHoras.query.get_or_404(registro_id)
    reporte_id = registro.reporte_id
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

    if not _verificar_ownership_proyecto(registro.reporte.proyecto):
        if is_ajax:
            return jsonify({'ok': False, 'error': 'Acceso denegado.'}), 403
        flash("Acceso denegado. No eres coordinador de este proyecto.", "danger")
        return redirect(url_for('horas.index'))

    if registro.reporte.estado != 'BORRADOR':
        if is_ajax:
            return jsonify({'ok': False, 'error': 'El reporte ya está cerrado.'}), 400
        flash("No se pueden eliminar registros de un reporte cerrado.", "warning")
        return redirect(url_for('horas.capturar', reporte_id=reporte_id))

    try:
        db.session.delete(registro)
        db.session.commit()
        if is_ajax:
            return jsonify({'ok': True})
        flash("Registro eliminado.", "info")
    except Exception as e:
        db.session.rollback()
        if is_ajax:
            return jsonify({'ok': False, 'error': 'Error al eliminar.'}), 500
        flash("Error al eliminar el registro.", "danger")

    return redirect(url_for('horas.capturar', reporte_id=reporte_id))

@bp.route('/editar_registro/<int:registro_id>', methods=['POST'])
@login_required
@limiter.limit("60 per minute")
def editar_registro(registro_id):
    registro = RegistroDiarioHoras.query.get_or_404(registro_id)
    reporte = registro.reporte
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

    def err(msg, status=400):
        if is_ajax:
            return jsonify({'ok': False, 'error': msg}), status
        flash(msg, 'danger')
        return redirect(url_for('horas.capturar', reporte_id=reporte.id))

    if not _verificar_ownership_proyecto(reporte.proyecto):
        return err("Acceso denegado.", 403)

    if reporte.estado != 'BORRADOR':
        return err("Este reporte ya está cerrado.")

    try:
        data = request.form
        hora_entrada_str = data.get('hora_entrada', '').strip()
        hora_salida_str = data.get('hora_salida', '').strip()
        tomo_comida = bool(data.get('tomo_comida'))
        aplica_viaticos = bool(data.get('aplica_viaticos'))
        viaticos_modo = data.get('viaticos_modo', 'perfil')
        monto_viaticos_manual_str = data.get('monto_viaticos_manual', '').strip()
        aplica_dia_festivo = bool(data.get('aplica_dia_festivo'))
        incidencia = data.get('incidencia', '').strip()
        horas_override_str = data.get('horas_productivas_override', '').strip()

        trabajador = registro.trabajador

        if (not hora_entrada_str or not hora_salida_str) and not incidencia:
            return err("Debes registrar hora de entrada y salida, o seleccionar una incidencia.")

        monto_viaticos_manual = None
        if aplica_viaticos:
            if viaticos_modo == 'manual':
                if not monto_viaticos_manual_str:
                    return err("Error: Debes ingresar un monto para los viáticos manuales.")
                try:
                    monto_viaticos_manual = float(monto_viaticos_manual_str)
                except (ValueError, TypeError):
                    return err("Error: El monto de viáticos ingresado no es un número válido.")
                if monto_viaticos_manual <= 0:
                    return err("Error: El monto manual de viáticos debe ser mayor a $0.00.")
            else:
                if trabajador.viaticos is None or trabajador.viaticos <= 0:
                    return err(f"Error: No se pueden habilitar viáticos para {trabajador.nombre}. Su perfil tiene $0.00 asignados.")

        if aplica_dia_festivo and (trabajador.pago_dia_festivo is None or trabajador.pago_dia_festivo <= 0):
            return err(f"Error: No se puede habilitar pago por día festivo para {trabajador.nombre}.")

        hora_entrada = None
        hora_salida = None
        horas_productivas = 0.0

        if hora_entrada_str and hora_salida_str:
            try:
                hora_entrada = datetime.strptime(hora_entrada_str, '%H:%M').time()
                hora_salida = datetime.strptime(hora_salida_str, '%H:%M').time()
            except ValueError:
                return err("Error: Formato de hora inválido.")

            if hora_entrada == hora_salida:
                return err("La hora de salida debe ser distinta a la hora de entrada.")

            registros_existentes = RegistroDiarioHoras.query.join(ReporteSemanal).filter(
                RegistroDiarioHoras.trabajador_id == trabajador.id,
                RegistroDiarioHoras.fecha == registro.fecha,
                ReporteSemanal.estado == 'BORRADOR',
                RegistroDiarioHoras.id != registro.id
            ).all()

            for reg in registros_existentes:
                if reg.hora_entrada and reg.hora_salida:
                    from app.utils import turnos_se_traslapan
                    if turnos_se_traslapan(hora_entrada, hora_salida, reg.hora_entrada, reg.hora_salida):
                        return err(f"Error: El horario choca con un registro existente en el proyecto '{reg.reporte.proyecto.nombre}'.")

            if horas_override_str:
                try:
                    horas_override = round(float(horas_override_str), 2)
                    if not (0 <= horas_override <= 24):
                        return err("Error: El override de horas debe estar entre 0 y 24.")
                    horas_productivas = horas_override
                except (ValueError, TypeError):
                    from app.utils import calcular_horas_productivas
                    horas_productivas = calcular_horas_productivas(
                        hora_entrada, hora_salida,
                        tipo_nomina=trabajador.tipo_nomina or 'Semanal',
                        tomo_comida=tomo_comida
                    )
            else:
                from app.utils import calcular_horas_productivas
                horas_productivas = calcular_horas_productivas(
                    hora_entrada, hora_salida,
                    tipo_nomina=trabajador.tipo_nomina or 'Semanal',
                    tomo_comida=tomo_comida
                )
        elif incidencia and horas_override_str:
            try:
                horas_productivas = round(float(horas_override_str), 2)
            except (ValueError, TypeError):
                pass

        registro.hora_entrada = hora_entrada
        registro.hora_salida = hora_salida
        registro.tomo_comida = tomo_comida
        registro.aplica_viaticos = aplica_viaticos
        registro.monto_viaticos_manual = monto_viaticos_manual
        registro.aplica_dia_festivo = aplica_dia_festivo
        registro.incidencia = incidencia if incidencia else None
        registro.horas_productivas = horas_productivas

        db.session.commit()

        if is_ajax:
            return jsonify({'ok': True, 'registro': _reg_dict(registro)})
        flash("Registro actualizado exitosamente.", "success")

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error editando registro: {traceback.format_exc()}")
        return err("Error interno al actualizar registro.")

    return redirect(url_for('horas.capturar', reporte_id=reporte.id))


@bp.route('/cerrar_reporte/<int:reporte_id>', methods=['POST'])
@login_required
@limiter.limit("10 per minute")
def cerrar_reporte(reporte_id):
    reporte = ReporteSemanal.query.get_or_404(reporte_id)

    if not _verificar_ownership_proyecto(reporte.proyecto):
        flash("Acceso denegado. No eres coordinador de este proyecto.", "danger")
        return redirect(url_for('horas.index'))

    if reporte.estado == 'BORRADOR':
        # Validar que tenga al menos un registro de horas
        if not reporte.registros:
            flash("No se puede cerrar el reporte sin horas registradas. Añade al menos un registro para cerrarlo.", "danger")
            return redirect(url_for('horas.capturar', reporte_id=reporte.id))

        reporte.estado = 'TERMINADO'
        db.session.commit()
        log_action(f"Cerró reporte semanal ID: {reporte.id} Proyecto: {reporte.proyecto.numero_proyecto}")
        flash("El reporte semanal ha sido CERRADO. Ya no se pueden agregar más horas y está listo para Prenómina.", "success")

        try:
            from app.models import crear_notif_admins
            num = reporte.proyecto.numero_proyecto if reporte.proyecto else '—'
            semana = reporte.fecha_inicio_semana.strftime('%d/%m/%Y') if reporte.fecha_inicio_semana else ''
            crear_notif_admins(
                tipo='REPORTE_CERRADO',
                titulo=f'Reporte de horas cerrado — Proyecto {num}',
                mensaje=f'El reporte semanal del proyecto {num} (semana del {semana}) fue enviado y está listo para prenómina.',
                url='/prenomina/',
            )
            db.session.commit()
        except Exception:
            current_app.logger.warning("No se pudo crear notificación de reporte cerrado.", exc_info=True)

    return redirect(url_for('horas.capturar', reporte_id=reporte.id))


# ─── Módulo Móvil Coordinadores ──────────────────────────────────────────────

@bp.route('/movil', methods=['GET'])
@login_required
def movil():
    role = session.get('role', 'user')
    if role not in ('coordinador', 'admin', 'super_admin'):
        flash("Acceso denegado.", "danger")
        return redirect(url_for('main.home'))

    user_id = session.get('user_id')
    hoy = date.today()

    if role == 'coordinador':
        proyectos = Proyecto.query.filter_by(activo=True, coordinador_id=user_id).all()
    else:
        proyectos = Proyecto.query.filter_by(activo=True).all()

    proyecto_ids = [p.id for p in proyectos]
    if proyecto_ids:
        reportes = ReporteSemanal.query.filter(
            ReporteSemanal.estado == 'BORRADOR',
            ReporteSemanal.proyecto_id.in_(proyecto_ids)
        ).order_by(ReporteSemanal.fecha_fin_semana.desc()).all()
    else:
        reportes = []

    return render_template('horas_movil.html', proyectos=proyectos, reportes=reportes, hoy=hoy)


@bp.route('/qr_check', methods=['POST'])
@login_required
@limiter.limit("30 per minute")
def qr_check():
    role = session.get('role', 'user')
    if role not in ('coordinador', 'admin', 'super_admin'):
        return jsonify({'ok': False, 'error': 'Acceso denegado'}), 403

    payload = request.get_json(silent=True)
    if not payload or 'qr_code' not in payload:
        return jsonify({'ok': False, 'error': 'Datos inválidos'}), 400

    qr_value = payload['qr_code']
    reporte_id = payload.get('reporte_id')

    trabajador = Trabajador.query.filter_by(qr_code=qr_value, activo=True).first()
    if not trabajador:
        return jsonify({'ok': False, 'error': 'QR no reconocido'}), 404

    hoy = date.today()

    if reporte_id:
        reporte = ReporteSemanal.query.get(int(reporte_id))
        if not reporte or reporte.estado != 'BORRADOR':
            return jsonify({'ok': False, 'error': 'El reporte ya fue cerrado o no existe'}), 404
        # Bloquear que un coordinador clockee en proyectos que no le pertenecen.
        if not _verificar_ownership_proyecto(reporte.proyecto):
            return jsonify({'ok': False, 'error': 'Acceso denegado. No eres coordinador de este proyecto.'}), 403
        if trabajador not in reporte.proyecto.participantes:
            return jsonify({'ok': False, 'error': f'{trabajador.nombre_completo} no está asignado a este proyecto'}), 404
        if not (reporte.fecha_inicio_semana <= hoy <= reporte.fecha_fin_semana):
            return jsonify({'ok': False, 'error': (
                f'La fecha de hoy ({hoy.strftime("%d/%m/%Y")}) no pertenece al periodo '
                f'{reporte.fecha_inicio_semana.strftime("%d/%m/%Y")} – {reporte.fecha_fin_semana.strftime("%d/%m/%Y")}. '
                f'Selecciona el reporte correcto.'
            )}), 400
    else:
        reportes_activos = ReporteSemanal.query.filter(
            ReporteSemanal.estado == 'BORRADOR',
            ReporteSemanal.fecha_inicio_semana <= hoy,
            ReporteSemanal.fecha_fin_semana >= hoy
        ).all()
        # Solo considerar reportes donde el caller tenga ownership (coordinador del proyecto o admin).
        reporte = next(
            (r for r in reportes_activos
             if trabajador in r.proyecto.participantes and _verificar_ownership_proyecto(r.proyecto)),
            None
        )
        if reporte is None:
            return jsonify({'ok': False, 'error': 'No hay reporte activo para este trabajador hoy'}), 404

    # WITH FOR UPDATE serializa requests simultáneos del mismo reporte (evita duplicados por race condition)
    ReporteSemanal.query.filter_by(id=reporte.id).with_for_update().first()

    registro = RegistroDiarioHoras.query.filter_by(
        reporte_id=reporte.id,
        trabajador_id=trabajador.id,
        fecha=hoy
    ).first()

    _now = datetime.now()
    _total_min = _now.hour * 60 + _now.minute
    _rounded = round(_total_min / 30) * 30
    from datetime import time as _dtime
    now_time = _dtime((_rounded // 60) % 24, _rounded % 60)

    try:
        if registro is None:
            registro = RegistroDiarioHoras(
                reporte_id=reporte.id,
                trabajador_id=trabajador.id,
                fecha=hoy,
                hora_entrada=now_time,
                tipo_nomina=trabajador.tipo_nomina or 'Semanal'
            )
            db.session.add(registro)
            db.session.commit()
            return jsonify({
                'ok': True,
                'tipo': 'ENTRADA',
                'hora': now_time.strftime('%H:%M'),
                'nombre': trabajador.nombre_completo
            })

        if registro.hora_entrada and not registro.hora_salida:
            from app.utils import calcular_horas_productivas
            registro.hora_salida = now_time
            registro.horas_productivas = calcular_horas_productivas(
                registro.hora_entrada, now_time,
                tipo_nomina=registro.tipo_nomina or 'Semanal',
                tomo_comida=bool(registro.tomo_comida)
            )
            db.session.commit()
            return jsonify({
                'ok': True,
                'tipo': 'SALIDA',
                'hora': now_time.strftime('%H:%M'),
                'nombre': trabajador.nombre_completo
            })

        return jsonify({'ok': False, 'error': 'Ya tiene entrada y salida registradas hoy'}), 409

    except Exception:
        db.session.rollback()
        current_app.logger.error("Error en qr_check: %s", traceback.format_exc())
        return jsonify({'ok': False, 'error': 'Error interno'}), 500


# ─── Panel Admin QR ──────────────────────────────────────────────────────────

@bp.route('/admin/qr', methods=['GET'])
@login_required
def admin_qr():
    role = session.get('role', 'user')
    if role not in ('admin', 'super_admin'):
        flash("Acceso denegado.", "danger")
        return redirect(url_for('horas.index'))

    trabajadores = Trabajador.query.filter_by(activo=True).order_by(Trabajador.nombre).all()
    return render_template('horas_admin_qr.html', trabajadores=trabajadores)


@bp.route('/admin/qr/generar', methods=['POST'])
@login_required
@limiter.limit("20 per minute")
def admin_qr_generar():
    role = session.get('role', 'user')
    if role not in ('admin', 'super_admin'):
        return jsonify({'ok': False, 'error': 'Acceso denegado'}), 403

    trabajador_id = request.form.get('trabajador_id')
    if not trabajador_id:
        return jsonify({'ok': False, 'error': 'Datos inválidos'}), 400

    trabajador = Trabajador.query.get_or_404(trabajador_id)

    try:
        import uuid
        trabajador.qr_code = str(uuid.uuid4())
        db.session.commit()
        imagen_url = url_for('horas.qr_imagen', qr_code=trabajador.qr_code)
        return jsonify({'ok': True, 'qr_code': trabajador.qr_code, 'imagen_url': imagen_url})
    except Exception:
        db.session.rollback()
        current_app.logger.error("Error generando QR: %s", traceback.format_exc())
        return jsonify({'ok': False, 'error': 'Error al generar QR'}), 500


@bp.route('/admin/qr/imagen/<qr_code>', methods=['GET'])
@login_required
def qr_imagen(qr_code):
    import qrcode
    import io
    img = qrcode.make(qr_code)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return send_file(buf, mimetype='image/png')


@bp.route('/admin/qr/print/<int:trabajador_id>', methods=['GET'])
@login_required
def qr_print(trabajador_id):
    role = session.get('role', 'user')
    if role not in ('admin', 'super_admin'):
        flash("Acceso denegado.", "danger")
        return redirect(url_for('horas.index'))

    trabajador = Trabajador.query.get_or_404(trabajador_id)
    if not trabajador.qr_code:
        flash("Este trabajador no tiene QR asignado.", "warning")
        return redirect(url_for('horas.admin_qr'))

    return render_template('horas_qr_print.html', trabajador=trabajador)


@bp.route('/capturar_movil/<int:reporte_id>', methods=['GET'])
@login_required
def capturar_movil(reporte_id):
    reporte = ReporteSemanal.query.get_or_404(reporte_id)
    user_id = session.get('user_id')
    user_role = session.get('role', 'user')
    if user_role == 'coordinador' and reporte.proyecto.coordinador_id != user_id:
        flash("Acceso denegado.", "danger")
        return redirect(url_for('horas.lista_movil'))

    trabajadores = sorted(reporte.proyecto.participantes, key=lambda t: t.nombre)
    incidencias_lista = [
        "Casamiento", "Descanso", "Falta", "Incapacidad", "Luto", "Paternidad",
        "Permiso", "Time x Time", "Vacaciones", "Viaje de Ida", "Viaje de vuelta a Pue.",
        "Retardo", "Levantamiento en campo", "Falta checada de entrada", "Falta checada de salida"
    ]

    semana_fechas = []
    d = reporte.fecha_inicio_semana
    while d <= reporte.fecha_fin_semana:
        semana_fechas.append(d)
        d += timedelta(days=1)

    registros_json = [
        {
            'id': reg.id,
            'trabajador_id': reg.trabajador_id,
            'fecha': reg.fecha.strftime('%Y-%m-%d'),
            'hora_entrada': reg.hora_entrada.strftime('%H:%M') if reg.hora_entrada else '',
            'hora_salida': reg.hora_salida.strftime('%H:%M') if reg.hora_salida else '',
            'tomo_comida': bool(reg.tomo_comida),
            'incidencia': reg.incidencia or '',
            'horas_productivas': float(reg.horas_productivas) if reg.horas_productivas else 0.0,
        }
        for reg in reporte.registros
    ]
    semana_fechas_json = [
        {
            'fecha': d.strftime('%Y-%m-%d'),
            'label': d.strftime('%d/%m'),
            'dia': ['Lun', 'Mar', 'Mié', 'Jue', 'Vie', 'Sáb', 'Dom'][d.weekday()],
            'fin_semana': d.weekday() >= 5,
        }
        for d in semana_fechas
    ]

    return render_template(
        'horas_captura_movil.html',
        reporte=reporte,
        trabajadores=trabajadores,
        incidencias_lista=incidencias_lista,
        registros_json=registros_json,
        semana_fechas_json=semana_fechas_json,
    )


@bp.route('/lista', methods=['GET'])
@login_required
def lista_movil():
    user_id = session.get('user_id')
    user_role = session.get('role', 'user')

    if user_role not in ('coordinador', 'admin', 'super_admin'):
        flash("Acceso denegado.", "danger")
        return redirect(url_for('main.home'))

    q = request.args.get('q', '').strip()

    if user_role == 'coordinador':
        proyectos = Proyecto.query.filter_by(activo=True, coordinador_id=user_id).all()
        proyecto_ids = [p.id for p in proyectos]
        query = ReporteSemanal.query.filter(ReporteSemanal.proyecto_id.in_(proyecto_ids))
    else:
        proyectos = Proyecto.query.filter_by(activo=True).all()
        query = ReporteSemanal.query

    if q:
        query = query.join(Proyecto).filter(
            db.or_(
                Proyecto.nombre.ilike(f'%{q}%'),
                Proyecto.numero_proyecto.ilike(f'%{q}%')
            )
        )

    reportes = query.order_by(ReporteSemanal.created_at.desc()).limit(60).all()
    return render_template('horas_lista_movil.html', reportes=reportes, proyectos=proyectos, q=q)
