"""CRUD de lectores Hikvision + prueba de conexión.

Registra:
  GET    /api/hikvision/dispositivos                 listar
  POST   /api/hikvision/dispositivos                 crear
  GET    /api/hikvision/dispositivos/<id>            obtener
  PUT    /api/hikvision/dispositivos/<id>            actualizar
  DELETE /api/hikvision/dispositivos/<id>            eliminar
  POST   /api/hikvision/dispositivos/<id>/probar     probar conexión
  POST   /api/hikvision/dispositivos/<id>/foto       subir o cambiar la foto del lector
  GET    /api/hikvision/dispositivos/<id>/foto       la foto
  DELETE /api/hikvision/dispositivos/<id>/foto       quitarla

La contraseña entra por el payload pero NUNCA sale: ninguna respuesta de este
módulo la incluye, y `DispositivoHikvision.to_dict()` la omite por diseño.
"""
from __future__ import annotations

import time

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
from app.realtime import emit_to_role
from app.utils import allowed_image_file, archivos, image_to_webp, log_action

from ._core import bp, error, obtener_dispositivo_o_404

# Longitudes máximas, espejo de las columnas del modelo.
_LARGOS = {'nombre': 120, 'host': 120, 'usuario': 64, 'password': 200,
           'ubicacion': 120, 'notas': 500}

# La foto del lector solo sirve para reconocerlo en pantalla: 1024 px sobran.
FOTO_MAX_PX = 1024


def _texto_opcional(valor) -> str | None:
    valor = (valor or '').strip() if isinstance(valor, str) else ''
    return valor or None


def _resumenes() -> dict[int, dict]:
    """Cifras de cada lector para la lista, SIN consultar a los equipos.

    Empleados sincronizados, accesos permitidos de hoy (día de la oficina) y el
    último acceso. Todo sale de la base: abrir la lista no le cuesta nada a
    ningún lector.
    """
    from datetime import datetime, timezone

    from sqlalchemy import func

    from app.models import EventoHikvision, SyncEmpleadoHikvision

    from .actividad import _zona_del_lector

    sincronizados = dict(
        db.session.query(SyncEmpleadoHikvision.dispositivo_id, func.count())
        .filter(SyncEmpleadoHikvision.estado == 'SINCRONIZADO')
        .group_by(SyncEmpleadoHikvision.dispositivo_id).all()
    )
    ahora = datetime.now(timezone.utc)
    resumen = {}
    for (disp_id,) in db.session.query(DispositivoHikvision.id):
        local = ahora.astimezone(_zona_del_lector(disp_id))
        inicio_hoy = local.replace(hour=0, minute=0, second=0, microsecond=0)
        accesos_hoy = db.session.query(func.count(EventoHikvision.id)).filter(
            EventoHikvision.dispositivo_id == disp_id,
            EventoHikvision.tipo == 'permitido',
            EventoHikvision.fecha_hora >= inicio_hoy,
        ).scalar() or 0
        ultimo = EventoHikvision.query.filter_by(dispositivo_id=disp_id, tipo='permitido') \
            .order_by(EventoHikvision.fecha_hora.desc(), EventoHikvision.serial_no.desc()).first()
        resumen[disp_id] = {
            'empleados': sincronizados.get(disp_id, 0),
            'accesos_hoy': accesos_hoy,
            'ultimo_acceso': {
                'hora': ultimo.hora_local,
                'nombre': ultimo.nombre_en_equipo or ultimo.employee_no or '',
            } if ultimo else None,
        }
    return resumen


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
    resumenes = _resumenes()
    return jsonify({'items': [{**d.to_dict(), 'resumen': resumenes.get(d.id)} for d in filas]})


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
        ubicacion=_texto_opcional(datos.get('ubicacion')),
        notas=_texto_opcional(datos.get('notas')),
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
    if 'ubicacion' in datos:
        d.ubicacion = _texto_opcional(datos['ubicacion'])
    if 'notas' in datos:
        d.notas = _texto_opcional(datos['notas'])
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


# ── Foto del lector ───────────────────────────────────────────────────────────

@bp.route('/dispositivos/<int:dispositivo_id>/foto', methods=['POST'])
@jwt_required
@limiter.limit('20 per minute')
@api_transactional('No se pudo guardar la foto del lector')
def subir_foto(dispositivo_id):
    """Foto de cómo se ve el lector instalado (multipart, campo `foto`).

    Mismas reglas y mismo almacenamiento que la foto de perfil de un empleado:
    JPG/PNG reales de hasta 5 MB, convertidos a WebP. La anterior se borra
    DESPUÉS de guardar la nueva, para no quedar sin foto si algo falla.
    """
    err = require_admin()
    if err:
        return err

    d = obtener_dispositivo_o_404(dispositivo_id)
    archivo = request.files.get('foto')
    if not archivo or not archivo.filename:
        return error('No se envió ninguna fotografía.', 422)
    if not allowed_image_file(archivo):
        return error('Solo se permiten imágenes JPG o PNG reales de hasta 5 MB.', 422)

    try:
        datos = image_to_webp(archivo, max_dim=FOTO_MAX_PX).getvalue()
    except Exception:  # noqa: BLE001 — imagen corrupta que pasó la verificación de tipo
        return error('La imagen no se pudo procesar. Prueba con otra.', 422)

    anterior = d.foto
    d.foto = f'lectores/lector_{d.id}_{int(time.time())}.webp'
    archivos.guardar(d.foto, datos, 'image/webp')
    db.session.commit()
    if anterior and anterior != d.foto:
        archivos.eliminar(anterior)
    log_action(f'Cambió la foto del lector "{d.nombre}" ({d.host}:{d.puerto})')
    db.session.commit()
    emit_to_role(['admin', 'super_admin'], 'hikvision:changed', {
        'dispositivo_id': d.id, 'action': 'foto',
    })
    return jsonify(d.to_dict())


@bp.route('/dispositivos/<int:dispositivo_id>/foto', methods=['GET'])
@jwt_required
def ver_foto(dispositivo_id):
    err = require_admin()
    if err:
        return err
    d = obtener_dispositivo_o_404(dispositivo_id)
    if not d.foto:
        return error('Este lector no tiene foto.', 404)
    resp = archivos.enviar(d.foto, mimetype='image/webp')
    if resp is None:
        return error('No se encontró el archivo de la foto.', 404)
    return resp


@bp.route('/dispositivos/<int:dispositivo_id>/foto', methods=['DELETE'])
@jwt_required
@api_transactional('No se pudo quitar la foto del lector')
def quitar_foto(dispositivo_id):
    err = require_admin()
    if err:
        return err
    d = obtener_dispositivo_o_404(dispositivo_id)
    if d.foto:
        anterior, d.foto = d.foto, None
        db.session.commit()
        archivos.eliminar(anterior)
        log_action(f'Quitó la foto del lector "{d.nombre}" ({d.host}:{d.puerto})')
        db.session.commit()
    return jsonify(d.to_dict())
