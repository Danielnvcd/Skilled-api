"""API JSON para el módulo de Horas (consumida por el SPA React).

Espejo del blueprint clásico `horas.py` pero protegido por JWT. Reusa
`calcular_horas_productivas` y `turnos_se_traslapan` de `app.utils` para no
duplicar la lógica de cálculo / detección de solapamiento de turnos.

Alcance v1: listado de reportes semanales, abrir/cerrar reporte, captura con
CRUD de registros diarios. No incluye vista móvil ni el flujo QR (eso queda
en el blueprint clásico).
"""
import traceback
from datetime import date, datetime, timedelta

from flask import Blueprint, current_app, g, jsonify, request, send_file
from sqlalchemy.orm import joinedload, selectinload

from app.extensions import db, limiter
from app.models import Proyecto, RegistroDiarioHoras, ReporteSemanal, Trabajador
from app.routes.api_auth import jwt_required
from app.utils import calcular_horas_productivas, log_action, turnos_se_traslapan

bp = Blueprint('api_horas', __name__, url_prefix='/api/horas')

DIAS_SEMANA = ['Lun', 'Mar', 'Mié', 'Jue', 'Vie', 'Sáb', 'Dom']

INCIDENCIAS = [
    'Casamiento', 'Descanso', 'Falta', 'Incapacidad', 'Luto', 'Paternidad',
    'Permiso', 'Time x Time', 'Vacaciones', 'Viaje de Ida', 'Viaje de vuelta a Pue.',
    'Retardo', 'Levantamiento en campo', 'Falta checada de entrada', 'Falta checada de salida',
]


# ── Helpers ────────────────────────────────────────────────────────────────────

def _u():
    return g._jwt_user


def _role() -> str:
    return _u().role


def _is_admin() -> bool:
    return _role() in ('admin', 'super_admin')


def _is_coordinador() -> bool:
    return _role() == 'coordinador'


def _puede_acceder_proyecto(proyecto: Proyecto) -> bool:
    """Coordinadores solo a sus proyectos; admin/super_admin a todos."""
    if _is_admin():
        return True
    if _is_coordinador():
        return proyecto.coordinador_id == _u().id
    return False


def _hora_a_str(t) -> str:
    return t.strftime('%H:%M') if t else ''


def _parse_time(s: str):
    if not s:
        return None
    return datetime.strptime(s, '%H:%M').time()


def _reporte_row(r: ReporteSemanal) -> dict:
    """Resumen para la tabla principal."""
    p = r.proyecto
    return {
        'id': r.id,
        'estado': r.estado,
        'fecha_inicio': r.fecha_inicio_semana.isoformat(),
        'fecha_fin': r.fecha_fin_semana.isoformat(),
        'proyecto': {
            'id': p.id,
            'numero_proyecto': p.numero_proyecto,
            'nombre': p.nombre or '',
        } if p else None,
        'registros_count': len(r.registros),
        'created_at': r.created_at.isoformat() if r.created_at else None,
    }


def _registro_dict(reg: RegistroDiarioHoras) -> dict:
    return {
        'id': reg.id,
        'reporte_id': reg.reporte_id,
        'trabajador_id': reg.trabajador_id,
        'fecha': reg.fecha.isoformat(),
        'hora_entrada': _hora_a_str(reg.hora_entrada),
        'hora_salida': _hora_a_str(reg.hora_salida),
        'tomo_comida': bool(reg.tomo_comida),
        'aplica_viaticos': bool(reg.aplica_viaticos),
        'monto_viaticos_manual': float(reg.monto_viaticos_manual) if reg.monto_viaticos_manual is not None else None,
        'viaticos_modo': 'manual' if reg.monto_viaticos_manual is not None else 'perfil',
        'aplica_dia_festivo': bool(reg.aplica_dia_festivo),
        'incidencia': reg.incidencia or '',
        'horas_productivas': float(reg.horas_productivas) if reg.horas_productivas is not None else 0.0,
    }


def _trabajador_row(t: Trabajador) -> dict:
    return {
        'id': t.id,
        'no_empleado': t.no_empleado,
        'nombre': t.nombre,
        'tipo_nomina': t.tipo_nomina or 'Semanal',
        'viaticos': float(t.viaticos) if t.viaticos is not None else 0.0,
        'pago_dia_festivo': float(t.pago_dia_festivo) if t.pago_dia_festivo is not None else 0.0,
    }


def _semana_fechas(inicio: date, fin: date) -> list[dict]:
    out = []
    d = inicio
    while d <= fin:
        out.append({
            'fecha': d.isoformat(),
            'label': d.strftime('%d/%m'),
            'dia': DIAS_SEMANA[d.weekday()],
            'fin_semana': d.weekday() >= 5,
        })
        d += timedelta(days=1)
    return out


# ── Endpoints ──────────────────────────────────────────────────────────────────

@bp.route('/reportes', methods=['GET'])
@jwt_required
def listar_reportes():
    page = max(1, request.args.get('page', 1, type=int))
    per_page = min(100, max(1, request.args.get('per_page', 20, type=int)))
    q = (request.args.get('q') or '').strip()
    estado = (request.args.get('estado') or '').strip().upper()

    query = ReporteSemanal.query.options(
        joinedload(ReporteSemanal.proyecto),
        selectinload(ReporteSemanal.registros),
    )

    if _is_coordinador():
        proyecto_ids = [p.id for p in Proyecto.query.filter_by(coordinador_id=_u().id).all()]
        if not proyecto_ids:
            return jsonify({'items': [], 'total': 0, 'page': page, 'pages': 0, 'per_page': per_page})
        query = query.filter(ReporteSemanal.proyecto_id.in_(proyecto_ids))

    if q:
        query = query.join(Proyecto).filter(db.or_(
            Proyecto.nombre.ilike(f'%{q}%'),
            Proyecto.numero_proyecto.ilike(f'%{q}%'),
        ))

    if estado in ('BORRADOR', 'TERMINADO', 'PRENOMINA_CERRADA'):
        query = query.filter(ReporteSemanal.estado == estado)

    pagination = query.order_by(ReporteSemanal.created_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False,
    )

    return jsonify({
        'items': [_reporte_row(r) for r in pagination.items],
        'total': pagination.total,
        'page': pagination.page,
        'pages': pagination.pages,
        'per_page': pagination.per_page,
    })


@bp.route('/proyectos-disponibles', methods=['GET'])
@jwt_required
def proyectos_disponibles():
    """Proyectos activos para abrir un reporte; respeta ownership de coordinador."""
    query = Proyecto.query.filter_by(activo=True)
    if _is_coordinador():
        query = query.filter_by(coordinador_id=_u().id)
    proyectos = query.order_by(Proyecto.numero_proyecto).all()
    return jsonify([
        {
            'id': p.id,
            'numero_proyecto': p.numero_proyecto,
            'nombre': p.nombre or '',
        } for p in proyectos
    ])


@bp.route('/reportes', methods=['POST'])
@jwt_required
def crear_reporte():
    data = request.get_json(silent=True) or {}
    proyecto_id = data.get('proyecto_id')
    fecha_inicio_str = data.get('fecha_inicio')
    fecha_fin_str = data.get('fecha_fin')

    if not proyecto_id or not fecha_inicio_str or not fecha_fin_str:
        return jsonify({'error': 'proyecto_id, fecha_inicio y fecha_fin son obligatorios'}), 400

    proyecto = Proyecto.query.get_or_404(proyecto_id)
    if not _puede_acceder_proyecto(proyecto):
        return jsonify({'error': 'Acceso denegado. No eres coordinador de este proyecto.'}), 403

    try:
        fecha_inicio = datetime.strptime(fecha_inicio_str, '%Y-%m-%d').date()
        fecha_fin = datetime.strptime(fecha_fin_str, '%Y-%m-%d').date()
    except ValueError:
        return jsonify({'error': 'Formato de fecha inválido (YYYY-MM-DD)'}), 400

    if fecha_inicio >= fecha_fin:
        return jsonify({'error': 'La fecha de inicio debe ser anterior a la fecha de fin'}), 400

    # Bloqueo si la prenómina ya cerró esa ventana
    semana_cerrada = ReporteSemanal.query.filter(
        ReporteSemanal.estado == 'PRENOMINA_CERRADA',
        ReporteSemanal.fecha_inicio_semana <= fecha_fin,
        ReporteSemanal.fecha_fin_semana >= fecha_inicio,
    ).first()
    if semana_cerrada:
        return jsonify({'error': f'La prenómina del {fecha_inicio_str} al {fecha_fin_str} ya está CERRADA.'}), 409

    # Bloqueo si el proyecto ya tiene un reporte solapado
    overlap = ReporteSemanal.query.filter(
        ReporteSemanal.proyecto_id == proyecto_id,
        ReporteSemanal.fecha_inicio_semana <= fecha_fin,
        ReporteSemanal.fecha_fin_semana >= fecha_inicio,
    ).first()
    if overlap:
        return jsonify({'error': 'Este proyecto ya tiene un reporte en esa semana.'}), 409

    try:
        nuevo = ReporteSemanal(
            proyecto_id=proyecto_id,
            fecha_inicio_semana=fecha_inicio,
            fecha_fin_semana=fecha_fin,
            estado='BORRADOR',
            creado_por_id=_u().id,
        )
        db.session.add(nuevo)
        db.session.commit()
        log_action(f"API: abrió reporte semanal para proyecto ID {proyecto_id}")
        return jsonify({'id': nuevo.id, 'estado': nuevo.estado}), 201
    except Exception:
        db.session.rollback()
        current_app.logger.error("Error creando reporte: %s", traceback.format_exc())
        return jsonify({'error': 'Error al abrir el reporte'}), 500


@bp.route('/reportes/<int:reporte_id>', methods=['GET'])
@jwt_required
def detalle_reporte(reporte_id):
    r = ReporteSemanal.query.options(
        joinedload(ReporteSemanal.proyecto).selectinload(Proyecto.participantes),
        selectinload(ReporteSemanal.registros),
    ).get_or_404(reporte_id)

    if not _puede_acceder_proyecto(r.proyecto):
        return jsonify({'error': 'Acceso denegado'}), 403

    trabajadores = sorted(r.proyecto.participantes, key=lambda t: (t.nombre or '').lower())

    return jsonify({
        'id': r.id,
        'estado': r.estado,
        'fecha_inicio': r.fecha_inicio_semana.isoformat(),
        'fecha_fin': r.fecha_fin_semana.isoformat(),
        'proyecto': {
            'id': r.proyecto.id,
            'numero_proyecto': r.proyecto.numero_proyecto,
            'nombre': r.proyecto.nombre or '',
        },
        'editable': r.estado == 'BORRADOR' and _puede_acceder_proyecto(r.proyecto),
        'trabajadores': [_trabajador_row(t) for t in trabajadores],
        'semana_fechas': _semana_fechas(r.fecha_inicio_semana, r.fecha_fin_semana),
        'registros': [_registro_dict(reg) for reg in r.registros],
        'incidencias': INCIDENCIAS,
    })


@bp.route('/reportes/<int:reporte_id>/cerrar', methods=['POST'])
@jwt_required
def cerrar_reporte(reporte_id):
    r = ReporteSemanal.query.get_or_404(reporte_id)
    if not _puede_acceder_proyecto(r.proyecto):
        return jsonify({'error': 'Acceso denegado'}), 403
    if r.estado != 'BORRADOR':
        return jsonify({'error': 'El reporte ya está cerrado'}), 409
    if not r.registros:
        return jsonify({'error': 'No se puede cerrar sin registros'}), 400

    try:
        r.estado = 'TERMINADO'
        db.session.commit()
        log_action(f"API: cerró reporte semanal ID {r.id} ({r.proyecto.numero_proyecto})")

        try:
            from app.models import crear_notif_admins
            num = r.proyecto.numero_proyecto if r.proyecto else '—'
            semana = r.fecha_inicio_semana.strftime('%d/%m/%Y')
            crear_notif_admins(
                tipo='REPORTE_CERRADO',
                titulo=f'Reporte de horas cerrado — Proyecto {num}',
                mensaje=f'El reporte semanal del proyecto {num} (semana del {semana}) está listo para prenómina.',
                url='/prenomina/',
            )
            db.session.commit()
        except Exception:
            current_app.logger.warning("No se pudo crear notificación de reporte cerrado", exc_info=True)

        return jsonify({'ok': True, 'estado': r.estado})
    except Exception:
        db.session.rollback()
        current_app.logger.error("Error cerrando reporte: %s", traceback.format_exc())
        return jsonify({'error': 'Error al cerrar el reporte'}), 500


# ── Lógica compartida para crear/editar registros ─────────────────────────────

def _validar_y_aplicar_registro(reg: RegistroDiarioHoras, payload: dict, *, trabajador: Trabajador, reporte: ReporteSemanal):
    """Mutación in-place del registro a partir del payload, con validaciones.

    Devuelve `None` en éxito o `(mensaje, status)` en error.
    """
    hora_entrada_str = (payload.get('hora_entrada') or '').strip()
    hora_salida_str = (payload.get('hora_salida') or '').strip()
    tomo_comida = bool(payload.get('tomo_comida'))
    aplica_viaticos = bool(payload.get('aplica_viaticos'))
    viaticos_modo = payload.get('viaticos_modo') or 'perfil'
    monto_viaticos_manual_in = payload.get('monto_viaticos_manual')
    aplica_dia_festivo = bool(payload.get('aplica_dia_festivo'))
    incidencia = (payload.get('incidencia') or '').strip()

    if (not hora_entrada_str or not hora_salida_str) and not incidencia:
        return ('Debes registrar hora de entrada y salida, o seleccionar una incidencia.', 400)

    monto_viaticos_manual = None
    if aplica_viaticos:
        if viaticos_modo == 'manual':
            try:
                monto_viaticos_manual = float(monto_viaticos_manual_in) if monto_viaticos_manual_in is not None else None
            except (TypeError, ValueError):
                return ('El monto de viáticos no es un número válido.', 400)
            if not monto_viaticos_manual or monto_viaticos_manual <= 0:
                return ('El monto manual de viáticos debe ser mayor a $0.00.', 400)
        else:
            if not trabajador.viaticos or trabajador.viaticos <= 0:
                return (f'No se pueden habilitar viáticos para {trabajador.nombre}. Su perfil tiene $0.00.', 400)

    if aplica_dia_festivo and (not trabajador.pago_dia_festivo or trabajador.pago_dia_festivo <= 0):
        return (f'No se puede habilitar día festivo para {trabajador.nombre}. Su perfil tiene $0.00.', 400)

    hora_entrada = None
    hora_salida = None
    horas_productivas = 0.0

    if hora_entrada_str and hora_salida_str:
        try:
            hora_entrada = _parse_time(hora_entrada_str)
            hora_salida = _parse_time(hora_salida_str)
        except ValueError:
            return ('Formato de hora inválido (HH:MM)', 400)

        if hora_entrada == hora_salida:
            return ('La hora de salida debe ser distinta a la hora de entrada.', 400)

        # Detección de traslape contra otros registros BORRADOR del mismo trabajador/día
        q = RegistroDiarioHoras.query.join(ReporteSemanal).filter(
            RegistroDiarioHoras.trabajador_id == trabajador.id,
            RegistroDiarioHoras.fecha == reg.fecha,
            ReporteSemanal.estado == 'BORRADOR',
        )
        if reg.id:
            q = q.filter(RegistroDiarioHoras.id != reg.id)
        for otro in q.all():
            if otro.hora_entrada and otro.hora_salida:
                if turnos_se_traslapan(hora_entrada, hora_salida, otro.hora_entrada, otro.hora_salida):
                    p_otro = otro.reporte.proyecto.nombre if otro.reporte and otro.reporte.proyecto else '—'
                    return (
                        f'Choca con un registro existente del proyecto "{p_otro}" '
                        f'({otro.hora_entrada.strftime("%H:%M")} a {otro.hora_salida.strftime("%H:%M")}).',
                        409,
                    )

        horas_productivas = calcular_horas_productivas(
            hora_entrada, hora_salida,
            tipo_nomina=trabajador.tipo_nomina or 'Semanal',
            tomo_comida=tomo_comida,
        )

    reg.hora_entrada = hora_entrada
    reg.hora_salida = hora_salida
    reg.tomo_comida = tomo_comida
    reg.aplica_viaticos = aplica_viaticos
    reg.monto_viaticos_manual = monto_viaticos_manual
    reg.aplica_dia_festivo = aplica_dia_festivo
    reg.incidencia = incidencia or None
    reg.horas_productivas = horas_productivas
    reg.tipo_nomina = trabajador.tipo_nomina or 'Semanal'
    return None


@bp.route('/reportes/<int:reporte_id>/registros', methods=['POST'])
@jwt_required
def crear_registro(reporte_id):
    reporte = ReporteSemanal.query.get_or_404(reporte_id)
    if not _puede_acceder_proyecto(reporte.proyecto):
        return jsonify({'error': 'Acceso denegado'}), 403
    if reporte.estado != 'BORRADOR':
        return jsonify({'error': 'El reporte ya está cerrado'}), 409

    payload = request.get_json(silent=True) or {}
    trabajador_id = payload.get('trabajador_id')
    fecha_str = payload.get('fecha')

    if not trabajador_id or not fecha_str:
        return jsonify({'error': 'trabajador_id y fecha son obligatorios'}), 400

    try:
        fecha = datetime.strptime(fecha_str, '%Y-%m-%d').date()
    except ValueError:
        return jsonify({'error': 'Formato de fecha inválido'}), 400

    if not (reporte.fecha_inicio_semana <= fecha <= reporte.fecha_fin_semana):
        return jsonify({'error': 'La fecha no pertenece a la semana del reporte'}), 400

    trabajador = Trabajador.query.get(trabajador_id)
    if not trabajador:
        return jsonify({'error': 'Trabajador no encontrado'}), 404
    if trabajador not in reporte.proyecto.participantes:
        return jsonify({'error': 'Este trabajador no está asignado al proyecto'}), 400

    reg = RegistroDiarioHoras(reporte_id=reporte.id, trabajador_id=trabajador.id, fecha=fecha)
    err = _validar_y_aplicar_registro(reg, payload, trabajador=trabajador, reporte=reporte)
    if err:
        return jsonify({'error': err[0]}), err[1]

    try:
        db.session.add(reg)
        db.session.commit()
        return jsonify(_registro_dict(reg)), 201
    except Exception:
        db.session.rollback()
        current_app.logger.error("Error creando registro: %s", traceback.format_exc())
        return jsonify({'error': 'Error al guardar el registro'}), 500


@bp.route('/registros/<int:registro_id>', methods=['PUT'])
@jwt_required
def editar_registro(registro_id):
    reg = RegistroDiarioHoras.query.get_or_404(registro_id)
    reporte = reg.reporte
    if not _puede_acceder_proyecto(reporte.proyecto):
        return jsonify({'error': 'Acceso denegado'}), 403
    if reporte.estado != 'BORRADOR':
        return jsonify({'error': 'El reporte ya está cerrado'}), 409

    payload = request.get_json(silent=True) or {}
    err = _validar_y_aplicar_registro(reg, payload, trabajador=reg.trabajador, reporte=reporte)
    if err:
        return jsonify({'error': err[0]}), err[1]

    try:
        db.session.commit()
        return jsonify(_registro_dict(reg))
    except Exception:
        db.session.rollback()
        current_app.logger.error("Error editando registro: %s", traceback.format_exc())
        return jsonify({'error': 'Error al actualizar el registro'}), 500


@bp.route('/registros/<int:registro_id>', methods=['DELETE'])
@jwt_required
def eliminar_registro(registro_id):
    reg = RegistroDiarioHoras.query.get_or_404(registro_id)
    reporte = reg.reporte
    if not _puede_acceder_proyecto(reporte.proyecto):
        return jsonify({'error': 'Acceso denegado'}), 403
    if reporte.estado != 'BORRADOR':
        return jsonify({'error': 'El reporte ya está cerrado'}), 409

    try:
        db.session.delete(reg)
        db.session.commit()
        return jsonify({'ok': True})
    except Exception:
        db.session.rollback()
        current_app.logger.error("Error eliminando registro: %s", traceback.format_exc())
        return jsonify({'error': 'Error al eliminar'}), 500


# ── Móvil: menú + QR check-in ─────────────────────────────────────────────────

@bp.route('/movil/resumen', methods=['GET'])
@jwt_required
def movil_resumen():
    """Datos para la pantalla móvil del coordinador: proyectos + reportes BORRADOR."""
    if not (_is_admin() or _is_coordinador()):
        return jsonify({'error': 'Acceso denegado'}), 403

    uid = _u().id
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
    if not (_is_admin() or _is_coordinador()):
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


# ── Admin: gestión de QR de trabajadores ──────────────────────────────────────

@bp.route('/qr/trabajadores', methods=['GET'])
@jwt_required
def qr_listar_trabajadores():
    """Lista de trabajadores activos con su QR actual (solo admin)."""
    if not _is_admin():
        return jsonify({'error': 'Acceso denegado'}), 403

    trabajadores = (
        Trabajador.query.filter_by(activo=True)
        .order_by(Trabajador.nombre)
        .all()
    )
    return jsonify({
        'items': [
            {
                'id': t.id,
                'no_empleado': t.no_empleado,
                'nombre_completo': t.nombre_completo,
                'area': t.area or '',
                'qr_code': t.qr_code or '',
            }
            for t in trabajadores
        ],
    })


@bp.route('/qr/generar/<int:trabajador_id>', methods=['POST'])
@jwt_required
@limiter.limit('20 per minute')
def qr_generar(trabajador_id):
    """Genera (o regenera) el QR de un trabajador. Solo admin."""
    if not _is_admin():
        return jsonify({'ok': False, 'error': 'Acceso denegado'}), 403

    trabajador = Trabajador.query.get_or_404(trabajador_id)

    try:
        import uuid
        trabajador.qr_code = str(uuid.uuid4())
        db.session.commit()
        log_action(f"API: regeneró QR de {trabajador.nombre} ({trabajador.no_empleado})")
        return jsonify({
            'ok': True,
            'qr_code': trabajador.qr_code,
        })
    except Exception:
        db.session.rollback()
        current_app.logger.error("Error generando QR: %s", traceback.format_exc())
        return jsonify({'ok': False, 'error': 'Error al generar QR'}), 500


@bp.route('/qr/imagen/<qr_code>', methods=['GET'])
@jwt_required
def qr_imagen(qr_code):
    """Devuelve el PNG del QR. Cualquier usuario autenticado puede leerlo (se
    necesita conocer el código para acceder)."""
    import io
    import qrcode
    img = qrcode.make(qr_code)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return send_file(buf, mimetype='image/png')
