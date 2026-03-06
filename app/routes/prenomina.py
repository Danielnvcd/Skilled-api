from flask import Blueprint, render_template, request, flash, redirect, url_for, jsonify, current_app
from datetime import datetime, timedelta
from decimal import Decimal
import traceback
from app.extensions import db
from app.models import ReporteSemanal, Prenomina, Trabajador, Prestamo, RegistroDiarioHoras, DescuentoPrenomina, DepositoExtra, AbonoPrestamo
from app.utils import login_required, log_action, to_dec, recalcular_totales_prenomina

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
            
    # Prenóminas ya procesadas (solo extraemos las fechas distintas, sin cargar todas las filas)
    prenominas_fechas = { f[0].strftime('%Y-%m-%d') for f in db.session.query(Prenomina.fecha_inicio).distinct().all() }
    
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
    estado_actual = prenominas[0].estado if ya_guardada else 'PENDIENTE'

    # Si aún no hay prenominas guardadas en BBDD para esta semana consolida, generamos el preview
    if not prenominas:
        prenominas = calcular_preview_prenomina(fecha_obj, reportes)
    else:
        # Calcular las horas dinámicamente en batch (un solo query para todos los trabajadores)
        reporte_ids = [r.id for r in reportes]
        todos_registros = RegistroDiarioHoras.query.filter(
            RegistroDiarioHoras.reporte_id.in_(reporte_ids)
        ).all()
        # Agrupar por trabajador
        registros_por_trabajador = {}
        for reg in todos_registros:
            registros_por_trabajador.setdefault(reg.trabajador_id, []).append(reg)
        for p in prenominas:
            regs = registros_por_trabajador.get(p.trabajador_id, [])
            total_horas = sum(r.horas_productivas or 0 for r in regs)
            p.total_horas_calculadas = to_dec(total_horas)
        
    proyectos_involucrados = [r.proyecto for r in reportes]
        
    return render_template('prenomina_generar.html', fecha_inicio=fecha_obj, fecha_fin=reportes[0].fecha_fin_semana, fecha_str=fecha_str, prenominas=prenominas, proyectos_involucrados=proyectos_involucrados, ya_guardada=ya_guardada, estado_actual=estado_actual)

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
                p.estado = 'ABIERTA'
                db.session.add(p)
                
            for r in reportes:
                r.estado = 'PRENOMINA_CERRADA'
            db.session.commit()
            
            log_action(f'crear_prenomina: Prenómina guardada y cerrada globalmente para la semana {fecha_str}')
            return jsonify({'success': True, 'message': 'Nómina global generada y guardada correctamente.'})
        else:
            return jsonify({'success': False, 'message': 'La prenómina para esta semana ya fue guardada anteriormente.'}), 400
            
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error al guardar prenómina global: {traceback.format_exc()}")
        return jsonify({'success': False, 'message': 'Ocurrió un error al intentar guardar la prenómina.'}), 500

@bp.route('/editar/<fecha_str>')
@login_required
def editar(fecha_str):
    try:
        fecha_obj = datetime.strptime(fecha_str, '%Y-%m-%d').date()
    except ValueError:
        flash("Fecha inválida.", "danger")
        return redirect(url_for('prenomina.index'))
    
    prenominas = Prenomina.query.filter_by(fecha_inicio=fecha_obj).all()
    if not prenominas:
        flash("No hay prenóminas para esta fecha.", "warning")
        return redirect(url_for('prenomina.index'))
    
    estado_actual = prenominas[0].estado if prenominas else 'PENDIENTE'
    if estado_actual == 'APROBADO':
        flash("Esta prenómina ya fue cerrada y no puede editarse.", "warning")
        return redirect(url_for('prenomina.generar', fecha_str=fecha_str))
    
    # Obtener incidencias del reporte de horas para esta semana
    reportes = ReporteSemanal.query.filter(
        ReporteSemanal.fecha_inicio_semana == fecha_obj,
        ReporteSemanal.estado.in_(['TERMINADO', 'PRENOMINA_CERRADA'])
    ).all()
    
    # Reconstruir las horas trabajadas en batch (un solo query para todos los trabajadores)
    reporte_ids = [r.id for r in reportes]
    todos_registros = RegistroDiarioHoras.query.filter(
        RegistroDiarioHoras.reporte_id.in_(reporte_ids)
    ).all()
    registros_por_trabajador = {}
    for reg in todos_registros:
        registros_por_trabajador.setdefault(reg.trabajador_id, []).append(reg)
    for p in prenominas:
        regs = registros_por_trabajador.get(p.trabajador_id, [])
        total_horas = sum(r.horas_productivas or 0 for r in regs)
        p.total_horas_calculadas = to_dec(total_horas)
    
    incidencias_por_trabajador = {}
    incidencias_descontables = ['Falta', 'Retardo', 'Falta checada de entrada', 'Falta checada de salida', 'Permiso', 'Luto', 'Casamiento']
    for r in reportes:
        for reg in r.registros:
            if reg.incidencia and reg.incidencia in incidencias_descontables:
                if reg.trabajador_id not in incidencias_por_trabajador:
                    incidencias_por_trabajador[reg.trabajador_id] = []
                incidencias_por_trabajador[reg.trabajador_id].append({
                    'fecha': reg.fecha.strftime('%Y-%m-%d'),
                    'incidencia': reg.incidencia,
                    'horas': float(reg.horas_productivas or 0)  # horas no es monetario
                })
    
    # Préstamos activos por trabajador
    prestamos_por_trabajador = {}
    for p in prenominas:
        prestamos_activos = Prestamo.query.filter_by(trabajador_id=p.trabajador_id, estado='ACTIVO').all()
        if prestamos_activos:
            prestamos_por_trabajador[p.trabajador_id] = prestamos_activos
    
    fecha_fin = prenominas[0].fecha_fin if prenominas else None
    
    return render_template('prenomina_editar.html',
        prenominas=prenominas,
        fecha_str=fecha_str,
        fecha_inicio=fecha_obj,
        fecha_fin=fecha_fin,
        estado_actual=estado_actual,
        incidencias_por_trabajador=incidencias_por_trabajador,
        prestamos_por_trabajador=prestamos_por_trabajador
    )

@bp.route('/cerrar/<fecha_str>', methods=['POST'])
@login_required
def cerrar_prenomina(fecha_str):
    try:
        fecha_obj = datetime.strptime(fecha_str, '%Y-%m-%d').date()
        prenominas = Prenomina.query.filter_by(fecha_inicio=fecha_obj, estado='ABIERTA').all()
        if not prenominas:
            return jsonify({'success': False, 'message': 'No hay prenóminas abiertas para cerrar.'}), 400
        
        from flask import session
        for p in prenominas:
            p.estado = 'APROBADO'
            
            # Aplicar descuentos de préstamos reales a la deuda
            if p.descuento_prestamos and to_dec(p.descuento_prestamos) > Decimal('0'):
                prestamos_activos = Prestamo.query.filter_by(trabajador_id=p.trabajador_id, estado='ACTIVO').all()
                for prestamo in prestamos_activos:
                    descuento = to_dec(prestamo.descuento_semanal)
                    restante = to_dec(prestamo.monto_restante)
                    
                    # Si el saldo ya está en 0, marcar como liquidado y saltar (sin registrar abono)
                    if restante <= 0:
                        prestamo.monto_restante = 0
                        prestamo.estado = 'LIQUIDADO'
                        prestamo.activo = False
                        continue
                    
                    if descuento <= 0:
                        continue
                    
                    # Abono real = lo menor entre el descuento programado y lo que falta por pagar
                    abono_real = min(descuento, restante)
                    
                    abono = AbonoPrestamo(
                        prestamo_id=prestamo.id,
                        monto=abono_real,
                        fecha_abono=datetime.now().date(),
                        tipo='NOMINA',
                        registrado_por_id=session.get('user_id'),
                        notas=f'Descuento automático por prenómina del {fecha_str}'
                    )
                    db.session.add(abono)
                    
                    prestamo.monto_restante = restante - abono_real
                    if prestamo.monto_restante <= 0:
                        prestamo.monto_restante = 0
                        prestamo.estado = 'LIQUIDADO'
                        prestamo.activo = False
        
        db.session.commit()
        log_action(f'cerrar_prenomina: Nómina global cerrada para la semana {fecha_str}')
        return jsonify({'success': True, 'message': 'Nómina cerrada exitosamente. Ya no se pueden realizar cambios.'})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error al cerrar prenómina: {traceback.format_exc()}")
        return jsonify({'success': False, 'message': 'Ocurrió un error al cerrar la prenómina.'}), 500

@bp.route('/api/descuento', methods=['POST'])
@login_required
def api_agregar_descuento():
    try:
        data = request.get_json(silent=True)
        if not data:
            return jsonify({'success': False, 'message': 'Datos inválidos o vacíos.'}), 400
        
        # Validar campos requeridos
        campos_requeridos = ['prenomina_id', 'tipo', 'concepto', 'monto']
        faltantes = [c for c in campos_requeridos if not data.get(c)]
        if faltantes:
            return jsonify({'success': False, 'message': f'Faltan campos obligatorios: {", ".join(faltantes)}'}), 400
        
        try:
            prenomina_id = int(data['prenomina_id'])
            monto = Decimal(str(data['monto']))
        except (ValueError, TypeError):
            return jsonify({'success': False, 'message': 'prenomina_id y monto deben ser numéricos.'}), 400
        
        tipo = data['tipo']
        concepto = data['concepto']
        fecha_inc = data.get('fecha_incidencia')
        
        if monto <= 0:
            return jsonify({'success': False, 'message': 'El monto debe ser mayor a cero.'}), 400
        
        prenomina = Prenomina.query.get_or_404(prenomina_id)
        if prenomina.estado != 'ABIERTA':
            return jsonify({'success': False, 'message': 'Solo se pueden editar prenóminas ABIERTAS.'}), 400
        
        desc = DescuentoPrenomina(
            prenomina_id=prenomina_id,
            trabajador_id=prenomina.trabajador_id,
            tipo=tipo,
            concepto=concepto,
            monto=monto,
            fecha_incidencia=datetime.strptime(fecha_inc, '%Y-%m-%d').date() if fecha_inc else None
        )
        db.session.add(desc)
        db.session.commit()
        
        recalcular_totales_prenomina(prenomina)
        
        return jsonify({'success': True, 'message': 'Descuento agregado.', 'id': desc.id,
            'total_deducciones': float(prenomina.total_deducciones),
            'total_a_pagar': float(prenomina.total_a_pagar)})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error al agregar descuento: {traceback.format_exc()}")
        return jsonify({'success': False, 'message': 'Ocurrió un error al agregar el descuento.'}), 500

@bp.route('/api/descuento/<int:id>', methods=['DELETE'])
@login_required
def api_eliminar_descuento(id):
    try:
        desc = DescuentoPrenomina.query.get_or_404(id)
        prenomina = desc.prenomina
        if prenomina.estado != 'ABIERTA':
            return jsonify({'success': False, 'message': 'Solo se pueden editar prenóminas ABIERTAS.'}), 400
        
        db.session.delete(desc)
        db.session.commit()
        recalcular_totales_prenomina(prenomina)
        
        return jsonify({'success': True, 'message': 'Descuento eliminado.',
            'total_deducciones': float(prenomina.total_deducciones),
            'total_a_pagar': float(prenomina.total_a_pagar)})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error al eliminar descuento: {traceback.format_exc()}")
        return jsonify({'success': False, 'message': 'Ocurrió un error al eliminar el descuento.'}), 500

@bp.route('/api/deposito', methods=['POST'])
@login_required
def api_agregar_deposito():
    try:
        data = request.get_json(silent=True)
        if not data:
            return jsonify({'success': False, 'message': 'Datos inválidos o vacíos.'}), 400
        
        campos_requeridos = ['prenomina_id', 'monto', 'concepto']
        faltantes = [c for c in campos_requeridos if not data.get(c)]
        if faltantes:
            return jsonify({'success': False, 'message': f'Faltan campos obligatorios: {", ".join(faltantes)}'}), 400
        
        try:
            prenomina_id = int(data['prenomina_id'])
            monto = Decimal(str(data['monto']))
        except (ValueError, TypeError):
            return jsonify({'success': False, 'message': 'prenomina_id y monto deben ser numéricos.'}), 400
        
        concepto = data['concepto']
        
        if monto <= 0:
            return jsonify({'success': False, 'message': 'El monto debe ser mayor a cero.'}), 400
        
        prenomina = Prenomina.query.get_or_404(prenomina_id)
        if prenomina.estado != 'ABIERTA':
            return jsonify({'success': False, 'message': 'Solo se pueden editar prenóminas ABIERTAS.'}), 400
        
        dep = DepositoExtra(
            prenomina_id=prenomina_id,
            trabajador_id=prenomina.trabajador_id,
            monto=monto,
            concepto=concepto
        )
        db.session.add(dep)
        db.session.commit()
        recalcular_totales_prenomina(prenomina)
        
        return jsonify({'success': True, 'message': 'Depósito agregado.', 'id': dep.id,
            'total_percepciones': float(prenomina.total_percepciones),
            'total_a_pagar': float(prenomina.total_a_pagar)})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error al agregar depósito: {traceback.format_exc()}")
        return jsonify({'success': False, 'message': 'Ocurrió un error al agregar el depósito.'}), 500

@bp.route('/api/deposito/<int:id>', methods=['DELETE'])
@login_required
def api_eliminar_deposito(id):
    try:
        dep = DepositoExtra.query.get_or_404(id)
        prenomina = dep.prenomina
        if prenomina.estado != 'ABIERTA':
            return jsonify({'success': False, 'message': 'Solo se pueden editar prenóminas ABIERTAS.'}), 400
        
        db.session.delete(dep)
        db.session.commit()
        recalcular_totales_prenomina(prenomina)
        
        return jsonify({'success': True, 'message': 'Depósito eliminado.',
            'total_percepciones': float(prenomina.total_percepciones),
            'total_a_pagar': float(prenomina.total_a_pagar)})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error al eliminar depósito: {traceback.format_exc()}")
        return jsonify({'success': False, 'message': 'Ocurrió un error al eliminar el depósito.'}), 500

# _recalcular_prenomina removida — usar recalcular_totales_prenomina() desde utils.py

def calcular_preview_prenomina(fecha_obj, reportes):
    """
    Función utilitaria (lógica de negocio).
    Toma una lista de ReporteSemanal de todos los proyectos de ESA semana,
    y traduce esas horas sumadas para cada trabajador devolviendo Prenominas globales simuladas.
    """
    preview = []
    
    # Obtenemos ids únicos de los trabajadores involucrados en esta semana
    trabajadores_ids = set()
    all_registros = []  # Recopilar todos los registros una sola vez
    for r in reportes:
        for reg in r.registros:
            trabajadores_ids.add(reg.trabajador_id)
            all_registros.append(reg)
            
    if not trabajadores_ids:
        return preview
    
    fecha_fin_semana = reportes[0].fecha_fin_semana if reportes else None
    
    # Batch-load: un solo query para todos los trabajadores en vez de N queries individuales
    trabajadores_map = {t.id: t for t in Trabajador.query.filter(Trabajador.id.in_(trabajadores_ids)).all()}
    
    # Batch-load: un solo query para todos los préstamos activos de estos trabajadores
    todos_prestamos = Prestamo.query.filter(
        Prestamo.trabajador_id.in_(trabajadores_ids),
        Prestamo.estado == 'ACTIVO'
    ).all()
    prestamos_por_trabajador = {}
    for pr in todos_prestamos:
        prestamos_por_trabajador.setdefault(pr.trabajador_id, []).append(pr)
    
    # Agrupar registros por trabajador (sin queries adicionales)
    registros_por_trabajador = {}
    for reg in all_registros:
        registros_por_trabajador.setdefault(reg.trabajador_id, []).append(reg)
    
    for t_id in trabajadores_ids:
        trabajador = trabajadores_map.get(t_id)
        if not trabajador:
            continue
        
        registros_trabajador = registros_por_trabajador.get(t_id, [])
        
        # 1. Totalizar horas productivas consolidando proyectos
        total_horas = sum(r.horas_productivas or 0 for r in registros_trabajador)
        
        p = Prenomina(
            reporte_semanal_id=None,
            trabajador_id=trabajador.id,
            trabajador=trabajador,
            fecha_inicio=fecha_obj,
            fecha_fin=fecha_fin_semana,
            tipo_pago=trabajador.tipo_pago or 'EFECTIVO',
            pago_festivos=Decimal('0'),
            depositos_otros=Decimal('0'),
            depositos_prestamos=Decimal('0'),
            descuentos_otros=Decimal('0'),
            descuento_prestamos=Decimal('0'),
            descuento_incidencias=Decimal('0'),
            recuperacion_manual=Decimal('0')
        )
        
        # Guardar para visualización temporal en pantalla
        p.total_horas_calculadas = to_dec(total_horas)
        
        tipo = trabajador.tipo_nomina or 'Semanal'
        p.salario_base = Decimal('0')
        p.pago_horas_extras = Decimal('0')
        
        salario_pactado = to_dec(trabajador.salario_real_pactado_x_sem)
        
        if tipo == 'Por hora':
            p.salario_base = to_dec(total_horas) * salario_pactado
        elif tipo == 'Cuadrado':
            p.salario_base = salario_pactado
        else: # Semanal
            p.salario_base = salario_pactado
            if total_horas > 50:
                horas_extras = to_dec(total_horas) - Decimal('50')
                costo_hr_extra = to_dec(trabajador.hr_extra)
                p.pago_horas_extras = horas_extras * costo_hr_extra
        
        # Deducciones maestras
        p.descuento_infonavit = to_dec(trabajador.infonavit)
        # Ajuste Inbursa: sumar descuentos dinámicos del módulo de ajustes
        # Busca descuentos cuya fecha caiga dentro de la semana de prenómina
        from app.models import AjusteDescuento
        import logging
        ajuste_descuentos = AjusteDescuento.query.filter(
            AjusteDescuento.trabajador_id == trabajador.id,
            AjusteDescuento.fecha_descuento >= fecha_obj,
            AjusteDescuento.fecha_descuento <= fecha_fin_semana
        ).all()
        logging.info(f"Ajuste Inbursa para {trabajador.nombre_apellidos}: semana {fecha_obj}-{fecha_fin_semana}, descuentos encontrados: {len(ajuste_descuentos)}, montos: {[float(d.monto) for d in ajuste_descuentos]}")
        if ajuste_descuentos:
            p.ajuste_inbursa = sum(to_dec(d.monto) for d in ajuste_descuentos)
        else:
            p.ajuste_inbursa = to_dec(trabajador.ajuste_inbursa)
        
        # Cálculo de Incidencias Consolidadas
        total_descuento_incidencias = Decimal('0')
        if tipo in ['Semanal', 'Cuadrado']:
            horas_ausentes_incidencia = Decimal('0')
            incidencias_descontables = ['Falta', 'Retardo', 'Falta checada de entrada', 'Falta checada de salida', 'Permiso', 'Luto', 'Casamiento']
            
            for reg in registros_trabajador:
                if reg.incidencia in incidencias_descontables:
                    if not reg.horas_productivas or reg.horas_productivas == 0:
                        horas_ausentes_incidencia += Decimal('10')
                        
            if horas_ausentes_incidencia > 0:
                 costo_hora_ord = salario_pactado / Decimal('50')
                 total_descuento_incidencias = horas_ausentes_incidencia * costo_hora_ord
                 p.descuento_incidencias = total_descuento_incidencias
                 
        # Viáticos: sumar por día usando monto manual o del perfil
        total_viaticos = Decimal('0')
        for reg in registros_trabajador:
            if reg.aplica_viaticos:
                if reg.monto_viaticos_manual is not None:
                    total_viaticos += to_dec(reg.monto_viaticos_manual)
                elif trabajador.viaticos:
                    total_viaticos += to_dec(trabajador.viaticos)
        p.pago_viaticos = total_viaticos
        
        # Pago por Días Festivos (toggle por registro)
        if trabajador.pago_dia_festivo:
            dias_festivos = sum(1 for reg in registros_trabajador if reg.aplica_dia_festivo)
            p.pago_festivos = to_dec(trabajador.pago_dia_festivo) * Decimal(str(dias_festivos))
        else:
            p.pago_festivos = Decimal('0')
              
        # Préstamos activos (desde el batch pre-cargado, sin query adicional)
        prestamos_activos = prestamos_por_trabajador.get(t_id, [])
        p.descuento_prestamos = sum((to_dec(pr.descuento_semanal) for pr in prestamos_activos), Decimal('0'))
             
        p.total_percepciones = to_dec(p.salario_base) + to_dec(p.pago_horas_extras) + to_dec(p.pago_viaticos) + to_dec(p.pago_festivos)
        p.total_deducciones = to_dec(p.descuento_infonavit) + to_dec(p.ajuste_inbursa) + to_dec(p.descuento_incidencias) + to_dec(p.descuento_prestamos) + to_dec(p.descuentos_otros)
        p.total_a_pagar = p.total_percepciones - p.total_deducciones
        
        preview.append(p)
        
    return preview
