"""Registros diarios de horas: CRUD + bulk upsert.

Registra:
  /reportes/<int:reporte_id>/registros          POST
  /reportes/<int:reporte_id>/registros/bulk     POST
  /registros/<int:registro_id>                  PUT, DELETE
"""
import traceback
from datetime import datetime

from flask import current_app, jsonify, request

from app.extensions import db
from app.models import RegistroDiarioHoras, ReporteSemanal, Trabajador
from app.routes.api_auth import jwt_required
from app.utils import calcular_horas_productivas, turnos_se_traslapan

from ._core import (
    bp,
    _puede_acceder_proyecto, _parse_time, _registro_dict,
)


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

    # Permitimos guardar con solo hora_entrada (trabajador "dentro", esperando
    # salida) — caso del kiosko RFID al pasar la primera tarjeta del día.
    # Requisitos mínimos: o hay entrada, o hay incidencia. Salida sin entrada
    # sí es error porque no podemos calcular nada.
    if not hora_entrada_str and not incidencia:
        return ('Debes registrar al menos la hora de entrada, o seleccionar una incidencia.', 400)
    if hora_salida_str and not hora_entrada_str:
        return ('No puedes registrar salida sin entrada.', 400)

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

    if hora_entrada_str:
        try:
            hora_entrada = _parse_time(hora_entrada_str)
        except ValueError:
            return ('Formato de hora inválido (HH:MM)', 400)

    if hora_salida_str:
        try:
            hora_salida = _parse_time(hora_salida_str)
        except ValueError:
            return ('Formato de hora inválido (HH:MM)', 400)

    if hora_entrada and hora_salida:
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
    reporte = db.get_or_404(ReporteSemanal, reporte_id)
    if not _puede_acceder_proyecto(reporte.proyecto):
        return jsonify({'error': 'Acceso denegado'}), 403
    if reporte.estado != 'BORRADOR':
        return jsonify({'error': 'El reporte ya está cerrado'}), 409

    payload = request.get_json(silent=True) or {}
    trabajador_id = payload.get('trabajador_id')
    fecha_str = payload.get('fecha')

    if not trabajador_id or not fecha_str:
        return jsonify({'error': 'trabajador_id y fecha son obligatorios'}), 400

    # Idempotencia: si el cliente (kiosko offline) reintenta, el mismo client_record_id
    # debe devolver el registro existente sin crear duplicado.
    #
    # IDOR fix: `client_record_id` es único globalmente, pero la idempotencia
    # solo aplica al MISMO reporte. Si el registro existente pertenece a otro
    # reporte (potencialmente fuera del scope del coordinador), devolvemos 404
    # — no 403, para no confirmarle al atacante que el ID existe en otra parte.
    client_record_id = (payload.get('client_record_id') or '').strip() or None
    if client_record_id:
        existente = RegistroDiarioHoras.query.filter_by(client_record_id=client_record_id).first()
        if existente:
            if existente.reporte_id != reporte.id:
                return jsonify({'error': 'Registro no encontrado'}), 404
            return jsonify(_registro_dict(existente)), 200

    try:
        fecha = datetime.strptime(fecha_str, '%Y-%m-%d').date()
    except ValueError:
        return jsonify({'error': 'Formato de fecha inválido'}), 400

    if not (reporte.fecha_inicio_semana <= fecha <= reporte.fecha_fin_semana):
        return jsonify({'error': 'La fecha no pertenece a la semana del reporte'}), 400

    trabajador = db.session.get(Trabajador, trabajador_id)
    if not trabajador:
        return jsonify({'error': 'Trabajador no encontrado'}), 404
    if trabajador not in reporte.proyecto.participantes:
        return jsonify({'error': 'Este trabajador no está asignado al proyecto'}), 400

    reg = RegistroDiarioHoras(reporte_id=reporte.id, trabajador_id=trabajador.id, fecha=fecha)
    if client_record_id:
        reg.client_record_id = client_record_id
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


@bp.route('/reportes/<int:reporte_id>/registros/bulk', methods=['POST'])
@jwt_required
def bulk_upsert_registros(reporte_id):
    """Crea o actualiza varios registros en una sola transacción.

    Body: { "registros": [{trabajador_id, fecha, hora_entrada?, hora_salida?,
            tomo_comida?, incidencia?}, ...] }  (1..200 filas).

    Para cada fila:
      - upsert por (trabajador_id, fecha) — si ya existe, se actualiza;
        si no, se crea.
      - se valida con `_validar_y_aplicar_registro` (mismas reglas que la
        creación/edición individual: traslapes, fechas dentro de la semana,
        formato de hora, etc.).
      - si una fila falla validación, se omite (no rollback total); el error
        se reporta en `skipped: [{idx, reason}]`.

    El emit de `reporte:registros_cambio` lo hace el hook
    `_register_registros_emit_hook` automáticamente al commit; no se duplica
    desde aquí.
    """
    reporte = db.get_or_404(ReporteSemanal, reporte_id)
    if not _puede_acceder_proyecto(reporte.proyecto):
        return jsonify({'error': 'Acceso denegado'}), 403
    if reporte.estado != 'BORRADOR':
        return jsonify({'error': 'El reporte ya está cerrado'}), 409

    payload = request.get_json(silent=True) or {}
    rows = payload.get('registros') or []
    if not isinstance(rows, list) or not rows:
        return jsonify({'error': 'Lista de registros vacía'}), 422
    if len(rows) > 200:
        return jsonify({'error': 'Máximo 200 registros por operación'}), 422

    # Map de trabajadores válidos del proyecto: id -> Trabajador
    participantes = {t.id: t for t in reporte.proyecto.participantes}

    # Precarga de registros existentes en la semana, indexados por (tid, fecha)
    existentes = {
        (r.trabajador_id, r.fecha): r
        for r in RegistroDiarioHoras.query.filter_by(reporte_id=reporte.id).all()
    }

    created = []
    updated = []
    skipped = []

    for idx, raw in enumerate(rows):
        if not isinstance(raw, dict):
            skipped.append({'idx': idx, 'reason': 'formato inválido'})
            continue
        try:
            trabajador_id = int(raw.get('trabajador_id'))
        except (TypeError, ValueError):
            skipped.append({'idx': idx, 'reason': 'trabajador_id inválido'})
            continue
        fecha_str = (raw.get('fecha') or '').strip()
        if not fecha_str:
            skipped.append({'idx': idx, 'reason': 'fecha vacía'})
            continue
        try:
            fecha = datetime.strptime(fecha_str, '%Y-%m-%d').date()
        except ValueError:
            skipped.append({'idx': idx, 'reason': 'fecha inválida'})
            continue
        if not (reporte.fecha_inicio_semana <= fecha <= reporte.fecha_fin_semana):
            skipped.append({'idx': idx, 'reason': 'fecha fuera de la semana'})
            continue
        trabajador = participantes.get(trabajador_id)
        if not trabajador:
            skipped.append({'idx': idx, 'reason': 'trabajador no asignado al proyecto'})
            continue

        existing = existentes.get((trabajador_id, fecha))
        if existing:
            reg = existing
            is_new = False
        else:
            reg = RegistroDiarioHoras(reporte_id=reporte.id, trabajador_id=trabajador.id, fecha=fecha)
            is_new = True

        # Preservar viáticos/festivo del existente si no vienen en el payload
        # (el flujo "pegar desde Excel" no captura esos campos).
        merged = dict(raw)
        if existing and 'aplica_viaticos' not in raw:
            merged['aplica_viaticos'] = bool(existing.aplica_viaticos)
            merged['viaticos_modo'] = 'manual' if existing.monto_viaticos_manual is not None else 'perfil'
            merged['monto_viaticos_manual'] = (
                float(existing.monto_viaticos_manual) if existing.monto_viaticos_manual is not None else None
            )
        if existing and 'aplica_dia_festivo' not in raw:
            merged['aplica_dia_festivo'] = bool(existing.aplica_dia_festivo)

        err = _validar_y_aplicar_registro(reg, merged, trabajador=trabajador, reporte=reporte)
        if err:
            skipped.append({'idx': idx, 'reason': err[0]})
            continue

        if is_new:
            db.session.add(reg)
            created.append(idx)
            # Para que upserts dentro del mismo batch (mismo tid+fecha) no
            # generen duplicados, indexamos el nuevo en `existentes`.
            existentes[(trabajador_id, fecha)] = reg
        else:
            updated.append(idx)

    if not created and not updated:
        return jsonify({
            'ok': True, 'created': 0, 'updated': 0,
            'skipped': skipped,
        })

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.error('Error bulk upsert registros: %s', traceback.format_exc())
        return jsonify({'error': 'Error al guardar en lote'}), 500

    return jsonify({
        'ok': True,
        'created': len(created),
        'updated': len(updated),
        'skipped': skipped,
    })


@bp.route('/registros/<int:registro_id>', methods=['PUT'])
@jwt_required
def editar_registro(registro_id):
    reg = db.get_or_404(RegistroDiarioHoras, registro_id)
    reporte = reg.reporte
    if not _puede_acceder_proyecto(reporte.proyecto):
        return jsonify({'error': 'Acceso denegado'}), 403
    if reporte.estado != 'BORRADOR':
        return jsonify({'error': 'El reporte ya está cerrado'}), 409

    payload = request.get_json(silent=True) or {}

    # LWW: si el cliente declara cuándo modificó su copia local y el servidor tiene
    # una versión más reciente, devolvemos 409 con el estado actual para que el
    # cliente decida (sobrescribir, descartar local, o resolver manualmente).
    cliente_modificado_en_str = (payload.get('modificado_en') or '').strip()
    if cliente_modificado_en_str and reg.modificado_en:
        try:
            cliente_dt = datetime.fromisoformat(cliente_modificado_en_str.replace('Z', '+00:00'))
        except ValueError:
            return jsonify({'error': 'modificado_en con formato inválido (esperado ISO 8601)'}), 400
        # SQLite no preserva tzinfo aunque la columna declare timezone=True: el
        # valor que regresa SQLAlchemy llega naive. Asumimos UTC en ambos lados.
        from datetime import timezone as _tz
        if cliente_dt.tzinfo is None:
            cliente_dt = cliente_dt.replace(tzinfo=_tz.utc)
        servidor_dt = reg.modificado_en
        if servidor_dt.tzinfo is None:
            servidor_dt = servidor_dt.replace(tzinfo=_tz.utc)
        if servidor_dt > cliente_dt:
            return jsonify({
                'error': 'conflicto',
                'conflicto': True,
                'servidor': _registro_dict(reg),
            }), 409

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
    reg = db.get_or_404(RegistroDiarioHoras, registro_id)
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
