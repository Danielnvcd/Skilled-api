"""Selección de empleados y sincronización contra el lector.

Registra:
  GET    /api/hikvision/dispositivos/<id>/empleados                    candidatos + estado
  POST   /api/hikvision/dispositivos/<id>/sincronizar                  enviar al lector
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
from app.models import SyncEmpleadoHikvision, Trabajador, _now_utc
from app.realtime import emit_to_role
from app.routes.api_trabajadores._core import _save_foto
from app.routes._api_helpers import api_transactional, require_admin
from app.routes.api_auth import jwt_required
from app.services.hikvision import ClienteHikvision, ErrorFoto, ErrorHikvision
from app.services.hikvision import usuarios as svc_usuarios
from app.utils import allowed_image_file, log_action

from ._core import (
    bp,
    candidatos_query,
    error,
    fila_candidato,
    obtener_dispositivo_o_404,
    sync_por_trabajador,
)

# Tope por llamada. Cada empleado son varias peticiones al equipo (consulta,
# alta, foto, verificación), así que una tanda enorme agotaría el timeout de
# gunicorn antes de terminar.
MAX_POR_TANDA = 50


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


def _sincronizar_uno(cli, dispositivo, trabajador, estados, caps, *,
                     foto: tuple[bytes, str] | None = None,
                     marcar_error: bool = True) -> dict:
    """Sincroniza a UN empleado. Nunca lanza: devuelve el resultado de su intento.

    Que no lance es la razón de que una foto mala de una persona no cancele la
    tanda entera de las demás.

    `foto` = (jpeg, hash) ya preparados, para enviar una foto que todavía no es
    la de perfil. `marcar_error=False` no toca la fila si falla: al probar una
    foto nueva, que el lector la rechace no significa que el registro que ya
    tenía haya dejado de funcionar.
    """
    resultado = {
        'trabajador_id': trabajador.id,
        'no_empleado': trabajador.no_empleado,
        'nombre_completo': trabajador.nombre_completo,
        'ok': False,
        'estado': 'ERROR',
        'accion': '',
        'error': '',
    }

    fila = estados.get(trabajador.id)
    try:
        employee_no = svc_usuarios.employee_no_de(trabajador, maximo=caps['employee_no_max'])

        # Se prepara la foto ANTES de tocar el equipo: si no se puede leer ni
        # convertir, no se le crea el usuario.
        jpeg, hash_foto = foto or svc_usuarios.preparar_foto(trabajador)

        accion = svc_usuarios.crear_o_actualizar(
            cli, trabajador, employee_no, nombre_max=caps['nombre_max'],
        )

        # Desde aquí el usuario YA existe en el equipo. Que la foto se haya
        # convertido bien no garantiza que el lector pueda modelar el rostro:
        # eso lo decide su motor facial, y lo hace DESPUÉS del alta. Si falla,
        # quedaría un usuario sin cara —inútil en un lector facial y confuso al
        # auditar el equipo—, así que se revierte.
        #
        # Solo se revierte lo que esta llamada creó. Si el usuario ya existía,
        # borrarlo destruiría un registro que hasta hace un momento funcionaba,
        # y una foto nueva mala no es razón para dejar a alguien fuera.
        try:
            svc_usuarios.subir_rostro(cli, employee_no, jpeg)

            # Verificación explícita contra el equipo: sin esto daríamos por
            # buena una sincronización que el lector pudo no haber completado.
            if not svc_usuarios.tiene_rostro(cli, employee_no):
                raise ErrorHikvision(
                    'El lector aceptó la fotografía pero no la registró. Inténtalo de nuevo.',
                    detalle=f'FDSearch sin coincidencias tras subir el rostro de {employee_no}',
                )
        except ErrorHikvision:
            if accion == 'creado':
                try:
                    svc_usuarios.eliminar(cli, employee_no)
                except ErrorHikvision as e_limpieza:
                    # La reversión es best-effort: si tampoco se puede borrar,
                    # se deja constancia en el log y gana el error original,
                    # que es el que explica qué hay que arreglar.
                    current_app.logger.warning(
                        'Hikvision: no se pudo revertir el alta de %s tras fallar el '
                        'rostro: %s', employee_no, e_limpieza.detalle,
                    )
            raise

        # Si el número de empleado cambió en el ERP, el lector acaba de recibir
        # un usuario NUEVO con el número nuevo y todavía tiene el viejo. Sin
        # esto quedaría una segunda identidad de la misma persona en el equipo
        # que el ERP ya no puede ver ni quitar.
        if (fila is not None and fila.estado == 'SINCRONIZADO'
                and fila.employee_no_remoto != employee_no):
            try:
                svc_usuarios.eliminar(cli, fila.employee_no_remoto)
            except ErrorHikvision as e_viejo:
                current_app.logger.warning(
                    'Hikvision: no se pudo quitar el número anterior %s de trab=%s: %s',
                    fila.employee_no_remoto, trabajador.id, e_viejo.detalle,
                )

        if fila is None:
            fila = SyncEmpleadoHikvision(
                dispositivo_id=dispositivo.id,
                trabajador_id=trabajador.id,
                employee_no_remoto=employee_no,
            )
            db.session.add(fila)
            estados[trabajador.id] = fila

        fila.employee_no_remoto = employee_no
        fila.estado = 'SINCRONIZADO'
        fila.hash_datos = svc_usuarios.huella_datos(trabajador, employee_no)
        fila.hash_foto = hash_foto
        # Con `foto` explícita la key aún no existe: la fija quien la guarda.
        fila.foto_key = None if foto else trabajador.foto_perfil
        fila.ultimo_error = None
        fila.ultimo_intento = _now_utc()
        fila.sincronizado_en = _now_utc()

        resultado.update(ok=True, estado='SINCRONIZADO', accion=accion)

    except ErrorHikvision as e:
        current_app.logger.warning(
            'Hikvision sync disp=%s trab=%s: %s', dispositivo.id, trabajador.id, e.detalle,
        )
        resultado['error'] = e.mensaje
        if not marcar_error:
            return resultado
        if fila is None:
            fila = SyncEmpleadoHikvision(
                dispositivo_id=dispositivo.id,
                trabajador_id=trabajador.id,
                employee_no_remoto=(trabajador.no_empleado or '')[:32] or '?',
            )
            db.session.add(fila)
            estados[trabajador.id] = fila
        fila.estado = 'ERROR'
        fila.ultimo_error = e.mensaje[:500]
        fila.ultimo_intento = _now_utc()

    return resultado


@bp.route('/dispositivos/<int:dispositivo_id>/sincronizar', methods=['POST'])
@jwt_required
@limiter.limit('10 per minute')
@api_transactional('No se pudo completar la sincronización')
def sincronizar(dispositivo_id):
    """Envía al lector los empleados indicados.

    Body: {"trabajador_ids": [1, 2, 3]}

    Si uno falla, los demás siguen. La respuesta trae el detalle por persona.
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
        ids = {int(i) for i in ids}
    except (TypeError, ValueError):
        return error('La lista de empleados contiene valores inválidos.', 422)

    # Se re-filtra contra la MISMA condición del listado. Un id que no salga de
    # aquí es alguien que no es de oficina, está dado de baja, o no existe —
    # da igual lo que haya mandado el cliente.
    permitidos = candidatos_query().filter(Trabajador.id.in_(ids)).all()
    encontrados = {t.id for t in permitidos}
    rechazados = [
        {
            'trabajador_id': i, 'ok': False, 'estado': 'RECHAZADO',
            'error': 'No es personal de oficina activo, o no existe.',
        }
        for i in sorted(ids - encontrados)
    ]

    # Sin fotografía no se intenta siquiera: se reporta y se sigue.
    from app.services.hikvision import fotos as svc_fotos
    con_foto = [t for t in permitidos if svc_fotos.tiene_foto(t)]
    sin_foto = [
        {
            'trabajador_id': t.id, 'no_empleado': t.no_empleado,
            'nombre_completo': t.nombre_completo, 'ok': False, 'estado': 'SIN_FOTO',
            'error': 'No tiene fotografía de perfil. Súbela en su ficha.',
        }
        for t in permitidos if not svc_fotos.tiene_foto(t)
    ]

    resultados = []
    estados = sync_por_trabajador(d.id)

    if con_foto:
        try:
            with ClienteHikvision.desde_dispositivo(d) as cli:
                caps = cli.capacidades_usuario()
                for t in con_foto:
                    resultados.append(_sincronizar_uno(cli, d, t, estados, caps))
        except ErrorHikvision as e:
            # Falla la CONEXIÓN, no un empleado: no tiene sentido seguir.
            current_app.logger.warning('Hikvision sincronizar(%s): %s', d.id, e.detalle)
            d.ultimo_estado = 'ERROR'
            d.ultimo_error = e.mensaje
            d.ultima_conexion = _now_utc()
            db.session.commit()
            return jsonify({'ok': False, 'error': e.mensaje}), 502

    resultados.extend(sin_foto)
    resultados.extend(rechazados)

    d.ultimo_estado = 'OK'
    d.ultimo_error = None
    d.ultima_conexion = _now_utc()
    db.session.commit()

    ok = sum(1 for r in resultados if r['ok'])
    fallos = len(resultados) - ok
    log_action(
        f'Sincronizó {ok} empleado(s) con el lector "{d.nombre}" '
        f'({d.host}:{d.puerto}); {fallos} sin sincronizar'
    )
    db.session.commit()
    emit_to_role(['admin', 'super_admin'], 'hikvision:changed', {
        'dispositivo_id': d.id, 'action': 'sincronizado',
    })

    return jsonify({
        'ok': fallos == 0,
        'resultados': resultados,
        'resumen': {'sincronizados': ok, 'fallidos': fallos, 'total': len(resultados)},
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
