"""Endpoints móviles para coordinador: resumen y QR check-in.

Registra:
  /movil/resumen      GET
  /qr-check           POST
"""
import traceback
from datetime import date, datetime

from flask import current_app, jsonify, request
from sqlalchemy.orm import joinedload

from app.extensions import db, limiter
from app.models import Proyecto, RegistroDiarioHoras, ReporteSemanal, Trabajador
from app.routes._api_helpers import current_user, is_admin
from app.routes.api_auth import jwt_required
from app.utils import calcular_horas_productivas

from ._core import (
    bp,
    _is_coordinador, _puede_acceder_proyecto,
)


@bp.route('/movil/resumen', methods=['GET'])
@jwt_required
def movil_resumen():
    """Datos para la pantalla móvil del coordinador: proyectos + reportes BORRADOR."""
    if not (is_admin() or _is_coordinador()):
        return jsonify({'error': 'Acceso denegado'}), 403

    uid = current_user().id
    if _is_coordinador():
        proyectos = Proyecto.query.filter_by(activo=True, coordinador_id=uid).all()
    else:
        proyectos = Proyecto.query.filter_by(activo=True).all()

    proyecto_ids = [p.id for p in proyectos]
    hoy = date.today()

    if proyecto_ids:
        reportes = (
            ReporteSemanal.query
            .options(joinedload(ReporteSemanal.proyecto))
            .filter(
                ReporteSemanal.estado == 'BORRADOR',
                ReporteSemanal.proyecto_id.in_(proyecto_ids),
            )
            .order_by(ReporteSemanal.fecha_fin_semana.desc())
            .all()
        )
    else:
        reportes = []

    return jsonify({
        'hoy': hoy.isoformat(),
        'proyectos': [
            {'id': p.id, 'numero_proyecto': p.numero_proyecto, 'nombre': p.nombre or ''}
            for p in proyectos
        ],
        'reportes': [
            {
                'id': r.id,
                'numero': r.proyecto.numero_proyecto if r.proyecto else '',
                'nombre': r.proyecto.nombre if r.proyecto else '',
                'inicio': r.fecha_inicio_semana.isoformat(),
                'fin': r.fecha_fin_semana.isoformat(),
                'es_actual': r.fecha_inicio_semana <= hoy <= r.fecha_fin_semana,
            }
            for r in reportes
        ],
    })


@bp.route('/qr-check', methods=['POST'])
@jwt_required
@limiter.limit('30 per minute')
def qr_check():
    """Registra entrada/salida de un trabajador mediante su QR.

    Equivalente JWT del endpoint clásico horas.qr_check. Payload:
      { "qr_code": "<uuid>", "reporte_id": <int opcional> }
    """
    if not (is_admin() or _is_coordinador()):
        return jsonify({'ok': False, 'error': 'Acceso denegado'}), 403

    payload = request.get_json(silent=True) or {}
    qr_value = (payload.get('qr_code') or '').strip()
    if not qr_value:
        return jsonify({'ok': False, 'error': 'Datos inválidos'}), 400
    reporte_id = payload.get('reporte_id')

    trabajador = Trabajador.query.filter_by(qr_code=qr_value, activo=True).first()
    if not trabajador:
        return jsonify({'ok': False, 'error': 'QR no reconocido'}), 404

    hoy = date.today()

    if reporte_id:
        reporte = ReporteSemanal.query.get(int(reporte_id))
        if not reporte or reporte.estado != 'BORRADOR':
            return jsonify({'ok': False, 'error': 'El reporte ya fue cerrado o no existe'}), 404
        if not _puede_acceder_proyecto(reporte.proyecto):
            return jsonify({'ok': False, 'error': 'No eres coordinador de este proyecto.'}), 403
        if trabajador not in reporte.proyecto.participantes:
            return jsonify({
                'ok': False,
                'error': f'{trabajador.nombre_completo} no está asignado a este proyecto',
            }), 404
        if not (reporte.fecha_inicio_semana <= hoy <= reporte.fecha_fin_semana):
            return jsonify({'ok': False, 'error': (
                f'La fecha de hoy ({hoy.strftime("%d/%m/%Y")}) no pertenece al periodo '
                f'{reporte.fecha_inicio_semana.strftime("%d/%m/%Y")} – {reporte.fecha_fin_semana.strftime("%d/%m/%Y")}.'
            )}), 400
    else:
        reportes_activos = ReporteSemanal.query.filter(
            ReporteSemanal.estado == 'BORRADOR',
            ReporteSemanal.fecha_inicio_semana <= hoy,
            ReporteSemanal.fecha_fin_semana >= hoy,
        ).all()
        reporte = next(
            (r for r in reportes_activos
             if trabajador in r.proyecto.participantes and _puede_acceder_proyecto(r.proyecto)),
            None,
        )
        if reporte is None:
            return jsonify({
                'ok': False,
                'error': 'No hay reporte activo para este trabajador hoy',
            }), 404

    # Lock contra race conditions de doble-scan
    ReporteSemanal.query.filter_by(id=reporte.id).with_for_update().first()

    registro = RegistroDiarioHoras.query.filter_by(
        reporte_id=reporte.id,
        trabajador_id=trabajador.id,
        fecha=hoy,
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
                tipo_nomina=trabajador.tipo_nomina or 'Semanal',
            )
            db.session.add(registro)
            db.session.commit()
            return jsonify({
                'ok': True,
                'tipo': 'ENTRADA',
                'hora': now_time.strftime('%H:%M'),
                'nombre': trabajador.nombre_completo,
            })

        if registro.hora_entrada and not registro.hora_salida:
            registro.hora_salida = now_time
            registro.horas_productivas = calcular_horas_productivas(
                registro.hora_entrada, now_time,
                tipo_nomina=registro.tipo_nomina or 'Semanal',
                tomo_comida=bool(registro.tomo_comida),
            )
            db.session.commit()
            return jsonify({
                'ok': True,
                'tipo': 'SALIDA',
                'hora': now_time.strftime('%H:%M'),
                'nombre': trabajador.nombre_completo,
            })

        return jsonify({
            'ok': False,
            'error': 'Ya tiene entrada y salida registradas hoy',
        }), 409

    except Exception:
        db.session.rollback()
        current_app.logger.error("Error en qr_check: %s", traceback.format_exc())
        return jsonify({'ok': False, 'error': 'Error interno'}), 500
