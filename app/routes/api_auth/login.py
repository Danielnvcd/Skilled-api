"""Endpoints del ciclo de auth: /login, /verify-2fa, /refresh, /logout.

Importa helpers de twofa.py (backup codes, lockout 2FA) y sessions.py
(notificación de device nuevo). Esos módulos no importan login.py — sin ciclo.
"""
from datetime import datetime, timedelta, timezone

import jwt
import pyotp
import secrets
from flask import current_app, g, jsonify, request
from werkzeug.security import check_password_hash

from app.constants import REFRESH_TOKEN_LIFETIME_DAYS
from app.extensions import db, limiter
from app.models import RefreshToken, User
from app.utils import log_action

from ._core import (
    _DUMMY_PW_HASH,
    _MAX_BACKUP_CODE_LEN,
    _MAX_PASSWORD_LEN,
    _MAX_TOTP_CODE_LEN,
    _MAX_USERNAME_LEN,
    _RT_COOKIE,
    _check_lockout,
    _clear_login_failures,
    _csrf_protected_cookie_endpoint,
    _format_ttl,
    _hash_token,
    _register_login_failure,
    _user_to_dict,
    bp,
)
from .sessions import _notify_new_device_login
from .tokens import (
    _clear_rt_cookie,
    _decode_token,
    _encode_access_token,
    _encode_pre_2fa_token,
    _is_rt_just_rotated,
    _issue_refresh_token,
    _mark_rt_just_rotated,
    _revoke_jti,
    _set_rt_cookie,
    _store_rt_meta,
    _totp_code_already_used,
)
from .twofa import (
    _BACKUP_CODES_COUNT,
    _check_twofa_lockout,
    _clear_twofa_failures,
    _count_active_backup_codes,
    _register_twofa_failure,
    _try_consume_backup_code,
)


@bp.route('/login', methods=['POST'])
@limiter.limit("4 per minute")
@limiter.limit(
    "8 per minute",
    key_func=lambda: f"api_login_user:{((request.get_json(silent=True) or {}).get('username') or '').lower().strip()}",
)
def api_login():
    data = request.get_json(silent=True) or {}
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''
    remember = bool(data.get('remember'))

    if not username or not password:
        return jsonify({'error': 'Usuario y contraseña son obligatorios'}), 400

    # Cap de longitud: defensa contra DoS por hash de payloads gigantes y
    # contra truncamiento silencioso. Si el usuario manda > MAX, rechazamos
    # antes de tocar BD o hashear.
    if len(username) > _MAX_USERNAME_LEN or len(password) > _MAX_PASSWORD_LEN:
        return jsonify({'error': 'Credenciales incorrectas'}), 401

    lockout_ttl = _check_lockout(username)
    if lockout_ttl:
        return jsonify({
            'error': f'Cuenta bloqueada por demasiados intentos. Intenta en {_format_ttl(lockout_ttl)}.',
        }), 423

    u = User.query.filter_by(username=username).first()
    if u:
        password_ok = check_password_hash(u.password_hash, password)
    else:
        check_password_hash(_DUMMY_PW_HASH, password)
        password_ok = False

    if not password_ok:
        _register_login_failure(username)
        post_fail_ttl = _check_lockout(username)
        if post_fail_ttl:
            return jsonify({
                'error': f'Cuenta bloqueada por demasiados intentos. Intenta en {_format_ttl(post_fail_ttl)}.',
            }), 423
        from app.extensions import get_real_client_ip_flask
        if u:
            g._jwt_user = u
        # User-Agent capeado a 200 chars para evitar logs gigantes. La IP ya
        # viene validada por get_real_client_ip_flask (rechaza spoof del
        # CF-Connecting-IP si la conexión directa no es de Cloudflare).
        ua = (request.headers.get('User-Agent') or '')[:200]
        log_action(
            f"API login fallido para '{username[:80]}' desde IP {get_real_client_ip_flask()} "
            f"UA={ua!r}"
        )
        return jsonify({'error': 'Credenciales incorrectas'}), 401

    _clear_login_failures(username)

    # Borrado lógico: una cuenta desactivada no inicia sesión, aunque la
    # contraseña sea correcta. Se chequea DESPUÉS de validar la contraseña para
    # no revelar el estado de la cuenta a quien no conoce las credenciales.
    if not u.activo:
        from app.extensions import get_real_client_ip_flask
        log_action(f"API login rechazado: cuenta desactivada '{username[:80]}' desde IP {get_real_client_ip_flask()}")
        return jsonify({'error': 'Tu cuenta está desactivada. Contacta al administrador.'}), 403

    if u.totp_secret:
        return jsonify({
            'requires2fa': True,
            'stepToken': _encode_pre_2fa_token(u),
        })

    token = _encode_access_token(u)
    u.last_seen = datetime.now()
    db.session.commit()
    g._jwt_user = u
    from app.extensions import get_real_client_ip_flask
    ip = get_real_client_ip_flask()
    ua = (request.headers.get('User-Agent') or '')[:200]
    log_action(f"API login exitoso desde IP {ip} UA={ua!r}")
    # Si es device nuevo, avisar al usuario (no aplica para login con 2FA —
    # aquel se notifica más abajo, en api_verify_2fa).
    _notify_new_device_login(u.id, ip, ua)

    resp = jsonify({'token': token, 'user': _user_to_dict(u)})
    # Emitimos refresh token siempre (no depende de `remember`) para que el
    # SPA pueda rotar el JWT corto cada ~20 min sin forzar relogin al usuario.
    # `remember` se mantiene en el payload por compatibilidad, pero ya no
    # condiciona la emisión. El logout limpia la cookie igual.
    _set_rt_cookie(resp, _issue_refresh_token(u.id))
    return resp


def _api_verify_2fa_user_key() -> str:
    """Key per-user para el rate limit de verify-2fa.

    Decodifica el stepToken sin verificación de firma (solo extrae `sub`) —
    es OK porque solo usamos el resultado para bucketear el rate limit; la
    autenticación real ocurre dentro del endpoint con _decode_token().

    Falla seguro: si no podemos extraer sub, devolvemos un bucket "anon" que
    comparte rate limit con todos los anónimos.
    """
    try:
        data = request.get_json(silent=True) or {}
        step = data.get('stepToken') or ''
        if not step:
            return 'api_v2fa_user:anon'
        # decode sin verificación — solo lectura de claims públicos
        payload = jwt.decode(step, options={'verify_signature': False, 'verify_aud': False, 'verify_iss': False})
        sub = str(payload.get('sub') or 'anon')
        return f'api_v2fa_user:{sub}'
    except Exception:
        return 'api_v2fa_user:anon'


@bp.route('/verify-2fa', methods=['POST'])
@limiter.limit("4 per minute")
@limiter.limit("8 per minute", key_func=_api_verify_2fa_user_key)
def api_verify_2fa():
    data = request.get_json(silent=True) or {}
    step_token = data.get('stepToken') or ''
    code = (data.get('code') or '').strip()
    # Cap único contra DoS: backup codes (14 chars) y TOTP (6) caben en
    # _MAX_BACKUP_CODE_LEN. No necesitamos flag is_backup_code — el endpoint
    # intenta TOTP primero y cae a backup si falla.
    if len(code) > _MAX_BACKUP_CODE_LEN:
        return jsonify({'error': 'Código inválido'}), 401

    payload = _decode_token(step_token, 'pre_2fa')
    if not payload:
        return jsonify({'error': 'Sesión 2FA expirada. Inicia sesión de nuevo.'}), 401

    try:
        uid = int(payload['sub'])
    except (TypeError, ValueError, KeyError):
        return jsonify({'error': 'Sesión inválida. Inicia sesión de nuevo.'}), 401

    # Lockout escalado: si esta cuenta acumuló N fallos de 2FA en la ventana,
    # rechazar antes de evaluar el código. Bloquea brute-force del TOTP en
    # casos donde el atacante ya tiene la password (phishing, credential stuffing).
    lock_ttl = _check_twofa_lockout(uid)
    if lock_ttl:
        return jsonify({
            'error': f'Demasiados intentos 2FA fallidos. Intenta de nuevo en {_format_ttl(lock_ttl)}.',
        }), 423

    user = User.query.get(uid)
    if not user or not user.totp_secret:
        return jsonify({'error': 'Sesión inválida. Inicia sesión de nuevo.'}), 401

    if (user.password_version or 1) != payload.get('pv', 1):
        return jsonify({'error': 'Tu contraseña cambió. Inicia sesión de nuevo.'}), 401

    g._jwt_user = user

    # Validar código: primero TOTP. Si falla, probar como backup code.
    # El usuario que perdió el teléfono pega el backup code directo en el
    # mismo campo, sin UI extra. Para TOTP exigimos formato corto (≤8 chars)
    # para no malgastar CPU verificando códigos de respaldo contra el secret.
    totp_ok = len(code) <= _MAX_TOTP_CODE_LEN and pyotp.TOTP(user.totp_secret).verify(code, valid_window=1)
    backup_used = False
    if not totp_ok:
        backup_used = _try_consume_backup_code(uid, code)

    if not totp_ok and not backup_used:
        _register_twofa_failure(uid)
        post_fail_ttl = _check_twofa_lockout(uid)
        if post_fail_ttl:
            return jsonify({
                'error': f'Cuenta bloqueada por 2FA fallido. Intenta en {_format_ttl(post_fail_ttl)}.',
            }), 423
        from app.extensions import get_real_client_ip_flask
        ua = (request.headers.get('User-Agent') or '')[:200]
        log_action(
            f"API 2FA fallido para {user.username} desde IP {get_real_client_ip_flask()} "
            f"UA={ua!r}"
        )
        return jsonify({'error': 'Código incorrecto'}), 401

    # Anti-replay del TOTP: si pasó por TOTP y este código se usó hace <90s,
    # rechazar. Para backup codes no aplica (ya están marked consumed en BD).
    if totp_ok and _totp_code_already_used(user.id, code):
        log_action(f"API 2FA replay bloqueado para {user.username}")
        return jsonify({'error': 'Este código ya fue usado. Espera al siguiente.'}), 401

    # Éxito: limpiar el contador de fallos 2FA para esta cuenta.
    _clear_twofa_failures(user.id)

    # Si fue backup code, notificar al usuario por bitácora + notif in-app.
    # Es un evento sensible: o el dispositivo se perdió, o alguien con la
    # password está usando los backup codes filtrados.
    if backup_used:
        remaining = _count_active_backup_codes(user.id)
        log_action(
            f"Login 2FA con BACKUP CODE para {user.username} "
            f"(restantes: {remaining})"
        )
        try:
            from app.models import Notificacion
            from app.realtime import emit_to_user
            notif = Notificacion(
                usuario_id=user.id,
                tipo='LOGIN_BACKUP_CODE',
                titulo='Login con código de respaldo',
                mensaje=(
                    f'Se usó un código de respaldo para iniciar sesión. Quedan '
                    f'{remaining} de {_BACKUP_CODES_COUNT}. Si no fuiste tú, '
                    f'cambia la contraseña y regenera los códigos.'
                ),
                url='/perfil',
            )
            db.session.add(notif)
            db.session.commit()
            try:
                emit_to_user(user.id, 'notif:new', {'id': notif.id, 'tipo': notif.tipo})
            except Exception:
                pass
        except Exception as e:
            try:
                db.session.rollback()
            except Exception:
                pass
            current_app.logger.warning('No se pudo crear notif LOGIN_BACKUP_CODE: %s', e)

    token = _encode_access_token(user)
    user.last_seen = datetime.now()
    db.session.commit()
    from app.extensions import get_real_client_ip_flask
    ip = get_real_client_ip_flask()
    ua = (request.headers.get('User-Agent') or '')[:200]
    log_action(f"API login 2FA exitoso para {user.username} desde IP {ip} UA={ua!r}")
    _notify_new_device_login(user.id, ip, ua)

    resp = jsonify({'token': token, 'user': _user_to_dict(user)})
    # En el flujo 2FA emitimos refresh token siempre para no obligar a reautenticar
    # cada 20 min cuando hay TOTP activo (UX). El SPA puede limpiar la cookie con logout.
    _set_rt_cookie(resp, _issue_refresh_token(user.id))
    return resp


def _api_refresh_user_key() -> str:
    """Key per-user para el rate limit de refresh.

    Hash del raw_rt cookie como bucket: requests con la misma cookie comparten
    rate limit, independiente de la IP. Cubre el caso de una cookie filtrada
    siendo replayed desde múltiples IPs (proxy chain) — el rate limit per-IP
    no detiene eso.

    Si no hay cookie, devolvemos un bucket compartido — el endpoint igual
    rechazará el request por falta de RT, pero al menos no compartimos rate
    limit con el resto del mundo.
    """
    raw_rt = request.cookies.get(_RT_COOKIE)
    if not raw_rt:
        return 'api_refresh:no_cookie'
    # Hash truncado de la cookie cruda (sha256 hexadecimal). 16 chars son
    # suficientes para el bucket; no exponemos la cookie en logs del limiter.
    return f'api_refresh:{_hash_token(raw_rt)[:16]}'


@bp.route('/refresh', methods=['POST'])
@limiter.limit("30 per minute")
@limiter.limit("15 per minute", key_func=_api_refresh_user_key)
def api_refresh():
    # HIGH-02: rechazar requests que no vengan de XHR/fetch del SPA.
    csrf_err = _csrf_protected_cookie_endpoint()
    if csrf_err:
        return csrf_err
    raw_rt = request.cookies.get(_RT_COOKIE)
    if not raw_rt:
        return jsonify({'error': 'Refresh token no presente'}), 401

    now = datetime.now(timezone.utc)
    h = _hash_token(raw_rt)
    tok = RefreshToken.query.filter_by(token_hash=h).first()
    if not tok:
        resp = jsonify({'error': 'Refresh token inválido'})
        _clear_rt_cookie(resp)
        return resp, 401

    exp = tok.expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)

    # ── Detección de replay vs race (token reuse) ───────────────────────────
    # Si el cliente presenta un RT ya revocado, hay dos escenarios:
    #
    # (a) Race legítimo: dos pestañas del mismo navegador hicieron refresh
    #     casi simultáneamente. Una ganó (rotó el RT viejo); la otra llega
    #     unos ms después con el mismo RT, ya marcado revoked. NO es ataque.
    #
    # (b) Replay malicioso: un atacante robó la cookie (XSS, malware) y la
    #     usa después de que el usuario legítimo ya rotó. Es señal clara
    #     de compromiso → revocar la familia entera (todos los RTs del
    #     usuario) y forzar re-login. Patrón OAuth (RFC 6749 §10.4).
    #
    # Para distinguir, marcamos cada RT recién rotado en Redis con TTL 10s.
    # Replay dentro de esa ventana = race; fuera = ataque.
    if tok.revoked:
        if _is_rt_just_rotated(tok.id):
            # Race legítimo: pestaña vecina llegó tarde. Devolvemos 401 sin
            # revocar la familia para no botar al usuario de TODAS sus pestañas.
            resp = jsonify({'error': 'Refresh token ya rotado, reintenta'})
            _clear_rt_cookie(resp)
            return resp, 401

        # Replay fuera de la ventana de gracia: asumir compromiso.
        try:
            RefreshToken.query.filter_by(user_id=tok.user_id, revoked=False).update(
                {'revoked': True}, synchronize_session=False,
            )
            db.session.commit()
            log_action(
                f"Posible robo de refresh token detectado (replay). Todas las sesiones "
                f"del user_id={tok.user_id} fueron revocadas."
            )
            current_app.logger.warning(
                "Refresh token replay detected for user_id=%s; revoked entire RT family",
                tok.user_id,
            )
        except Exception as e:
            db.session.rollback()
            current_app.logger.error("Error revocando familia tras replay: %s", e)
        resp = jsonify({'error': 'Sesión comprometida. Vuelve a iniciar sesión.'})
        _clear_rt_cookie(resp)
        return resp, 401

    if exp <= now:
        tok.revoked = True
        db.session.commit()
        resp = jsonify({'error': 'Refresh token expirado'})
        _clear_rt_cookie(resp)
        return resp, 401

    user_id = tok.user_id

    # Revocación atómica: si dos requests intentan rotar la misma cookie en
    # paralelo (dos pestañas, refresh proactivo + reactivo), solo el primero
    # logrará el UPDATE con revoked=False; el segundo recibirá rowcount=0 y
    # debe rechazarse en lugar de emitir un RT nuevo (que dejaría dos válidos
    # en BD, con el riesgo de que el "perdedor" haga logout cuando una
    # pestaña intente usarlo).
    updated = (
        db.session.query(RefreshToken)
        .filter(RefreshToken.id == tok.id, RefreshToken.revoked == False)  # noqa: E712
        .update({RefreshToken.revoked: True}, synchronize_session=False)
    )
    db.session.commit()
    if updated == 0:
        # Race perdido: otro request ya rotó este token.
        resp = jsonify({'error': 'Refresh token ya rotado'})
        _clear_rt_cookie(resp)
        return resp, 401

    # Marca el RT recién revocado como "rotado en ventana de gracia". Si llega
    # otro request en los próximos _RT_ROTATION_GRACE_SECONDS con este mismo
    # RT, se trata como race (pestaña vecina) y NO como replay malicioso.
    _mark_rt_just_rotated(tok.id)

    user = User.query.get(user_id)
    if user is None:
        resp = jsonify({'error': 'Usuario no encontrado'})
        _clear_rt_cookie(resp)
        return resp, 401
    # Borrado lógico: si la cuenta fue desactivada, no renovamos sesión.
    if not user.activo:
        resp = jsonify({'error': 'Cuenta desactivada'})
        _clear_rt_cookie(resp)
        return resp, 401

    new_raw = secrets.token_urlsafe(32)
    new_tok = RefreshToken(
        token_hash=_hash_token(new_raw),
        user_id=user.id,
        expires_at=now + timedelta(days=REFRESH_TOKEN_LIFETIME_DAYS),
    )
    db.session.add(new_tok)
    db.session.flush()  # asegura new_tok.id antes del DELETE
    # Housekeeping: limpiar tokens viejos del usuario.
    RefreshToken.query.filter(
        RefreshToken.user_id == user.id,
        (RefreshToken.revoked == True) | (RefreshToken.expires_at <= now),  # noqa: E712
        RefreshToken.id != new_tok.id,
    ).delete(synchronize_session=False)
    db.session.commit()
    # Persistir metadata (UA + IP) del request que rotó. Si la sesión cambia
    # de IP repentinamente, /sessions lo refleja al siguiente refresh.
    _store_rt_meta(new_tok.id)

    access = _encode_access_token(user)
    resp = jsonify({'token': access, 'user': _user_to_dict(user)})
    _set_rt_cookie(resp, new_raw)
    return resp


@bp.route('/logout', methods=['POST'])
def api_logout():
    # HIGH-02: bloquear logout CSRF cross-site (especialmente con SameSite=None).
    csrf_err = _csrf_protected_cookie_endpoint()
    if csrf_err:
        return csrf_err

    # `session_closed` distingue un logout real (había token o cookie de sesión
    # que cerrar) de un no-op anónimo. El SPA dispara logout best-effort desde
    # bounceToLogin cuando un 401 ya mató la sesión; esos llegan sin credenciales
    # válidas y NO deben ensuciar la bitácora con "API logout por anon".
    session_closed = False

    # Revocar el JWT actual (vía jti) si el cliente lo mandó en Authorization.
    # Esto cierra el access token AL INSTANTE, sin esperar a su exp (≤20 min).
    # Sin Redis no podemos blacklistear — degradamos: el JWT queda vivo hasta
    # su exp, pero los refresh tokens igual se revocan abajo.
    auth_h = request.headers.get('Authorization', '')
    if auth_h.startswith('Bearer '):
        access_tok = auth_h.split(' ', 1)[1].strip()
        payload = _decode_token(access_tok, 'access')
        if payload and payload.get('jti') and payload.get('exp'):
            _revoke_jti(payload['jti'], int(payload['exp']))
            session_closed = True

    raw_rt = request.cookies.get(_RT_COOKIE)
    if raw_rt:
        h = _hash_token(raw_rt)
        tok = RefreshToken.query.filter_by(token_hash=h).first()
        if tok and not tok.revoked:
            tok.revoked = True
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()
        if tok:
            session_closed = True
            user = User.query.get(tok.user_id)
            if user:
                g._jwt_user = user

    # Solo auditamos cuando de verdad cerramos algo (token revocado o RT hallado).
    if session_closed:
        log_action("API logout")
    resp = jsonify({'ok': True})
    _clear_rt_cookie(resp)
    return resp
