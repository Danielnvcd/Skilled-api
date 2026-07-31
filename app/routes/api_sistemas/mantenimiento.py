"""Mantenimiento: crecimiento de tablas, purga de bitácora e imágenes a R2.

  GET  /api/sistemas/almacenamiento        tamaño de las tablas que más crecen
  POST /api/sistemas/purgar-bitacora       borra AuditLog anterior a N meses
  GET  /api/sistemas/imagenes              estado del pipeline de imágenes a R2
  POST /api/sistemas/imagenes/reintentar   reencola las que quedaron en ERROR
"""
from datetime import datetime, timedelta, timezone

from flask import current_app, g, jsonify, request
from sqlalchemy import text

from app.extensions import db, limiter
from app.models import AuditLog
from app.routes._api_helpers import require_panel_sistemas
from app.routes.api_auth import jwt_required
from app.utils import log_action

from ._core import bp


# Tablas que crecen sin control: una fila por evento, sin nada que las pode.
# Son las que conviene vigilar; el resto crece con el negocio y se autolimita.
_TABLAS_VIGILADAS = (
    ('audit_log', 'Bitácora de acciones'),
    ('refresh_tokens', 'Sesiones (refresh tokens)'),
    ('notificaciones', 'Notificaciones'),
    ('movimientos_inventario', 'Movimientos de inventario'),
)


@bp.route('/almacenamiento', methods=['GET'])
@jwt_required
@limiter.limit('20 per minute')
def almacenamiento():
    """Cuánto ocupan las tablas que crecen indefinidamente.

    El tamaño en disco solo se puede pedir a PostgreSQL; en SQLite —que es lo
    que usan los tests— se devuelve el conteo de filas y `bytes: null`. Se
    degrada en vez de fallar para que la vista siga siendo útil en cualquier
    entorno.
    """
    err = require_panel_sistemas()
    if err:
        return err

    es_postgres = db.engine.dialect.name == 'postgresql'
    filas = []

    for tabla, etiqueta in _TABLAS_VIGILADAS:
        registro = {'tabla': tabla, 'etiqueta': etiqueta,
                    'filas': None, 'bytes': None, 'tamano': None}
        try:
            registro['filas'] = db.session.execute(
                text(f'SELECT COUNT(*) FROM {tabla}')  # noqa: S608 — nombre de lista fija
            ).scalar()
        except Exception:
            # La tabla puede no existir todavía en un entorno dado.
            db.session.rollback()
            filas.append(registro)
            continue

        if es_postgres:
            try:
                registro['bytes'] = db.session.execute(
                    text('SELECT pg_total_relation_size(:t)'), {'t': tabla},
                ).scalar()
                registro['tamano'] = db.session.execute(
                    text('SELECT pg_size_pretty(pg_total_relation_size(:t))'), {'t': tabla},
                ).scalar()
            except Exception:
                db.session.rollback()
        filas.append(registro)

    # Antigüedad del registro más viejo de la bitácora: es lo que dice si una
    # purga tiene sentido y cuánto liberaría.
    mas_antiguo = None
    try:
        mas_antiguo = db.session.query(db.func.min(AuditLog.created_at)).scalar()
    except Exception:
        db.session.rollback()

    return jsonify({
        'tablas': filas,
        'motor': db.engine.dialect.name,
        'tamano_disponible': es_postgres,
        'bitacora_desde': mas_antiguo.isoformat() if mas_antiguo else None,
    })


@bp.route('/purgar-bitacora', methods=['POST'])
@jwt_required
@limiter.limit('3 per hour')
def purgar_bitacora():
    """Borra entradas de bitácora anteriores a `meses`.

    Es DESTRUCTIVO e irreversible, así que:
      - Mínimo 3 meses: no se permite vaciar la bitácora reciente, que es
        justamente la que sirve para investigar un incidente.
      - Se registra en la propia bitácora ANTES de borrar, para que quede
        constancia de quién purgó y cuánto.
      - Límite de 3 por hora: una purga es un evento excepcional.
    """
    err = require_panel_sistemas()
    if err:
        return err

    data = request.get_json(silent=True) or {}
    try:
        meses = int(data.get('meses') or 12)
    except (TypeError, ValueError):
        meses = 12

    if meses < 3:
        return jsonify({
            'error': 'Por seguridad no se puede purgar la bitácora de los últimos 3 meses.',
        }), 400

    corte = datetime.now(timezone.utc) - timedelta(days=meses * 30)

    try:
        a_borrar = AuditLog.query.filter(AuditLog.created_at < corte).count()
        if not a_borrar:
            return jsonify({'ok': True, 'borrados': 0,
                            'mensaje': 'No había entradas anteriores a esa fecha.'})

        # Se deja constancia ANTES del borrado: si algo falla a mitad, el
        # intento queda registrado igual.
        log_action(
            f"Panel de sistemas purgó la bitácora anterior a {corte.date().isoformat()} "
            f"({a_borrar} entradas) — ejecutado por {g._jwt_user.username}"
        )
        db.session.commit()

        AuditLog.query.filter(AuditLog.created_at < corte).delete(synchronize_session=False)
        db.session.commit()
        return jsonify({'ok': True, 'borrados': a_borrar,
                        'corte': corte.date().isoformat()})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error('Error purgando bitácora: %s', e)
        return jsonify({'error': 'No se pudo purgar la bitácora'}), 500


@bp.route('/imagenes', methods=['GET'])
@jwt_required
@limiter.limit('20 per minute')
def imagenes():
    """Estado del pipeline de imágenes hacia Cloudflare R2.

    Las imágenes de productos y categorías se convierten a WebP y se suben a R2
    en segundo plano, con estados PENDIENTE → PROCESANDO → OK / ERROR. Los
    ERROR no se reintentan solos ni avisan a nadie: hasta ahora una imagen que
    fallaba se quedaba callada para siempre. Esta vista los saca a la luz.
    """
    err = require_panel_sistemas()
    if err:
        return err

    from app.models import CategoriaConfig, Producto

    def _contar(modelo):
        filas = (
            db.session.query(modelo.imagen_estado, db.func.count())
            .filter(modelo.imagen_estado != None)  # noqa: E711
            .group_by(modelo.imagen_estado)
            .all()
        )
        return {estado: n for estado, n in filas}

    productos = _contar(Producto)
    categorias = _contar(CategoriaConfig)

    # Detalle de los fallidos: sin esto sabes que hay 7 errores pero no cuáles.
    fallidos = []
    for p in Producto.query.filter(Producto.imagen_estado == 'ERROR').limit(50).all():
        fallidos.append({'tipo': 'producto', 'id': p.id,
                         'nombre': f'{p.codigo} — {p.descripcion}'[:120],
                         'imagen_url': p.imagen_url})
    for c in CategoriaConfig.query.filter(CategoriaConfig.imagen_estado == 'ERROR').limit(50).all():
        fallidos.append({'tipo': 'categoria', 'id': c.id,
                         'nombre': c.nombre, 'imagen_url': c.imagen_url})

    return jsonify({
        'productos': productos,
        'categorias': categorias,
        'fallidos': fallidos,
        'total_error': (productos.get('ERROR', 0) + categorias.get('ERROR', 0)),
        'total_pendiente': (productos.get('PENDIENTE', 0) + categorias.get('PENDIENTE', 0)),
    })


@bp.route('/imagenes/reintentar', methods=['POST'])
@jwt_required
@limiter.limit('10 per hour')
def reintentar_imagenes():
    """Reencola las imágenes en ERROR para que el pipeline las vuelva a intentar.

    No sube nada de forma síncrona: solo las marca como PENDIENTE y las encola,
    igual que hace el flujo normal cuando se guarda una imagen. Así una tanda
    grande no bloquea la petición ni al worker que la atiende.
    """
    err = require_panel_sistemas()
    if err:
        return err

    from app.models import CategoriaConfig, Producto
    from app.routes.inventario_api.imagenes import encolar_sync

    try:
        items = []
        for p in Producto.query.filter(Producto.imagen_estado == 'ERROR').all():
            p.imagen_estado = 'PENDIENTE'
            items.append(('producto', p.id))
        for c in CategoriaConfig.query.filter(CategoriaConfig.imagen_estado == 'ERROR').all():
            c.imagen_estado = 'PENDIENTE'
            items.append(('categoria', c.id))

        if not items:
            return jsonify({'ok': True, 'reencoladas': 0,
                            'mensaje': 'No hay imágenes en error.'})

        db.session.commit()
        encolar_sync(g._jwt_user.id, items)
        log_action(
            f'Panel de sistemas reintentó {len(items)} imágenes en error '
            f'(ejecutado por {g._jwt_user.username})'
        )
        db.session.commit()
        return jsonify({'ok': True, 'reencoladas': len(items)})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error('Error reintentando imágenes: %s', e)
        return jsonify({'error': 'No se pudieron reencolar las imágenes'}), 500
