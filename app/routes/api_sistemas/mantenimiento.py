"""Mantenimiento: crecimiento de tablas, purga por tabla e imágenes a R2.

  GET  /api/sistemas/almacenamiento        tamaño de las tablas que más crecen
  GET  /api/sistemas/purgar/previa         cuántas filas borraría (sin borrar)
  POST /api/sistemas/purgar                borra de la tabla elegida
  POST /api/sistemas/purgar-bitacora       atajo histórico (delega en el anterior)
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


# ─── Purga por tabla ──────────────────────────────────────────────────────────
#
# Cada tabla tiene su PROPIA política. Es lo que separa "purgar" de "romper":
# ninguna de estas tablas tiene claves foráneas apuntándole (verificado contra
# los metadatos de SQLAlchemy), así que borrar filas no rompe integridad
# referencial — pero el daño posible es distinto en cada una, y por eso el piso
# de antigüedad y el filtro extra también lo son.
#
#   min_meses     antigüedad mínima que SIEMPRE se conserva
#   filtro_extra  condición adicional; lo que no la cumpla nunca se borra
#   fecha         columna por la que se mide la antigüedad (¡no todas usan la misma!)

def _politicas():
    """Devuelve el registro de tablas purgables. Es una función y no una
    constante porque `filtro_extra` necesita la hora actual en cada llamada."""
    from app.models import MovimientoInventario, Notificacion, RefreshToken
    ahora = datetime.now(timezone.utc)

    return {
        'audit_log': {
            'etiqueta': 'Bitácora de acciones',
            'modelo': AuditLog,
            'fecha': AuditLog.created_at,
            'min_meses': 3,
            'filtro_extra': None,
            'nota': 'Se conservan siempre los últimos 3 meses: son los que sirven '
                    'para investigar un incidente.',
            'riesgo': 'bajo',
        },
        'refresh_tokens': {
            'etiqueta': 'Sesiones (refresh tokens)',
            'modelo': RefreshToken,
            'fecha': RefreshToken.created_at,
            'min_meses': 1,
            # Solo tokens ya muertos. Sin este filtro, purgar cerraría la sesión
            # de gente que está trabajando en ese momento.
            'filtro_extra': db.or_(RefreshToken.revoked == True,  # noqa: E712
                                   RefreshToken.expires_at < ahora),
            'nota': 'Solo se borran tokens ya revocados o vencidos. Las sesiones '
                    'activas no se tocan: nadie pierde su sesión por purgar.',
            'riesgo': 'bajo',
        },
        'notificaciones': {
            'etiqueta': 'Notificaciones',
            'modelo': Notificacion,
            'fecha': Notificacion.created_at,
            'min_meses': 1,
            # Una notificación sin leer sigue siendo un pendiente para alguien.
            'filtro_extra': Notificacion.leida == True,  # noqa: E712
            'nota': 'Solo las que ya fueron leídas. Las pendientes se conservan '
                    'aunque sean viejas.',
            'riesgo': 'bajo',
        },
        'movimientos_inventario': {
            'etiqueta': 'Movimientos de inventario',
            'modelo': MovimientoInventario,
            # OJO: esta tabla mide antigüedad por `fecha`, no por `created_at`.
            'fecha': MovimientoInventario.fecha,
            # Piso alto a propósito: el cálculo de consumo y de mínimos sugeridos
            # mira los últimos 30 días, así que 24 meses lo deja muy lejos.
            'min_meses': 24,
            'filtro_extra': None,
            'nota': 'El stock NO se recalcula sumando movimientos (vive en sus '
                    'propias tablas), así que purgar no altera existencias. Lo que '
                    'se pierde es la trazabilidad: de qué almacén salió cada pieza '
                    'y quién la movió. Suele haber obligación de conservarlo.',
            'riesgo': 'alto',
        },
    }


def _resolver(tabla, meses):
    """Valida tabla y periodo. Devuelve (politica, corte, error_response)."""
    politicas = _politicas()
    pol = politicas.get((tabla or '').strip())
    if not pol:
        return None, None, (jsonify({
            'error': 'Tabla no purgable.',
            'purgables': sorted(politicas.keys()),
        }), 400)

    try:
        meses = int(meses)
    except (TypeError, ValueError):
        return None, None, (jsonify({'error': 'El periodo debe ser un número de meses.'}), 400)

    if meses < pol['min_meses']:
        return None, None, (jsonify({
            'error': f"Por seguridad no se puede purgar «{pol['etiqueta']}» con menos de "
                     f"{pol['min_meses']} mes(es) de antigüedad. {pol['nota']}",
        }), 400)

    corte = datetime.now(timezone.utc) - timedelta(days=meses * 30)
    return pol, corte, None


def _query_borrables(pol, corte):
    q = pol['modelo'].query.filter(pol['fecha'] < corte)
    if pol['filtro_extra'] is not None:
        q = q.filter(pol['filtro_extra'])
    return q


@bp.route('/purgar/previa', methods=['GET'])
@jwt_required
@limiter.limit('30 per minute')
def purgar_previa():
    """Cuántas filas borraría la purga, SIN borrar nada.

    Existe para que nadie confirme a ciegas: la UI enseña el número exacto antes
    de que el usuario apriete el botón. `?tabla=` sin `?meses=` devuelve solo el
    catálogo de tablas purgables con su política.
    """
    err = require_panel_sistemas()
    if err:
        return err

    politicas = _politicas()
    tabla = (request.args.get('tabla') or '').strip()

    if not tabla:
        return jsonify({'tablas': [
            {'tabla': k, 'etiqueta': v['etiqueta'], 'min_meses': v['min_meses'],
             'nota': v['nota'], 'riesgo': v['riesgo']}
            for k, v in politicas.items()
        ]})

    pol, corte, error = _resolver(tabla, request.args.get('meses', type=int) or 0)
    if error:
        return error

    try:
        borrables = _query_borrables(pol, corte).count()
        total = pol['modelo'].query.count()
    except Exception as e:
        db.session.rollback()
        current_app.logger.error('Error en previa de purga (%s): %s', tabla, e)
        return jsonify({'error': 'No se pudo calcular la previa'}), 500

    return jsonify({
        'tabla': tabla,
        'etiqueta': pol['etiqueta'],
        'borrables': borrables,
        'total': total,
        'conservadas': total - borrables,
        'corte': corte.date().isoformat(),
        'min_meses': pol['min_meses'],
        'nota': pol['nota'],
        'riesgo': pol['riesgo'],
    })


@bp.route('/purgar', methods=['POST'])
@jwt_required
@limiter.limit('3 per hour')
def purgar_tabla():
    """Borra de la tabla elegida las filas anteriores a `meses`.

    Es DESTRUCTIVO e irreversible, así que:
      - Cada tabla tiene su piso de antigüedad y su filtro (ver `_politicas`).
      - Se registra en la bitácora ANTES de borrar: si algo falla a mitad, el
        intento queda constando igual.
      - Límite de 3 por hora: una purga es un evento excepcional.
    """
    err = require_panel_sistemas()
    if err:
        return err

    data = request.get_json(silent=True) or {}
    pol, corte, error = _resolver(data.get('tabla'), data.get('meses'))
    if error:
        return error

    try:
        a_borrar = _query_borrables(pol, corte).count()
        if not a_borrar:
            return jsonify({'ok': True, 'borrados': 0,
                            'mensaje': 'No había filas que cumplieran esos criterios.'})

        log_action(
            f"Panel de sistemas purgó «{pol['etiqueta']}» anterior a "
            f"{corte.date().isoformat()} ({a_borrar} filas) — "
            f"ejecutado por {g._jwt_user.username}"
        )
        db.session.commit()

        _query_borrables(pol, corte).delete(synchronize_session=False)
        db.session.commit()
        return jsonify({'ok': True, 'borrados': a_borrar,
                        'tabla': data.get('tabla'), 'etiqueta': pol['etiqueta'],
                        'corte': corte.date().isoformat()})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error('Error purgando %s: %s', data.get('tabla'), e)
        return jsonify({'error': 'No se pudo completar la purga'}), 500


@bp.route('/purgar-bitacora', methods=['POST'])
@jwt_required
@limiter.limit('3 per hour')
def purgar_bitacora():
    """Atajo histórico: purga la bitácora. Delega en `/purgar` para que exista
    UNA sola implementación y las reglas no puedan divergir."""
    err = require_panel_sistemas()
    if err:
        return err

    data = request.get_json(silent=True) or {}
    pol, corte, error = _resolver('audit_log', data.get('meses') or 12)
    if error:
        return error

    try:
        a_borrar = _query_borrables(pol, corte).count()
        if not a_borrar:
            return jsonify({'ok': True, 'borrados': 0,
                            'mensaje': 'No había entradas anteriores a esa fecha.'})

        log_action(
            f"Panel de sistemas purgó la bitácora anterior a {corte.date().isoformat()} "
            f"({a_borrar} entradas) — ejecutado por {g._jwt_user.username}"
        )
        db.session.commit()

        _query_borrables(pol, corte).delete(synchronize_session=False)
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
