"""CRUD de lectores Hikvision + prueba de conexión.

Registra:
  GET    /api/hikvision/dispositivos                 listar
  POST   /api/hikvision/dispositivos                 crear
  GET    /api/hikvision/dispositivos/<id>            obtener
  PUT    /api/hikvision/dispositivos/<id>            actualizar
  DELETE /api/hikvision/dispositivos/<id>            eliminar
  POST   /api/hikvision/dispositivos/<id>/probar     probar conexión

La contraseña entra por el payload pero NUNCA sale: ninguna respuesta de este
módulo la incluye, y `DispositivoHikvision.to_dict()` la omite por diseño.
"""
from __future__ import annotations

from flask import current_app, jsonify, request

from app.extensions import db, limiter
from app.models import DispositivoHikvision, _now_utc
from app.routes._api_helpers import api_transactional, require_admin
from app.routes.api_auth import jwt_required
from app.services.hikvision import (
    ClienteHikvision,
    ErrorHikvision,
    validar_host,
    validar_puerto,
)
from app.utils import log_action

from ._core import bp, error, obtener_dispositivo_o_404

# Longitudes máximas, espejo de las columnas del modelo.
_LARGOS = {'nombre': 120, 'host': 120, 'usuario': 64, 'password': 200}


def _validar_payload(datos: dict, *, es_alta: bool) -> str | None:
    """Devuelve el primer mensaje de error, o None si el payload es válido."""
    obligatorios = ['nombre', 'host', 'usuario'] + (['password'] if es_alta else [])
    for campo in obligatorios:
        if not (datos.get(campo) or '').strip():
            return f'El campo "{campo}" es obligatorio.'

    for campo, maximo in _LARGOS.items():
        valor = datos.get(campo)
        if isinstance(valor, str) and len(valor.strip()) > maximo:
            return f'El campo "{campo}" excede {maximo} caracteres.'

    try:
        validar_host(datos.get('host', ''))
        validar_puerto(datos.get('puerto') or 80)
    except ErrorHikvision as e:
        return e.mensaje
    return None


@bp.route('/dispositivos', methods=['GET'])
@jwt_required
def listar():
    err = require_admin()
    if err:
        return err
    filas = DispositivoHikvision.query.order_by(DispositivoHikvision.nombre).all()
    return jsonify({'items': [d.to_dict() for d in filas]})


@bp.route('/dispositivos/<int:dispositivo_id>', methods=['GET'])
@jwt_required
def obtener(dispositivo_id):
    err = require_admin()
    if err:
        return err
    return jsonify(obtener_dispositivo_o_404(dispositivo_id).to_dict())


@bp.route('/dispositivos', methods=['POST'])
@jwt_required
@limiter.limit('20 per minute')
@api_transactional('No se pudo guardar el lector')
def crear():
    err = require_admin()
    if err:
        return err

    datos = request.get_json(silent=True) or {}
    problema = _validar_payload(datos, es_alta=True)
    if problema:
        return error(problema, 422)

    host = datos['host'].strip()
    puerto = validar_puerto(datos.get('puerto') or 80)

    if DispositivoHikvision.query.filter_by(host=host, puerto=puerto).first():
        return error(f'Ya existe un lector configurado en {host}:{puerto}.', 409)

    d = DispositivoHikvision(
        nombre=datos['nombre'].strip(),
        host=host,
        puerto=puerto,
        usuario=datos['usuario'].strip(),
        password=datos['password'],
        activo=bool(datos.get('activo', True)),
    )
    db.session.add(d)
    db.session.commit()
    # El log lleva host y puerto, jamás la contraseña.
    log_action(f'Dio de alta el lector Hikvision "{d.nombre}" ({d.host}:{d.puerto})')
    db.session.commit()
    return jsonify(d.to_dict()), 201


@bp.route('/dispositivos/<int:dispositivo_id>', methods=['PUT'])
@jwt_required
@limiter.limit('30 per minute')
@api_transactional('No se pudo actualizar el lector')
def actualizar(dispositivo_id):
    err = require_admin()
    if err:
        return err

    d = obtener_dispositivo_o_404(dispositivo_id)
    datos = request.get_json(silent=True) or {}
    problema = _validar_payload(
        {**{'nombre': d.nombre, 'host': d.host, 'usuario': d.usuario, 'puerto': d.puerto}, **datos},
        es_alta=False,
    )
    if problema:
        return error(problema, 422)

    if 'nombre' in datos:
        d.nombre = datos['nombre'].strip()
    if 'host' in datos:
        d.host = datos['host'].strip()
    if 'puerto' in datos:
        d.puerto = validar_puerto(datos['puerto'])
    if 'usuario' in datos:
        d.usuario = datos['usuario'].strip()
    if 'activo' in datos:
        d.activo = bool(datos['activo'])
    # Contraseña: solo se toca si llega con contenido. Mandar el campo vacío
    # desde un formulario significa "no la cambies", no "bórrala" — un lector
    # sin contraseña no serviría para nada.
    if (datos.get('password') or '').strip():
        d.password = datos['password']

    otro = DispositivoHikvision.query.filter(
        DispositivoHikvision.host == d.host,
        DispositivoHikvision.puerto == d.puerto,
        DispositivoHikvision.id != d.id,
    ).first()
    if otro:
        return error(f'Ya existe otro lector configurado en {d.host}:{d.puerto}.', 409)

    db.session.commit()
    log_action(f'Editó el lector Hikvision "{d.nombre}" ({d.host}:{d.puerto})')
    db.session.commit()
    return jsonify(d.to_dict())


@bp.route('/dispositivos/<int:dispositivo_id>', methods=['DELETE'])
@jwt_required
@api_transactional('No se pudo eliminar el lector')
def eliminar(dispositivo_id):
    err = require_admin()
    if err:
        return err

    d = obtener_dispositivo_o_404(dispositivo_id)
    descripcion = f'"{d.nombre}" ({d.host}:{d.puerto})'
    # Borrar la configuración del ERP NO borra a los usuarios del equipo: el
    # lector seguiría dejando pasar a quien ya tenga dentro. Se avisa en la UI.
    db.session.delete(d)
    db.session.commit()
    log_action(f'Eliminó el lector Hikvision {descripcion}')
    db.session.commit()
    return jsonify({'ok': True})


@bp.route('/dispositivos/<int:dispositivo_id>/probar', methods=['POST'])
@jwt_required
@limiter.limit('10 per minute')
def probar(dispositivo_id):
    """Prueba de vida y credenciales contra el equipo.

    Cachea en la fila lo que reporta el lector (modelo, serie, firmware) y el
    resultado del intento, para que la pantalla lo muestre sin volver a llamar.

    El límite de 10/min no es cosmético: Hikvision bloquea la cuenta ~30 min
    tras varios intentos fallidos, así que martillar este botón con una
    contraseña mala dejaría el lector inaccesible.
    """
    err = require_admin()
    if err:
        return err

    d = obtener_dispositivo_o_404(dispositivo_id)
    try:
        with ClienteHikvision.desde_dispositivo(d) as cli:
            info = cli.info_dispositivo()
            hora = cli.hora_dispositivo()
    except ErrorHikvision as e:
        current_app.logger.warning('Hikvision probar(%s): %s', d.id, e.detalle)
        d.ultimo_estado = 'ERROR'
        d.ultimo_error = e.mensaje
        d.ultima_conexion = _now_utc()
        db.session.commit()
        return jsonify({'ok': False, 'error': e.mensaje, 'dispositivo': d.to_dict()}), 502

    d.modelo = info['modelo']
    d.numero_serie = info['numero_serie']
    d.firmware = info['firmware']
    d.ultimo_estado = 'OK'
    d.ultimo_error = None
    d.ultima_conexion = _now_utc()
    db.session.commit()
    log_action(f'Probó la conexión con el lector "{d.nombre}" ({d.host}:{d.puerto}): OK')
    db.session.commit()

    return jsonify({
        'ok': True,
        'dispositivo': d.to_dict(),
        'info': info,
        # El reloj se devuelve aparte porque un lector en hora equivocada
        # registra checadas inservibles, y eso conviene verlo antes de la fase
        # de asistencia, no después.
        'hora': hora,
    })
