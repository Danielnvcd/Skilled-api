"""Estado del equipo, reloj y auditoría de usuarios del lector.

Registra:
  GET    /api/hikvision/dispositivos/<id>/estado                       info + reloj + capacidad
  POST   /api/hikvision/dispositivos/<id>/hora                         poner el reloj en hora
  POST   /api/hikvision/dispositivos/<id>/ntp                          sincronizar el reloj por NTP
  GET    /api/hikvision/dispositivos/<id>/salud                        salud de la conexión
  GET    /api/hikvision/dispositivos/<id>/auditoria                    ERP vs. equipo
  DELETE /api/hikvision/dispositivos/<id>/usuarios-equipo/<emp_no>     borrar un usuario ajeno

La auditoría existe porque el lector también se puede administrar desde su
propia pantalla o desde su web: alguien puede dar de alta a una persona ahí,
fuera del ERP, y esa persona abriría la puerta sin que el ERP lo sepa.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from flask import current_app, jsonify, request
from sqlalchemy import func

from app.extensions import db, limiter
from app.models import EventoHikvision, SucesoEscuchaHikvision, SyncEmpleadoHikvision, _now_utc
from app.routes._api_helpers import api_transactional, require_admin
from app.routes.api_auth import jwt_required
from app.services.hikvision import (
    ClienteHikvision, ErrorConfiguracion, ErrorDispositivo, ErrorHikvision,
)
from app.services.hikvision import usuarios as svc_usuarios
from app.services.hikvision.vigilancia import desfase_segundos
from app.utils import log_action

from ._core import bp, error, obtener_dispositivo_o_404

# A partir de cuántos segundos de diferencia con el servidor se avisa. Un
# minuto ya mueve checadas de entrada al minuto siguiente.
DESFASE_TOLERADO_S = 60


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
            # Opcionales: un modelo sin estos endpoints no debe tumbar la pantalla.
            try:
                ntp = cli.ntp()
            except ErrorDispositivo:
                ntp = None
            try:
                red = cli.red()
            except ErrorDispositivo:
                red = None
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
        'ntp': ntp,
        'red': red,
    })


@bp.route('/dispositivos/<int:dispositivo_id>/ntp', methods=['POST'])
@jwt_required
@limiter.limit('6 per minute')
@api_transactional('No se pudo configurar el NTP del lector')
def configurar_ntp(dispositivo_id):
    """Pone el reloj del lector a sincronizarse solo por NTP.

    Body: {"servidor": "<IP o nombre>", "intervalo_min": 60}

    Mejor que «Ajustar hora»: el reloj se mantiene en hora sin intervención.
    El lector no permite probar el servidor antes (`ntpServers/test` →
    notSupport); si no sincroniza, la vigilancia del reloj de la escucha
    alerta a los admins. Queda en la bitácora.
    """
    err = require_admin()
    if err:
        return err

    d = obtener_dispositivo_o_404(dispositivo_id)
    if not d.activo:
        return error('Este lector está desactivado.', 409)

    datos = request.get_json(silent=True) or {}
    servidor = (datos.get('servidor') or '').strip()
    try:
        intervalo = int(datos.get('intervalo_min') or 60)
    except (TypeError, ValueError):
        return error('El intervalo debe ser un número de minutos.', 422)

    try:
        with ClienteHikvision.desde_dispositivo(d) as cli:
            hora = cli.configurar_ntp(servidor, intervalo)
            ntp = cli.ntp()
    except ErrorHikvision as e:
        if isinstance(e, ErrorConfiguracion):
            return error(e.mensaje, 422)
        return _registrar_error(d, e, 'configurar_ntp')

    d.ultimo_estado = 'OK'
    d.ultimo_error = None
    d.ultima_conexion = _now_utc()
    db.session.commit()
    log_action(f'Configuró NTP {servidor} cada {intervalo} min en el lector "{d.nombre}" '
               f'({d.host}:{d.puerto})')
    db.session.commit()
    return jsonify({'ok': True, 'hora': _hora_con_desfase(hora), 'ntp': ntp})


@bp.route('/dispositivos/<int:dispositivo_id>/salud', methods=['GET'])
@jwt_required
def salud(dispositivo_id):
    """Salud de la conexión ERP ↔ lector, SIN consultar al lector.

    Todo sale de la base: la bitácora de la escucha y los tiempos de los
    eventos (`recibido_en` − `fecha_hora` es cuánto tardó cada acceso en
    llegar al ERP). Se puede abrir cuantas veces se quiera.
    """
    err = require_admin()
    if err:
        return err

    d = obtener_dispositivo_o_404(dispositivo_id)
    ahora = datetime.now(timezone.utc)
    hace_24h = ahora - timedelta(hours=24)

    sucesos_24h = SucesoEscuchaHikvision.query.filter(
        SucesoEscuchaHikvision.dispositivo_id == d.id,
        SucesoEscuchaHikvision.creado_en >= hace_24h,
    ).all()
    conexiones = sum(1 for s in sucesos_24h if s.tipo == 'CONECTADO')
    desconexiones = sum(1 for s in sucesos_24h if s.tipo == 'DESCONECTADO')

    tiempos = []
    for fecha, recibido in db.session.query(EventoHikvision.fecha_hora, EventoHikvision.recibido_en).filter(
        EventoHikvision.dispositivo_id == d.id,
        EventoHikvision.recibido_en >= hace_24h,
    ):
        if fecha is None or recibido is None:
            continue
        if fecha.tzinfo is None:
            fecha = fecha.replace(tzinfo=timezone.utc)
        if recibido.tzinfo is None:
            recibido = recibido.replace(tzinfo=timezone.utc)
        tiempos.append((recibido - fecha).total_seconds())
    # En vivo = llegó en menos de 5 min; lo demás se recuperó tras una caída
    # (o viene de la carga inicial) y deformaría el promedio.
    en_vivo = sorted(t for t in tiempos if -120 <= t <= 300)
    recuperados = sum(1 for t in tiempos if t > 300)

    ultimo = db.session.query(func.max(EventoHikvision.fecha_hora)).filter(
        EventoHikvision.dispositivo_id == d.id,
    ).scalar()
    if ultimo is not None and ultimo.tzinfo is None:
        ultimo = ultimo.replace(tzinfo=timezone.utc)

    recientes = SucesoEscuchaHikvision.query.filter_by(dispositivo_id=d.id).order_by(
        SucesoEscuchaHikvision.creado_en.desc(), SucesoEscuchaHikvision.id.desc(),
    ).limit(20).all()
    ultimo_problema = next((s for s in recientes if s.tipo != 'CONECTADO'), None)

    return jsonify({
        'ok': True,
        'tiempo_real': d.estado_escucha(ahora),
        'epoca_eventos': d.epoca_eventos,
        'ultimas_24h': {
            'conexiones': conexiones,
            'desconexiones': desconexiones,
            'eventos': len(tiempos),
            'recuperados_tras_caida': recuperados,
            'retraso_mediana_s': round(en_vivo[len(en_vivo) // 2], 1) if en_vivo else None,
            'retraso_max_s': round(en_vivo[-1], 1) if en_vivo else None,
        },
        'ultimo_evento': ultimo.isoformat() if ultimo else None,
        'segundos_desde_ultimo_evento': round((ahora - ultimo).total_seconds()) if ultimo else None,
        'ultimo_problema': ultimo_problema.to_dict() if ultimo_problema else None,
        'sucesos': [s.to_dict() for s in recientes],
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
