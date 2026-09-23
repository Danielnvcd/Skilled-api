"""Puerta y relé del lector.

Registra:
  GET  /api/hikvision/dispositivos/<id>/puerta         parámetros + estado en vivo
  POST /api/hikvision/dispositivos/<id>/puerta/abrir   apertura remota

Recordatorio de por qué esto es CHICO: los empleados sincronizados ya abren la
puerta solos al ser reconocidos (se les da de alta con permiso permanente sobre
la puerta 1). Lo de aquí es la apertura manual desde el ERP, para visitas o
imprevistos.
"""
from __future__ import annotations

from flask import current_app, jsonify

from app.extensions import db, limiter
from app.models import _now_utc
from app.routes._api_helpers import api_transactional, require_admin
from app.routes.api_auth import jwt_required
from app.services.hikvision import ClienteHikvision, ErrorHikvision
from app.services.hikvision import puertas as svc_puertas
from app.utils import log_action

from ._core import bp, error, obtener_dispositivo_o_404


@bp.route('/dispositivos/<int:dispositivo_id>/puerta', methods=['GET'])
@jwt_required
def estado_puerta(dispositivo_id):
    """Configuración y estado en vivo de la puerta del lector."""
    err = require_admin()
    if err:
        return err

    d = obtener_dispositivo_o_404(dispositivo_id)
    try:
        with ClienteHikvision.desde_dispositivo(d) as cli:
            return jsonify({
                'ok': True,
                'parametros': svc_puertas.parametros(cli),
                'estado': svc_puertas.estado(cli),
            })
    except ErrorHikvision as e:
        current_app.logger.warning('Hikvision puerta(%s): %s', d.id, e.detalle)
        return jsonify({'ok': False, 'error': e.mensaje}), 502


@bp.route('/dispositivos/<int:dispositivo_id>/puerta/abrir', methods=['POST'])
@jwt_required
# Límite bajo a propósito: esto abre una puerta física. Un botón que se puede
# pulsar sin freno desde un navegador es una puerta que se puede mantener
# abierta desde un navegador.
@limiter.limit('6 per minute')
@api_transactional('No se pudo abrir la puerta')
def abrir_puerta(dispositivo_id):
    """Abre la puerta del lector de forma remota.

    Queda SIEMPRE en la bitácora, con quién lo hizo: es la única acción del
    módulo que tiene efecto en el mundo físico, y auditarla importa más que
    cualquier otra cosa que se registre aquí.
    """
    err = require_admin()
    if err:
        return err

    d = obtener_dispositivo_o_404(dispositivo_id)
    if not d.activo:
        return error('Este lector está desactivado.', 409)

    try:
        with ClienteHikvision.desde_dispositivo(d) as cli:
            svc_puertas.abrir(cli)
            parametros = svc_puertas.parametros(cli)
    except ErrorHikvision as e:
        current_app.logger.warning('Hikvision abrir_puerta(%s): %s', d.id, e.detalle)
        # También se registra el INTENTO fallido: para una acción física,
        # "alguien trató de abrir y no pudo" es información de seguridad.
        log_action(f'Intentó abrir la puerta del lector "{d.nombre}" ({d.host}): {e.mensaje}')
        db.session.commit()
        return jsonify({'ok': False, 'error': e.mensaje}), 502

    d.ultima_conexion = _now_utc()
    d.ultimo_estado = 'OK'
    d.ultimo_error = None
    db.session.commit()
    log_action(f'Abrió remotamente la puerta del lector "{d.nombre}" ({d.host}:{d.puerto})')
    db.session.commit()

    return jsonify({
        'ok': True,
        'segundos_apertura': parametros['segundos_apertura'],
    })
