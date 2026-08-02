"""Gestión de QR de trabajadores + kiosko RFID.

Registra:
  /qr/trabajadores                                 GET
  /qr/generar/<int:trabajador_id>                  POST
  /qr/imagen/<qr_code>                             GET
  /qr/imagen/<int:trabajador_id>                   GET
  /rfid/asociar                                    POST
  /rfid/trabajadores-reporte/<int:reporte_id>      GET

El kiosko de asistencias (Electron + sidecar Python) usa los endpoints RFID
solo cuando hay internet:
  1. Al loguear el coordinador: GET /rfid/trabajadores-reporte/<id> para cachear
     qué tarjeta corresponde a qué trabajador en cada reporte BORRADOR activo.
  2. Al asociar una tarjeta nueva en sitio: POST /rfid/asociar con el UID leído.
El registro de asistencias en sí usa los endpoints estándar POST/PUT/DELETE de
registros — con client_record_id (idempotencia) y modificado_en (LWW).
"""
import io
import traceback
import uuid

import qrcode
from flask import current_app, jsonify, request, send_file
from sqlalchemy.orm import joinedload, selectinload

from app.extensions import db, limiter
from app.models import Proyecto, ReporteSemanal, Trabajador
from app.realtime import emit_to_role
from app.routes._api_helpers import current_user, is_admin
from app.routes.api_auth import jwt_required
from app.utils import log_action

from ._core import (
    bp,
    _is_coordinador, _puede_acceder_proyecto,
)


@bp.route('/qr/trabajadores', methods=['GET'])
@jwt_required
def qr_listar_trabajadores():
    """Lista de trabajadores activos con su QR actual (solo admin).

    Incluye datos requeridos para imprimir credenciales corporativas tamaño
    CR80 (foto, puesto, área, tipo sangre y contacto de emergencia para el
    reverso de la credencial).
    """
    if not is_admin():
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
                'puesto': t.puesto or '',
                'foto_perfil': t.foto_perfil or '',
                'tipo_sangre': t.tipo_sangre or '',
                'contacto_emergencia': t.contacto_emergencia or '',
                'numero_contacto_emerg': t.numero_contacto_emerg or '',
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
    if not is_admin():
        return jsonify({'ok': False, 'error': 'Acceso denegado'}), 403

    trabajador = db.get_or_404(Trabajador, trabajador_id)

    try:
        trabajador.qr_code = str(uuid.uuid4())
        db.session.commit()
        log_action(f"API: regeneró QR de {trabajador.nombre} ({trabajador.no_empleado})")
        emit_to_role(['admin', 'super_admin'], 'empleado:changed', {
            'id': trabajador.id, 'action': 'qr_regenerado',
        })
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
    img = qrcode.make(qr_code)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return send_file(buf, mimetype='image/png')


@bp.route('/qr/imagen/<int:trabajador_id>', methods=['GET'])
@jwt_required
def qr_imagen_trabajador(trabajador_id):
    """Devuelve el PNG del QR del trabajador. Solo admin.

    Si el trabajador aún no tiene `qr_code`, lo genera al vuelo (mismo flujo
    que `/qr/generar` pero ahorra el round-trip POST → GET desde el frontend).
    """
    if not is_admin():
        return jsonify({'error': 'Acceso denegado'}), 403

    trabajador = db.get_or_404(Trabajador, trabajador_id)

    # Generar si no existe — útil para empleados nuevos sin QR todavía.
    if not trabajador.qr_code:
        try:
            trabajador.qr_code = str(uuid.uuid4())
            db.session.commit()
            log_action(f"API: QR generado on-demand para {trabajador.nombre} ({trabajador.no_empleado})")
            emit_to_role(['admin', 'super_admin'], 'empleado:changed', {
                'id': trabajador.id, 'action': 'qr_regenerado',
            })
        except Exception:
            db.session.rollback()
            return jsonify({'error': 'Error generando QR'}), 500

    img = qrcode.make(trabajador.qr_code)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return send_file(buf, mimetype='image/png')


def _normalizar_uid(raw: str) -> str:
    """Normaliza un UID RFID a hexadecimal en mayúsculas, sin prefijos ni espacios.

    Distintos lectores (Wiegand vía Arduino, USB-HID) entregan el UID en
    formatos heterogéneos: decimal puro, hex con/sin '0x', con guiones, etc.
    Normalizamos para que la búsqueda y la unicidad funcionen sin importar la
    fuente del lector.
    """
    if not raw:
        return ''
    s = raw.strip().upper().replace(' ', '').replace('-', '').replace(':', '')
    if s.startswith('0X'):
        s = s[2:]
    # Si es decimal puro lo dejamos así (no asumimos base); para hex validamos.
    return s


@bp.route('/rfid/asociar', methods=['POST'])
@jwt_required
@limiter.limit('20 per minute')
def rfid_asociar():
    """Asocia una tarjeta RFID a un trabajador.

    Body: {"trabajador_id": int, "uid": "ABCD1234"}
    El UID se normaliza (uppercase, sin separadores) antes de guardarlo para
    evitar duplicados por diferencias de formato entre lectores.
    """
    if not (is_admin() or _is_coordinador()):
        return jsonify({'ok': False, 'error': 'Acceso denegado'}), 403

    payload = request.get_json(silent=True) or {}
    trabajador_id = payload.get('trabajador_id')
    uid = _normalizar_uid(payload.get('uid') or '')

    if not trabajador_id or not uid:
        return jsonify({'ok': False, 'error': 'trabajador_id y uid son obligatorios'}), 400
    if len(uid) > 64:
        return jsonify({'ok': False, 'error': 'UID demasiado largo (máx 64 chars)'}), 400

    trabajador = db.get_or_404(Trabajador, trabajador_id)

    # Si el coordinador intenta asociar tarjeta a un trabajador, debe ser de un
    # proyecto suyo (mismo criterio que el resto del módulo).
    if _is_coordinador():
        proyectos_coord = Proyecto.query.filter_by(coordinador_id=current_user().id).all()
        if not any(trabajador in p.participantes for p in proyectos_coord):
            return jsonify({'ok': False, 'error': 'Este trabajador no está en tus proyectos'}), 403

    # Unicidad: si el UID ya pertenece a otro trabajador, rechazar para no pisar.
    duenio = Trabajador.query.filter(
        Trabajador.rfid_uid == uid,
        Trabajador.id != trabajador.id,
    ).first()
    if duenio:
        return jsonify({
            'ok': False,
            'error': f'Esta tarjeta ya está asociada a {duenio.nombre_completo} ({duenio.no_empleado}).',
        }), 409

    try:
        trabajador.rfid_uid = uid
        db.session.commit()
        log_action(f"API: asoció RFID {uid} a {trabajador.nombre} ({trabajador.no_empleado})")
        emit_to_role(['admin', 'super_admin', 'coordinador'], 'empleado:changed', {
            'id': trabajador.id, 'action': 'rfid_asociado',
        })
        return jsonify({
            'ok': True,
            'trabajador_id': trabajador.id,
            'rfid_uid': trabajador.rfid_uid,
        })
    except Exception:
        db.session.rollback()
        current_app.logger.error("Error asociando RFID: %s", traceback.format_exc())
        return jsonify({'ok': False, 'error': 'Error al asociar la tarjeta'}), 500


@bp.route('/rfid/trabajadores-reporte/<int:reporte_id>', methods=['GET'])
@jwt_required
def rfid_trabajadores_reporte(reporte_id):
    """Devuelve los trabajadores del reporte con su rfid_uid y datos para el
    cache local del kiosko (no_empleado, nombre, foto, tipo_nomina, viaticos,
    pago_dia_festivo). El kiosko llama este endpoint al descargar un reporte
    antes de irse offline.
    """
    r = db.get_or_404(ReporteSemanal, reporte_id, options=[
        joinedload(ReporteSemanal.proyecto).selectinload(Proyecto.participantes),
    ])

    if not _puede_acceder_proyecto(r.proyecto):
        return jsonify({'error': 'Acceso denegado'}), 403

    items = []
    for t in sorted(r.proyecto.participantes, key=lambda x: (x.nombre or '').lower()):
        items.append({
            'id': t.id,
            'no_empleado': t.no_empleado,
            'nombre_completo': t.nombre_completo,
            'tipo_nomina': t.tipo_nomina or 'Semanal',
            'viaticos': float(t.viaticos) if t.viaticos is not None else 0.0,
            'pago_dia_festivo': float(t.pago_dia_festivo) if t.pago_dia_festivo is not None else 0.0,
            'rfid_uid': t.rfid_uid or '',
            # `/uploads/` nunca estuvo servido por Flask: apuntamos al endpoint
            # real (con JWT), que ahora lee de R2 privado o de disco.
            'foto_url': f'/api/trabajadores/{t.id}/foto' if t.foto_perfil else None,
        })

    return jsonify({
        'reporte_id': r.id,
        'fecha_inicio': r.fecha_inicio_semana.isoformat(),
        'fecha_fin': r.fecha_fin_semana.isoformat(),
        'trabajadores': items,
    })
