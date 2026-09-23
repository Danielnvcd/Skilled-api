"""Estado del equipo, reloj y auditoría de usuarios del lector.

Registra:
  GET    /api/hikvision/dispositivos/<id>/estado                       info + reloj + capacidad
  POST   /api/hikvision/dispositivos/<id>/hora                         poner el reloj en hora
  GET    /api/hikvision/dispositivos/<id>/auditoria                    ERP vs. equipo
  DELETE /api/hikvision/dispositivos/<id>/usuarios-equipo/<emp_no>     borrar un usuario ajeno

La auditoría existe porque el lector también se puede administrar desde su
propia pantalla o desde su web: alguien puede dar de alta a una persona ahí,
fuera del ERP, y esa persona abriría la puerta sin que el ERP lo sepa.
"""
from __future__ import annotations

from datetime import datetime, timezone

from flask import current_app, jsonify

from app.extensions import db, limiter
from app.models import SyncEmpleadoHikvision, _now_utc
from app.routes._api_helpers import api_transactional, require_admin
from app.routes.api_auth import jwt_required
from app.services.hikvision import ClienteHikvision, ErrorHikvision
from app.services.hikvision import usuarios as svc_usuarios
from app.utils import log_action

from ._core import bp, error, obtener_dispositivo_o_404

# A partir de cuántos segundos de diferencia con el servidor se avisa. Un
# minuto ya mueve checadas de entrada al minuto siguiente.
DESFASE_TOLERADO_S = 60


def desfase_segundos(hora_local: str, ahora_utc: datetime) -> int | None:
    """Segundos que el reloj del equipo va adelantado (+) o atrasado (−)."""
    try:
        equipo = datetime.fromisoformat(hora_local)
    except (TypeError, ValueError):
        return None
    if equipo.tzinfo is None:
        return None
    return round((equipo - ahora_utc).total_seconds())


def _hora_con_desfase(hora: dict) -> dict:
    desfase = desfase_segundos(hora.get('hora_local', ''), datetime.now(timezone.utc))
    return {
        **hora,
        'desfase_segundos': desfase,
        'en_hora': desfase is not None and abs(desfase) <= DESFASE_TOLERADO_S,
    }


def _registrar_error(d, e: ErrorHikvision, contexto: str):
    current_app.logger.warning('Hikvision %s(%s): %s', contexto, d.id, e.detalle)
    d.ultimo_estado = 'ERROR'
    d.ultimo_error = e.mensaje
    d.ultima_conexion = _now_utc()
    db.session.commit()
    return jsonify({'ok': False, 'error': e.mensaje}), 502


@bp.route('/dispositivos/<int:dispositivo_id>/estado', methods=['GET'])
@jwt_required
@limiter.limit('30 per minute')
def estado_equipo(dispositivo_id):
    """Todo lo que la pestaña «Equipo» necesita, en una sola ida al lector."""
    err = require_admin()
    if err:
        return err

    d = obtener_dispositivo_o_404(dispositivo_id)
    try:
        with ClienteHikvision.desde_dispositivo(d) as cli:
            info = cli.info_dispositivo()
            hora = cli.hora_dispositivo()
            capacidad = cli.capacidad()
    except ErrorHikvision as e:
        return _registrar_error(d, e, 'estado')

    # Se aprovecha la consulta para refrescar lo cacheado: si alguien
    # actualizó el firmware del equipo, la ficha lo refleja sin «Probar».
    d.modelo = info['modelo']
    d.numero_serie = info['numero_serie']
    d.firmware = info['firmware']
    d.ultimo_estado = 'OK'
    d.ultimo_error = None
    d.ultima_conexion = _now_utc()
    db.session.commit()

    return jsonify({
        'ok': True,
        'dispositivo': d.to_dict(),
        'info': info,
        'hora': _hora_con_desfase(hora),
        'capacidad': capacidad,
    })


@bp.route('/dispositivos/<int:dispositivo_id>/hora', methods=['POST'])
@jwt_required
@limiter.limit('6 per minute')
@api_transactional('No se pudo ajustar la hora del lector')
def ajustar_hora(dispositivo_id):
    """Pone el reloj del lector en la hora del servidor.

    Importa por las checadas: el equipo sella cada acceso con SU reloj, y con
    la hora mal la asistencia se registra corrida. Queda en la bitácora.
    """
    err = require_admin()
    if err:
        return err

    d = obtener_dispositivo_o_404(dispositivo_id)
    if not d.activo:
        return error('Este lector está desactivado.', 409)

    try:
        with ClienteHikvision.desde_dispositivo(d) as cli:
            antes = cli.hora_dispositivo()
            despues = cli.ajustar_hora(datetime.now(timezone.utc))
    except ErrorHikvision as e:
        return _registrar_error(d, e, 'ajustar_hora')

    d.ultimo_estado = 'OK'
    d.ultimo_error = None
    d.ultima_conexion = _now_utc()
    db.session.commit()
    log_action(
        f'Ajustó la hora del lector "{d.nombre}" ({d.host}:{d.puerto}): '
        f'{antes.get("hora_local")} → {despues.get("hora_local")}'
    )
    db.session.commit()

    return jsonify({'ok': True, 'hora': _hora_con_desfase(despues)})


@bp.route('/dispositivos/<int:dispositivo_id>/auditoria', methods=['GET'])
@jwt_required
@limiter.limit('10 per minute')
def auditoria(dispositivo_id):
    """Compara quién está en el equipo contra quién cree el ERP que está.

    Devuelve dos listas:
      · `solo_en_equipo`: usuarios del lector que el ERP no dio de alta. Abren
        la puerta y el ERP no los controla.
      · `solo_en_erp`: empleados que el ERP marca como sincronizados pero el
        equipo ya no tiene (p. ej. alguien los borró desde el lector).
    """
    err = require_admin()
    if err:
        return err

    d = obtener_dispositivo_o_404(dispositivo_id)
    try:
        with ClienteHikvision.desde_dispositivo(d) as cli:
            en_equipo = svc_usuarios.listar_todos(cli)
    except ErrorHikvision as e:
        return _registrar_error(d, e, 'auditoria')

    filas = SyncEmpleadoHikvision.query.filter_by(dispositivo_id=d.id).all()
    conocidos = {f.employee_no_remoto: f for f in filas}
    numeros_equipo = {(u.get('employeeNo') or '').strip() for u in en_equipo}

    solo_en_equipo = [
        {
            'employee_no': (u.get('employeeNo') or '').strip(),
            'nombre': u.get('name') or '',
            'rostros': int(u.get('numOfFace') or 0),
            'tarjetas': int(u.get('numOfCard') or 0),
            'huellas': int(u.get('numOfFP') or 0),
        }
        for u in en_equipo
        if (u.get('employeeNo') or '').strip() not in conocidos
    ]
    solo_en_erp = [
        {
            'trabajador_id': f.trabajador_id,
            'employee_no': f.employee_no_remoto,
            'nombre': f.trabajador.nombre_completo if f.trabajador else '',
        }
        for f in filas
        if f.estado == 'SINCRONIZADO' and f.employee_no_remoto not in numeros_equipo
    ]

    return jsonify({
        'ok': True,
        'total_en_equipo': len(en_equipo),
        'solo_en_equipo': solo_en_equipo,
        'solo_en_erp': solo_en_erp,
    })


@bp.route('/dispositivos/<int:dispositivo_id>/usuarios-equipo/<employee_no>',
          methods=['DELETE'])
@jwt_required
@limiter.limit('30 per minute')
@api_transactional('No se pudo borrar el usuario del lector')
def borrar_usuario_ajeno(dispositivo_id, employee_no):
    """Borra del equipo un usuario que el ERP NO dio de alta.

    A los empleados del ERP se les quita con «Quitar del lector», que además
    limpia su fila de estado; por eso aquí se rechazan.
    """
    err = require_admin()
    if err:
        return err

    d = obtener_dispositivo_o_404(dispositivo_id)
    employee_no = (employee_no or '').strip()
    if not employee_no or len(employee_no) > 32:
        return error('Número de empleado inválido.', 422)

    if SyncEmpleadoHikvision.query.filter_by(
            dispositivo_id=d.id, employee_no_remoto=employee_no).first():
        return error(
            'Ese usuario lo administra el ERP. Quítalo desde la lista de empleados.', 409,
        )

    try:
        with ClienteHikvision.desde_dispositivo(d) as cli:
            svc_usuarios.eliminar(cli, employee_no)
    except ErrorHikvision as e:
        return _registrar_error(d, e, 'borrar_usuario_ajeno')

    log_action(
        f'Borró del lector "{d.nombre}" ({d.host}:{d.puerto}) al usuario {employee_no}, '
        'que no había sido dado de alta desde el ERP'
    )
    db.session.commit()
    return jsonify({'ok': True})
