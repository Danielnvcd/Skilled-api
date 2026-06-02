"""API JSON para Prenómina (SPA React).

Espejo del blueprint clásico `prenomina.py` pero protegido por JWT. Reusa:
- `calcular_preview_prenomina` (lógica de cálculo) del módulo clásico
- `recalcular_totales_prenomina` de utils
- Template Jinja `recibo_pdf.html` para los recibos PDF y correos
  (la apariencia del recibo queda idéntica a la vista clásica)
"""
import os
import traceback
from datetime import datetime
from decimal import Decimal
from io import BytesIO

from flask import Blueprint, current_app, g, jsonify, render_template, request, send_file
from flask_mail import Message
from sqlalchemy.orm import joinedload, selectinload
from werkzeug.utils import secure_filename

from app.extensions import db, mail
from app.models import (
    AbonoPrestamo,
    AjusteDescuento,
    DepositoExtra,
    DescuentoPrenomina,
    Prenomina,
    Prestamo,
    RegistroDiarioHoras,
    ReporteSemanal,
    crear_notif_admins,
)
from app.realtime import emit_to_role
from app.routes.api_auth import jwt_required
from app.routes.prenomina import calcular_preview_prenomina
from app.routes.reportes import _aplicar_estilos_y_retornar, _sanitize_rows
from app.utils import log_action, recalcular_totales_prenomina, to_dec

bp = Blueprint('api_prenomina', __name__, url_prefix='/api/prenomina')


# ── Helpers ────────────────────────────────────────────────────────────────────

def _u():
    return g._jwt_user


def _admin_required():
    if _u().role not in ('admin', 'super_admin'):
        return jsonify({'error': 'Acceso denegado'}), 403
    return None


def _parse_fecha(fecha_str: str):
    return datetime.strptime(fecha_str, '%Y-%m-%d').date()


def _reportes_de_semana(fecha_obj):
    return ReporteSemanal.query.filter(
        ReporteSemanal.fecha_inicio_semana == fecha_obj,
        ReporteSemanal.estado.in_(['TERMINADO', 'PRENOMINA_CERRADA']),
    ).all()


def _trabajador_min(t) -> dict:
    if not t:
        return None
    return {
        'id': t.id,
        'no_empleado': t.no_empleado,
        'nombre': t.nombre,
        'nombre_apellidos': t.nombre_apellidos,
        'nombre_completo': t.nombre_completo,
        'correo': t.correo or '',
        'tipo_nomina': t.tipo_nomina or '',
        'tipo_pago': t.tipo_pago or '',
    }


def _num(v) -> float:
    if v is None:
        return 0.0
    return float(to_dec(v))


def _prenomina_dict(p: Prenomina, *, with_detail: bool = False) -> dict:
    data = {
        'id': p.id,
        'trabajador_id': p.trabajador_id,
        'trabajador': _trabajador_min(p.trabajador),
        'fecha_inicio': p.fecha_inicio.isoformat() if p.fecha_inicio else None,
        'fecha_fin': p.fecha_fin.isoformat() if p.fecha_fin else None,
        'estado': p.estado or 'PENDIENTE',
        'tipo_pago': p.tipo_pago or '',
        'total_horas_calculadas': _num(getattr(p, 'total_horas_calculadas', None)),
        # Percepciones
        'salario_base': _num(p.salario_base),
        'pago_horas_extras': _num(p.pago_horas_extras),
        'pago_viaticos': _num(p.pago_viaticos),
        'pago_festivos': _num(p.pago_festivos),
        'depositos_otros': _num(p.depositos_otros),
        'depositos_prestamos': _num(p.depositos_prestamos),
        # Deducciones
        'descuento_infonavit': _num(p.descuento_infonavit),
        'ajuste_inbursa': _num(p.ajuste_inbursa),
        'descuentos_otros': _num(p.descuentos_otros),
        'descuento_prestamos': _num(p.descuento_prestamos),
        'descuento_incidencias': _num(p.descuento_incidencias),
        'recuperacion_manual': _num(p.recuperacion_manual),
        # Totales
        'total_percepciones': _num(p.total_percepciones),
        'total_deducciones': _num(p.total_deducciones),
        'total_a_pagar': _num(p.total_a_pagar),
    }
    if with_detail and p.id is not None:
        data['descuentos_detalle'] = [
            {
                'id': d.id,
                'tipo': d.tipo,
                'concepto': d.concepto,
                'monto': _num(d.monto),
                'fecha_incidencia': d.fecha_incidencia.isoformat() if d.fecha_incidencia else None,
            } for d in (p.descuentos_detalle or [])
        ]
        data['depositos_detalle'] = [
            {
                'id': d.id,
                'concepto': d.concepto,
                'monto': _num(d.monto),
            } for d in (p.depositos_detalle or [])
        ]
    return data


def _build_recibos_data(reportes, prenominas):
    """Arma la estructura `recibos_data` que espera `recibo_pdf.html`."""
    reporte_ids = [r.id for r in reportes]
    todos_registros = RegistroDiarioHoras.query.options(
        joinedload(RegistroDiarioHoras.reporte).joinedload(ReporteSemanal.proyecto)
    ).filter(
        RegistroDiarioHoras.reporte_id.in_(reporte_ids)
    ).order_by(RegistroDiarioHoras.trabajador_id, RegistroDiarioHoras.fecha).all()

    por_trabajador = {}
    for reg in todos_registros:
        por_trabajador.setdefault(reg.trabajador_id, []).append(reg)

    reporte_generico = reportes[0]
    recibos = []
    for p in prenominas:
        regs = por_trabajador.get(p.trabajador_id, [])
        total_hrs = sum(r.horas_productivas or 0 for r in regs)
        proyectos_vistos = {}
        for reg in regs:
            if reg.reporte and reg.reporte.proyecto and not reg.incidencia:
                proy = reg.reporte.proyecto
                proyectos_vistos[proy.id] = proy
        nombre_proyecto = (
            ' | '.join(pr.nombre for pr in proyectos_vistos.values())
            if proyectos_vistos
            else (reporte_generico.proyecto.nombre if reporte_generico.proyecto else 'Sin asignar')
        )
        recibos.append({
            'p': p,
            'registros_trabajador': regs,
            'total_hrs': total_hrs,
            'nombre_proyecto': nombre_proyecto,
        })
    return recibos, reporte_generico


def _render_recibos_pdf(reportes, prenominas) -> BytesIO | None:
    """Genera el PDF con xhtml2pdf. Devuelve None si falla."""
    from xhtml2pdf import pisa

    recibos_data, reporte_generico = _build_recibos_data(reportes, prenominas)
    logo_path = os.path.join(current_app.static_folder, 'imagenes', 'skilled_white_bg.jpg')
    html_salida = render_template(
        'recibo_pdf.html',
        reporte=reporte_generico,
        recibos_data=recibos_data,
        logo_path=logo_path,
    )
    pdf = BytesIO()
    status = pisa.CreatePDF(BytesIO(html_salida.encode('utf-8')), dest=pdf)
    if status.err:
        return None
    pdf.seek(0)
    return pdf


# ── Endpoints: índice y preview ───────────────────────────────────────────────

@bp.route('/semanas', methods=['GET'])
@jwt_required
def listar_semanas():
    denied = _admin_required()
    if denied:
        return denied

    from app.models import Proyecto as _Proyecto  # local import: evita ciclos
    reportes = (
        ReporteSemanal.query
        .options(joinedload(ReporteSemanal.proyecto).joinedload(_Proyecto.coordinador))
        .filter(ReporteSemanal.estado.in_(['TERMINADO', 'PRENOMINA_CERRADA']))
        .order_by(ReporteSemanal.fecha_inicio_semana.desc())
        .all()
    )

    semanas = {}
    for r in reportes:
        key = r.fecha_inicio_semana.isoformat()
        if key not in semanas:
            semanas[key] = {
                'fecha_str': key,
                'fecha_inicio': r.fecha_inicio_semana.isoformat(),
                'fecha_fin': r.fecha_fin_semana.isoformat(),
                'proyectos': [],
                'estado_reportes': 'PRENOMINA_CERRADA',
            }
        if r.proyecto:
            coord = r.proyecto.coordinador
            coord_name = ''
            if coord:
                coord_name = coord.full_name or coord.username or ''
            initials = '?'
            if coord_name:
                parts = coord_name.split()
                initials = (parts[0][:1] + (parts[1][:1] if len(parts) > 1 else '')).upper()
            semanas[key]['proyectos'].append({
                'id': r.proyecto.id,
                'numero_proyecto': r.proyecto.numero_proyecto,
                'nombre': r.proyecto.nombre or '',
                'coordinador_nombre': coord_name,
                'coordinador_initials': initials,
            })
        if r.estado != 'PRENOMINA_CERRADA':
            semanas[key]['estado_reportes'] = 'TERMINADO'

    # Estado de la prenómina (PENDIENTE / ABIERTA / APROBADO) por fecha
    estados_q = db.session.query(Prenomina.fecha_inicio, Prenomina.estado).distinct().all()
    estados_por_fecha = {}
    for fecha, estado in estados_q:
        key = fecha.isoformat()
        # Prioridad: APROBADO > ABIERTA > PENDIENTE
        prev = estados_por_fecha.get(key)
        if prev == 'APROBADO':
            continue
        if estado == 'APROBADO' or prev is None or (prev == 'PENDIENTE' and estado == 'ABIERTA'):
            estados_por_fecha[key] = estado

    items = []
    for fecha_str, s in semanas.items():
        s['estado_prenomina'] = estados_por_fecha.get(fecha_str, 'PENDIENTE')
        items.append(s)
    items.sort(key=lambda x: x['fecha_inicio'], reverse=True)
    return jsonify({'items': items})


@bp.route('/semanas/<fecha_str>/preview', methods=['GET'])
@jwt_required
def preview_semana(fecha_str):
    denied = _admin_required()
    if denied:
        return denied

    try:
        fecha_obj = _parse_fecha(fecha_str)
    except ValueError:
        return jsonify({'error': 'Fecha inválida'}), 400

    reportes = _reportes_de_semana(fecha_obj)
    if not reportes:
        return jsonify({'error': 'No hay reportes cerrados para esta semana'}), 404

    prenominas_db = (
        Prenomina.query
        .options(
            selectinload(Prenomina.trabajador),
            selectinload(Prenomina.descuentos_detalle),
            selectinload(Prenomina.depositos_detalle),
        )
        .filter_by(fecha_inicio=fecha_obj)
        .all()
    )
    ya_guardada = len(prenominas_db) > 0

    if not ya_guardada:
        prenominas = calcular_preview_prenomina(fecha_obj, reportes)
    else:
        # Recalcular total_horas dinámicamente en batch
        reporte_ids = [r.id for r in reportes]
        registros = RegistroDiarioHoras.query.filter(
            RegistroDiarioHoras.reporte_id.in_(reporte_ids)
        ).all()
        por_trabajador = {}
        for reg in registros:
            por_trabajador.setdefault(reg.trabajador_id, []).append(reg)
        for p in prenominas_db:
            regs = por_trabajador.get(p.trabajador_id, [])
            p.total_horas_calculadas = to_dec(sum(r.horas_productivas or 0 for r in regs))
        prenominas = prenominas_db

    proyectos = []
    for r in reportes:
        if r.proyecto:
            coord = r.proyecto.coordinador
            coord_name = (coord.full_name or coord.username) if coord else ''
            proyectos.append({
                'id': r.proyecto.id,
                'numero_proyecto': r.proyecto.numero_proyecto,
                'nombre': r.proyecto.nombre or '',
                'coordinador_nombre': coord_name,
            })

    estado_actual = prenominas_db[0].estado if ya_guardada else 'PENDIENTE'

    return jsonify({
        'fecha_str': fecha_str,
        'fecha_inicio': fecha_obj.isoformat(),
        'fecha_fin': reportes[0].fecha_fin_semana.isoformat(),
        'ya_guardada': ya_guardada,
        'estado_actual': estado_actual,
        'proyectos': proyectos,
        'prenominas': [_prenomina_dict(p, with_detail=ya_guardada) for p in prenominas],
    })


@bp.route('/semanas/<fecha_str>/guardar', methods=['POST'])
@jwt_required
def guardar_semana(fecha_str):
    denied = _admin_required()
    if denied:
        return denied

    try:
        fecha_obj = _parse_fecha(fecha_str)
    except ValueError:
        return jsonify({'error': 'Fecha inválida'}), 400

    reportes = _reportes_de_semana(fecha_obj)
    if not reportes:
        return jsonify({'error': 'No hay reportes cerrados para esta semana'}), 400

    if Prenomina.query.filter_by(fecha_inicio=fecha_obj).first():
        return jsonify({'error': 'La prenómina ya fue guardada para esta semana'}), 409

    try:
        nuevas = calcular_preview_prenomina(fecha_obj, reportes)
        for p in nuevas:
            p.reporte_semanal_id = None
            p.estado = 'ABIERTA'
            db.session.add(p)
        for r in reportes:
            r.estado = 'PRENOMINA_CERRADA'
        db.session.commit()
        log_action(f'API: prenómina global guardada para semana {fecha_str}')
        emit_to_role(['admin', 'super_admin'], 'prenomina:changed', {
            'fecha': fecha_str, 'action': 'guardada',
        })
        return jsonify({'success': True, 'creadas': len(nuevas)})
    except Exception:
        db.session.rollback()
        current_app.logger.error("Error guardando prenómina: %s", traceback.format_exc())
        return jsonify({'error': 'Error al guardar la prenómina'}), 500


# ── Endpoints: editor ────────────────────────────────────────────────────────

@bp.route('/semanas/<fecha_str>/editar', methods=['GET'])
@jwt_required
def detalle_editor(fecha_str):
    denied = _admin_required()
    if denied:
        return denied

    try:
        fecha_obj = _parse_fecha(fecha_str)
    except ValueError:
        return jsonify({'error': 'Fecha inválida'}), 400

    prenominas = (
        Prenomina.query
        .options(
            selectinload(Prenomina.trabajador),
            selectinload(Prenomina.descuentos_detalle),
            selectinload(Prenomina.depositos_detalle),
        )
        .filter_by(fecha_inicio=fecha_obj)
        .all()
    )
    if not prenominas:
        return jsonify({'error': 'No hay prenóminas guardadas para esta semana'}), 404

    estado_actual = prenominas[0].estado
    fecha_fin = prenominas[0].fecha_fin

    # Horas e incidencias por trabajador
    reportes = _reportes_de_semana(fecha_obj)
    reporte_ids = [r.id for r in reportes]
    registros = RegistroDiarioHoras.query.filter(
        RegistroDiarioHoras.reporte_id.in_(reporte_ids)
    ).all() if reporte_ids else []

    horas_por_trab = {}
    incidencias_por_trab = {}
    for reg in registros:
        horas_por_trab.setdefault(reg.trabajador_id, 0.0)
        horas_por_trab[reg.trabajador_id] += float(reg.horas_productivas or 0)
        if reg.incidencia and reg.incidencia.strip():
            arr = incidencias_por_trab.setdefault(reg.trabajador_id, [])
            existe = any(
                i['fecha'] == reg.fecha.isoformat() and i['incidencia'] == reg.incidencia
                for i in arr
            )
            if not existe:
                arr.append({
                    'fecha': reg.fecha.isoformat(),
                    'incidencia': reg.incidencia,
                    'horas': float(reg.horas_productivas or 0),
                })

    for p in prenominas:
        p.total_horas_calculadas = to_dec(horas_por_trab.get(p.trabajador_id, 0.0))

    # Préstamos activos por trabajador
    trab_ids = [p.trabajador_id for p in prenominas]
    prestamos = Prestamo.query.filter(
        Prestamo.trabajador_id.in_(trab_ids),
        Prestamo.estado == 'ACTIVO',
    ).all()
    prestamos_por_trab = {}
    for pr in prestamos:
        prestamos_por_trab.setdefault(pr.trabajador_id, []).append({
            'id': pr.id,
            'monto_total': _num(pr.monto_total),
            'monto_restante': _num(pr.monto_restante),
            'descuento_semanal': _num(pr.descuento_semanal),
            'plazo_semanas': pr.plazo_semanas,
            'motivo': pr.motivo or '',
        })

    return jsonify({
        'fecha_str': fecha_str,
        'fecha_inicio': fecha_obj.isoformat(),
        'fecha_fin': fecha_fin.isoformat() if fecha_fin else None,
        'estado_actual': estado_actual,
        'editable': estado_actual == 'ABIERTA',
        'prenominas': [_prenomina_dict(p, with_detail=True) for p in prenominas],
        'incidencias_por_trabajador': incidencias_por_trab,
        'prestamos_por_trabajador': prestamos_por_trab,
    })


@bp.route('/semanas/<fecha_str>/cerrar', methods=['POST'])
@jwt_required
def cerrar_semana(fecha_str):
    denied = _admin_required()
    if denied:
        return denied

    try:
        fecha_obj = _parse_fecha(fecha_str)
    except ValueError:
        return jsonify({'error': 'Fecha inválida'}), 400

    reporte_terminado = ReporteSemanal.query.filter(
        ReporteSemanal.estado.in_(['TERMINADO', 'PRENOMINA_CERRADA']),
        ReporteSemanal.fecha_inicio_semana == fecha_obj,
    ).first()
    if not reporte_terminado:
        return jsonify({'error': 'No existe reporte de horas TERMINADO para esta fecha'}), 400

    prenominas = Prenomina.query.filter_by(fecha_inicio=fecha_obj, estado='ABIERTA').all()
    if not prenominas:
        return jsonify({'error': 'No hay prenóminas abiertas para cerrar'}), 400

    try:
        for p in prenominas:
            p.estado = 'APROBADO'

            # Marcar Ajustes Inbursa como cobrados (mismo criterio que el blueprint clásico)
            ajustes = AjusteDescuento.query.filter(
                AjusteDescuento.trabajador_id == p.trabajador_id,
                AjusteDescuento.fecha_descuento >= p.fecha_inicio,
                AjusteDescuento.fecha_descuento <= p.fecha_fin,
                AjusteDescuento.cobrado == False,  # noqa: E712
            ).all()
            for aj in ajustes:
                aj.cobrado = True

            # Aplicar descuentos a préstamos activos como abonos reales
            if p.descuento_prestamos and to_dec(p.descuento_prestamos) > Decimal('0'):
                prestamos_act = Prestamo.query.filter_by(
                    trabajador_id=p.trabajador_id, estado='ACTIVO',
                ).all()
                for pr in prestamos_act:
                    descuento = to_dec(pr.descuento_semanal)
                    restante = to_dec(pr.monto_restante)
                    if restante <= 0:
                        pr.monto_restante = 0
                        pr.estado = 'LIQUIDADO'
                        pr.activo = False
                        continue
                    if descuento <= 0:
                        continue
                    abono_real = min(descuento, restante)
                    db.session.add(AbonoPrestamo(
                        prestamo_id=pr.id,
                        monto=abono_real,
                        fecha_abono=datetime.now().date(),
                        tipo='NOMINA',
                        registrado_por_id=_u().id,
                        notas=f'Descuento automático por prenómina del {fecha_str}',
                    ))
                    pr.monto_restante = restante - abono_real
                    if pr.monto_restante <= 0:
                        pr.monto_restante = 0
                        pr.estado = 'LIQUIDADO'
                        pr.activo = False

        db.session.commit()
        log_action(f'API: prenómina cerrada para semana {fecha_str}')
        emit_to_role(['admin', 'super_admin'], 'prenomina:changed', {
            'fecha': fecha_str, 'action': 'cerrada',
        })

        try:
            crear_notif_admins(
                tipo='PRENOMINA_CERRADA',
                titulo=f'Prenómina aprobada — semana {fecha_str}',
                mensaje=f'La nómina de la semana del {fecha_str} fue cerrada y aprobada con {len(prenominas)} registro(s).',
                url='/prenomina/',
            )
            db.session.commit()
        except Exception:
            current_app.logger.warning("No se pudo crear notificación de prenómina cerrada", exc_info=True)

        return jsonify({'success': True, 'aprobadas': len(prenominas)})
    except Exception:
        db.session.rollback()
        current_app.logger.error("Error cerrando prenómina: %s", traceback.format_exc())
        return jsonify({'error': 'Error al cerrar la prenómina'}), 500


# ── Endpoints: descuentos / depósitos / viáticos / festivos ──────────────────

def _validar_payload_monto(data, *campos):
    faltantes = [c for c in campos if data.get(c) in (None, '')]
    if faltantes:
        return None, (f'Faltan campos: {", ".join(faltantes)}', 400)
    try:
        out = []
        for c in campos:
            v = data[c]
            out.append(Decimal(str(v)) if 'monto' in c else int(v) if c.endswith('_id') else v)
        return out, None
    except (TypeError, ValueError):
        return None, ('Valores numéricos inválidos', 400)


# Enums permitidos por el modelo DescuentoPrenomina.
_DESCUENTO_TIPOS = {'INCIDENCIA', 'MANUAL', 'PRESTAMO'}
# Límite duro alineado con la columna BD String(250).
_CONCEPTO_MAX = 250
# Cap defensivo de montos para evitar errores tipográficos catastróficos
# (admin mete $9999999 por accidente) y posibles overflow.
_MONTO_MAX = Decimal('999999.99')


@bp.route('/descuentos', methods=['POST'])
@jwt_required
def agregar_descuento():
    denied = _admin_required()
    if denied:
        return denied

    data = request.get_json(silent=True) or {}
    required = ['prenomina_id', 'tipo', 'concepto', 'monto']
    if any(data.get(c) in (None, '') for c in required):
        return jsonify({'error': f'Faltan campos: {", ".join(required)}'}), 400

    tipo = str(data['tipo']).strip().upper()
    if tipo not in _DESCUENTO_TIPOS:
        return jsonify({
            'error': f'tipo inválido. Permitidos: {", ".join(sorted(_DESCUENTO_TIPOS))}'
        }), 400

    concepto = str(data['concepto']).strip()
    if not concepto or len(concepto) > _CONCEPTO_MAX:
        return jsonify({'error': f'concepto vacío o > {_CONCEPTO_MAX} caracteres'}), 400

    try:
        prenomina_id = int(data['prenomina_id'])
        monto = Decimal(str(data['monto']))
    except (TypeError, ValueError):
        return jsonify({'error': 'prenomina_id y monto deben ser numéricos'}), 400

    if monto <= 0 or monto > _MONTO_MAX:
        return jsonify({'error': f'monto fuera de rango (0 < monto ≤ {_MONTO_MAX})'}), 400

    prenomina = Prenomina.query.get_or_404(prenomina_id)
    if prenomina.estado != 'ABIERTA':
        return jsonify({'error': 'Solo se pueden editar prenóminas ABIERTAS'}), 400

    try:
        fecha_inc_str = data.get('fecha_incidencia')
        fecha_inc = _parse_fecha(fecha_inc_str) if fecha_inc_str else None
        # Sanity: la fecha de incidencia no debería ser futura (datos limpios)
        from datetime import date as _date
        if fecha_inc and fecha_inc > _date.today():
            return jsonify({'error': 'fecha_incidencia no puede ser futura'}), 400
        desc = DescuentoPrenomina(
            prenomina_id=prenomina_id,
            trabajador_id=prenomina.trabajador_id,
            tipo=tipo,
            concepto=concepto,
            monto=monto,
            fecha_incidencia=fecha_inc,
        )
        db.session.add(desc)
        db.session.flush()
        db.session.expire(prenomina, ['descuentos_detalle'])
        recalcular_totales_prenomina(prenomina)
        db.session.commit()
        log_action(f"API descuento_agregado: prenomina_id={prenomina_id} monto={monto}")
        return jsonify({
            'id': desc.id,
            'total_deducciones': _num(prenomina.total_deducciones),
            'total_a_pagar': _num(prenomina.total_a_pagar),
        }), 201
    except Exception:
        db.session.rollback()
        current_app.logger.error("Error agregando descuento: %s", traceback.format_exc())
        return jsonify({'error': 'Error al agregar el descuento'}), 500


@bp.route('/descuentos/<int:id>', methods=['DELETE'])
@jwt_required
def eliminar_descuento(id):
    denied = _admin_required()
    if denied:
        return denied

    desc = DescuentoPrenomina.query.get_or_404(id)
    prenomina = desc.prenomina
    if prenomina.estado != 'ABIERTA':
        return jsonify({'error': 'Solo se pueden editar prenóminas ABIERTAS'}), 400

    try:
        db.session.delete(desc)
        db.session.flush()
        db.session.expire(prenomina, ['descuentos_detalle'])
        recalcular_totales_prenomina(prenomina)
        db.session.commit()
        log_action(f"API descuento_eliminado: descuento_id={id} prenomina_id={prenomina.id}")
        return jsonify({
            'total_deducciones': _num(prenomina.total_deducciones),
            'total_a_pagar': _num(prenomina.total_a_pagar),
        })
    except Exception:
        db.session.rollback()
        current_app.logger.error("Error eliminando descuento: %s", traceback.format_exc())
        return jsonify({'error': 'Error al eliminar el descuento'}), 500


@bp.route('/depositos', methods=['POST'])
@jwt_required
def agregar_deposito():
    denied = _admin_required()
    if denied:
        return denied

    data = request.get_json(silent=True) or {}
    required = ['prenomina_id', 'concepto', 'monto']
    if any(data.get(c) in (None, '') for c in required):
        return jsonify({'error': f'Faltan campos: {", ".join(required)}'}), 400

    concepto = str(data['concepto']).strip()
    if not concepto or len(concepto) > _CONCEPTO_MAX:
        return jsonify({'error': f'concepto vacío o > {_CONCEPTO_MAX} caracteres'}), 400

    try:
        prenomina_id = int(data['prenomina_id'])
        monto = Decimal(str(data['monto']))
    except (TypeError, ValueError):
        return jsonify({'error': 'prenomina_id y monto deben ser numéricos'}), 400

    if monto <= 0 or monto > _MONTO_MAX:
        return jsonify({'error': f'monto fuera de rango (0 < monto ≤ {_MONTO_MAX})'}), 400

    prenomina = Prenomina.query.get_or_404(prenomina_id)
    if prenomina.estado != 'ABIERTA':
        return jsonify({'error': 'Solo se pueden editar prenóminas ABIERTAS'}), 400

    try:
        dep = DepositoExtra(
            prenomina_id=prenomina_id,
            trabajador_id=prenomina.trabajador_id,
            monto=monto,
            concepto=concepto,
        )
        db.session.add(dep)
        db.session.flush()
        db.session.expire(prenomina, ['depositos_detalle'])
        recalcular_totales_prenomina(prenomina)
        db.session.commit()
        log_action(f"API deposito_agregado: prenomina_id={prenomina_id} monto={monto}")
        return jsonify({
            'id': dep.id,
            'total_percepciones': _num(prenomina.total_percepciones),
            'total_a_pagar': _num(prenomina.total_a_pagar),
        }), 201
    except Exception:
        db.session.rollback()
        current_app.logger.error("Error agregando depósito: %s", traceback.format_exc())
        return jsonify({'error': 'Error al agregar el depósito'}), 500


@bp.route('/depositos/<int:id>', methods=['DELETE'])
@jwt_required
def eliminar_deposito(id):
    denied = _admin_required()
    if denied:
        return denied

    dep = DepositoExtra.query.get_or_404(id)
    prenomina = dep.prenomina
    if prenomina.estado != 'ABIERTA':
        return jsonify({'error': 'Solo se pueden editar prenóminas ABIERTAS'}), 400

    try:
        db.session.delete(dep)
        db.session.flush()
        db.session.expire(prenomina, ['depositos_detalle'])
        recalcular_totales_prenomina(prenomina)
        db.session.commit()
        log_action(f"API deposito_eliminado: deposito_id={id} prenomina_id={prenomina.id}")
        return jsonify({
            'total_percepciones': _num(prenomina.total_percepciones),
            'total_a_pagar': _num(prenomina.total_a_pagar),
        })
    except Exception:
        db.session.rollback()
        current_app.logger.error("Error eliminando depósito: %s", traceback.format_exc())
        return jsonify({'error': 'Error al eliminar el depósito'}), 500


def _patch_monto(field_name: str, payload_key: str):
    """Helper para los endpoints viáticos / festivos: validan + asignan + recalculan."""
    denied = _admin_required()
    if denied:
        return denied

    data = request.get_json(silent=True) or {}
    prenomina_id = data.get('prenomina_id')
    monto = data.get(payload_key)
    if prenomina_id is None or monto is None:
        return jsonify({'error': f'Faltan campos: prenomina_id, {payload_key}'}), 400

    try:
        prenomina_id = int(prenomina_id)
        monto = Decimal(str(monto))
    except (TypeError, ValueError):
        return jsonify({'error': 'Valores numéricos inválidos'}), 400

    if monto < 0:
        return jsonify({'error': 'El monto no puede ser negativo'}), 400

    prenomina = Prenomina.query.get_or_404(prenomina_id)
    if prenomina.estado != 'ABIERTA':
        return jsonify({'error': 'Solo se pueden editar prenóminas ABIERTAS'}), 400

    try:
        setattr(prenomina, field_name, monto)
        recalcular_totales_prenomina(prenomina)
        db.session.commit()
        return jsonify({
            field_name: _num(getattr(prenomina, field_name)),
            'total_percepciones': _num(prenomina.total_percepciones),
            'total_a_pagar': _num(prenomina.total_a_pagar),
        })
    except Exception:
        db.session.rollback()
        current_app.logger.error("Error patch %s: %s", field_name, traceback.format_exc())
        return jsonify({'error': 'Error al actualizar'}), 500


@bp.route('/viaticos', methods=['PATCH'])
@jwt_required
def patch_viaticos():
    return _patch_monto('pago_viaticos', 'monto_viaticos')


@bp.route('/festivos', methods=['PATCH'])
@jwt_required
def patch_festivos():
    return _patch_monto('pago_festivos', 'monto_festivos')


# ── PDFs ────────────────────────────────────────────────────────────────────

@bp.route('/semanas/<fecha_str>/imprimir', methods=['GET'])
@jwt_required
def imprimir_consolidado(fecha_str):
    denied = _admin_required()
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
    denied = _admin_required()
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
    denied = _admin_required()
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
    denied = _admin_required()
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
    denied = _admin_required()
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
    forbid = _admin_required()
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
