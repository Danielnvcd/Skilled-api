"""Endpoints de perfil propio y directorio interno.

Incluye:
  - /me, /me/activity (perfil propio + auditoría)
  - /users, /users/<id>, /users/<id>/foto (directorio)
  - /profile, /profile/foto (edición + foto)
  - /change-password/<id> (cambio propio con TOTP si aplica)
"""
import os
import uuid

import pyotp
from flask import current_app, g, jsonify, request, send_from_directory
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db, limiter
from app.models import RefreshToken, User
from app.utils import log_action

from ._core import (
    _MAX_PASSWORD_LEN,
    _MAX_TOTP_CODE_LEN,
    _is_admin_user,
    _user_to_dict,
    _user_to_dict_public,
    bp,
)
from .jwt_required import jwt_required
from .tokens import _totp_code_already_used


@bp.route('/me', methods=['GET'])
@jwt_required
@limiter.limit("60 per minute")
def api_me():
    return jsonify(_user_to_dict(g._jwt_user))


@bp.route('/me/activity', methods=['GET'])
@jwt_required
@limiter.limit("30 per minute")
def api_my_activity():
    """Devuelve las últimas acciones del PROPIO usuario en el audit log.

    Útil en el perfil para que el usuario vea qué hizo recientemente:
    logins, cambios de password, configuración de 2FA, generación de
    backup codes, etc. Solo lectura, sin PII de otros usuarios.

    `?limit=N` (default 20, máx 50).
    """
    from app.models import AuditLog
    user = g._jwt_user
    try:
        limit = int(request.args.get('limit') or 20)
    except (TypeError, ValueError):
        limit = 20
    limit = max(1, min(limit, 50))

    rows = (
        AuditLog.query
        .filter(AuditLog.user == user.username)
        .order_by(AuditLog.created_at.desc())
        .limit(limit)
        .all()
    )
    return jsonify([
        {
            'id': r.id,
            'action': r.action,
            'ip': r.ip,
            'created_at': r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ])


# ── Directorio / perfiles ajenos ────────────────────────────────────────────

@bp.route('/users', methods=['GET'])
@jwt_required
def api_list_users():
    """Lista de usuarios para el directorio.

    HIGH-01 fix: admin/super_admin reciben datos completos (role, 2FA, last_seen);
    el resto recibe vista pública (sin role / 2FA / last_seen) — esto evita que
    coordinadores, inventario o solicitantes_material enumeren admins sin 2FA
    para targetear con brute-force o ataquen ventanas de actividad.
    """
    serializer = _user_to_dict if _is_admin_user(g._jwt_user) else _user_to_dict_public
    users = User.query.all()
    return jsonify([serializer(u) for u in users])


@bp.route('/users/<int:user_id>', methods=['GET'])
@jwt_required
def api_get_user(user_id: int):
    """Detalle de un usuario para ver su perfil desde el directorio.

    HIGH-01 fix: si el solicitante es admin o es el propio usuario, ve todo.
    Otros usuarios reciben la vista pública (sin role/2FA/last_seen).
    """
    user = db.session.get(User, user_id)
    if not user:
        return jsonify({'error': 'Usuario no encontrado'}), 404
    is_self = (user.id == g._jwt_user.id)
    serializer = _user_to_dict if (is_self or _is_admin_user(g._jwt_user)) else _user_to_dict_public
    return jsonify(serializer(user))


@bp.route('/users/<int:user_id>/foto', methods=['GET'])
@jwt_required
def api_get_user_foto(user_id: int):
    """Sirve la foto de perfil con autenticación JWT (Bearer en Authorization)."""
    user = db.session.get(User, user_id)
    if not user or not user.profile_pic or user.profile_pic == 'default.png':
        return jsonify({'error': 'Sin foto'}), 404
    filename = user.profile_pic
    # Anti path-traversal: profile_pic se genera siempre internamente con secure_filename
    if '/' in filename or '\\' in filename or '..' in filename:
        return jsonify({'error': 'Ruta inválida'}), 400
    folder = current_app.config['UPLOAD_FOLDER']
    full = os.path.join(folder, filename)
    if not os.path.exists(full):
        return jsonify({'error': 'Foto no encontrada en disco'}), 404
    return send_from_directory(folder, filename)


# ── Perfil propio ───────────────────────────────────────────────────────────

@bp.route('/profile', methods=['POST'])
@jwt_required
@limiter.limit('15 per minute')
def api_update_profile():
    """Actualiza datos del perfil propio (multipart para soportar la foto)."""
    from app.utils import allowed_image_file, image_to_webp

    user = g._jwt_user
    data = request.form if request.form else (request.get_json(silent=True) or {})

    # Aceptar tanto snake_case como camelCase (compatibilidad con frontends mixtos)
    def field(name_snake, name_camel=None):
        return data.get(name_snake) or (data.get(name_camel) if name_camel else None)

    full_name = field('full_name', 'fullName')
    area = field('area')
    position = field('position')
    factory = field('factory')
    contact_info = field('contact_info', 'contactInfo')

    if full_name is not None: user.full_name = full_name
    if area is not None: user.area = area
    if position is not None: user.position = position
    if factory is not None: user.factory = factory
    if contact_info is not None: user.contact_info = contact_info

    foto = request.files.get('profile_pic') or request.files.get('profilePic')
    if foto and foto.filename:
        if not allowed_image_file(foto):
            return jsonify({'error': 'Foto rechazada: solo se permiten imágenes JPG o PNG reales.'}), 400
        unique_filename = f"profile_{user.id}_{uuid.uuid4().hex[:8]}.webp"
        upload_path = os.path.join(current_app.config['UPLOAD_FOLDER'], unique_filename)
        webp_buf = image_to_webp(foto)
        with open(upload_path, 'wb') as f:
            f.write(webp_buf.getvalue())
        # Borrar foto vieja (si no es default) para no llenar disco
        if user.profile_pic and user.profile_pic != 'default.png':
            old = os.path.join(current_app.config['UPLOAD_FOLDER'], user.profile_pic)
            if os.path.exists(old):
                try:
                    os.remove(old)
                except Exception as e:
                    current_app.logger.warning('No se pudo eliminar foto vieja: %s', e)
        user.profile_pic = unique_filename

    try:
        db.session.commit()
        log_action(f"Actualizó su perfil ({user.username})")
        return jsonify(_user_to_dict(user))
    except Exception as e:
        db.session.rollback()
        current_app.logger.error('Error actualizando perfil API: %s', e)
        return jsonify({'error': 'Error al actualizar el perfil'}), 500


@bp.route('/profile/foto', methods=['DELETE'])
@jwt_required
@limiter.limit('10 per minute')
def api_delete_profile_foto():
    """Elimina la foto de perfil del propio usuario.

    Borra el archivo del disco (si no es la default) y limpia el campo
    profile_pic a None. La UI mostrará las iniciales del usuario después.
    """
    user = g._jwt_user
    old = user.profile_pic
    user.profile_pic = None

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        current_app.logger.error('Error eliminando foto de perfil: %s', e)
        return jsonify({'error': 'Error al eliminar la foto'}), 500

    # Limpieza de disco best-effort — no falla la op si el archivo no existe.
    if old and old != 'default.png':
        path = os.path.join(current_app.config['UPLOAD_FOLDER'], old)
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception as e:
                current_app.logger.warning('No se pudo eliminar archivo de foto: %s', e)

    log_action(f"Eliminó su foto de perfil ({user.username})")
    return jsonify(_user_to_dict(user))


@bp.route('/change-password/<int:user_id>', methods=['POST'])
@jwt_required
@limiter.limit('10 per minute')
def api_change_own_password(user_id: int):
    """Cambia la contraseña propia. Requiere current_password y, si 2FA está
    activo, también un código TOTP actual válido (anti-replay).

    Refuerzo de seguridad: sin la exigencia de TOTP, un atacante con sesión
    activa + contraseña filtrada (phishing en la propia app) podía cambiar la
    contraseña sin tocar el 2FA. Aunque el 2FA seguiría protegiendo el login,
    el atacante podría bloquear al usuario legítimo y mantener su acceso vía
    la sesión que sigue viva (los RTs se revocan acá, pero el JWT actual no).
    Exigir TOTP en el mismo flujo cierra ese hueco.
    """
    from app.utils import is_strong_password
    user = g._jwt_user
    if user.id != user_id:
        return jsonify({'error': 'Solo puedes cambiar tu propia contraseña'}), 403

    data = request.get_json(silent=True) or {}
    current_password = data.get('current_password') or data.get('currentPassword') or ''
    new_password = data.get('new_password') or data.get('newPassword') or ''
    totp_code = (data.get('code') or data.get('totp_code') or '').strip()

    if not current_password or not new_password:
        return jsonify({'error': 'Contraseña actual y nueva son obligatorias'}), 400
    if len(current_password) > _MAX_PASSWORD_LEN or len(new_password) > _MAX_PASSWORD_LEN:
        return jsonify({'error': 'Contraseña excede longitud permitida'}), 400
    if not check_password_hash(user.password_hash, current_password):
        return jsonify({'error': 'La contraseña actual es incorrecta'}), 401

    # Exigir TOTP cuando el usuario lo tiene activo. Devolvemos un flag
    # `requires_totp` para que el cliente sepa mostrar el campo y no presente
    # el error como "contraseña incorrecta" (lo cual confundiría al usuario).
    if user.totp_secret:
        if not totp_code:
            return jsonify({
                'error': 'Se requiere código 2FA para cambiar la contraseña',
                'requires_totp': True,
            }), 401
        if len(totp_code) > _MAX_TOTP_CODE_LEN:
            return jsonify({'error': 'Código 2FA inválido'}), 401
        if not pyotp.TOTP(user.totp_secret).verify(totp_code, valid_window=1):
            return jsonify({'error': 'Código 2FA incorrecto'}), 401
        if _totp_code_already_used(user.id, totp_code):
            return jsonify({'error': 'Ese código ya fue usado. Espera al siguiente.'}), 401

    if not is_strong_password(new_password):
        return jsonify({
            'error': 'La contraseña nueva es débil. Mínimo 12 caracteres con mayúsculas, minúsculas, números y símbolos, y que no sea una contraseña común.',
        }), 400
    if current_password == new_password:
        return jsonify({'error': 'La nueva contraseña debe ser diferente a la actual'}), 400

    try:
        user.password_hash = generate_password_hash(new_password)
        user.password_version = (user.password_version or 1) + 1
        # SEGURIDAD: NO desactivar 2FA al cambiar la contraseña propia. El TOTP
        # es un factor independiente — el usuario que rota su password no debe
        # perder el segundo factor (UX y postura defensiva). Para quitar el
        # TOTP el usuario debe usar el flujo dedicado de "Desactivar 2FA"
        # (requiere current_password).
        RefreshToken.query.filter_by(user_id=user.id, revoked=False).update({'revoked': True})
        db.session.commit()
        log_action('Cambio de contraseña propio (API)')
        return jsonify({'ok': True})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error('Error cambiando contraseña API: %s', e)
        return jsonify({'error': 'Error al actualizar la contraseña'}), 500
