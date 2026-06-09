"""Endpoints de sesiones activas + helpers de notificación de login desde
dispositivo desconocido.

`_notify_new_device_login` se importa desde `login.py` para avisar al usuario
cuando un login (con o sin 2FA) viene de un combo IP+UA nuevo.
"""
import hashlib as _h
from datetime import datetime, timezone

from flask import current_app, g, jsonify

from app.extensions import db, limiter
from app.models import RefreshToken
from app.utils import log_action

from ._core import bp
from .jwt_required import jwt_required
from .tokens import _load_rt_meta


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
    from app.extensions import get_redis
    r = get_redis()
    if not r:
        # Sin Redis no podemos saber. Asumir "conocido" para NO spammear con
        # notifs de "nuevo device" en cada login.
        return True
    key = f'known_device:{user_id}:{fp}'
    # SET NX: si no existía, marcamos como conocido y devolvemos False (=era nuevo).
    # Si existía, devolvemos True (=ya conocido).
    was_new = r.set(key, '1', nx=True, ex=90 * 86400)
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
    """Revoca una sesión específica del propio usuario."""
    user = g._jwt_user
    tok = RefreshToken.query.filter_by(id=session_id, user_id=user.id).first()
    if not tok:
        return jsonify({'error': 'Sesión no encontrada'}), 404
    if not tok.revoked:
        tok.revoked = True
        db.session.commit()
        log_action(f'Revocó sesión #{session_id} ({user.username})')
    return jsonify({'ok': True})


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
