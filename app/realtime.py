"""Capa de tiempo real (Socket.IO) para el SPA.

Reemplaza el polling cada 60s de la campana de notificaciones por push.
Pensado para coexistir con Flask-JWT (igual que el resto de la API).

Diseño:
- `async_mode='gevent'` en prod — el worker `geventwebsocket` de Gunicorn
  hace el upgrade HTTP→WS real. En dev (sin SOCKETIO_ASYNC_MODE setado)
  cae a 'threading' y usa Werkzeug, lo cual basta para iterar local.
- `message_queue=REDIS_URL` cuando hay Redis — permite que cualquier worker
  emita un evento a la sala del usuario aunque la conexión WS viva en otro
  worker. Sin Redis (entornos de test) cae a memoria local (single worker).
- Autenticación en el handshake: el cliente debe mandar `auth.token` con el
  JWT que ya usa para REST. Si el token no es válido, se rechaza la conexión.
- Cada usuario se une a la sala `user:{id}`. Los emit() target esa sala.

En producción (WebSocket sobre Cloudflare Tunnel + nginx) hay que validar:
  - nginx: proxy_http_version 1.1; proxy_set_header Upgrade $http_upgrade;
           proxy_set_header Connection "upgrade"; (ya en nginx.config)
  - Cloudflare: WebSocket habilitado en el panel (lo está por default en
    planes pagos; en Free está OK para nuestro tráfico).
  - gunicorn debe usar geventwebsocket worker (no gthread, no eventlet).
    eventlet rompe psycopg3; gthread no soporta el upgrade WS.
"""
from __future__ import annotations

import logging
import os

from flask import request
from flask_socketio import SocketIO, join_room, disconnect
from socketio.exceptions import ConnectionRefusedError as SocketIOConnectionRefusedError

socketio = SocketIO()

_logger = logging.getLogger(__name__)


def init_socketio(app) -> SocketIO:
    """Inicializa SocketIO sobre la app Flask. Se llama desde create_app()."""
    cors_origins = [
        o.strip()
        for o in os.environ.get(
            'CORS_ORIGINS', 'http://localhost:5173,http://127.0.0.1:5173'
        ).split(',')
        if o.strip()
    ]

    redis_url = os.environ.get('REDIS_URL')
    message_queue = redis_url if redis_url and not redis_url.startswith('memory://') else None

    # Dev usa `threading` (Werkzeug `socketio.run`). Prod usa gunicorn con
    # `--worker-class geventwebsocket...` + SOCKETIO_ASYNC_MODE=gevent (ver
    # gunicorn.serviceee). El monkey-patching lo hace run.py al boot.
    async_mode = os.environ.get('SOCKETIO_ASYNC_MODE', 'threading')

    socketio.init_app(
        app,
        cors_allowed_origins=cors_origins,
        async_mode=async_mode,
        message_queue=message_queue,
        # Path por defecto /socket.io/ — el cliente debe usar el mismo.
        # ping_interval bajo + ping_timeout más alto detecta caídas sin chatear de más.
        ping_interval=25,
        ping_timeout=60,
        # No copiamos la Flask session al ctx del handler. La auth va por JWT
        # en `auth.token` del handshake — la session de cookies del SPA no
        # aplica. Además, Flask 3.1+ hizo `RequestContext.session` read-only,
        # rompiendo el copiado automático de Flask-SocketIO 5.4.x.
        manage_session=False,
        # Logs ruidosos solo en debug; en prod los manda al logger normal.
        logger=False,
        engineio_logger=False,
    )
    _register_handlers()
    _register_notif_emit_hook()
    _register_reporte_estado_emit_hook()
    _register_registros_emit_hook()
    _register_audit_emit_hook()
    _register_abono_emit_hook()
    return socketio


def _register_notif_emit_hook() -> None:
    """Engancha `after_insert` / `after_commit` para emitir notif:new.

    En `after_insert` ya tenemos el id (autoincrement está poblado) y el resto
    de columnas del INSERT. Guardamos un snapshot en `session.info` y lo
    emitimos en `after_commit` — así, si la transacción se hace rollback, el
    cliente nunca ve una notif fantasma.

    No re-consultamos la BD en `after_commit` porque la sesión ya está en
    estado committed y nuevas queries lanzarían un InvalidRequestError. El
    snapshot trae todo lo que el frontend necesita; el `no_leidas` se deja
    en `null` para que el SPA incremente su propio contador (cheap +1).
    """
    from sqlalchemy import event
    from app.extensions import db
    from app.models import Notificacion
    from app.routes.notificaciones import _tiempo_relativo

    @event.listens_for(Notificacion, 'after_insert')
    def _stash_notif(mapper, connection, target):  # noqa: ARG001
        session = db.session
        try:
            bucket = session.info.setdefault('_pending_notif_emits', [])
            bucket.append({
                'id': target.id,
                'usuario_id': target.usuario_id,
                'tipo': target.tipo,
                'titulo': target.titulo,
                'mensaje': target.mensaje,
                'url': target.url,
                'leida': bool(target.leida),
                'created_at': target.created_at,
            })
        except Exception:
            pass

    @event.listens_for(db.session, 'after_commit')
    def _flush_notif_emits(session):
        bucket = session.info.pop('_pending_notif_emits', None)
        if not bucket:
            return
        for snap in bucket:
            try:
                created = snap.get('created_at')
                emit_to_user(snap['usuario_id'], 'notif:new', {
                    'notif': {
                        'id': snap['id'],
                        'tipo': snap['tipo'],
                        'titulo': snap['titulo'],
                        'mensaje': snap['mensaje'],
                        'url': snap['url'] or '',
                        'leida': snap['leida'],
                        'tiempo': _tiempo_relativo(created),
                        'created_at': created.isoformat() if created else None,
                    },
                    'no_leidas': None,
                })
            except Exception as e:  # pragma: no cover
                _logger.warning('emit notif post-commit falló id=%s err=%s', snap.get('id'), e)

    @event.listens_for(db.session, 'after_rollback')
    def _clear_notif_emits(session):
        session.info.pop('_pending_notif_emits', None)


def _register_reporte_estado_emit_hook() -> None:
    """Emite `reporte:estado_cambio` a la sala `reporte:{id}` cuando cambia el
    estado de un ReporteSemanal.

    Misma forma que el hook de Notificacion: snapshot en `after_update` (antes
    de que la sesión se commitee — get_history sigue viendo el delta), flush en
    `after_commit`. Así un rollback no fantasmea el evento al kiosko.

    Cubre cualquier ruta que mute `r.estado` (api_horas.cerrar_reporte,
    api_prenomina, las versiones HTML, futuras). El kiosko se subscribe con
    `join:reporte` por cada reporte local que tiene en BORRADOR.
    """
    from sqlalchemy import event, inspect
    from app.extensions import db
    from app.models import ReporteSemanal

    @event.listens_for(ReporteSemanal, 'after_update')
    def _stash_reporte_estado(mapper, connection, target):  # noqa: ARG001
        try:
            hist = inspect(target).attrs.estado.history
            if not hist.has_changes():
                return
            session = db.session
            bucket = session.info.setdefault('_pending_reporte_estado_emits', [])
            bucket.append({
                'reporte_id': target.id,
                'estado': target.estado,
            })
        except Exception:
            pass

    @event.listens_for(db.session, 'after_commit')
    def _flush_reporte_estado_emits(session):
        bucket = session.info.pop('_pending_reporte_estado_emits', None)
        if not bucket:
            return
        for snap in bucket:
            try:
                emit_to_reporte(snap['reporte_id'], 'reporte:estado_cambio', snap)
            except Exception as e:  # pragma: no cover
                _logger.warning(
                    'emit reporte:estado_cambio post-commit falló id=%s err=%s',
                    snap.get('reporte_id'), e,
                )

    @event.listens_for(db.session, 'after_rollback')
    def _clear_reporte_estado_emits(session):
        session.info.pop('_pending_reporte_estado_emits', None)


def _register_registros_emit_hook() -> None:
    """Emite `reporte:registros_cambio` cuando cambia un RegistroDiarioHoras.

    Reemplaza el polling cada 30s del Grid del kiosko (`Grid.tsx`) que pegaba
    `/api/horas/reportes/{id}` y `/trabajadores-reporte/{id}` para enterarse
    de cambios hechos desde el SPA. Ahora el server avisa por push.

    Coalesce: si una transacción modifica N registros del mismo reporte, se
    emite UN solo evento por reporte (no N). El payload trae los
    `client_record_ids` afectados para que el kiosko pueda suprimir el
    re-pull cuando los cambios fueron originados por él mismo (el client
    record id viene del propio kiosko cuando empuja capturas RFID).
    """
    from sqlalchemy import event
    from app.extensions import db
    from app.models import RegistroDiarioHoras

    def _stash(target):
        try:
            bucket = db.session.info.setdefault('_pending_reg_emits', [])
            bucket.append((target.reporte_id, target.client_record_id))
        except Exception:
            pass

    @event.listens_for(RegistroDiarioHoras, 'after_insert')
    def _on_reg_insert(mapper, connection, target):  # noqa: ARG001
        _stash(target)

    @event.listens_for(RegistroDiarioHoras, 'after_update')
    def _on_reg_update(mapper, connection, target):  # noqa: ARG001
        _stash(target)

    @event.listens_for(RegistroDiarioHoras, 'after_delete')
    def _on_reg_delete(mapper, connection, target):  # noqa: ARG001
        _stash(target)

    @event.listens_for(db.session, 'after_commit')
    def _flush_reg_emits(session):
        bucket = session.info.pop('_pending_reg_emits', None)
        if not bucket:
            return
        by_reporte: dict[int, set[str]] = {}
        for reporte_id, crid in bucket:
            if not reporte_id:
                continue
            by_reporte.setdefault(int(reporte_id), set())
            if crid:
                by_reporte[int(reporte_id)].add(crid)
        for reporte_id, crids in by_reporte.items():
            try:
                emit_to_reporte(reporte_id, 'reporte:registros_cambio', {
                    'reporte_id': reporte_id,
                    'client_record_ids': sorted(crids),
                })
            except Exception as e:  # pragma: no cover
                _logger.warning(
                    'emit reporte:registros_cambio post-commit falló id=%s err=%s',
                    reporte_id, e,
                )

    @event.listens_for(db.session, 'after_rollback')
    def _clear_reg_emits(session):
        session.info.pop('_pending_reg_emits', None)


def _register_audit_emit_hook() -> None:
    """Emite `bitacora:new` a admin/super_admin cuando se inserta un AuditLog.

    Mismo patrón que `_register_notif_emit_hook`: snapshot en `after_insert`
    (id ya está poblado), flush en `after_commit` para no fantasmear eventos
    si la transacción se hace rollback.

    Frontend: `Bitacora.jsx` y `Dashboard.jsx` (actividad_reciente) escuchan
    este evento vía `invalidateOn` y refrescan su caché.
    """
    from sqlalchemy import event
    from app.extensions import db
    from app.models import AuditLog

    @event.listens_for(AuditLog, 'after_insert')
    def _stash_audit(mapper, connection, target):  # noqa: ARG001
        try:
            bucket = db.session.info.setdefault('_pending_audit_emits', [])
            bucket.append({
                'id': target.id,
                'user': target.user,
                'action': target.action,
                'ip': target.ip,
                'created_at': target.created_at,
            })
        except Exception:
            pass

    @event.listens_for(db.session, 'after_commit')
    def _flush_audit_emits(session):
        bucket = session.info.pop('_pending_audit_emits', None)
        if not bucket:
            return
        for snap in bucket:
            try:
                created = snap.get('created_at')
                emit_to_role(['admin', 'super_admin'], 'bitacora:new', {
                    'id': snap['id'],
                    'user': snap.get('user'),
                    'action': snap.get('action'),
                    'ip': snap.get('ip'),
                    'created_at': created.isoformat() if created else None,
                })
            except Exception as e:  # pragma: no cover
                _logger.warning('emit bitacora:new post-commit falló id=%s err=%s', snap.get('id'), e)

    @event.listens_for(db.session, 'after_rollback')
    def _clear_audit_emits(session):
        session.info.pop('_pending_audit_emits', None)


def _register_abono_emit_hook() -> None:
    """Emite `abono:new` cuando se inserta un AbonoPrestamo.

    Cubre tanto los abonos manuales (creados desde `/api/prestamos`) como los
    automáticos generados por el cierre de prenómina (NOMINA), que son los
    que el emit explícito en endpoints no alcanza.

    Mismo patrón snapshot+flush que el resto de hooks: si la transacción se
    revierte, el evento nunca se emite.
    """
    from sqlalchemy import event
    from app.extensions import db
    from app.models import AbonoPrestamo

    @event.listens_for(AbonoPrestamo, 'after_insert')
    def _stash_abono(mapper, connection, target):  # noqa: ARG001
        try:
            bucket = db.session.info.setdefault('_pending_abono_emits', [])
            bucket.append({
                'id': target.id,
                'prestamo_id': target.prestamo_id,
                'monto': float(target.monto) if target.monto else 0.0,
                'fecha_abono': target.fecha_abono.isoformat() if target.fecha_abono else None,
                'tipo': target.tipo,
            })
        except Exception:
            pass

    @event.listens_for(db.session, 'after_commit')
    def _flush_abono_emits(session):
        bucket = session.info.pop('_pending_abono_emits', None)
        if not bucket:
            return
        # Lookup de trabajador_id por prestamo_id (una sola query si hay varios).
        from app.models import Prestamo
        prestamo_ids = list({b['prestamo_id'] for b in bucket if b.get('prestamo_id')})
        trabajador_by_prestamo = {}
        if prestamo_ids:
            try:
                rows = (
                    db.session.query(Prestamo.id, Prestamo.trabajador_id)
                    .filter(Prestamo.id.in_(prestamo_ids))
                    .all()
                )
                trabajador_by_prestamo = {pid: tid for pid, tid in rows}
            except Exception:
                trabajador_by_prestamo = {}
        for snap in bucket:
            try:
                emit_to_role(['admin', 'super_admin'], 'abono:new', {
                    **snap,
                    'trabajador_id': trabajador_by_prestamo.get(snap.get('prestamo_id')),
                })
            except Exception as e:  # pragma: no cover
                _logger.warning('emit abono:new post-commit falló id=%s err=%s', snap.get('id'), e)

    @event.listens_for(db.session, 'after_rollback')
    def _clear_abono_emits(session):
        session.info.pop('_pending_abono_emits', None)


def _register_handlers() -> None:
    """Eventos básicos: connect (auth) / disconnect / join:reporte."""

    @socketio.on('connect')
    def _on_connect(auth):  # type: ignore[no-redef]
        # auth viene del cliente: io(..., { auth: { token } })
        token = (auth or {}).get('token') if isinstance(auth, dict) else None
        if not token:
            _logger.info('socket: rechazado (sin token) sid=%s', request.sid)
            # ConnectionRefusedError viaja como `err.message` en el cliente, así
            # el SocketContext puede distinguir un fallo de auth de un fallo de
            # red y disparar refresh de token solo cuando aplica.
            raise SocketIOConnectionRefusedError('token_missing')

        # Import diferido para evitar dependencia circular con app.routes.api_auth
        from app.routes.api_auth import _decode_token
        from app.models import User

        payload = _decode_token(token, 'access')
        if not payload:
            _logger.info('socket: rechazado (token inválido) sid=%s', request.sid)
            raise SocketIOConnectionRefusedError('token_expired')

        try:
            user_id = int(payload['sub'])
        except (KeyError, TypeError, ValueError):
            raise SocketIOConnectionRefusedError('token_invalid')

        user = User.query.get(user_id)
        if not user:
            raise SocketIOConnectionRefusedError('user_not_found')
        # Invalidar si la contraseña fue rotada después de emitir el JWT.
        if (user.password_version or 1) != payload.get('pv', 1):
            raise SocketIOConnectionRefusedError('token_revoked')

        room = f'user:{user.id}'
        join_room(room)
        # Sala por rol para broadcasts dirigidos (ej. 'role:admin' al emitir
        # cambios de empleado/proyecto/usuario). El rol se normaliza a minúsculas
        # para que `emit_to_role(['admin'])` matchee aunque la BD tenga 'Admin'.
        role_name = (user.role or '').lower()
        if role_name:
            join_room(f'role:{role_name}')
        # Guardamos el user_id en la session del socket para que otros handlers
        # (join:reporte) puedan revalidar permisos sin pedir el token de nuevo.
        socketio.server.save_session(request.sid, {'user_id': user.id})
        # Marcar presencia. Reconexiones automáticas + el handler 'heartbeat'
        # mantienen este timestamp fresco mientras el SPA esté abierto.
        try:
            from datetime import datetime
            from app.extensions import db as _db
            user.last_seen = datetime.now()
            _db.session.commit()
        except Exception:
            try:
                from app.extensions import db as _db
                _db.session.rollback()
            except Exception:
                pass
        _logger.debug(
            'socket: conectado sid=%s user_id=%s role=%s room=%s',
            request.sid, user.id, role_name, room,
        )
        return True

    @socketio.on('disconnect')
    def _on_disconnect():  # type: ignore[no-redef]
        _logger.debug('socket: desconectado sid=%s', request.sid)

    @socketio.on('join:reporte')
    def _on_join_reporte(data):  # type: ignore[no-redef]
        """Suscribe al cliente a la sala `reporte:{id}` si tiene acceso.

        Devuelve un ack `{'ok': bool, 'reporte_id': int, 'error'?: str}` para
        que el kiosko sepa si quedó suscrito (y reintente si no).
        """
        reporte_id = (data or {}).get('reporte_id') if isinstance(data, dict) else None
        try:
            reporte_id = int(reporte_id) if reporte_id is not None else None
        except (TypeError, ValueError):
            reporte_id = None
        if not reporte_id:
            return {'ok': False, 'reporte_id': None, 'error': 'reporte_id requerido'}

        try:
            sess = socketio.server.get_session(request.sid) or {}
        except Exception:
            sess = {}
        user_id = sess.get('user_id')
        if not user_id:
            # Sin sesión válida ya no podemos validar acceso: desconectamos al
            # cliente para que rehaga el handshake.
            disconnect()
            return {'ok': False, 'reporte_id': reporte_id, 'error': 'sin sesión'}

        from app.models import ReporteSemanal, User
        from app.routes.api_horas import _puede_acceder_proyecto

        reporte = ReporteSemanal.query.get(reporte_id)
        if not reporte:
            return {'ok': False, 'reporte_id': reporte_id, 'error': 'no existe'}
        user = User.query.get(user_id)
        if not user:
            return {'ok': False, 'reporte_id': reporte_id, 'error': 'sin usuario'}

        # _puede_acceder_proyecto usa _u()/_is_admin() que leen del contexto
        # JWT. En el handler WS no hay contexto JWT, así que aplicamos la misma
        # regla a mano: admin/super_admin ve todo; coordinador solo sus
        # proyectos. Mantener sincronizado con api_horas._puede_acceder_proyecto.
        role = (user.role or '').lower()
        es_admin = role in ('admin', 'super_admin')
        es_coordinador = role == 'coordinador'
        if not es_admin and not (es_coordinador and reporte.proyecto.coordinador_id == user.id):
            return {'ok': False, 'reporte_id': reporte_id, 'error': 'acceso denegado'}

        room = f'reporte:{reporte_id}'
        join_room(room)
        _logger.debug('socket: join sid=%s user_id=%s room=%s', request.sid, user_id, room)
        return {'ok': True, 'reporte_id': reporte_id}

    @socketio.on('heartbeat')
    def _on_heartbeat():  # type: ignore[no-redef]
        """Pulso de presencia desde el SPA: refresca `user.last_seen`.

        El cliente lo emite cada ~90s mientras el socket esté conectado, para
        que el indicador "en línea" del directorio/usuarios no expire por
        inactividad si el usuario solo está leyendo (sin requests HTTP)."""
        try:
            sess = socketio.server.get_session(request.sid) or {}
        except Exception:
            sess = {}
        user_id = sess.get('user_id')
        if not user_id:
            return
        try:
            from datetime import datetime
            from app.extensions import db as _db
            from app.models import User
            u = User.query.get(user_id)
            if u:
                u.last_seen = datetime.now()
                _db.session.commit()
        except Exception:
            try:
                from app.extensions import db as _db
                _db.session.rollback()
            except Exception:
                pass


# ── API de emisión usada desde los blueprints ──────────────────────────────

def emit_to_user(user_id: int, event: str, payload: dict) -> None:
    """Emite `event` con `payload` solo a las conexiones del usuario `user_id`.

    Es seguro llamarla aunque el usuario no esté conectado (no-op). También
    es seguro llamarla desde cualquier worker: si hay `message_queue` (Redis),
    el evento llega a quien lo tenga conectado.
    """
    try:
        socketio.emit(event, payload, to=f'user:{user_id}')
    except Exception as e:  # pragma: no cover — emisión nunca debe tumbar la request
        _logger.warning('socket.emit_to_user falló user_id=%s event=%s err=%s', user_id, event, e)


def emit_to_role(roles, event: str, payload: dict) -> None:
    """Emite `event` con `payload` a las salas `role:{nombre}`.

    `roles` puede ser un string ('admin') o una lista (['admin','inventario']).
    Los nombres se normalizan a minúsculas para coincidir con la sala unida en
    el handshake. Es seguro llamarla aunque nadie esté conectado.

    Pensado para broadcasts de cambios de catálogo (empleados, proyectos,
    usuarios…) donde la mutación es visible para varios usuarios del mismo
    rol y no tiene sentido apuntar a un user_id puntual.
    """
    if isinstance(roles, str):
        roles = [roles]
    for r in roles or ():
        room = f'role:{(r or "").lower()}'
        if room == 'role:':
            continue
        try:
            socketio.emit(event, payload, to=room)
        except Exception as e:  # pragma: no cover — emisión nunca debe tumbar la request
            _logger.warning(
                'socket.emit_to_role falló role=%s event=%s err=%s', r, event, e,
            )


def emit_to_reporte(reporte_id: int, event: str, payload: dict) -> None:
    """Emite `event` con `payload` a la sala `reporte:{id}`.

    La sala se puebla con `join:reporte` desde el kiosko (un kiosko que tiene
    cargado el reporte BORRADOR localmente se subscribe para enterarse de
    cambios de estado y dejar de aceptar lecturas RFID al instante).
    """
    try:
        socketio.emit(event, payload, to=f'reporte:{reporte_id}')
    except Exception as e:  # pragma: no cover
        _logger.warning('socket.emit_to_reporte falló reporte_id=%s event=%s err=%s', reporte_id, event, e)
