"""Pipeline de imágenes de productos → WebP → Cloudflare R2.

Flujo (solo activo cuando `r2_enabled()` — producción con credenciales):
  1. Al importar / crear / editar un producto con `imagen_url` EXTERNA (http(s),
     no ya de nuestro R2), se marca el producto: imagen_source_url + estado
     PENDIENTE. `imagen_url` NO se toca todavía (el catálogo sigue mostrando
     algo). Ver `marcar_para_sync` / `encolar_sync`.
  2. Un background task (socketio.start_background_task) procesa cada producto:
     descarga segura → magic-bytes → re-encode a WebP (destruye payloads) →
     sube a R2 con key por hash de contenido → apunta imagen_url a la URL
     pública de R2 → estado OK. Emite progreso al usuario que lo lanzó.
  3. Al terminar emite `producto:changed` para que el catálogo (useResource con
     invalidateOn) refresque y muestre las imágenes de R2.

Endpoints:
  GET  /productos/imagenes/estado        conteos por estado (para el SPA)
  POST /productos/imagenes/sincronizar   backfill: encola todo el catálogo con
                                         imagen externa aún no migrada
"""
import io
import uuid
import hashlib
import logging

from flask import jsonify, request, current_app

from app.extensions import db, limiter
from app.models import Producto
from app.realtime import socketio, emit_to_user, emit_to_role
from app.utils import image_to_webp
from app.utils import r2
from app.utils.image_fetch import descargar_imagen_segura

from ._core import bp, _require_inventario_admin, _INV_ROLES

logger = logging.getLogger(__name__)

# Cota de la imagen final (lado mayor en px). Suficiente para catálogo/galería
# y mantiene los WebP chicos.
_MAX_DIM = 1200


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


def marcar_para_sync(prod: Producto, imagen_url: str) -> bool:
    """Si R2 está activo e `imagen_url` es externa, marca el producto para sync
    (imagen_source_url + estado PENDIENTE) y devuelve True. No commitea — el
    caller decide cuándo. Devuelve False (no-op) si R2 está apagado o la URL no
    aplica: así en local no cambia absolutamente nada."""
    if not r2.r2_enabled():
        return False
    if not _es_url_externa(imagen_url):
        return False
    prod.imagen_source_url = imagen_url.strip()
    prod.imagen_estado = 'PENDIENTE'
    prod.imagen_error = None
    return True


def encolar_sync(user_id: int, producto_ids) -> str | None:
    """Lanza el background task de sincronización si R2 está activo y hay ids.
    Devuelve el job_id (para que el SPA correlacione el progreso) o None."""
    ids = [int(i) for i in (producto_ids or [])]
    if not r2.r2_enabled() or not ids:
        return None
    job_id = uuid.uuid4().hex[:12]
    app = current_app._get_current_object()
    socketio.start_background_task(_run_sync, app, user_id, ids, job_id)
    return job_id


def _emit_progreso(user_id, job_id, total, hechas, ok, error, actual, estado):
    emit_to_user(user_id, 'producto:imagen_progreso', {
        'job_id': job_id,
        'total': total,
        'hechas': hechas,
        'ok': ok,
        'error': error,
        'actual': actual,     # SKU en proceso (o None)
        'estado': estado,     # 'running' | 'done'
    })


def _procesar_uno(producto_id: int):
    """Procesa un producto: descarga → WebP → R2 → actualiza imagen_url.
    Devuelve (resultado, sku) con resultado en {'ok','error','skip'}.
    Maneja su propia transacción (commit por producto para progreso incremental).
    """
    prod = Producto.query.filter(Producto.id == producto_id).first()
    if not prod:
        return 'skip', None
    sku = prod.codigo
    source = (prod.imagen_source_url or '').strip()
    if not source:
        return 'skip', sku

    prod.imagen_estado = 'PROCESANDO'
    prod.imagen_error = None
    db.session.commit()

    try:
        data, _mime = descargar_imagen_segura(source)
        # Re-encode a WebP: el objeto que sube a R2 es un ráster recién
        # renderizado; ningún payload embebido en el original sobrevive.
        webp = image_to_webp(io.BytesIO(data), max_dim=_MAX_DIM).getvalue()
        digest = hashlib.sha256(webp).hexdigest()
        key = f'productos/{digest}.webp'
        if not r2.object_exists(key):
            r2.subir_webp(key, webp)
        url = r2.public_url(key)

        prod.imagen_url = url
        prod.imagen_r2_key = key
        prod.imagen_estado = 'OK'
        prod.imagen_error = None
        db.session.commit()
        return 'ok', sku
    except Exception as e:
        db.session.rollback()
        # Re-cargar para marcar el error (el rollback expiró el objeto).
        prod = Producto.query.filter(Producto.id == producto_id).first()
        if prod:
            prod.imagen_estado = 'ERROR'
            prod.imagen_error = str(e)[:300]
            db.session.commit()
            sku = prod.codigo
        logger.warning('Imagen producto #%s (%s) falló: %s', producto_id, sku, e)
        return 'error', sku


def _run_sync(app, user_id, producto_ids, job_id):
    """Background task: procesa la lista secuencialmente emitiendo progreso.

    Secuencial a propósito: con gevent (prod) cada descarga bloqueante cede el
    control, así el worker sigue atendiendo requests; y evita ráfagas contra R2.
    """
    with app.app_context():
        total = len(producto_ids)
        hechas = ok = error = 0
        _emit_progreso(user_id, job_id, total, 0, 0, 0, None, 'running')
        for pid in producto_ids:
            actual_sku = None
            try:
                resultado, actual_sku = _procesar_uno(pid)
                if resultado == 'ok':
                    ok += 1
                elif resultado == 'error':
                    error += 1
            except Exception as e:  # pragma: no cover — defensa: nunca tumbar el task
                error += 1
                logger.warning('sync imagen producto %s excepción: %s', pid, e)
                try:
                    db.session.rollback()
                except Exception:
                    pass
            hechas += 1
            _emit_progreso(user_id, job_id, total, hechas, ok, error, actual_sku, 'running')

        _emit_progreso(user_id, job_id, total, hechas, ok, error, None, 'done')
        # Refresca catálogos abiertos en otras sesiones (websockets-first).
        try:
            emit_to_role(_INV_ROLES, 'producto:changed', {
                'action': 'imagenes_sync', 'ok': ok, 'error': error,
            })
        except Exception:
            pass
        db.session.remove()


# ─── Endpoints ────────────────────────────────────────────────────────────────

@bp.route('/productos/imagenes/estado', methods=['GET'])
@_require_inventario_admin
def get_imagenes_estado():
    """Conteos por estado del pipeline de imágenes (para el resumen del SPA)."""
    rows = dict(
        db.session.query(Producto.imagen_estado, db.func.count(Producto.id))
        .filter(Producto.activo == True)  # noqa: E712
        .group_by(Producto.imagen_estado)
        .all()
    )
    ok = int(rows.get('OK', 0) or 0)
    pendientes = int(rows.get('PENDIENTE', 0) or 0)
    procesando = int(rows.get('PROCESANDO', 0) or 0)
    error = int(rows.get('ERROR', 0) or 0)
    return jsonify({
        'enabled': r2.r2_enabled(),
        'ok': ok,
        'pendientes': pendientes,
        'procesando': procesando,
        'error': error,
        'total': ok + pendientes + procesando + error,
    })


@bp.route('/productos/imagenes/sincronizar', methods=['POST'])
@_require_inventario_admin
@limiter.limit('3 per minute')
def sincronizar_imagenes():
    """Backfill: encola TODO el catálogo activo cuya imagen sea externa aún no
    migrada (o quedó PENDIENTE/ERROR). Devuelve job_id + cuántos encoló para que
    el SPA muestre la barra de progreso."""
    if not r2.r2_enabled():
        return jsonify({'detail': 'El almacenamiento R2 no está configurado en este entorno.'}), 400

    candidatos = (
        Producto.query
        .filter(
            Producto.activo == True,  # noqa: E712
            db.or_(
                db.and_(Producto.imagen_url.isnot(None), Producto.imagen_url != ''),
                Producto.imagen_estado.in_(['PENDIENTE', 'ERROR']),
            ),
        )
        .all()
    )
    ids = []
    for p in candidatos:
        if _es_url_externa(p.imagen_url or ''):
            p.imagen_source_url = (p.imagen_url or '').strip()
            if p.imagen_estado != 'PROCESANDO':
                p.imagen_estado = 'PENDIENTE'
                p.imagen_error = None
            ids.append(p.id)
        elif p.imagen_estado in ('PENDIENTE', 'ERROR') and (p.imagen_source_url or '').strip():
            ids.append(p.id)

    if ids:
        db.session.commit()

    job_id = encolar_sync(request.current_user.id, ids)
    return jsonify({'job_id': job_id, 'encolados': len(ids)})
