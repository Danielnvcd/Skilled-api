"""Actividad del lector: accesos permitidos y rechazados, con su foto.

Registra:
  GET  /api/hikvision/dispositivos/<id>/eventos              eventos guardados + resumen de hoy
  POST /api/hikvision/dispositivos/<id>/eventos/sincronizar  traer lo pendiente del lector
  GET  /api/hikvision/dispositivos/<id>/eventos/captura      foto que tomó el equipo

Los eventos se LEEN DE LA BASE, no del lector: los guarda el proceso de escucha
(`flask hikvision escuchar`) en cuanto ocurren y avisa al navegador por
Socket.IO (`hikvision:evento`). Abrir la pantalla ya no le cuesta nada al
equipo. Si la escucha no corre, la respuesta lo dice (`tiempo_real.en_vivo`) y
el botón «Traer eventos» hace la misma ingesta a mano.

La única llamada al lector que queda aquí es la foto, y solo cuando alguien la
abre.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from flask import Response, current_app, jsonify, request
from sqlalchemy import func

from app.extensions import db, limiter
from app.models import EventoHikvision, SyncEmpleadoHikvision
from app.realtime import emit_to_role
from app.routes._api_helpers import api_transactional, require_admin
from app.routes.api_auth import jwt_required
from app.services.hikvision import ClienteHikvision, ErrorHikvision, ingesta
from app.services.hikvision import eventos as svc_eventos

from ._core import bp, error, obtener_dispositivo_o_404

LIMITE_MAX = 200
HORAS_MAX = 24 * 31


def _entero(valor, defecto: int, minimo: int, maximo: int) -> int:
    try:
        return max(minimo, min(maximo, int(valor)))
    except (TypeError, ValueError):
        return defecto


def _zona_del_lector(dispositivo_id: int):
    """Desfase horario del lector, tomado de su evento más reciente.

    «Hoy» es el día de la OFICINA, no el del servidor (que corre en UTC). Sin
    eventos guardados no hay de dónde sacarlo y se usa UTC.
    """
    hora = db.session.query(EventoHikvision.hora_local).filter(
        EventoHikvision.dispositivo_id == dispositivo_id,
    ).order_by(EventoHikvision.serial_no.desc()).limit(1).scalar()
    try:
        return datetime.fromisoformat(hora).tzinfo or timezone.utc
    except (TypeError, ValueError):
        return timezone.utc


@bp.route('/dispositivos/<int:dispositivo_id>/eventos', methods=['GET'])
@jwt_required
def listar_eventos(dispositivo_id):
    """Eventos guardados, del más reciente al más antiguo.

    Query: filtro=todos|permitidos|denegados, horas (1..744, 24 por defecto),
    limite (1..200, 100 por defecto).
    """
    err = require_admin()
    if err:
        return err

    filtro = (request.args.get('filtro') or 'todos').strip().lower()
    if filtro not in svc_eventos.FILTROS:
        return error('Filtro inválido.', 422)
    horas = _entero(request.args.get('horas'), 24, 1, HORAS_MAX)
    limite = _entero(request.args.get('limite'), 100, 1, LIMITE_MAX)

    d = obtener_dispositivo_o_404(dispositivo_id)
    ahora = datetime.now(timezone.utc)

    consulta = EventoHikvision.query.filter(
        EventoHikvision.dispositivo_id == d.id,
        EventoHikvision.fecha_hora >= ahora - timedelta(hours=horas),
    )
    tipo = svc_eventos.FILTROS[filtro]
    if tipo:
        consulta = consulta.filter(EventoHikvision.tipo == tipo)
    filas = consulta.order_by(
        EventoHikvision.fecha_hora.desc(), EventoHikvision.serial_no.desc(),
    ).limit(limite).all()

    # El trabajador se resuelve al guardar, pero alguien sincronizado DESPUÉS
    # de sus primeros accesos quedaría sin enlace: se completa al leer.
    por_numero = {
        f.employee_no_remoto: f.trabajador_id
        for f in SyncEmpleadoHikvision.query.filter_by(dispositivo_id=d.id)
    }
    items = []
    for fila in filas:
        e = fila.to_dict()
        if e['trabajador_id'] is None:
            e['trabajador_id'] = por_numero.get(e['employee_no'])
        items.append(e)

    zona = _zona_del_lector(d.id)
    hoy_local = ahora.astimezone(zona)
    inicio_hoy = hoy_local.replace(hour=0, minute=0, second=0, microsecond=0)
    conteo = dict(
        db.session.query(EventoHikvision.tipo, func.count()).filter(
            EventoHikvision.dispositivo_id == d.id,
            EventoHikvision.fecha_hora >= inicio_hoy,
            EventoHikvision.tipo.in_(['permitido', 'denegado']),
        ).group_by(EventoHikvision.tipo).all()
    )

    return jsonify({
        'ok': True,
        'items': items,
        'hoy': {
            'permitidos': conteo.get('permitido', 0),
            'denegados': conteo.get('denegado', 0),
        },
        'fecha_hoy': hoy_local.date().isoformat(),
        'tiempo_real': d.estado_escucha(ahora),
    })


@bp.route('/dispositivos/<int:dispositivo_id>/eventos/sincronizar', methods=['POST'])
@jwt_required
@limiter.limit('6 per minute')
@api_transactional('No se pudieron traer los eventos del lector')
def sincronizar_eventos(dispositivo_id):
    """Trae del lector lo que falte, una vez. Para cuando la escucha no corre.

    Es la MISMA ingesta que usa la escucha (`ingesta.ponerse_al_dia`), así que
    usar las dos a la vez no duplica nada.
    """
    err = require_admin()
    if err:
        return err

    d = obtener_dispositivo_o_404(dispositivo_id)
    try:
        with ClienteHikvision.desde_dispositivo(d) as cli:
            nuevos = ingesta.ponerse_al_dia(cli, d.id)
    except ErrorHikvision as e:
        current_app.logger.warning('Hikvision sincronizar_eventos(%s): %s', d.id, e.detalle)
        db.session.rollback()
        return jsonify({'ok': False, 'error': e.mensaje}), 502
    db.session.commit()

    if nuevos:
        emit_to_role(['admin', 'super_admin'], 'hikvision:evento', {
            'dispositivo_id': d.id, 'total': len(nuevos),
            'eventos': [e.to_dict() for e in nuevos[-20:]],
        })
    return jsonify({'ok': True, 'nuevos': len(nuevos)})


@bp.route('/dispositivos/<int:dispositivo_id>/eventos/captura', methods=['GET'])
@jwt_required
@limiter.limit('120 per minute')
def captura_evento(dispositivo_id):
    """La foto que tomó el lector en un intento de acceso (JPEG).

    `ruta` debe ser una de las que devuelve `/eventos` (bajo `/LOCALS/pic/`):
    este endpoint no sirve para bajar cualquier otro archivo del equipo.
    """
    err = require_admin()
    if err:
        return err

    ruta = request.args.get('ruta') or ''
    if not svc_eventos.es_ruta_captura_valida(ruta):
        return error('Ruta de captura inválida.', 422)

    d = obtener_dispositivo_o_404(dispositivo_id)
    try:
        with ClienteHikvision.desde_dispositivo(d) as cli:
            jpeg = cli.descargar(ruta)
    except ErrorHikvision as e:
        current_app.logger.warning('Hikvision captura(%s): %s', d.id, e.detalle)
        return jsonify({'ok': False, 'error': e.mensaje}), 502

    # Sin cache a propósito (el `no-store` del blueprint aplica también aquí):
    # es la cara de una persona.
    return Response(jpeg, mimetype='image/jpeg')
