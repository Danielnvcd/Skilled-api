"""Operaciones sensibles del admin: revocar sesiones y resetear contraseña
de otros usuarios."""
from flask import current_app, jsonify, request
from werkzeug.security import generate_password_hash

from app.extensions import db, limiter
from app.models import RefreshToken, User
from app.routes._api_helpers import current_user, is_super_admin, require_gestion_usuarios
from app.routes.api_auth import jwt_required
from app.utils import is_strong_password, log_action

from ._core import bp


@bp.route('/<int:user_id>/sessions', methods=['DELETE'])
@jwt_required
@limiter.limit('10 per minute')
def admin_revocar_sesiones(user_id):
    """Forzar logout de un usuario en todos sus dispositivos.

    Útil cuando:
    - Un user reporta pérdida/robo de su laptop/teléfono.
    - Sospecha de cuenta comprometida.
    - Empleado dado de baja inmediato.

    Esto revoca todos los refresh tokens, pero NO invalida el JWT access token
    actual (TTL ≤ 20 min). Para forzar logout instantáneo en todos lados,
    cambiar también la contraseña (lo cual incrementa password_version e
    invalida los JWT en uso).
    """
    err = require_gestion_usuarios()
    if err:
        return err

    user = db.session.get(User, user_id)
    if not user:
        return jsonify({'error': 'Usuario no encontrado'}), 404

    # Anti-escalación: `super_admin` es la cuenta de recuperación del sistema y
    # solo otro super_admin puede sacarla de sus sesiones. Cualquier otro rol
    # (incluido admin/RRHH) SÍ puede ser desconectado por `sistemas`: poder
    # cerrarle la sesión a una cuenta comprometida es justamente para lo que
    # existe el rol, y bloquearlo dejaría el incidente sin quién lo contenga.
    if user.role == 'super_admin' and user.id != current_user().id and not is_super_admin():
        return jsonify({
            'error': 'Solo super_admin puede revocar sesiones de una cuenta super_admin'
        }), 403

    n = RefreshToken.query.filter_by(user_id=user.id, revoked=False).update({'revoked': True})
    # Incrementar password_version invalida TODOS los JWT vivos del usuario
    # al instante (no espera al exp). Único camino para forzar logout
    # inmediato sin esperar 20 min ni mantener lista negra masiva de jtis.
    user.password_version = (user.password_version or 1) + 1
    db.session.commit()
    log_action(
        f"Admin revocó {n} sesiones del usuario '{user.username}' "
        f"y forzó logout (password_version++)"
    )
    return jsonify({'ok': True, 'revocadas': n})


@bp.route('/<int:user_id>/password', methods=['POST'])
@jwt_required
@limiter.limit('10 per minute')
def cambiar_password(user_id):
    err = require_gestion_usuarios()
    if err:
        return err

    user = db.session.get(User, user_id)
    if not user:
        return jsonify({'error': 'Usuario no encontrado'}), 404

    # Solo el propio admin puede cambiar la contraseña del usuario 'admin'
    if user.username == 'admin' and user.id != current_user().id:
        return jsonify({'error': 'Solo el usuario administrador puede cambiar su propia contraseña'}), 403

    # Anti-escalación: la contraseña de un `super_admin` solo la resetea otro
    # super_admin — si no, `sistemas` podría tomar la cuenta de recuperación y
    # no quedaría ningún control por encima suyo.
    #
    # Para los demás roles el reseteo SÍ está permitido: es la operación de
    # soporte más común (usuario que perdió su contraseña). Nota: resetear la
    # contraseña NO borra el `totp_secret` (ver más abajo), así que ni siquiera
    # esto alcanza para entrar a una cuenta ajena que tenga 2FA.
    if user.role == 'super_admin' and user.id != current_user().id and not is_super_admin():
        return jsonify({
            'error': 'Solo super_admin puede resetear la contraseña de una cuenta super_admin'
        }), 403

    data = request.get_json(silent=True) or {}
    new_password = data.get('new_password') or data.get('newPassword') or ''
    if not new_password:
        return jsonify({'error': 'La contraseña no puede estar vacía'}), 400
    if not is_strong_password(new_password):
        return jsonify({
            'error': 'La contraseña nueva es débil. Asegúrate de incluir 12 caracteres, mayúsculas, minúsculas, números y símbolos, y que no sea una contraseña común.',
        }), 400

    try:
        user.password_hash = generate_password_hash(new_password)
        user.password_version = (user.password_version or 1) + 1
        # SEGURIDAD: NO borrar `totp_secret` aquí. El segundo factor es
        # independiente del primero — si un admin (incluso malicioso) cambia
        # la contraseña de otro admin, no debe quedar habilitado para entrar
        # solo con la contraseña nueva. El usuario afectado conserva su 2FA.
        # Para resetear el TOTP, el propio usuario debe re-configurarlo desde
        # /api/auth/setup-2fa (requiere current_password).
        RefreshToken.query.filter_by(user_id=user.id, revoked=False).update({'revoked': True})
        db.session.commit()
        if user.id == current_user().id:
            log_action('Cambio de contraseña propio')
        else:
            log_action(f"Cambió la contraseña del usuario '{user.username}'")
        return jsonify({'ok': True})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error('Error cambiando contraseña: %s', e)
        return jsonify({'error': 'Error al actualizar la contraseña'}), 500
