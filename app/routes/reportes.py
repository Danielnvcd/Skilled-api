import io
from flask import Blueprint, send_file, flash, redirect, current_app, request, url_for
from sqlalchemy import func
from datetime import datetime
import pandas as pd
from openpyxl.styles import Font, PatternFill, Alignment

from app.extensions import db
from app.models import (
    Trabajador, Prenomina, ReporteSemanal, RegistroDiarioHoras,
    Proyecto, Prestamo, AjustePeriodo, AjusteTrabajadorPeriodo, AjusteDescuento,
    AbonoPrestamo
)
from app.utils import login_required, admin_required, log_action

bp = Blueprint('reportes', __name__, url_prefix='/reportes')

@bp.route('/excel_global', methods=['GET'])
@login_required
@admin_required
def _aplicar_estilos_y_retornar(writer, output, filename):
    """Aplica el diseño base azul y retorna la respuesta de Flask para descargar el Excel."""
    header_fill = PatternFill(start_color="3B82F6", end_color="3B82F6", fill_type="solid")
    header_font = Font(name="Calibri", bold=True, color="FFFFFF")
    align_center = Alignment(horizontal="center", vertical="center")

    workbook = writer.book
    for sheet_name in workbook.sheetnames:
        worksheet = workbook[sheet_name]
        
        # Formato de Headers
        for col in range(1, worksheet.max_column + 1):
            cell = worksheet.cell(row=1, column=col)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = align_center

        # Auto-Ajuste de columnas
        for col in worksheet.columns:
            max_length = 0
            column = col[0].column_letter
            for cell in col:
                try:
                    if len(str(cell.value)) > max_length:
                        max_length = len(str(cell.value))
                except:
                    pass
            adjusted_width = (max_length + 2)
            worksheet.column_dimensions[column].width = min(adjusted_width, 40)
    
    writer.close()
    
    output.seek(0)
    
    log_action(f"Exportó Reporte Excel: {filename}")
    
    return send_file(
        output,
        download_name=filename,
        as_attachment=True,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )


@bp.route('/excel_prenomina/<fecha_str>', methods=['GET'])
@login_required
@admin_required
def excel_prenomina(fecha_str):
    try:
        try:
            fecha_obj = datetime.strptime(fecha_str, '%Y-%m-%d').date()
        except ValueError:
            flash("Formato de fecha inválido.", "danger")
            return redirect(request.referrer or url_for('prenomina.index'))

        output = io.BytesIO()
        writer = pd.ExcelWriter(output, engine='openpyxl')
        
        prenominas = Prenomina.query.filter_by(fecha_inicio=fecha_obj).all()
        if not prenominas:
            # Need to get review calculation like the UI does.
            from app.routes.prenomina import calcular_preview_prenomina
            reportes = ReporteSemanal.query.filter(
                ReporteSemanal.fecha_inicio_semana == fecha_obj,
                ReporteSemanal.estado.in_(['TERMINADO', 'PRENOMINA_CERRADA'])
            ).all()
            prenominas = calcular_preview_prenomina(fecha_obj, reportes)

        if not prenominas:
             flash("No hay datos calculables para la semana.", "warning")
             return redirect(request.referrer or url_for('prenomina.index'))

        data = []
        for p in prenominas:
            data.append({
                'Semana Inicio': p.fecha_inicio.strftime('%Y-%m-%d') if p.fecha_inicio else '',
                'Semana Fin': p.fecha_fin.strftime('%Y-%m-%d') if p.fecha_fin else '',
                'No. Empleado': p.trabajador.no_empleado if p.trabajador else '',
                'Nombre del Empleado': f"{p.trabajador.nombre} {p.trabajador.nombre_apellidos}" if p.trabajador else '',
                'Estado': p.estado if p.reporte_semanal_id is None else 'PREVIEW',
                'Salario Base': float(p.salario_base or 0),
                'Pago Horas Extras': float(p.pago_horas_extras or 0),
                'Pago Viáticos': float(p.pago_viaticos or 0),
                'Pago Festivos': float(p.pago_festivos or 0),
                'Otros Depósitos': float(p.depositos_otros or 0),
                'Depósitos Préstamos': float(p.depositos_prestamos or 0),
                'Total Percepciones': float(p.total_percepciones or 0),
                'Descuento Infonavit': float(p.descuento_infonavit or 0),
                'Ajuste Inbursa': float(p.ajuste_inbursa or 0),
                'Otros Descuentos': float(p.descuentos_otros or 0),
                'Abono Préstamos': float(p.descuento_prestamos or 0),
                'Descuento Incidencias': float(p.descuento_incidencias or 0),
                'Total Deducciones': float(p.total_deducciones or 0),
                'TOTAL A PAGAR': float(p.total_a_pagar or 0),
                'Método de Pago': p.tipo_pago or ''
            })
        df = pd.DataFrame(data)
        df.to_excel(writer, sheet_name='Prenómina', index=False)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M")
        return _aplicar_estilos_y_retornar(writer, output, f"Reporte_Prenomina_{fecha_str}_{timestamp}.xlsx")

    except Exception as e:
        current_app.logger.error(f"Error generando Excel Prenómina: {e}")
        flash("Error exportando reporte.", "danger")
        return redirect(request.referrer or '/')



@bp.route('/excel_historico/<fecha_str>', methods=['GET'])
@login_required
@admin_required
def excel_historico(fecha_str):
    try:
        try:
            fecha_obj = datetime.strptime(fecha_str, '%Y-%m-%d').date()
        except ValueError:
            flash("Formato de fecha inválido.", "danger")
            return redirect(request.referrer or url_for('historico.index'))

        output = io.BytesIO()
        writer = pd.ExcelWriter(output, engine='openpyxl')
        
        prenominas = Prenomina.query.filter(
            Prenomina.fecha_inicio == fecha_obj, 
            Prenomina.estado == 'APROBADO'
        ).order_by(Prenomina.trabajador_id).all()

        if not prenominas:
             flash("No se encontraron prenominas aprobadas para esta semana.", "warning")
             return redirect(request.referrer or url_for('historico.index'))

        data = []
        for p in prenominas:
            data.append({
                'Semana Inicio': p.fecha_inicio.strftime('%Y-%m-%d') if p.fecha_inicio else '',
                'Semana Fin': p.fecha_fin.strftime('%Y-%m-%d') if p.fecha_fin else '',
                'No. Empleado': p.trabajador.no_empleado if p.trabajador else '',
                'Nombre del Empleado': f"{p.trabajador.nombre} {p.trabajador.nombre_apellidos}" if p.trabajador else '',
                'Total Percepciones': float(p.total_percepciones or 0),
                'Total Deducciones': float(p.total_deducciones or 0),
                'TOTAL PAGADO': float(p.total_a_pagar or 0),
                'Método de Pago': p.tipo_pago or ''
            })
        df = pd.DataFrame(data)
        df.to_excel(writer, sheet_name='Histórico', index=False)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M")
        return _aplicar_estilos_y_retornar(writer, output, f"Reporte_Historico_{fecha_str}_{timestamp}.xlsx")

    except Exception as e:
        current_app.logger.error(f"Error generando Excel Histórico: {e}")
        flash("Error exportando reporte.", "danger")
        return redirect(request.referrer or '/')


@bp.route('/excel_proyecto_total/<int:proyecto_id>', methods=['GET'])
@login_required
@admin_required
def excel_proyecto_total(proyecto_id):
    try:
        proyecto = Proyecto.query.get_or_404(proyecto_id)
        
        output = io.BytesIO()
        writer = pd.ExcelWriter(output, engine='openpyxl')
        
        reportes = ReporteSemanal.query.filter_by(proyecto_id=proyecto.id, estado='PRENOMINA_CERRADA').order_by(ReporteSemanal.fecha_inicio_semana).all()
        
        if not reportes:
             flash("No hay semanas cerradas para este proyecto.", "warning")
             return redirect(request.referrer or url_for('proyecto_total.index'))

        data = []
        for rep in reportes:
            # Find workers that participated in this project this week
            trabajadores_in_project = db.session.query(RegistroDiarioHoras.trabajador_id).filter(
                RegistroDiarioHoras.reporte_id == rep.id, 
                RegistroDiarioHoras.horas_productivas > 0
            ).distinct().all()
            
            t_ids = [t[0] for t in trabajadores_in_project]
            
            prenominas_rep = Prenomina.query.filter(
                Prenomina.fecha_inicio == rep.fecha_inicio_semana,
                Prenomina.estado == 'APROBADO',
                Prenomina.trabajador_id.in_(t_ids)
            ).all() if t_ids else []

            for p in prenominas_rep:
                data.append({
                    'Semana Inicio': rep.fecha_inicio_semana.strftime('%Y-%m-%d'),
                    'Semana Fin': rep.fecha_fin_semana.strftime('%Y-%m-%d'),
                    'No. Empleado': p.trabajador.no_empleado if p.trabajador else '',
                    'Nombre del Empleado': f"{p.trabajador.nombre} {p.trabajador.nombre_apellidos}" if p.trabajador else '',
                    'Salario Base': float(p.salario_base or 0),
                    'Pago Horas Extras': float(p.pago_horas_extras or 0),
                    'Pago Viáticos': float(p.pago_viaticos or 0),
                    'Pago Festivos': float(p.pago_festivos or 0),
                    'Otros Depósitos': float(p.depositos_otros or 0),
                    'Depósitos Préstamos': float(p.depositos_prestamos or 0),
                    'Total Percepciones': float(p.total_percepciones or 0),
                    'Descuento Infonavit': float(p.descuento_infonavit or 0),
                    'Ajuste Inbursa': float(p.ajuste_inbursa or 0),
                    'Otros Descuentos': float(p.descuentos_otros or 0),
                    'Abono Préstamos': float(p.descuento_prestamos or 0),
                    'Descuento Incidencias': float(p.descuento_incidencias or 0),
                    'Total Deducciones': float(p.total_deducciones or 0),
                    'TOTAL A PAGAR': float(p.total_a_pagar or 0)
                })
        
        if not data:
             flash("No hay datos de nómina para este proyecto.", "warning")
             return redirect(request.referrer or url_for('proyecto_total.index'))

        df = pd.DataFrame(data)
        df.to_excel(writer, sheet_name='Proyecto Total', index=False)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M")
        return _aplicar_estilos_y_retornar(writer, output, f"Reporte_ProyectoTotal_{proyecto.numero_proyecto}_{timestamp}.xlsx")

    except Exception as e:
        current_app.logger.error(f"Error generando Excel Proyecto: {e}")
        flash("Error exportando reporte.", "danger")
        return redirect(request.referrer or '/')


@bp.route('/excel_prestamos/<int:trabajador_id>', methods=['GET'])
@login_required
@admin_required
def excel_prestamos(trabajador_id):
    try:
        trabajador = Trabajador.query.get_or_404(trabajador_id)
        output = io.BytesIO()
        writer = pd.ExcelWriter(output, engine='openpyxl')
        
        prestamos = Prestamo.query.filter_by(trabajador_id=trabajador.id).order_by(Prestamo.creado_en.desc()).all()
        if not prestamos:
             flash("Este trabajador no tiene préstamos registrados.", "warning")
             return redirect(request.referrer or url_for('prestamos.index'))

        data = []
        for pr in prestamos:
            total_abonado = sum(float(a.monto or 0) for a in pr.abonos)
            saldo = float(pr.monto_total or 0) - total_abonado
            data.append({
                'ID Préstamo': pr.id,
                'Fecha Registro': pr.creado_en.strftime('%Y-%m-%d'),
                'Monto Original': float(pr.monto_total or 0),
                'Total Abonado': total_abonado,
                'Saldo Restante': saldo,
                'Descuento Semanal': float(pr.descuento_semanal or 0),
                'Estado': pr.estado,
                'Motivo': pr.motivo or ''
            })
        df = pd.DataFrame(data)
        df.to_excel(writer, sheet_name='Préstamos', index=False)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M")
        nombre_file = f"Prestamos_{trabajador.no_empleado}_{timestamp}.xlsx"
        return _aplicar_estilos_y_retornar(writer, output, nombre_file)

    except Exception as e:
        current_app.logger.error(f"Error generando Excel Préstamos: {e}")
        flash("Error exportando reporte.", "danger")
        return redirect(request.referrer or '/')


@bp.route('/excel_ajustes/<int:periodo_id>', methods=['GET'])
@login_required
@admin_required
def excel_ajustes(periodo_id):
    try:
        from app.models import AjustePeriodo, AjusteDescuento
        periodo = AjustePeriodo.query.get_or_404(periodo_id)
        
        output = io.BytesIO()
        writer = pd.ExcelWriter(output, engine='openpyxl')
        
        ajustes = AjusteDescuento.query.filter_by(periodo_id=periodo.id).all()
        if not ajustes:
             flash("No hay descuentos registrados en este periodo.", "warning")
             return redirect(request.referrer or url_for('ajustes.index'))

        data = []
        for aj in ajustes:
            data.append({
                'No. Empleado': aj.trabajador.no_empleado if aj.trabajador else '',
                'Nombre del Empleado': aj.trabajador.nombre_completo if aj.trabajador else '',
                'Fecha Aplicación': aj.fecha_descuento.strftime('%Y-%m-%d'),
                'Monto Descontado': float(aj.monto or 0),
                'Notas': aj.notas or ''
            })
        df = pd.DataFrame(data)
        df.to_excel(writer, sheet_name='Ajuste Inbursa', index=False)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M")
        nombre_file = f"Ajuste_Inbursa_{periodo.id}_{timestamp}.xlsx"
        return _aplicar_estilos_y_retornar(writer, output, nombre_file)

    except Exception as e:
        current_app.logger.error(f"Error generando Excel Ajustes: {e}")
        flash("Error exportando reporte.", "danger")
        return redirect(request.referrer or '/')
