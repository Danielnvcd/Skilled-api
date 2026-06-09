"""PDFs, envío por correo y exportación Excel.

Registra:
  /semanas/<fecha_str>/imprimir                                     GET
  /semanas/<fecha_str>/trabajadores/<int:trabajador_id>/imprimir    GET
  /semanas/<fecha_str>/trabajadores/<int:trabajador_id>/correo      POST
  /semanas/<fecha_str>/correo/bulk                                  POST
  /semanas/<fecha_str>/correo                                       POST
  /semanas/<fecha_str>/excel                                        GET
"""
import traceback
from datetime import datetime
from io import BytesIO

from flask import current_app, jsonify, request, send_file
from flask_mail import Message
from sqlalchemy.orm import selectinload
from werkzeug.utils import secure_filename

from app.extensions import mail
from app.models import Prenomina, ReporteSemanal
from app.realtime import emit_to_role
from app.routes._api_helpers import require_admin
from app.routes._api_helpers import _aplicar_estilos_y_retornar, _sanitize_rows
from app.routes.api_auth import jwt_required
from app.utils import log_action

from ._core import (
    bp,
    _parse_fecha, _reportes_de_semana, _render_recibos_pdf,
    calcular_preview_prenomina,
)


@bp.route('/semanas/<fecha_str>/imprimir', methods=['GET'])
@jwt_required
def imprimir_consolidado(fecha_str):
    denied = require_admin()
    if denied:
        return denied

    try:
        fecha_obj = _parse_fecha(fecha_str)
    except ValueError:
        return jsonify({'error': 'Fecha inválida'}), 400

    reportes = _reportes_de_semana(fecha_obj)
    if not reportes:
        return jsonify({'error': 'No hay reportes cerrados para esta semana'}), 404

    prenominas = Prenomina.query.options(
        selectinload(Prenomina.trabajador),
        selectinload(Prenomina.descuentos_detalle),
        selectinload(Prenomina.depositos_detalle),
    ).filter_by(fecha_inicio=fecha_obj).all()
    if not prenominas:
        prenominas = calcular_preview_prenomina(fecha_obj, reportes)

    pdf = _render_recibos_pdf(reportes, prenominas)
    if not pdf:
        return jsonify({'error': 'Error al generar el PDF'}), 500

    filename = secure_filename(f'Prenomina_Consolidada_{fecha_obj.strftime("%d%m")}.pdf')
    return send_file(pdf, mimetype='application/pdf', as_attachment=False, download_name=filename)


@bp.route('/semanas/<fecha_str>/trabajadores/<int:trabajador_id>/imprimir', methods=['GET'])
@jwt_required
def imprimir_individual(fecha_str, trabajador_id):
    denied = require_admin()
    if denied:
        return denied

    try:
        fecha_obj = _parse_fecha(fecha_str)
    except ValueError:
        return jsonify({'error': 'Fecha inválida'}), 400

    reportes = _reportes_de_semana(fecha_obj)
    if not reportes:
        return jsonify({'error': 'No hay reportes cerrados para esta semana'}), 404

    prenomina = Prenomina.query.options(
        selectinload(Prenomina.trabajador),
        selectinload(Prenomina.descuentos_detalle),
        selectinload(Prenomina.depositos_detalle),
    ).filter_by(fecha_inicio=fecha_obj, trabajador_id=trabajador_id).first()
    if not prenomina:
        todas = calcular_preview_prenomina(fecha_obj, reportes)
        prenomina = next((p for p in todas if p.trabajador_id == trabajador_id), None)
    if not prenomina:
        return jsonify({'error': 'Trabajador no encontrado en esta nómina'}), 404

    pdf = _render_recibos_pdf(reportes, [prenomina])
    if not pdf:
        return jsonify({'error': 'Error al generar el PDF'}), 500

    nombre_limpio = (prenomina.trabajador.nombre_apellidos or 'Recibo').replace(' ', '_')
    timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    filename = secure_filename(f'Recibo_{nombre_limpio}_{timestamp}.pdf')
    return send_file(pdf, mimetype='application/pdf', as_attachment=False, download_name=filename)


# ── Correo ──────────────────────────────────────────────────────────────────

def _enviar_recibo_por_correo(reportes, prenomina, destinatario, fecha_obj) -> tuple[bool, str]:
    pdf = _render_recibos_pdf(reportes, [prenomina])
    if not pdf:
        return False, 'Error al generar el PDF'

    nombre_completo = prenomina.trabajador.nombre_completo
    nombre_limpio = (prenomina.trabajador.nombre_apellidos or 'Recibo').replace(' ', '_')
    nombre_archivo = f'Recibo_{nombre_limpio}_{fecha_obj.strftime("%d%m%Y")}.pdf'

    msg = Message(
        subject=f'Recibo de Nómina — Semana {fecha_obj.strftime("%d/%m/%Y")}',
        recipients=[destinatario],
        body=(
            f'Hola {nombre_completo},\n\n'
            f'Adjunto encontrarás tu recibo de nómina correspondiente a la semana del '
            f'{fecha_obj.strftime("%d/%m/%Y")}.\n\n'
            f'Cualquier duda comunícate con el área de administración.\n\nSaludos.'
        ),
    )
    msg.attach(nombre_archivo, 'application/pdf', pdf.getvalue())
    mail.send(msg)
    return True, 'ok'


@bp.route('/semanas/<fecha_str>/trabajadores/<int:trabajador_id>/correo', methods=['POST'])
@jwt_required
def enviar_correo(fecha_str, trabajador_id):
    denied = require_admin()
    if denied:
        return denied

    try:
        fecha_obj = _parse_fecha(fecha_str)
    except ValueError:
        return jsonify({'error': 'Fecha inválida'}), 400

    reportes = _reportes_de_semana(fecha_obj)
    if not reportes:
        return jsonify({'error': 'No hay reportes cerrados para esta semana'}), 404

    prenomina = Prenomina.query.options(
        selectinload(Prenomina.trabajador),
        selectinload(Prenomina.descuentos_detalle),
        selectinload(Prenomina.depositos_detalle),
    ).filter_by(fecha_inicio=fecha_obj, trabajador_id=trabajador_id).first()
    if not prenomina:
        todas = calcular_preview_prenomina(fecha_obj, reportes)
        prenomina = next((p for p in todas if p.trabajador_id == trabajador_id), None)
    if not prenomina:
        return jsonify({'error': 'Trabajador no encontrado en esta nómina'}), 404

    destinatario = prenomina.trabajador.correo
    if not destinatario:
        return jsonify({'error': f'{prenomina.trabajador.nombre_completo} no tiene correo registrado'}), 400

    try:
        ok, msg = _enviar_recibo_por_correo(reportes, prenomina, destinatario, fecha_obj)
        if not ok:
            return jsonify({'error': msg}), 500
        log_action(f'API enviar_correo: recibo a {destinatario} para semana {fecha_str}')
        return jsonify({'success': True, 'destinatario': destinatario})
    except Exception:
        current_app.logger.error("Error enviando correo: %s", traceback.format_exc())
        return jsonify({'error': 'Error al enviar el correo'}), 500


@bp.route('/semanas/<fecha_str>/correo/bulk', methods=['POST'])
@jwt_required
def enviar_correo_bulk(fecha_str):
    """Envía recibo por correo a un subconjunto de trabajadores de la semana.

    Body: { "trabajador_ids": [int, ...] }  (1..100 ids)
    Devuelve la misma forma que `enviar_correo_todos` para que el modal de
    resultados del SPA se reuse sin cambios.
    """
    denied = require_admin()
    if denied:
        return denied

    try:
        fecha_obj = _parse_fecha(fecha_str)
    except ValueError:
        return jsonify({'error': 'Fecha inválida'}), 400

    payload = request.get_json(silent=True) or {}
    raw_ids = payload.get('trabajador_ids') or []
    if not isinstance(raw_ids, list) or not raw_ids:
        return jsonify({'error': 'Lista de trabajadores vacía'}), 422
    if len(raw_ids) > 100:
        return jsonify({'error': 'Máximo 100 trabajadores por operación'}), 422
    try:
        ids = sorted({int(i) for i in raw_ids})
    except (TypeError, ValueError):
        return jsonify({'error': 'IDs deben ser enteros'}), 422

    reportes = _reportes_de_semana(fecha_obj)
    if not reportes:
        return jsonify({'error': 'No hay reportes cerrados para esta semana'}), 404

    prenominas_q = Prenomina.query.options(
        selectinload(Prenomina.trabajador),
        selectinload(Prenomina.descuentos_detalle),
        selectinload(Prenomina.depositos_detalle),
    ).filter_by(fecha_inicio=fecha_obj).filter(Prenomina.trabajador_id.in_(ids)).all()

    # Si la prenómina no está guardada aún, el subset puede no estar en DB:
    # caemos al preview en memoria y filtramos por ids ahí.
    if not prenominas_q:
        todas = calcular_preview_prenomina(fecha_obj, reportes)
        prenominas_q = [p for p in todas if p.trabajador_id in set(ids)]
    if not prenominas_q:
        return jsonify({'error': 'Ningún trabajador encontrado en esta nómina'}), 404

    enviados = sin_correo = errores = 0
    resultados = []
    ids_enviados = []
    for p in prenominas_q:
        nombre = p.trabajador.nombre_completo
        correo = p.trabajador.correo
        if not correo:
            sin_correo += 1
            resultados.append({'nombre': nombre, 'correo': '—', 'estado': 'sin_correo'})
            continue
        try:
            ok, msg = _enviar_recibo_por_correo(reportes, p, correo, fecha_obj)
            if not ok:
                raise RuntimeError(msg)
            enviados += 1
            ids_enviados.append(p.trabajador_id)
            resultados.append({'nombre': nombre, 'correo': correo, 'estado': 'enviado'})
        except Exception as e:
            errores += 1
            resultados.append({'nombre': nombre, 'correo': correo, 'estado': 'error', 'detalle': str(e)[:120]})

    log_action(
        f'API enviar_correo_bulk: semana {fecha_str} — '
        f'pedidos={len(ids)}, enviados={enviados}, sin_correo={sin_correo}, errores={errores}'
    )

    # Push a otros admins para que sepan que se envió un lote. No cambia
    # estado de DB visible en la lista de semanas, pero permite que un futuro
    # badge de "ya se enviaron recibos" reaccione sin polling.
    if enviados:
        emit_to_role(['admin', 'super_admin'], 'prenomina:changed', {
            'fecha': fecha_str,
            'action': 'correos_enviados',
            'enviados': enviados,
            'trabajador_ids': ids_enviados,
        })

    return jsonify({
        'success': True,
        'enviados': enviados,
        'sin_correo': sin_correo,
        'errores': errores,
        'resultados': resultados,
    })


@bp.route('/semanas/<fecha_str>/correo', methods=['POST'])
@jwt_required
def enviar_correo_todos(fecha_str):
    denied = require_admin()
    if denied:
        return denied

    try:
        fecha_obj = _parse_fecha(fecha_str)
    except ValueError:
        return jsonify({'error': 'Fecha inválida'}), 400

    reportes = _reportes_de_semana(fecha_obj)
    if not reportes:
        return jsonify({'error': 'No hay reportes cerrados para esta semana'}), 404

    prenominas = Prenomina.query.options(
        selectinload(Prenomina.trabajador),
        selectinload(Prenomina.descuentos_detalle),
        selectinload(Prenomina.depositos_detalle),
    ).filter_by(fecha_inicio=fecha_obj).all()
    if not prenominas:
        prenominas = calcular_preview_prenomina(fecha_obj, reportes)

    enviados = sin_correo = errores = 0
    resultados = []
    for p in prenominas:
        nombre = p.trabajador.nombre_completo
        correo = p.trabajador.correo
        if not correo:
            sin_correo += 1
            resultados.append({'nombre': nombre, 'correo': '—', 'estado': 'sin_correo'})
            continue
        try:
            ok, msg = _enviar_recibo_por_correo(reportes, p, correo, fecha_obj)
            if not ok:
                raise RuntimeError(msg)
            enviados += 1
            resultados.append({'nombre': nombre, 'correo': correo, 'estado': 'enviado'})
        except Exception as e:
            errores += 1
            resultados.append({'nombre': nombre, 'correo': correo, 'estado': 'error', 'detalle': str(e)[:120]})

    log_action(f'API enviar_correo_todos: semana {fecha_str} — enviados={enviados}, sin_correo={sin_correo}, errores={errores}')
    return jsonify({
        'success': True,
        'enviados': enviados,
        'sin_correo': sin_correo,
        'errores': errores,
        'resultados': resultados,
    })


# ── Excel ─────────────────────────────────────────────────────────────────────

@bp.route('/semanas/<fecha_str>/excel', methods=['GET'])
@jwt_required
def excel_prenomina(fecha_str):
    """Exporta la prenómina de una semana como Excel con el mismo formato del
    blueprint clásico (header azul, zebra, fila TOTAL, formato de moneda)."""
    forbid = require_admin()
    if forbid:
        return forbid

    try:
        fecha_obj = datetime.strptime(fecha_str, '%Y-%m-%d').date()
    except ValueError:
        return jsonify({'error': 'Formato de fecha inválido (YYYY-MM-DD)'}), 400

    import pandas as pd

    output = BytesIO()
    writer = pd.ExcelWriter(output, engine='openpyxl')

    prenominas = (
        Prenomina.query.options(selectinload(Prenomina.trabajador))
        .filter_by(fecha_inicio=fecha_obj)
        .all()
    )
    if not prenominas:
        # Si la semana no se ha guardado aún, calculamos preview en vivo.
        reportes = ReporteSemanal.query.filter(
            ReporteSemanal.fecha_inicio_semana == fecha_obj,
            ReporteSemanal.estado.in_(['TERMINADO', 'PRENOMINA_CERRADA']),
        ).all()
        prenominas = calcular_preview_prenomina(fecha_obj, reportes)

    if not prenominas:
        return jsonify({'error': 'No hay datos calculables para esa semana'}), 404

    data = []
    for p in prenominas:
        data.append({
            'Semana Inicio': p.fecha_inicio.strftime('%Y-%m-%d') if p.fecha_inicio else '',
            'Semana Fin': p.fecha_fin.strftime('%Y-%m-%d') if p.fecha_fin else '',
            'No. Empleado': p.trabajador.no_empleado if p.trabajador else '',
            'Nombre del Empleado': (
                f"{p.trabajador.nombre} {p.trabajador.nombre_apellidos}" if p.trabajador else ''
            ),
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
            'Método de Pago': p.tipo_pago or '',
        })

    if data:
        total_row = {k: '' for k in data[0].keys()}
        total_row['Semana Inicio'] = 'TOTAL'
        for k in (
            'Salario Base', 'Pago Horas Extras', 'Pago Viáticos', 'Pago Festivos',
            'Otros Depósitos', 'Depósitos Préstamos', 'Total Percepciones',
            'Descuento Infonavit', 'Ajuste Inbursa', 'Otros Descuentos',
            'Abono Préstamos', 'Descuento Incidencias', 'Total Deducciones',
            'TOTAL A PAGAR',
        ):
            total_row[k] = sum(d[k] for d in data)
        data.append(total_row)

    df = pd.DataFrame(_sanitize_rows(data))
    df.to_excel(writer, sheet_name='Prenómina', index=False)

    timestamp = datetime.now().strftime('%Y%m%d_%H%M')
    return _aplicar_estilos_y_retornar(
        writer, output, f'Reporte_Prenomina_{fecha_str}_{timestamp}.xlsx',
    )
