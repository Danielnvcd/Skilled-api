"""Endpoints de 2FA: setup, confirm, disable + backup codes (status/generar/revocar).

También contiene los helpers internos exclusivos del 2FA (lockout escalado para
TOTP, pin del secret de setup en Redis, hashing/format de backup codes).
"""
import hashlib as _h
import secrets
from datetime import datetime, timezone

import pyotp
from flask import current_app, g, jsonify, request
from werkzeug.security import check_password_hash

from app.extensions import db, limiter
from app.models import RefreshToken, TwoFactorBackupCode
from app.utils import log_action

from ._core import (
    _BACKUP_CODES_COUNT,
    _BACKUP_CODES_LOW_THRESHOLD,
    _LOCKOUT_DURATIONS,
    _LOCKOUT_LEVEL_TTL,
    _LOGIN_FAILS_THRESHOLD,
    _LOGIN_FAILS_WINDOW,
    _MAX_PASSWORD_LEN,
    _MAX_TOTP_CODE_LEN,
    _SETUP_2FA_TTL,
    bp,
)
from .jwt_required import jwt_required
from .tokens import _totp_code_already_used


# ── Backup codes para 2FA ───────────────────────────────────────────────────
# Permiten recuperar acceso cuando el usuario pierde el dispositivo TOTP
# (teléfono robado, app desinstalada). Cada código es one-shot; el usuario
# guarda los 10 en lugar seguro (impresos / password manager).
#
# Formato: 14 chars "XXXX-XXXX-XXXX" en alfabeto base32 limpio (sin 0/O/1/I).
# Equivalente a ~60 bits de entropía — más que suficiente, no brute-forceable
# en la ventana de TTL del stepToken (5 min).
#
# Storage: SHA-256 hex del código (no bcrypt — los códigos ya son aleatorios
# largos, bcrypt no aporta y duplica costo CPU). Plain text se muestra UNA SOLA
# VEZ al generarlos.

# Alfabeto sin caracteres ambiguos para que el usuario los lea bien al copiar
# desde papel impreso.
_BACKUP_CODE_ALPHABET = '23456789ABCDEFGHJKLMNPQRSTUVWXYZ'  # sin 0/O/1/I


def _hash_backup_code(code: str) -> str:
    """SHA-256 hex del código (normalizado a uppercase, sin guiones/espacios)."""
    normalized = code.upper().replace('-', '').replace(' ', '')
    return _h.sha256(normalized.encode()).hexdigest()


def _format_backup_code(raw_chars: str) -> str:
    """Inserta guiones cada 4 chars para legibilidad: ABCD-EFGH-JKLM."""
    return f'{raw_chars[:4]}-{raw_chars[4:8]}-{raw_chars[8:12]}'


def _generate_backup_codes(user_id: int) -> list[str]:
    """Genera _BACKUP_CODES_COUNT códigos, los persiste hasheados, devuelve plaintext.

    Invalida cualquier código anterior (consumido o no) del mismo usuario:
    el usuario ve una lista nueva y debe descartar la vieja.
    """
    # Borrar todos los existentes (consumidos o no): regenerar siempre
    # invalida los anteriores. Patrón estándar (Google, GitHub).
    TwoFactorBackupCode.query.filter_by(user_id=user_id).delete()
    plaintexts = []
    for _ in range(_BACKUP_CODES_COUNT):
        raw = ''.join(secrets.choice(_BACKUP_CODE_ALPHABET) for _ in range(12))
        formatted = _format_backup_code(raw)
        plaintexts.append(formatted)
        db.session.add(TwoFactorBackupCode(
            user_id=user_id,
            code_hash=_hash_backup_code(formatted),
        ))
    db.session.commit()
    return plaintexts


def _count_active_backup_codes(user_id: int) -> int:
    return TwoFactorBackupCode.query.filter_by(
        user_id=user_id, consumed_at=None,
    ).count()


def _try_consume_backup_code(user_id: int, code: str) -> bool:
    """Intenta consumir un código de respaldo. True si era válido (y queda marcado)."""
    if not code:
        return False
    h = _hash_backup_code(code)
    tok = TwoFactorBackupCode.query.filter_by(
        user_id=user_id, code_hash=h, consumed_at=None,
    ).first()
    if not tok:
        return False
    tok.consumed_at = datetime.now(timezone.utc)
    db.session.commit()
    return True


# ── Lockout escalado para 2FA ───────────────────────────────────────────────
# El blueprint auth.py protege el password con lockout escalado (10m → 24h),
# pero el 2FA no lo tenía. Un atacante con la password real podría brute-forcear
# el código TOTP (6 dígitos = 1M combinaciones) en ventanas de 30s antes de
# que la pestaña expire.
#
# Aplicamos la misma infraestructura: mismo window/threshold/durations/level TTL,
# pero con keys distintas para que el contador no se cruce con el de password.
# Key dimension: user_id (sacado del stepToken pre_2fa). Si no podemos extraer
# el sub, no contamos — falla open para no DOSearse a sí mismo si Redis está down
# (el rate limit per-IP del endpoint sigue activo como red de seguridad).

def _twofa_lockout_key(user_id: int) -> str:        return f'twofa_lockout:{user_id}'
def _twofa_level_key(user_id: int) -> str:          return f'twofa_lockout_level:{user_id}'
def _twofa_fails_key(user_id: int) -> str:          return f'twofa_fails:{user_id}'


def _check_twofa_lockout(user_id: int):
    from app.extensions import get_redis
    r = get_redis()
    if not r:
        return None
    ttl = r.ttl(_twofa_lockout_key(user_id))
    return ttl if (ttl is not None and ttl > 0) else None


def _register_twofa_failure(user_id: int) -> None:
    from app.extensions import get_redis
    r = get_redis()
    if not r:
        return
    fkey = _twofa_fails_key(user_id)
    fails = r.incr(fkey)
    if fails == 1:
        r.expire(fkey, _LOGIN_FAILS_WINDOW)
    if fails >= _LOGIN_FAILS_THRESHOLD:
        lkey = _twofa_level_key(user_id)
        try:
            level = int(r.get(lkey) or 0)
        except (TypeError, ValueError):
            level = 0
        duration = _LOCKOUT_DURATIONS[min(level, len(_LOCKOUT_DURATIONS) - 1)]
        r.setex(_twofa_lockout_key(user_id), duration, '1')
        r.setex(lkey, _LOCKOUT_LEVEL_TTL, level + 1)
        r.delete(fkey)


def _clear_twofa_failures(user_id: int) -> None:
    from app.extensions import get_redis
    r = get_redis()
    if not r:
        return
    r.delete(_twofa_fails_key(user_id), _twofa_lockout_key(user_id), _twofa_level_key(user_id))


# ── Pinning del secret de setup-2fa ─────────────────────────────────────────
# El flujo de activación de 2FA en dos pasos (/setup-2fa devuelve secret →
# /confirm-2fa valida con código) era vulnerable: el cliente podía mandar un
# secret arbitrario en confirm-2fa y el backend lo persistía sin verificar
# que viniera de un setup-2fa previo. Atacante con sesión + current_password
# podía instalar un TOTP bajo su control.
#
# Fix: al hacer /setup-2fa, guardar el secret en Redis con TTL 10min por
# usuario. En /confirm-2fa, el secret que envíe el cliente DEBE coincidir
# con el pineado.
#
# Si no hay Redis, degradamos: no pineamos y no podemos validar. En ese caso
# /confirm-2fa rechaza el flujo y obliga a configurar Redis (más seguro que
# degradar silenciosamente al comportamiento vulnerable).

def _setup_2fa_key(user_id: int) -> str:
    return f'totp_setup_secret:{user_id}'


def _pin_setup_2fa_secret(user_id: int, secret: str) -> bool:
    """Persiste el secret candidato. Devuelve True si se pudo pinear."""
    from app.extensions import get_redis
    r = get_redis()
    if not r:
        return False
    r.setex(_setup_2fa_key(user_id), _SETUP_2FA_TTL, secret)
    return True


def _peek_setup_2fa_secret(user_id: int) -> str | None:
    """Lee el secret pineado sin borrarlo. Para validar en confirm-2fa."""
    from app.extensions import get_redis
    r = get_redis()
    if not r:
        return None
    return r.get(_setup_2fa_key(user_id))


def _delete_setup_2fa_secret(user_id: int) -> None:
    """Borra el secret pineado. Se llama después de un confirm-2fa exitoso."""
    from app.extensions import get_redis
    r = get_redis()
    if not r:
        return
    r.delete(_setup_2fa_key(user_id))


# ── 2FA setup (flujo en dos pasos: pedir secret → confirmar código) ────────

@bp.route('/setup-2fa', methods=['POST'])
@jwt_required
@limiter.limit('4 per minute')
def api_setup_2fa():
    """Paso 1 del setup 2FA. Requiere current_password (reauth).
    Devuelve un secret y el QR en base64 para que el usuario lo escanee."""
    import base64
    import io as _io
    import qrcode

    user = g._jwt_user
    data = request.get_json(silent=True) or {}
    current_password = data.get('current_password') or data.get('currentPassword') or ''
    if not current_password:
        return jsonify({'error': 'Contraseña actual requerida'}), 400
    if len(current_password) > _MAX_PASSWORD_LEN:
        return jsonify({'error': 'La contraseña actual es incorrecta'}), 401
    if not check_password_hash(user.password_hash, current_password):
        return jsonify({'error': 'La contraseña actual es incorrecta'}), 401

    secret = pyotp.random_base32()
    # Pin server-side: el secret QUE EL CLIENTE DEVOLVERÁ en /confirm-2fa debe
    # coincidir con éste. Bloquea el ataque donde un cliente malicioso envía
    # un secret arbitrario en confirm-2fa para tomar control del TOTP.
    if not _pin_setup_2fa_secret(user.id, secret):
        current_app.logger.error(
            'setup-2fa: Redis no disponible para pinear el secret. Rechazando.'
        )
        return jsonify({
            'error': 'No se puede iniciar la configuración de 2FA en este momento. Intenta más tarde.',
        }), 503
    totp_uri = pyotp.totp.TOTP(secret).provisioning_uri(
        name=user.username,
        issuer_name='SistemaNominas',
    )
    img = qrcode.make(totp_uri)
    buffered = _io.BytesIO()
    img.save(buffered, format='PNG')
    qr_b64 = base64.b64encode(buffered.getvalue()).decode('utf-8')
    return jsonify({'secret': secret, 'qr': qr_b64})


@bp.route('/confirm-2fa', methods=['POST'])
@jwt_required
@limiter.limit('6 per minute')
def api_confirm_2fa():
    """Paso 2: valida el código TOTP contra el secret y lo persiste.

    Si el usuario YA tiene 2FA activo, además exige `current_2fa_code` válido
    contra el secret existente. Esto bloquea el ataque donde un atacante con
    sesión activa + contraseña cambia el dispositivo TOTP sin tener el actual.
    """
    user = g._jwt_user
    data = request.get_json(silent=True) or {}
    current_password = data.get('current_password') or data.get('currentPassword') or ''
    secret = data.get('secret') or ''
    code = (data.get('code') or '').strip()
    current_2fa_code = (data.get('current_2fa_code') or data.get('currentTwoFaCode') or '').strip()

    if not current_password or not secret or not code:
        return jsonify({'error': 'Faltan datos (contraseña, secret o código)'}), 400
    if len(current_password) > _MAX_PASSWORD_LEN:
        return jsonify({'error': 'La contraseña actual es incorrecta'}), 401
    if len(code) > _MAX_TOTP_CODE_LEN or len(current_2fa_code) > _MAX_TOTP_CODE_LEN:
        return jsonify({'error': 'Código inválido'}), 400
    # El secret TOTP es base32 de 32 chars. Capeamos a 64 con margen.
    if len(secret) > 64:
        return jsonify({'error': 'Secret inválido'}), 400
    if not check_password_hash(user.password_hash, current_password):
        return jsonify({'error': 'La contraseña actual es incorrecta'}), 401

    # Validar que el secret venga del /setup-2fa más reciente de este usuario.
    # Hacemos PEEK (sin borrar): si el código TOTP es incorrecto, el usuario
    # debe poder reintentar sin re-escanear el QR. El delete ocurre solo al
    # final cuando todo se valida exitosamente.
    pinned = _peek_setup_2fa_secret(user.id)
    if pinned is None:
        return jsonify({
            'error': 'La configuración de 2FA expiró. Vuelve a escanear el código QR.',
        }), 400
    if not secrets.compare_digest(pinned, secret):
        log_action(f"2FA confirm API: secret no coincide con el pineado para {user.username}")
        return jsonify({
            'error': 'El secret no coincide con el de la configuración. Vuelve a escanear el código QR.',
        }), 400

    # Re-keying: si ya hay 2FA activo, exigir el código actual del dispositivo
    # registrado antes de aceptar el nuevo secret.
    if user.totp_secret:
        if not current_2fa_code:
            return jsonify({
                'error': 'Ya tienes 2FA activo. Para cambiar de dispositivo necesitas el código actual.',
                'requires_current_2fa_code': True,
            }), 401
        if not pyotp.TOTP(user.totp_secret).verify(current_2fa_code, valid_window=1):
            log_action(f"2FA re-key API: código actual incorrecto para {user.username}")
            return jsonify({'error': 'Código 2FA actual incorrecto'}), 401
        if _totp_code_already_used(user.id, current_2fa_code):
            return jsonify({'error': 'Ese código ya fue usado. Espera al siguiente.'}), 401

    if not pyotp.TOTP(secret).verify(code, valid_window=1):
        log_action(f"2FA setup API: código incorrecto para {user.username}")
        return jsonify({'error': 'Código incorrecto'}), 400

    try:
        user.totp_secret = secret
        # Activar/cambiar 2FA es un evento de seguridad equivalente a cambiar
        # contraseña: invalida sesiones de otros dispositivos. El access token
        # actual (JWT corto, ≤20 min) sigue vivo hasta que expire, pero los
        # refresh tokens activos quedan revocados — la próxima vez que cualquier
        # otro dispositivo intente refrescar, será forzado a re-login.
        RefreshToken.query.filter_by(user_id=user.id, revoked=False).update({'revoked': True})
        db.session.commit()
        # Solo borrar el pin de Redis tras un commit exitoso. Así, si la BD
        # falla y hace rollback, el usuario puede reintentar sin re-escanear.
        _delete_setup_2fa_secret(user.id)
        log_action(f"2FA activado vía API para {user.username}")
        return jsonify({'ok': True})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error('Error guardando totp_secret: %s', e)
        return jsonify({'error': 'Error al activar 2FA'}), 500


@bp.route('/disable-2fa', methods=['POST'])
@jwt_required
@limiter.limit('4 per minute')
def api_disable_2fa():
    """Desactiva 2FA del propio usuario. Exige:
      - current_password (re-auth contra hijack de sesión).
      - code (TOTP actual válido — prueba que el usuario tiene el dispositivo).
    Doble requisito a propósito: si solo pidiéramos contraseña, un phisher con
    creds podría apagar el 2FA y luego loguear sin segundo factor."""
    user = g._jwt_user
    data = request.get_json(silent=True) or {}
    current_password = data.get('current_password') or data.get('currentPassword') or ''
    code = (data.get('code') or '').strip()

    if not user.totp_secret:
        return jsonify({'error': '2FA no está activo en esta cuenta'}), 400
    if not current_password or not code:
        return jsonify({'error': 'Contraseña actual y código 2FA son obligatorios'}), 400
    if len(current_password) > _MAX_PASSWORD_LEN or len(code) > _MAX_TOTP_CODE_LEN:
        return jsonify({'error': 'Credenciales incorrectas'}), 401
    if not check_password_hash(user.password_hash, current_password):
        return jsonify({'error': 'La contraseña actual es incorrecta'}), 401
    if not pyotp.TOTP(user.totp_secret).verify(code, valid_window=1):
        log_action(f"2FA disable: código incorrecto para {user.username}")
        return jsonify({'error': 'Código 2FA incorrecto'}), 401
    if _totp_code_already_used(user.id, code):
        return jsonify({'error': 'Ese código ya fue usado. Espera al siguiente.'}), 401

    try:
        user.totp_secret = None
        # Desactivar 2FA es un evento de seguridad: revocar todas las sesiones
        # vivas del usuario (otros dispositivos). Consistente con confirm-2fa y
        # con change-password.
        RefreshToken.query.filter_by(user_id=user.id, revoked=False).update({'revoked': True})
        # Y limpiar backup codes: sin 2FA no aplican.
        TwoFactorBackupCode.query.filter_by(user_id=user.id).delete()
        db.session.commit()
        log_action(f"2FA desactivado vía API para {user.username}")
        return jsonify({'ok': True})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error('Error desactivando 2FA: %s', e)
        return jsonify({'error': 'Error al desactivar 2FA'}), 500


# ── Backup codes para 2FA ───────────────────────────────────────────────────

@bp.route('/backup-codes', methods=['GET'])
@jwt_required
def api_backup_codes_status():
    """Devuelve cuántos códigos quedan activos. No revela los códigos."""
    user = g._jwt_user
    if not user.totp_secret:
        return jsonify({'enabled': False, 'remaining': 0})
    remaining = _count_active_backup_codes(user.id)
    return jsonify({
        'enabled': True,
        'remaining': remaining,
        'low': remaining <= _BACKUP_CODES_LOW_THRESHOLD,
    })


@bp.route('/backup-codes', methods=['POST'])
@jwt_required
@limiter.limit('4 per minute')
def api_generate_backup_codes():
    """Genera 10 códigos de respaldo y los devuelve UNA SOLA VEZ.

    Requisitos:
      - 2FA debe estar activo (no tiene sentido generarlos sin TOTP).
      - Reauth con current_password + código TOTP actual válido (no replay).

    Genera SIEMPRE invalida los anteriores (consumidos o no). El cliente debe
    advertir al usuario antes de llamar este endpoint.
    """
    user = g._jwt_user
    data = request.get_json(silent=True) or {}
    current_password = data.get('current_password') or data.get('currentPassword') or ''
    code = (data.get('code') or '').strip()

    if not user.totp_secret:
        return jsonify({'error': '2FA no está activo. Actívalo primero.'}), 400
    if not current_password or not code:
        return jsonify({'error': 'Contraseña actual y código 2FA son obligatorios'}), 400
    if len(current_password) > _MAX_PASSWORD_LEN or len(code) > _MAX_TOTP_CODE_LEN:
        return jsonify({'error': 'Credenciales incorrectas'}), 401
    if not check_password_hash(user.password_hash, current_password):
        return jsonify({'error': 'La contraseña actual es incorrecta'}), 401
    if not pyotp.TOTP(user.totp_secret).verify(code, valid_window=1):
        log_action(f"backup-codes: código TOTP incorrecto para {user.username}")
        return jsonify({'error': 'Código 2FA incorrecto'}), 401
    if _totp_code_already_used(user.id, code):
        return jsonify({'error': 'Ese código ya fue usado. Espera al siguiente.'}), 401

    try:
        codes = _generate_backup_codes(user.id)
        log_action(f"Backup codes 2FA regenerados ({len(codes)}) para {user.username}")
        return jsonify({
            'codes': codes,
            'count': len(codes),
            'warning': (
                'Estos códigos se muestran UNA SOLA VEZ. Guárdalos en lugar '
                'seguro. Cada código solo se puede usar una vez para acceder '
                'cuando no tengas el dispositivo TOTP. Regenerar invalida los '
                'anteriores.'
            ),
        })
    except Exception as e:
        db.session.rollback()
        current_app.logger.error('Error generando backup codes: %s', e)
        return jsonify({'error': 'Error al generar códigos de respaldo'}), 500


@bp.route('/backup-codes', methods=['DELETE'])
@jwt_required
@limiter.limit('4 per minute')
def api_revoke_backup_codes():
    """Revoca TODOS los códigos de respaldo del usuario.

    Útil si el usuario sospecha que la hoja impresa se perdió/robó. Requiere
    reauth con password + código TOTP actual.
    """
    user = g._jwt_user
    data = request.get_json(silent=True) or {}
    current_password = data.get('current_password') or data.get('currentPassword') or ''
    code = (data.get('code') or '').strip()

    if not user.totp_secret:
        return jsonify({'error': '2FA no está activo'}), 400
    if not current_password or not code:
        return jsonify({'error': 'Contraseña actual y código 2FA son obligatorios'}), 400
    if len(current_password) > _MAX_PASSWORD_LEN or len(code) > _MAX_TOTP_CODE_LEN:
        return jsonify({'error': 'Credenciales incorrectas'}), 401
    if not check_password_hash(user.password_hash, current_password):
        return jsonify({'error': 'La contraseña actual es incorrecta'}), 401
    if not pyotp.TOTP(user.totp_secret).verify(code, valid_window=1):
        return jsonify({'error': 'Código 2FA incorrecto'}), 401
    if _totp_code_already_used(user.id, code):
        return jsonify({'error': 'Ese código ya fue usado. Espera al siguiente.'}), 401

    n = TwoFactorBackupCode.query.filter_by(user_id=user.id).delete()
    db.session.commit()
    log_action(f"Backup codes 2FA revocados ({n}) para {user.username}")
    return jsonify({'ok': True, 'revocados': n})
