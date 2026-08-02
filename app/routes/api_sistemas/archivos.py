"""Archivos privados en R2: inventario y sincronización desde el panel de sistemas.

Es el equivalente, para los archivos privados, de lo que
`inventario_api/imagenes.py` hace con las imágenes del catálogo: ver cuánto
falta por migrar y lanzar la subida en segundo plano con progreso en vivo.

  GET  /api/sistemas/archivos              cuántos están en R2, en disco o perdidos
  POST /api/sistemas/archivos/sincronizar  sube a R2 los que siguen en disco

La diferencia con el pipeline de imágenes es que aquí NO hay columnas de estado
en la BD (ver `app/utils/archivos.py`: la key es la ruta que ya se guardaba). El
estado se calcula comparando tres conjuntos: lo que la BD referencia, lo que está
en el bucket (una sola pasada de `list_objects_v2`) y lo que queda en disco.

El backfill por línea de comandos (`scripts/migrar_archivos_a_r2.py`) usa el
mismo `keys_referenciadas()` de aquí, para que ambos caminos coincidan siempre.
"""
import os
import uuid
import logging

from flask import current_app, g, jsonify

from app.extensions import db, limiter
from app.realtime import socketio, emit_to_user
from app.routes._api_helpers import require_panel_sistemas
from app.routes.api_auth import jwt_required
from app.utils import archivos, log_action

from ._core import bp

logger = logging.getLogger(__name__)

# Tope por corrida: una tanda enorme no debe dejar al worker subiendo media hora
# sin que nadie pueda parar. Lo que exceda se sube en la siguiente pasada.
_MAX_POR_CORRIDA = int(os.environ.get('ARCHIVOS_SYNC_MAX_POR_CORRIDA', '1000'))

# Etiquetas legibles por familia, para el desglose de la UI.
_FAMILIAS = {
    'foto-usuario': 'Fotos de perfil de usuario',
    'foto-trabajador': 'Fotos de trabajador',
    'thumb-trabajador': 'Miniaturas de trabajador',
    'documento': 'Documentos de trabajador',
    'media-herramienta': 'Fotos de herramientas',
}


def keys_referenciadas():
    """Lista de (familia, key) que la BD referencia hoy, sin duplicados.

    Se recorre la BD y no el árbol de directorios a propósito: así sabemos qué
    archivos siguen vivos y cuáles son basura huérfana en disco."""
    from app.models import DocumentoTrabajador, MediaHerramienta, Trabajador, User
    from app.routes.api_trabajadores._core import thumb_key

    # `with_entities`: de cada tabla solo interesa UNA columna. Hidratar los
    # objetos ORM completos costaría memoria y tiempo proporcionales al padrón
    # entero cada vez que alguien abre el panel.
    pares = []

    for (pic,) in db.session.query(User.profile_pic).filter(
            User.profile_pic.isnot(None), User.profile_pic != 'default.png'):
        if pic:
            pares.append(('foto-usuario', pic))

    for (foto,) in db.session.query(Trabajador.foto_perfil).filter(
            Trabajador.foto_perfil.isnot(None)):
        if foto:
            pares.append(('foto-trabajador', foto))
            # La miniatura no vive en ninguna columna: se deriva del nombre.
            pares.append(('thumb-trabajador', thumb_key(foto)))

    for (ruta,) in db.session.query(DocumentoTrabajador.ruta_archivo):
        if ruta:
            pares.append(('documento', ruta))

    for (ruta,) in db.session.query(MediaHerramienta.ruta_archivo):
        if ruta:
            pares.append(('media-herramienta', ruta))

    vistas, unicas = set(), []
    for familia, key in pares:
        norm = (key or '').replace('\\', '/').strip().lstrip('/')
        if norm and norm not in vistas:
            vistas.add(norm)
            unicas.append((familia, norm))
    return unicas


def clasificar():
    """Reparte las keys referenciadas en tres cubetas comparándolas con el bucket.

    Devuelve (en_r2, pendientes, faltantes), cada una como lista de
    (familia, key):
      en_r2       ya migrado, nada que hacer
      pendientes  sigue en disco → es lo que sincroniza el botón
      faltantes   no se puede servir: ni en R2 ni en disco, o la ruta guardada
                  en la BD es inválida (traversal, unidad absoluta, vacía)

    Esta función existe para reportar sobre datos sucios, así que NO puede
    reventar por una fila sucia: `ruta_local` rechaza rutas inseguras lanzando,
    y una sola de esas tumbaría el panel entero. Se catalogan como faltantes
    —tampoco se pueden servir ni sincronizar— y salen en `detalle_faltantes`
    para que se vea cuál es la fila mala.
    """
    referenciadas = keys_referenciadas()
    en_bucket = archivos.listar_keys()

    en_r2, pendientes, faltantes = [], [], []
    for familia, key in referenciadas:
        if key in en_bucket:
            en_r2.append((familia, key))
            continue
        try:
            existe_en_disco = os.path.exists(archivos.ruta_local(key))
        except archivos.KeyInsegura:
            logger.warning('Ruta inválida en BD (%s): %r', familia, key)
            existe_en_disco = False
        if existe_en_disco:
            pendientes.append((familia, key))
        else:
            faltantes.append((familia, key))
    return en_r2, pendientes, faltantes


def _por_familia(pares):
    conteo = {}
    for familia, _key in pares:
        conteo[familia] = conteo.get(familia, 0) + 1
    return conteo


@bp.route('/archivos', methods=['GET'])
@jwt_required
@limiter.limit('20 per minute')
def estado_archivos():
    """Cuántos archivos privados están ya en R2 y cuántos siguen en disco."""
    err = require_panel_sistemas()
    if err:
        return err

    vacio = {'en_r2': 0, 'pendientes': 0, 'faltantes': 0, 'total': 0,
             'familias': [], 'detalle_faltantes': []}

    if not archivos.habilitado():
        # Sin bucket configurado no hay nada que reportar: la app guarda en disco
        # y funciona igual. La UI usa `enabled` para explicar eso en vez de
        # mostrar cero de todo como si algo estuviera roto.
        return jsonify({'enabled': False, 'error': None, **vacio})

    # Configurado pero incontactable (credencial mal escrita, bucket que no
    # existe, permisos). Se reporta como aviso accionable: si siguiéramos, todo
    # saldría como "pendiente" y sincronizar fallaría archivo por archivo.
    problema = archivos.comprobar()
    if problema:
        return jsonify({'enabled': True, 'error': problema, **vacio})

    en_r2, pendientes, faltantes = clasificar()
    c_r2, c_pend, c_falt = _por_familia(en_r2), _por_familia(pendientes), _por_familia(faltantes)

    familias = [
        {
            'clave': clave,
            'etiqueta': etiqueta,
            'en_r2': c_r2.get(clave, 0),
            'pendientes': c_pend.get(clave, 0),
            'faltantes': c_falt.get(clave, 0),
        }
        for clave, etiqueta in _FAMILIAS.items()
    ]

    return jsonify({
        'enabled': True,
        'error': None,
        'en_r2': len(en_r2),
        'pendientes': len(pendientes),
        'faltantes': len(faltantes),
        'total': len(en_r2) + len(pendientes) + len(faltantes),
        'familias': familias,
        # Los faltantes son dato roto que alguien debe mirar; se acota la lista
        # para no devolver miles de filas a la vista.
        'detalle_faltantes': [
            {'familia': _FAMILIAS.get(f, f), 'key': k} for f, k in faltantes[:50]
        ],
    })


# Candado para que dos administradores pulsando "Sincronizar" a la vez no suban
# el mismo lote dos veces. Es en Redis porque en prod hay varios workers y una
# variable de proceso no los vería. Si no hay Redis, NO se bloquea: subir es
# idempotente (misma key, mismo contenido), así que el peor caso es trabajo
# duplicado, y eso no justifica impedir la operación.
_CANDADO = 'archivos:sync:lock'
# TTL generoso: una corrida de 1000 archivos puede tardar. Si el worker muere a
# media tanda, el candado se libera solo en vez de quedarse trabado para siempre.
_CANDADO_TTL = 1800


def _tomar_candado(job_id: str) -> bool:
    from app.extensions import get_redis, redis_call
    if get_redis() is None:
        return True
    return bool(redis_call(
        lambda r: r.set(_CANDADO, job_id, nx=True, ex=_CANDADO_TTL),
        default=True,
    ))


def _soltar_candado(job_id: str) -> None:
    """Libera el candado solo si sigue siendo NUESTRO.

    Sin el chequeo de dueño, un job lento cuyo TTL ya expiró borraría el candado
    de la corrida siguiente al terminar."""
    from app.extensions import get_redis, redis_call
    if get_redis() is None:
        return
    def _borrar_si_es_mio(r):
        if r.get(_CANDADO) == job_id:
            r.delete(_CANDADO)
    redis_call(_borrar_si_es_mio)


def _emit_progreso(user_id, job_id, total, hechas, ok, error, actual, estado):
    emit_to_user(user_id, 'archivo:sync_progreso', {
        'job_id': job_id,
        'total': total,
        'hechas': hechas,
        'ok': ok,
        'error': error,
        'actual': actual,    # key en proceso (o None)
        'estado': estado,    # 'running' | 'done'
    })


def _run_sync(app, user_id, items, job_id):
    """Background task: sube a R2 los archivos pendientes, emitiendo progreso.

    Secuencial a propósito: con gevent (prod) cada subida cede el control, así el
    worker sigue atendiendo peticiones mientras corre la tanda."""
    with app.app_context():
        try:
            _subir_lote(user_id, items, job_id)
        finally:
            # En `finally`: si la tanda revienta por lo que sea, el candado no
            # puede quedarse tomado hasta que expire el TTL.
            _soltar_candado(job_id)
            db.session.remove()


def _subir_lote(user_id, items, job_id):
    """Recorre la lista subiendo archivo por archivo y emitiendo progreso."""
    total = len(items)
    hechas = ok = error = 0
    _emit_progreso(user_id, job_id, total, 0, 0, 0, None, 'running')

    for _familia, key in items:
        try:
            ruta = archivos.ruta_local(key)
            if not os.path.exists(ruta):
                # Se borró entre el listado y la subida: no es un fallo.
                error += 1
            else:
                with open(ruta, 'rb') as f:
                    datos = f.read()
                # `guardar` devuelve False si R2 falló y cayó a disco.
                if archivos.guardar(key, datos):
                    ok += 1
                else:
                    error += 1
        except Exception as e:  # pragma: no cover — defensa: nunca tumbar el task
            error += 1
            logger.warning('sync archivo %s falló: %s', key, e)

        hechas += 1
        _emit_progreso(user_id, job_id, total, hechas, ok, error, key, 'running')

    _emit_progreso(user_id, job_id, total, hechas, ok, error, None, 'done')


@bp.route('/archivos/sincronizar', methods=['POST'])
@jwt_required
@limiter.limit('3 per minute')
def sincronizar_archivos():
    """Sube a R2 los archivos privados que siguen en disco.

    No borra nada del disco: la copia local se queda como respaldo hasta que
    alguien corra `scripts/migrar_archivos_a_r2.py --borrar-local`. Subir es
    idempotente, así que repetir la operación no hace daño.
    """
    err = require_panel_sistemas()
    if err:
        return err

    if not archivos.habilitado():
        return jsonify({
            'error': 'El almacenamiento privado de R2 no está configurado en este entorno.',
        }), 400

    # Fallar aquí y no archivo por archivo: si el bucket no responde, encolar
    # 500 subidas solo produce 500 errores y un reporte inútil.
    problema = archivos.comprobar()
    if problema:
        return jsonify({'error': problema}), 400

    _en_r2, pendientes, _faltantes = clasificar()
    total_candidatos = len(pendientes)
    items = pendientes[:_MAX_POR_CORRIDA]
    restantes = total_candidatos - len(items)

    if not items:
        return jsonify({'job_id': None, 'encolados': 0, 'restantes': 0,
                        'mensaje': 'Todos los archivos ya están en R2.'})

    job_id = uuid.uuid4().hex[:12]
    # Se toma DESPUÉS de saber que hay trabajo: si no hay pendientes no tiene
    # sentido bloquear a nadie. 409 y no 400 porque no es un error del que pide:
    # la operación es válida, solo que otra igual va en camino.
    if not _tomar_candado(job_id):
        return jsonify({
            'error': 'Ya hay una sincronización en curso. Espera a que termine.',
        }), 409

    app = current_app._get_current_object()
    socketio.start_background_task(_run_sync, app, g._jwt_user.id, items, job_id)

    log_action(
        f'Panel de sistemas sincronizó {len(items)} archivo(s) privados a R2 '
        f'(ejecutado por {g._jwt_user.username})'
    )
    db.session.commit()

    return jsonify({'job_id': job_id, 'encolados': len(items), 'restantes': restantes})
