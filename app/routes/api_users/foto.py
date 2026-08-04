"""Foto de perfil: admin sube/reemplaza la foto de otro usuario."""
import traceback
import uuid

from flask import current_app, jsonify, request

from app.extensions import db, limiter
from app.models import User
from app.realtime import emit_to_role, ROLES_TODOS
from app.routes._api_helpers import require_gestion_usuarios
from app.routes.api_auth import jwt_required
from app.utils import allowed_image_file, archivos, image_to_webp, log_action

from ._core import _user_to_dict, bp


@bp.route('/<int:user_id>/foto', methods=['POST'])
@jwt_required
@limiter.limit('15 per minute')
def subir_foto(user_id):
    """Admin sube/reemplaza la foto de perfil de un usuario. Multipart con campo
    `foto_perfil` o `profile_pic`. Reusa la misma política que `/auth/profile`:
    valida que sea imagen real, genera filename único con uuid, borra la foto
    anterior si no es la default."""
    err = require_gestion_usuarios()
    if err:
        return err

    user = db.session.get(User, user_id)
    if not user:
        return jsonify({'error': 'Usuario no encontrado'}), 404

    foto = request.files.get('foto_perfil') or request.files.get('profile_pic')
    if not foto or not foto.filename:
        return jsonify({'error': 'Adjunta una imagen en el campo foto_perfil'}), 400
    if not allowed_image_file(foto):
        return jsonify({'error': 'Foto rechazada: solo se permiten imágenes JPG o PNG reales.'}), 400

    unique_filename = f"profile_{user.id}_{uuid.uuid4().hex[:8]}.webp"

    try:
        archivos.guardar(unique_filename, image_to_webp(foto).getvalue(), 'image/webp')
    except Exception as e:
        current_app.logger.error('Error guardando foto: %s', e)
        return jsonify({'error': 'No se pudo guardar la imagen'}), 500

    # Borrar foto vieja para no acumular basura (en R2 y en disco)
    if user.profile_pic and user.profile_pic != 'default.png':
        archivos.eliminar(user.profile_pic)

    user.profile_pic = unique_filename

    try:
        db.session.commit()
        log_action(f"Actualizó la foto del usuario '{user.username}'")
        emit_to_role(ROLES_TODOS, 'usuario:changed', {
            'id': user.id, 'action': 'foto',
        })
        return jsonify(_user_to_dict(user))
    except Exception as e:
        db.session.rollback()
        current_app.logger.error('Error guardando foto en DB: %s\n%s', e, traceback.format_exc())
        return jsonify({'error': 'Error al actualizar la foto'}), 500
