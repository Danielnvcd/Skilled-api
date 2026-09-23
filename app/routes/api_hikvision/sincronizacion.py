"""Selección de empleados y sincronización contra el lector.

Registra:
  GET    /api/hikvision/dispositivos/<id>/empleados                    candidatos + estado
  POST   /api/hikvision/dispositivos/<id>/sincronizar                  enviar al lector
  GET    /api/hikvision/tareas/<id>                                    tarea en segundo plano
  GET    /api/hikvision/dispositivos/<id>/tareas                       tareas activas
  DELETE /api/hikvision/dispositivos/<id>/empleados/<trab_id>          quitar del lector
  POST   /api/hikvision/dispositivos/<id>/empleados/<trab_id>/foto     cambiar foto y enviarla
  GET    /api/hikvision/dispositivos/<id>/empleados/<trab_id>/rostro   rostro que tiene el lector

Dos reglas que se aplican AQUÍ, en el servidor, y no solo en la interfaz:

  1. Solo empleados con `es_oficina = true`, activos y sin fecha de baja.
  2. Solo empleados con fotografía utilizable.

Una petición hecha a mano con curl salta el frontend pero no esto. La segunda
regla se comprueba dos veces: al filtrar la lista y, ya dentro del bucle, al
leer y convertir la imagen de verdad — porque la columna puede tener una ruta
cuyo archivo ya no exista.
"""
from __future__ import annotations

import hashlib

from flask import Response, current_app, jsonify, request

from app.extensions import db, limiter
from app.models import SyncEmpleadoHikvision, TareaHikvision, Trabajador, _now_utc
from app.realtime import emit_to_role
from app.routes.api_trabajadores._core import _save_foto
from app.routes._api_helpers import api_transactional, current_user, require_admin
from app.routes.api_auth import jwt_required
from app.services.hikvision import ClienteHikvision, ErrorFoto, ErrorHikvision
from app.services.hikvision import tareas as svc_tareas
from app.services.hikvision import usuarios as svc_usuarios
from app.services.hikvision.sincronizar import sincronizar_lote, sincronizar_uno
from app.utils import allowed_image_file, log_action

from ._core import (
    bp,
    candidatos_query,
    error,
    fila_candidato,
    obtener_dispositivo_o_404,
    sync_por_trabajador,
)

# Tope por llamada. Las tandas grandes ya no corren dentro de la petición (ver
# `sincronizar`), así que el límite solo acota el tamaño de una tarea.
MAX_POR_TANDA = 500
# Hasta cuántos empleados se sincroniza en la misma petición. ~3.6 s cada uno
# contra el equipo real: 5 caben con holgura en el límite de gunicorn.
SINCRONO_MAX = 5

# La cambiar_foto de abajo reutiliza la sincronización de UN empleado.
_sincronizar_uno = sincronizar_uno


@bp.route('/dispositivos/<int:dispositivo_id>/empleados', methods=['GET'])
@jwt_required
def listar_empleados(dispositivo_id):
    """Empleados de oficina con su estado en ESTE lector."""
    err = require_admin()
    if err:
        return err

    d = obtener_dispositivo_o_404(dispositivo_id)
    estados = sync_por_trabajador(d.id)
    candidatos = candidatos_query().all()
    filas = [fila_candidato(t, estados.get(t.id)) for t in candidatos]

    return jsonify({
        'dispositivo': d.to_dict(),
        'items': filas,
        'resumen': {
            'total': len(filas),
            'con_foto': sum(1 for f in filas if f['tiene_foto']),
            'sin_foto': sum(1 for f in filas if not f['tiene_foto']),
            # Un desactualizado SÍ está en el lector (con datos viejos), así
            # que cuenta como sincronizado además de tener su propio total.
            'sincronizados': sum(
                1 for f in filas if f['estado'] in ('SINCRONIZADO', 'DESACTUALIZADO')
            ),
            'desactualizados': sum(1 for f in filas if f['estado'] == 'DESACTUALIZADO'),
            'con_error': sum(1 for f in filas if f['estado'] == 'ERROR'),
        },
    })


@bp.route('/dispositivos/<int:dispositivo_id>/sincronizar', methods=['POST'])
@jwt_required
@limiter.limit('10 per minute')
@api_transactional('No se pudo completar la sincronización')
def sincronizar(dispositivo_id):
    """Envía al lector los empleados indicados.

    Body: {"trabajador_ids": [1, 2, 3]}

    Hasta `SINCRONO_MAX` empleados se sincroniza aquí mismo y la respuesta
    (200) trae el detalle por persona. Más que eso no cabe en el límite de
    gunicorn (~3.6 s por persona contra el equipo real): se crea una tarea que
    ejecuta el proceso de escucha y se responde 202 con ella; el avance llega
    por Socket.IO (`hikvision:tarea`) y el detalle con GET /tareas/<id>.
    """
    err = require_admin()
    if err:
        return err

    d = obtener_dispositivo_o_404(dispositivo_id)
    if not d.activo:
        return error('Este lector está desactivado. Actívalo antes de sincronizar.', 409)

    datos = request.get_json(silent=True) or {}
    ids = datos.get('trabajador_ids')
    if not isinstance(ids, list) or not ids:
        return error('Debes indicar al menos un empleado.', 422)
    if len(ids) > MAX_POR_TANDA:
        return error(
            f'Máximo {MAX_POR_TANDA} empleados por sincronización. '
            'Divide la selección en varias tandas.', 422,
        )
    try:
        ids = sorted({int(i) for i in ids})
    except (TypeError, ValueError):
        return error('La lista de empleados contiene valores inválidos.', 422)

    if len(ids) > SINCRONO_MAX:
        tarea = svc_tareas.encolar(d, ids, creado_por_id=current_user().id)
        db.session.commit()
        svc_tareas.avisar(tarea)
        return jsonify({'ok': True, 'tarea': tarea.to_dict()}), 202

    try:
        respuesta = sincronizar_lote(d, ids)
    except ErrorHikvision as e:
        # Falla la CONEXIÓN, no un empleado: no tiene sentido seguir.
        current_app.logger.warning('Hikvision sincronizar(%s): %s', d.id, e.detalle)
        db.session.rollback()
        d.ultimo_estado = 'ERROR'
        d.ultimo_error = e.mensaje
        d.ultima_conexion = _now_utc()
        db.session.commit()
        return jsonify({'ok': False, 'error': e.mensaje}), 502

    d.ultimo_estado = 'OK'
    d.ultimo_error = None
    d.ultima_conexion = _now_utc()
    db.session.commit()

    resumen = respuesta['resumen']
    log_action(
        f'Sincronizó {resumen["sincronizados"]} empleado(s) con el lector "{d.nombre}" '
        f'({d.host}:{d.puerto}); {resumen["fallidos"]} sin sincronizar'
    )
    db.session.commit()
    emit_to_role(['admin', 'super_admin'], 'hikvision:changed', {
        'dispositivo_id': d.id, 'action': 'sincronizado',
    })
    return jsonify(respuesta)


@bp.route('/tareas/<int:tarea_id>', methods=['GET'])
@jwt_required
def obtener_tarea(tarea_id):
    """Estado de una sincronización en segundo plano, con el detalle al terminar."""
    err = require_admin()
    if err:
        return err
    tarea = db.get_or_404(TareaHikvision, tarea_id)
    return jsonify(tarea.to_dict(con_resultados=tarea.estado in ('TERMINADA', 'ERROR')))


@bp.route('/dispositivos/<int:dispositivo_id>/tareas', methods=['GET'])
@jwt_required
def listar_tareas(dispositivo_id):
    """Tareas en curso o en cola del lector (para retomar el progreso al recargar)."""
    err = require_admin()
    if err:
        return err
    d = obtener_dispositivo_o_404(dispositivo_id)
    activas = TareaHikvision.query.filter(
        TareaHikvision.dispositivo_id == d.id,
        TareaHikvision.estado.in_(['PENDIENTE', 'EN_CURSO']),
    ).order_by(TareaHikvision.id).all()
    return jsonify({
        'items': [t.to_dict() for t in activas],
        # Si la escucha no corre, las tareas no avanzan: la pantalla lo dice.
        'trabajador_activo': d.estado_escucha()['en_vivo'],
    })


@bp.route('/dispositivos/<int:dispositivo_id>/empleados/<int:trabajador_id>', methods=['DELETE'])
@jwt_required
@limiter.limit('30 per minute')
@api_transactional('No se pudo quitar al empleado del lector')
def quitar_empleado(dispositivo_id, trabajador_id):
    """Borra al empleado del equipo y elimina su fila de estado.

    Se usa `employee_no_remoto` —el número que REALMENTE se envió— y no el
    actual del ERP: si alguien cambió el número de empleado después de
    sincronizar, el que conoce el lector sigue siendo el viejo.
    """
    err = require_admin()
    if err:
        return err

    d = obtener_dispositivo_o_404(dispositivo_id)
    fila = SyncEmpleadoHikvision.query.filter_by(
        dispositivo_id=d.id, trabajador_id=trabajador_id,
    ).first()
    if fila is None:
        return error('Ese empleado no está sincronizado con este lector.', 404)

    try:
        with ClienteHikvision.desde_dispositivo(d) as cli:
            svc_usuarios.eliminar(cli, fila.employee_no_remoto)
    except ErrorHikvision as e:
        current_app.logger.warning('Hikvision quitar(%s/%s): %s', d.id, trabajador_id, e.detalle)
        # A propósito NO se borra la fila si el equipo no confirmó: dejarla
        # marcada como error es lo único que permite reintentar. Borrarla haría
        # creer que el empleado ya no está en el lector cuando sí sigue.
        fila.estado = 'ERROR'
        fila.ultimo_error = e.mensaje[:500]
        fila.ultimo_intento = _now_utc()
        db.session.commit()
        return jsonify({'ok': False, 'error': e.mensaje}), 502

    db.session.delete(fila)
    db.session.commit()
    log_action(
        f'Quitó al empleado {fila.employee_no_remoto} del lector "{d.nombre}" '
        f'({d.host}:{d.puerto})'
    )
    db.session.commit()
    return jsonify({'ok': True})


def _candidato(trabajador_id: int) -> Trabajador | None:
    """El trabajador si es candidato (de oficina, activo, sin baja); si no, None."""
    return candidatos_query().filter(Trabajador.id == trabajador_id).first()


@bp.route('/dispositivos/<int:dispositivo_id>/empleados/<int:trabajador_id>/foto',
          methods=['POST'])
@jwt_required
@limiter.limit('10 per minute')
@api_transactional('No se pudo cambiar la fotografía')
def cambiar_foto(dispositivo_id, trabajador_id):
    """Cambia la foto del empleado y la envía al lector en un solo paso.

    Multipart, campo `foto` (JPG/PNG, mismas reglas que la ficha del empleado).

    El orden es a propósito: PRIMERO se prueba la foto contra el lector y solo
    si su motor facial la acepta se guarda como foto de perfil en el ERP. Al
    revés, una foto que el lector no puede modelar quedaría como foto de perfil
    y el empleado aparecería desactualizado sin forma de arreglarlo. Si el
    lector la rechaza no cambia nada: ni el ERP ni el rostro que ya tenía.
    """
    err = require_admin()
    if err:
        return err

    d = obtener_dispositivo_o_404(dispositivo_id)
    if not d.activo:
        return error('Este lector está desactivado. Actívalo antes de sincronizar.', 409)

    t = _candidato(trabajador_id)
    if t is None:
        return error('No es personal de oficina activo, o no existe.', 404)

    archivo = request.files.get('foto')
    if not archivo or not archivo.filename:
        return error('No se envió ninguna fotografía.', 422)
    # Se valida ANTES de tocar el equipo: si `_save_foto` la rechazara después,
    # el lector tendría una cara que el ERP no guardó.
    if not allowed_image_file(archivo):
        return error('Solo se permiten imágenes JPG o PNG reales de hasta 5 MB.', 422)

    datos = archivo.read()
    archivo.seek(0)
    try:
        foto = svc_usuarios.preparar_foto(t, datos)
    except ErrorFoto as e:
        return error(e.mensaje, 422)

    estados = sync_por_trabajador(d.id)
    try:
        with ClienteHikvision.desde_dispositivo(d) as cli:
            caps = cli.capacidades_usuario()
            resultado = _sincronizar_uno(
                cli, d, t, estados, caps, foto=foto, marcar_error=False,
            )
    except ErrorHikvision as e:
        current_app.logger.warning('Hikvision cambiar_foto(%s/%s): %s', d.id, t.id, e.detalle)
        return jsonify({'ok': False, 'error': e.mensaje}), 502

    if not resultado['ok']:
        return jsonify({'ok': False, 'error': resultado['error']}), 422

    _save_foto(t, archivo)
    fila = estados[t.id]
    fila.foto_key = t.foto_perfil
    d.ultimo_estado = 'OK'
    d.ultimo_error = None
    d.ultima_conexion = _now_utc()
    db.session.commit()

    log_action(
        f'Cambió la fotografía de {t.nombre_completo} ({t.no_empleado}) y la envió al '
        f'lector "{d.nombre}" ({d.host}:{d.puerto})'
    )
    db.session.commit()
    emit_to_role(['admin', 'super_admin'], 'hikvision:changed', {
        'dispositivo_id': d.id, 'action': 'foto',
    })
    emit_to_role(['admin', 'super_admin'], 'empleado:changed', {
        'id': t.id, 'action': 'foto',
    })

    return jsonify({
        'ok': True,
        'accion': resultado['accion'],
        'empleado': fila_candidato(t, fila),
    })


@bp.route('/dispositivos/<int:dispositivo_id>/empleados/<int:trabajador_id>/rostro',
          methods=['GET'])
@jwt_required
@limiter.limit('60 per minute')
def rostro_en_lector(dispositivo_id, trabajador_id):
    """El rostro que el lector tiene registrado para este empleado (JPEG).

    Es la cara con la que el equipo compara de verdad. Sirve para ver de un
    vistazo si coincide con la foto de perfil del ERP.
    """
    err = require_admin()
    if err:
        return err

    d = obtener_dispositivo_o_404(dispositivo_id)
    fila = SyncEmpleadoHikvision.query.filter_by(
        dispositivo_id=d.id, trabajador_id=trabajador_id,
    ).first()
    if fila is None:
        return error('Ese empleado no está en este lector.', 404)

    try:
        with ClienteHikvision.desde_dispositivo(d) as cli:
            jpeg = svc_usuarios.leer_rostro(cli, fila.employee_no_remoto)
    except ErrorHikvision as e:
        current_app.logger.warning('Hikvision rostro(%s/%s): %s', d.id, trabajador_id, e.detalle)
        return jsonify({'ok': False, 'error': e.mensaje}), 502

    if not jpeg:
        return error('El lector no tiene rostro registrado para este empleado.', 404)

    resp = Response(jpeg, mimetype='image/jpeg')
    resp.headers['ETag'] = hashlib.sha256(jpeg).hexdigest()[:16]
    return resp
