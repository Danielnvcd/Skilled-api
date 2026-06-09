"""Vista por semana: índice, preview, guardar, editor, cerrar.

Registra:
  /semanas                              GET
  /semanas/<fecha_str>/preview          GET
  /semanas/<fecha_str>/guardar          POST
  /semanas/<fecha_str>/editar           GET
  /semanas/<fecha_str>/cerrar           POST
"""
import traceback
from datetime import datetime
from decimal import Decimal

from flask import current_app, jsonify
from sqlalchemy.orm import joinedload, selectinload

from app.extensions import db
from app.models import (
    AbonoPrestamo, AjusteDescuento, Prenomina, Prestamo,
    RegistroDiarioHoras, ReporteSemanal, crear_notif_admins,
)
from app.realtime import emit_to_role
from app.routes._api_helpers import current_user, require_admin
from app.routes.api_auth import jwt_required
from app.utils import log_action, to_dec

from ._core import (
    bp,
    _parse_fecha, _reportes_de_semana, _prenomina_dict, _num,
    calcular_preview_prenomina,
)


@bp.route('/semanas', methods=['GET'])
@jwt_required
def listar_semanas():
    denied = require_admin()
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
    denied = require_admin()
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


@bp.route('/semanas/<fecha_str>/editar', methods=['GET'])
@jwt_required
def detalle_editor(fecha_str):
    denied = require_admin()
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
    denied = require_admin()
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
                        registrado_por_id=current_user().id,
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
