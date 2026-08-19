"""Endpoints del panel de sistemas.

  GET    /api/sistemas/estado              salud de infraestructura
  GET    /api/sistemas/peticiones          buffer de requests + agregados
  GET    /api/sistemas/sesiones            sesiones activas de TODOS los usuarios
  DELETE /api/sistemas/sesiones/<id>       revoca una sesión concreta
  GET    /api/sistemas/eventos-seguridad   vista enfocada del AuditLog

Todos exigen rol `sistemas`/`super_admin` con 2FA activo.
"""
import os
import time
from datetime import datetime, timedelta, timezone

from flask import current_app, jsonify, request

from app.extensions import db, limiter
from app.models import AuditLog, RefreshToken, User
from app.routes._api_helpers import current_user, is_super_admin, require_panel_sistemas
from app.routes.api_auth import jwt_required
from app.utils import log_action

from ._core import bp


# Momento de arranque del worker. Sirve para mostrar hace cuánto está vivo el
# proceso que atiende la petición (con varios workers, cada uno tiene el suyo).
_INICIO_PROCESO = time.time()


@bp.route('/estado', methods=['GET'])
@jwt_required
@limiter.limit('60 per minute')
def estado():
    """Salud de la infraestructura: Redis, base de datos y proceso."""
    err = require_panel_sistemas()
    if err:
        return err

    from app.extensions import get_redis

    # ── Redis ────────────────────────────────────────────────────────────────
    r = get_redis()
    redis_ok, redis_detalle = False, 'REDIS_URL no configurada'
    if r is not None:
        try:
            inicio = time.perf_counter()
            redis_ok = bool(r.ping())
            redis_detalle = f'{(time.perf_counter() - inicio) * 1000:.1f} ms'
        except Exception as e:
            redis_detalle = f'error: {type(e).__name__}'

    # ── Base de datos ────────────────────────────────────────────────────────
    # Un SELECT 1 mide latencia real de ida y vuelta sin tocar ninguna tabla.
    db_ok, db_detalle, pool = False, '', {}
    try:
        from sqlalchemy import text
        inicio = time.perf_counter()
        db.session.execute(text('SELECT 1'))
        db_ok = True
        db_detalle = f'{(time.perf_counter() - inicio) * 1000:.1f} ms'
        # El estado del pool es lo primero que se mira cuando la app "se
        # arrastra": si `en_uso` está pegado al tope, hay agotamiento.
        motor = db.engine.pool
        crudo_overflow = getattr(motor, 'overflow', lambda: None)()
        # SQLAlchemy arranca `overflow()` en -pool_size y va subiendo; un valor
        # negativo significa "no se está usando overflow". Mostrar "-9" en un
        # panel es desconcertante, así que lo normalizamos a 0 y publicamos el
        # dato que de verdad importa: cuántas conexiones más caben antes de que
        # las peticiones se queden esperando.
        overflow_en_uso = max(0, crudo_overflow) if crudo_overflow is not None else None
        tamano = getattr(motor, 'size', lambda: None)()
        pool = {
            'en_uso': getattr(motor, 'checkedout', lambda: None)(),
            'disponibles': getattr(motor, 'checkedin', lambda: None)(),
            'tamano': tamano,
            'overflow_en_uso': overflow_en_uso,
            'overflow_maximo': getattr(motor, '_max_overflow', None),
        }
    except Exception as e:
        db.session.rollback()
        db_detalle = f'error: {type(e).__name__}'

    # ── Antivirus ────────────────────────────────────────────────────────────
    # Con CLAMAV_FAIL_CLOSED=true, un clamd caído deja de aceptar documentos.
    # Sin este dato nadie se entera hasta que alguien de RRHH se queja.
    from app.utils import antivirus
    if not antivirus.habilitado():
        av = {'ok': None, 'fail_closed': False,
              'detalle': 'No configurado: los PDF se guardan sin escanear.'}
    else:
        problema = antivirus.ping()
        av = {
            'ok': problema is None,
            'fail_closed': antivirus.fail_closed(),
            'detalle': (antivirus.version() or 'clamd responde') if problema is None else problema,
        }

    defensas = [] if redis_ok else [
        'Revocación inmediata de JWT por jti (el logout no mata el token hasta su exp, ≤20 min)',
        'Lockout escalado por intentos fallidos de contraseña',
        'Lockout escalado por intentos fallidos de 2FA',
        'Anti-replay de códigos TOTP',
        'Consumo de un solo uso del stepToken de 2FA',
        'Detección de robo de refresh token (race vs replay)',
        'Registro de peticiones de este panel',
    ]

    if av['ok'] is False:
        defensas.append(
            'Escaneo antivirus de documentos — subir PDF responde 503 hasta que vuelva'
            if av['fail_closed'] else
            'Escaneo antivirus de documentos — los PDF se están aceptando SIN escanear'
        )

    return jsonify({
        # Redis es COMPARTIDO por todos los workers: este dato es global.
        'redis': {'ok': redis_ok, 'detalle': redis_detalle},
        # clamd es un demonio del host, común a todos los workers.
        'antivirus': av,
        # El pool de conexiones es POR WORKER (cada proceso tiene el suyo).
        'base_datos': {'ok': db_ok, 'detalle': db_detalle, 'pool': pool},
        'proceso': {
            'pid': os.getpid(),
            'uptime_segundos': int(time.time() - _INICIO_PROCESO),
            'entorno': os.environ.get('FLASK_ENV', 'desconocido'),
            'modo_socketio': os.environ.get('SOCKETIO_ASYNC_MODE', 'threading'),
        },
        # En producción corren 4 workers de gunicorn detrás de nginx, así que
        # `proceso` y `base_datos.pool` describen SOLO al worker que atendió
        # esta petición — el siguiente refresco puede caer en otro y mostrar
        # otro pid y otro uptime. Sin este aviso, el panel parecería estar
        # diciendo que el servidor se reinicia solo. Lo que sí es global:
        # `redis`, y por lo tanto todo el registro de peticiones (vive en
        # Redis justamente para que sea común a los 4 workers).
        'alcance': {
            'por_worker': ['proceso', 'base_datos.pool'],
            'global': ['redis', 'antivirus', 'peticiones'],
            'nota': (
                'Detrás de nginx corren varios workers de gunicorn: los datos '
                'del proceso cambian según cuál atienda cada petición.'
            ),
        },
        'defensas_degradadas': defensas,
    })


@bp.route('/peticiones', methods=['GET'])
@jwt_required
@limiter.limit('60 per minute')
def peticiones():
    """Últimas peticiones registradas + agregados por ruta.

    Los datos salen del buffer circular en Redis (ver `app/observabilidad.py`),
    nunca de la base. El tráfico sano va muestreado, así que los conteos son
    representativos, no exhaustivos — el panel lo indica explícitamente con
    `muestreo_ok`.
    """
    err = require_panel_sistemas()
    if err:
        return err

    from app.observabilidad import leer_contadores, leer_peticiones, resumen

    try:
        limite = int(request.args.get('limite') or 200)
    except (TypeError, ValueError):
        limite = 200
    try:
        dias = int(request.args.get('dias') or 7)
    except (TypeError, ValueError):
        dias = 7

    eventos = leer_peticiones(limite)

    # Resolver id → username en UNA query, no una por evento.
    ids = {e.get('uid') for e in eventos if e.get('uid')}
    nombres = {}
    if ids:
        for uid, uname in db.session.query(User.id, User.username).filter(User.id.in_(ids)).all():
            nombres[uid] = uname
    for e in eventos:
        e['usuario'] = nombres.get(e.get('uid'))

    return jsonify({
        # Muestra con detalle: el buffer circular. Va muestreado.
        'eventos': eventos,
        'resumen': resumen(eventos),
        # Métricas exactas: contadores que se incrementan en TODA petición.
        # Es lo que permite analizar de verdad — los conteos son reales, no
        # una estimación sobre la muestra.
        'contadores': leer_contadores(dias),
    })


@bp.route('/sesiones', methods=['GET'])
@jwt_required
@limiter.limit('60 per minute')
def sesiones():
    """Sesiones activas de TODOS los usuarios, con su UA/IP de origen."""
    err = require_panel_sistemas()
    if err:
        return err

    from app.routes.api_auth.tokens import _load_rt_meta

    ahora = datetime.now(timezone.utc)
    tokens = (
        RefreshToken.query
        .filter_by(revoked=False)
        .order_by(RefreshToken.created_at.desc())
        .limit(500)
        .all()
    )

    ids = {t.user_id for t in tokens}
    usuarios = {}
    if ids:
        for u in User.query.filter(User.id.in_(ids)).all():
            usuarios[u.id] = u

    salida = []
    for t in tokens:
        exp = t.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp <= ahora:
            continue
        u = usuarios.get(t.user_id)
        meta = _load_rt_meta(t.id)
        salida.append({
            'id': t.id,
            'usuario_id': t.user_id,
            'username': u.username if u else None,
            'rol': u.role if u else None,
            'creada': t.created_at.isoformat() if t.created_at else None,
            'expira': exp.isoformat(),
            'user_agent': meta.get('ua') or None,
            'ip': meta.get('ip') or None,
        })
    return jsonify(salida)


@bp.route('/sesiones/<int:session_id>', methods=['DELETE'])
@jwt_required
@limiter.limit('20 per minute')
def revocar_sesion(session_id: int):
    """Revoca una sesión concreta de cualquier usuario.

    Solo corta el refresh token: el access token en curso sigue vivo hasta su
    exp (≤20 min). Para expulsar a alguien al instante está
    `DELETE /api/users/<id>/sessions`, que además sube `password_version`.
    Se mantienen separados a propósito: cerrar UNA sesión sospechosa no debería
    tirar al usuario de todos sus dispositivos.
    """
    err = require_panel_sistemas()
    if err:
        return err

    tok = db.session.get(RefreshToken, session_id)
    if not tok:
        return jsonify({'error': 'Sesión no encontrada'}), 404

    objetivo = db.session.get(User, tok.user_id)
    # Misma anti-escalación que en api_users: la cuenta de recuperación solo la
    # toca otro super_admin.
    if (objetivo and objetivo.role == 'super_admin'
            and objetivo.id != current_user().id and not is_super_admin()):
        return jsonify({
            'error': 'Solo super_admin puede revocar sesiones de una cuenta super_admin',
        }), 403

    if not tok.revoked:
        tok.revoked = True
        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            current_app.logger.error('Error revocando sesión desde el panel: %s', e)
            return jsonify({'error': 'Error al revocar la sesión'}), 500
        log_action(
            f"Panel de sistemas revocó la sesión #{session_id} de "
            f"'{objetivo.username if objetivo else tok.user_id}'"
        )
    return jsonify({'ok': True})


# La lista vive en `app/audit_seguridad.py`: la comparte con `realtime.py`, que
# la necesita para decidir si un AuditLog recién insertado debe empujar
# `seguridad:new` al rol `sistemas`. Se reexporta con el nombre de siempre para
# no tocar a quien ya lo importa desde aquí.
from app.audit_seguridad import PATRONES_SEGURIDAD as _PATRONES_SEGURIDAD  # noqa: E402


@bp.route('/eventos-seguridad', methods=['GET'])
@jwt_required
@limiter.limit('60 per minute')
def eventos_seguridad():
    """Vista enfocada del AuditLog: solo lo relevante para seguridad.

    `?dias=N` (por defecto 7, máximo 90) y `?limite=N` (por defecto 100, máximo 500).
    """
    err = require_panel_sistemas()
    if err:
        return err

    try:
        dias = max(1, min(int(request.args.get('dias') or 7), 90))
    except (TypeError, ValueError):
        dias = 7
    try:
        limite = max(1, min(int(request.args.get('limite') or 100), 500))
    except (TypeError, ValueError):
        limite = 100

    desde = datetime.now(timezone.utc) - timedelta(days=dias)

    from sqlalchemy import or_
    filtros = [AuditLog.action.ilike(f'%{p}%') for p in _PATRONES_SEGURIDAD]
    filas = (
        AuditLog.query
        .filter(AuditLog.created_at >= desde)
        .filter(or_(*filtros))
        .order_by(AuditLog.created_at.desc())
        .limit(limite)
        .all()
    )

    return jsonify([
        {
            'id': f.id,
            'usuario': f.user,
            'accion': f.action,
            'ip': f.ip,
            'fecha': f.created_at.isoformat() if f.created_at else None,
        }
        for f in filas
    ])
