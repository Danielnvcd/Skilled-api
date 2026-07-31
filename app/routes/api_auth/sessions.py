"""Endpoints de sesiones activas + helpers de notificación de login desde
dispositivo desconocido.

`_notify_new_device_login` se importa desde `login.py` para avisar al usuario
cuando un login (con o sin 2FA) viene de un combo IP+UA nuevo.
"""
import hashlib as _h
from datetime import datetime, timezone

from flask import current_app, g, jsonify, request

from app.extensions import db, limiter
from app.models import RefreshToken
from app.utils import log_action

from ._core import bp, _RT_COOKIE, _hash_token, _is_admin_user
from .jwt_required import jwt_required
from .tokens import _load_rt_meta, _decode_token, _revoke_jti, _clear_rt_cookie


# ── Notificación de login desde device nuevo ────────────────────────────────
# Cuando un usuario inicia sesión con éxito desde una combinación (IP + UA) que
# nunca habíamos visto, le mandamos una notificación in-app. Permite que detecte
# logins ajenos rápido y dispare /sessions/all si no los reconoce.
#
# Tracking: hash de IP+UA por usuario en Redis con TTL largo (90 días). Si el
# hash existe, ya conocemos el device. Si no, es nuevo → notif + registrar.
#
# Sin Redis no podemos rastrear → no mandamos notifs (falla benigno: el
# /sessions sigue mostrando la sesión activa con su UA/IP).

def _device_fingerprint(ua: str, ip: str) -> str:
    """Hash corto para identificar combos UA+IP. No es PII reversible."""
    src = f'{ua or "_"}|{ip or "_"}'
    return _h.sha256(src.encode()).hexdigest()[:24]


def _is_known_device(user_id: int, fp: str) -> bool:
    from app.extensions import redis_call
    key = f'known_device:{user_id}:{fp}'
    # SET NX: si no existía, marcamos como conocido y devolvemos False (=era nuevo).
    # Si existía, devolvemos True (=ya conocido).
    # Sin Redis (o caído) no podemos saber: `default=None` hace que `not None`
    # sea True → "device conocido", para NO spammear con notifs de "nuevo
    # dispositivo" en cada login mientras dure la incidencia.
    was_new = redis_call(lambda r: r.set(key, '1', nx=True, ex=90 * 86400), default=None)
    return not was_new


def _notify_new_device_login(user_id: int, ip: str, ua: str) -> None:
    """Si el (user_id, IP+UA) no estaba registrado, manda notif al usuario."""
    fp = _device_fingerprint(ua, ip)
    if _is_known_device(user_id, fp):
        return
    try:
        from app.models import Notificacion
        from app.realtime import emit_to_user
        # UA suele tener formato verboso "Mozilla/5.0 (Windows NT...)". Capeamos.
        ua_short = (ua or 'desconocido')[:120]
        ip_short = (ip or 'desconocida')[:45]
        notif = Notificacion(
            usuario_id=user_id,
            tipo='LOGIN_NUEVO_DEVICE',
            titulo='Nuevo inicio de sesión',
            mensaje=(
                f'Hubo un inicio de sesión nuevo desde {ip_short}. '
                f'Si no fuiste tú, ve a Mi Perfil → Sesiones y revoca todas.'
            ),
            url='/perfil',
        )
        db.session.add(notif)
        db.session.commit()
        try:
            emit_to_user(user_id, 'notif:new', {'id': notif.id, 'tipo': notif.tipo})
        except Exception:
            pass
    except Exception as e:
        try:
            db.session.rollback()
        except Exception:
            pass
        current_app.logger.warning('No se pudo crear notif LOGIN_NUEVO_DEVICE: %s', e)


# ── Gestión de sesiones propias (refresh tokens activos) ────────────────────

@bp.route('/sessions', methods=['GET'])
@jwt_required
def api_list_sessions():
    """Lista las sesiones activas del propio usuario (cookies de refresh).

    Útil para que el usuario detecte logins ajenos y los revoque desde su
    pantalla de Perfil. Solo devuelve metadatos — el hash del token nunca
    sale del servidor.
    """
    user = g._jwt_user
    now = datetime.now(timezone.utc)
    tokens = (
        RefreshToken.query
        .filter_by(user_id=user.id, revoked=False)
        .order_by(RefreshToken.created_at.desc())
        .all()
    )
    out = []
    for t in tokens:
        exp = t.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp <= now:
            continue
        meta = _load_rt_meta(t.id)
        out.append({
            'id': t.id,
            'created_at': t.created_at.isoformat() if t.created_at else None,
            'expires_at': exp.isoformat(),
            # UA + IP del último login/rotación. Permite al usuario reconocer
            # sesiones extrañas y revocarlas. None si Redis estaba caído al
            # momento de emitir.
            'user_agent': meta.get('ua') or None,
            'ip': meta.get('ip') or None,
        })
    return jsonify(out)


@bp.route('/sessions/<int:session_id>', methods=['DELETE'])
@jwt_required
@limiter.limit("20 per minute")
def api_revoke_session(session_id: int):
    """Revoca una sesión específica del propio usuario.

    Si la sesión revocada es la que el usuario está usando AHORA (su mismo RT
    cookie), también matamos el access token en curso (por jti) y limpiamos la
    cookie, devolviendo `self: true` para que el SPA cierre sesión. Sin esto,
    revocar la sesión propia no sacaba al usuario: el RT quedaba revocado pero el
    JWT seguía vivo hasta su exp (~20 min)."""
    user = g._jwt_user
    tok = RefreshToken.query.filter_by(id=session_id, user_id=user.id).first()
    if not tok:
        return jsonify({'error': 'Sesión no encontrada'}), 404

    # ¿Es la sesión del request actual? Comparamos contra el RT cookie en curso.
    is_self = False
    raw_rt = request.cookies.get(_RT_COOKIE)
    if raw_rt:
        try:
            actual = RefreshToken.query.filter_by(token_hash=_hash_token(raw_rt)).first()
            is_self = bool(actual and actual.id == tok.id)
        except Exception:
            is_self = False

    if not tok.revoked:
        tok.revoked = True
        db.session.commit()
        log_action(f'Revocó sesión #{session_id} ({user.username})')

    if is_self:
        # Mata el access token actual al instante (no esperar a su exp).
        auth_h = request.headers.get('Authorization', '')
        if auth_h.startswith('Bearer '):
            payload = _decode_token(auth_h.split(' ', 1)[1].strip(), 'access')
            if payload and payload.get('jti') and payload.get('exp'):
                _revoke_jti(payload['jti'], int(payload['exp']))

    resp = jsonify({'ok': True, 'self': is_self})
    if is_self:
        _clear_rt_cookie(resp)
    return resp


@bp.route('/sessions/all', methods=['DELETE'])
@jwt_required
@limiter.limit("10 per minute")
def api_revoke_all_sessions():
    """Revoca TODAS las sesiones del propio usuario (pánico).

    Hace DOS cosas:
      1) Revoca todos los refresh tokens activos (corta el ciclo de refresh).
      2) Incrementa password_version → invalida cualquier JWT vivo del usuario
         (cualquier dispositivo presentando un access token viejo recibe 401
         al instante, sin esperar 20 min).

    Es la única forma sin lista negra masiva de invalidar TODOS los JWT del
    usuario al instante. Como efecto secundario, esta misma sesión también
    queda invalidada — el cliente debe re-loguear inmediatamente.
    """
    user = g._jwt_user
    RefreshToken.query.filter_by(user_id=user.id, revoked=False).update({'revoked': True})
    user.password_version = (user.password_version or 1) + 1
    db.session.commit()
    log_action(f'Revocó todas las sesiones ({user.username}) — JWT vivos invalidados')
    # Cierra cualquier WebSocket activo del usuario. Sin esto, el SPA seguía
    # recibiendo pushes (notif:new, bitacora:new) por unos segundos hasta que
    # el siguiente request HTTP recibiera 401 por pv mismatch.
    try:
        from app.realtime import force_logout_user
        force_logout_user(user.id)
    except Exception as e:
        current_app.logger.warning('force_logout_user falló en /sessions/all: %s', e)
    return jsonify({'ok': True})


@bp.route('/estado-seguridad', methods=['GET'])
@jwt_required
@limiter.limit("30 per minute")
def api_estado_seguridad():
    """Estado de las defensas que dependen de Redis. SOLO admin/super_admin.

    Por qué existe: varias protecciones viven en Redis y, por diseño, degradan
    en silencio si no está disponible — el servicio sigue en pie, pero sin
    blacklist de jti (un logout deja de matar el JWT al instante), sin lockout
    escalado, sin anti-replay de TOTP y sin consumo del stepToken de 2FA.
    Eso es lo correcto para disponibilidad, pero significa que se puede estar
    operando degradado sin que nadie lo note. Este endpoint lo hace visible.

    Deliberadamente NO va en `/health`: ese es público y responde a cualquier
    escáner. Exponer ahí qué componentes tenemos y cuáles están caídos le da a
    un atacante justo la señal que busca — "ahora mismo no hay lockout".
    """
    from app.extensions import get_redis

    if not _is_admin_user(g._jwt_user):
        return jsonify({'error': 'No autorizado'}), 403

    r = get_redis()
    redis_ok = False
    detalle = 'REDIS_URL no configurada'
    if r is not None:
        try:
            redis_ok = bool(r.ping())
            detalle = 'conectado' if redis_ok else 'ping sin respuesta'
        except Exception as e:
            detalle = f'error de conexión: {type(e).__name__}'

    return jsonify({
        'redis': {'ok': redis_ok, 'detalle': detalle},
        # Qué se pierde mientras Redis no responda. Sirve para que quien vea la
        # alerta entienda el impacto sin tener que leer el código.
        'defensas_degradadas': [] if redis_ok else [
            'Revocación inmediata de JWT por jti (el logout no mata el token hasta su exp, ≤20 min)',
            'Lockout escalado por intentos fallidos de contraseña',
            'Lockout escalado por intentos fallidos de 2FA',
            'Anti-replay de códigos TOTP',
            'Consumo de un solo uso del stepToken de 2FA',
            'Detección de robo de refresh token (race vs replay)',
            'Aviso de inicio de sesión desde dispositivo nuevo',
        ],
    })
