"""Pipeline de imágenes → WebP → Cloudflare R2 (productos y categorías).

Flujo (solo activo cuando `r2_enabled()` — producción con credenciales):
  1. Al importar / crear / editar un producto, o al editar la imagen de una
     categoría, con `imagen_url` EXTERNA (http(s), no ya de nuestro R2), se marca
     el registro: imagen_source_url + estado PENDIENTE. `imagen_url` NO se toca
     todavía. Ver `marcar_para_sync` / `encolar_sync`.
  2. Un background task (socketio.start_background_task) procesa cada registro:
     descarga segura → magic-bytes → guarda anti-bomba → re-encode a WebP
     (destruye payloads) → sube a R2 con key por hash de contenido → apunta
     imagen_url a la URL pública de R2 → estado OK. Emite progreso al usuario.
  3. Al terminar emite `producto:changed` para que catálogo/categorías refresquen.

Productos y categorías comparten el mismo pipeline: los ítems del lote se
identifican como (tipo, id) con tipo ∈ {'producto','categoria'}. Ambos modelos
tienen las columnas imagen_source_url/r2_key/estado/error.

Endpoints:
  GET  /productos/imagenes/estado        conteos por estado (productos+categorías)
  POST /productos/imagenes/sincronizar   backfill de ambos
"""
import io
import os
import uuid
import hashlib
import logging

from flask import jsonify, request, current_app

from app.extensions import db, limiter
from app.models import Producto, CategoriaConfig
from app.realtime import socketio, emit_to_user, emit_to_role
from app.utils import image_to_webp
from app.utils import r2
from app.utils.image_fetch import descargar_imagen_segura

from ._core import bp, _require_inventario_admin, _INV_ROLES

logger = logging.getLogger(__name__)

# Cota de la imagen final (lado mayor en px). Suficiente para catálogo/galería
# y mantiene los WebP chicos.
_MAX_DIM = 1200

# Tope de registros por corrida de "Sincronizar" (anti-DoS / anti-sorpresa de
# costos). Lo que exceda queda PENDIENTE y se procesa al volver a sincronizar.
_MAX_POR_CORRIDA = int(os.environ.get('IMG_SYNC_MAX_POR_CORRIDA', '2000'))

# Registro de tipos soportados: modelo, prefijo de key en R2 y cómo mostrar su
# etiqueta en el progreso (SKU para productos, nombre para categorías).
_TIPOS = {
    'producto': {'model': Producto, 'prefijo': 'productos', 'label': lambda o: o.codigo},
    'categoria': {'model': CategoriaConfig, 'prefijo': 'categorias', 'label': lambda o: o.nombre},
}


def _es_url_externa(url: str) -> bool:
    """True si `url` es http(s) externa que conviene descargar a R2.

    Falso para: vacío, paths locales (/static/...), o URLs que ya apuntan a
    nuestro propio dominio público de R2 (ya migradas → idempotente)."""
    if not url:
        return False
    u = url.strip().lower()
    if not (u.startswith('http://') or u.startswith('https://')):
        return False
    base = r2.public_base_url().lower()
    if base and u.startswith(base):
        return False
    return True


def marcar_para_sync(obj, imagen_url: str) -> bool:
    """Si R2 está activo e `imagen_url` es externa, marca el registro para sync
    (imagen_source_url + estado PENDIENTE) y devuelve True. Genérico: sirve para
    Producto y CategoriaConfig (ambos tienen esas columnas). No commitea — el
    caller decide. Devuelve False (no-op) si R2 está apagado o la URL no aplica."""
    if not r2.r2_enabled():
        return False
    if not _es_url_externa(imagen_url):
        return False
    obj.imagen_source_url = imagen_url.strip()
    obj.imagen_estado = 'PENDIENTE'
    obj.imagen_error = None
    return True


def _norm_item(it):
    """Normaliza un ítem del lote a (tipo, id). Acepta un int suelto (se asume
    'producto', para compatibilidad con los hooks de productos) o una tupla
    (tipo, id)."""
    if isinstance(it, (tuple, list)):
        return (str(it[0]), int(it[1]))
    return ('producto', int(it))


def encolar_sync(user_id: int, items) -> str | None:
    """Lanza el background task si R2 está activo y hay ítems. `items` es una
    lista de ids (productos) o de tuplas (tipo, id). Devuelve job_id o None."""
    norm = [_norm_item(i) for i in (items or [])]
    if not r2.r2_enabled() or not norm:
        return None
    job_id = uuid.uuid4().hex[:12]
    app = current_app._get_current_object()
    socketio.start_background_task(_run_sync, app, user_id, norm, job_id)
    return job_id


def _emit_progreso(user_id, job_id, total, hechas, ok, error, actual, estado):
    emit_to_user(user_id, 'producto:imagen_progreso', {
        'job_id': job_id,
        'total': total,
        'hechas': hechas,
        'ok': ok,
        'error': error,
        'actual': actual,     # SKU/nombre en proceso (o None)
        'estado': estado,     # 'running' | 'done'
    })


def _procesar_uno(tipo: str, obj_id: int):
    """Procesa un registro (producto o categoría): descarga → WebP → R2 →
    actualiza imagen_url. Devuelve (resultado, etiqueta) con resultado en
    {'ok','error','skip'}. Maneja su propia transacción (commit por registro)."""
    cfg = _TIPOS.get(tipo)
    if not cfg:
        return 'skip', None
    Model = cfg['model']
    obj = Model.query.filter(Model.id == obj_id).first()
    if not obj:
        return 'skip', None
    label = cfg['label'](obj)
    source = (obj.imagen_source_url or '').strip()
    if not source:
        return 'skip', label

    obj.imagen_estado = 'PROCESANDO'
    obj.imagen_error = None
    db.session.commit()

    try:
        data, _mime = descargar_imagen_segura(source)
        # Re-encode a WebP: el objeto que sube a R2 es un ráster recién
        # renderizado; ningún payload embebido en el original sobrevive.
        webp = image_to_webp(io.BytesIO(data), max_dim=_MAX_DIM).getvalue()
        digest = hashlib.sha256(webp).hexdigest()
        key = f"{cfg['prefijo']}/{digest}.webp"
        if not r2.object_exists(key):
            r2.subir_webp(key, webp)

        obj.imagen_url = r2.public_url(key)
        obj.imagen_r2_key = key
        obj.imagen_estado = 'OK'
        obj.imagen_error = None
        db.session.commit()
        return 'ok', label
    except Exception as e:
        db.session.rollback()
        obj = Model.query.filter(Model.id == obj_id).first()
        if obj:
            obj.imagen_estado = 'ERROR'
            obj.imagen_error = str(e)[:300]
            db.session.commit()
            label = cfg['label'](obj)
        logger.warning('Imagen %s #%s (%s) falló: %s', tipo, obj_id, label, e)
        return 'error', label


def _run_sync(app, user_id, items, job_id):
    """Background task: procesa la lista de (tipo, id) secuencialmente emitiendo
    progreso. Secuencial a propósito: con gevent (prod) cada descarga bloqueante
    cede el control, así el worker sigue atendiendo requests."""
    with app.app_context():
        total = len(items)
        hechas = ok = error = 0
        _emit_progreso(user_id, job_id, total, 0, 0, 0, None, 'running')
        for tipo, obj_id in items:
            actual = None
            try:
                resultado, actual = _procesar_uno(tipo, obj_id)
                if resultado == 'ok':
                    ok += 1
                elif resultado == 'error':
                    error += 1
            except Exception as e:  # pragma: no cover — defensa: nunca tumbar el task
                error += 1
                logger.warning('sync imagen %s %s excepción: %s', tipo, obj_id, e)
                try:
                    db.session.rollback()
                except Exception:
                    pass
            hechas += 1
            _emit_progreso(user_id, job_id, total, hechas, ok, error, actual, 'running')

        _emit_progreso(user_id, job_id, total, hechas, ok, error, None, 'done')
        # Refresca catálogos/categorías abiertos en otras sesiones (websockets-first).
        try:
            emit_to_role(_INV_ROLES, 'producto:changed', {
                'action': 'imagenes_sync', 'ok': ok, 'error': error,
            })
        except Exception:
            pass
        db.session.remove()


# ─── Endpoints ────────────────────────────────────────────────────────────────

def _contar_estados(Model, filtro_activo=False):
    q = db.session.query(Model.imagen_estado, db.func.count(Model.id))
    if filtro_activo:
        q = q.filter(Model.activo == True)  # noqa: E712
    return dict(q.group_by(Model.imagen_estado).all())


@bp.route('/productos/imagenes/estado', methods=['GET'])
@_require_inventario_admin
def get_imagenes_estado():
    """Conteos por estado del pipeline (productos + categorías) para el SPA."""
    prod = _contar_estados(Producto, filtro_activo=True)
    cat = _contar_estados(CategoriaConfig, filtro_activo=False)

    def g(k):
        return int((prod.get(k, 0) or 0)) + int((cat.get(k, 0) or 0))

    ok, pendientes, procesando, error = g('OK'), g('PENDIENTE'), g('PROCESANDO'), g('ERROR')
    return jsonify({
        'enabled': r2.r2_enabled(),
        'ok': ok,
        'pendientes': pendientes,
        'procesando': procesando,
        'error': error,
        'total': ok + pendientes + procesando + error,
    })


def _recolectar_candidatos(Model, tipo, filtro_activo=False):
    """Selecciona registros con imagen externa aún no migrada (o PENDIENTE/ERROR),
    los marca PENDIENTE y devuelve la lista de (tipo, id) a encolar."""
    q = Model.query
    if filtro_activo:
        q = q.filter(Model.activo == True)  # noqa: E712
    q = q.filter(db.or_(
        db.and_(Model.imagen_url.isnot(None), Model.imagen_url != ''),
        Model.imagen_estado.in_(['PENDIENTE', 'ERROR']),
    ))
    items = []
    for o in q.all():
        if _es_url_externa(o.imagen_url or ''):
            o.imagen_source_url = (o.imagen_url or '').strip()
            if o.imagen_estado != 'PROCESANDO':
                o.imagen_estado = 'PENDIENTE'
                o.imagen_error = None
            items.append((tipo, o.id))
        elif o.imagen_estado in ('PENDIENTE', 'ERROR') and (o.imagen_source_url or '').strip():
            items.append((tipo, o.id))
    return items


@bp.route('/productos/imagenes/sincronizar', methods=['POST'])
@_require_inventario_admin
@limiter.limit('3 per minute')
def sincronizar_imagenes():
    """Backfill: encola productos Y categorías cuya imagen sea externa aún no
    migrada (o quedó PENDIENTE/ERROR). Devuelve job_id + cuántos encoló."""
    if not r2.r2_enabled():
        return jsonify({'detail': 'El almacenamiento R2 no está configurado en este entorno.'}), 400

    items = (
        _recolectar_candidatos(Producto, 'producto', filtro_activo=True)
        + _recolectar_candidatos(CategoriaConfig, 'categoria', filtro_activo=False)
    )
    total_candidatos = len(items)
    if total_candidatos > _MAX_POR_CORRIDA:
        items = items[:_MAX_POR_CORRIDA]
    restantes = total_candidatos - len(items)

    if total_candidatos:
        db.session.commit()

    job_id = encolar_sync(request.current_user.id, items)
    return jsonify({
        'job_id': job_id,
        'encolados': len(items),
        'restantes': restantes,
    })
