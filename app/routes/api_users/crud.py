"""CRUD básico de usuarios: listar, crear, actualizar, eliminar."""
from flask import current_app, jsonify, request
from werkzeug.security import generate_password_hash

from app.extensions import db, limiter
from app.models import Trabajador, User
from app.realtime import emit_to_role
from app.routes._api_helpers import (
    api_transactional, current_user, is_super_admin, require_admin,
)
from app.routes.api_auth import jwt_required
from app.utils import is_strong_password, log_action

from ._core import _ROLE_ORDER, _VALID_NEW_ROLES, _user_to_dict, bp


@bp.route('', methods=['GET'])
@jwt_required
def listar():
    err = require_admin('Solo admin puede administrar usuarios')
    if err:
        return err
    users = User.query.all()
    users.sort(key=lambda u: (_ROLE_ORDER.get(u.role, 99), (u.username or '').lower()))
    return jsonify([_user_to_dict(u) for u in users])


@bp.route('', methods=['POST'])
@jwt_required
@limiter.limit('10 per minute')
@api_transactional('Error al crear el usuario')
def crear():
    err = require_admin('Solo admin puede administrar usuarios')
    if err:
        return err

    data = request.get_json(silent=True) or {}
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''
    role = (data.get('role') or '').strip()

    if not username or not password or not role:
        return jsonify({'error': 'Todos los campos son obligatorios'}), 400

    if role not in _VALID_NEW_ROLES:
        return jsonify({'error': 'Rol no válido'}), 400

    if User.query.filter_by(username=username).first():
        return jsonify({'error': 'El nombre de usuario ya existe'}), 409

    if not is_strong_password(password):
        return jsonify({
            'error': 'La contraseña es débil. Usa mínimo 8 caracteres con mayúsculas, minúsculas, números y símbolos.',
        }), 400

    new_user = User(
        username=username,
        password_hash=generate_password_hash(password),
        role=role,
    )
    db.session.add(new_user)
    db.session.commit()
    log_action(f"Creó usuario '{username}' con rol '{role}'")
    emit_to_role(['admin', 'super_admin'], 'usuario:changed', {
        'id': new_user.id, 'action': 'created',
    })
    return jsonify(_user_to_dict(new_user)), 201


@bp.route('/<int:user_id>', methods=['PUT'])
@jwt_required
@limiter.limit('20 per minute')
@api_transactional('Error al actualizar el usuario')
def actualizar(user_id):
    """Admin edita el perfil de otro usuario (full_name, area, position, factory,
    contact_info). Acepta JSON; los campos no enviados se mantienen iguales.

    Nota de seguridad: el rol NO puede modificarse desde este endpoint. Cambiar
    el rol de un usuario en caliente puede romper permisos en sesiones activas
    y abrir caminos de escalación. Si necesitas mover a alguien de rol, elimina
    y recrea la cuenta con el rol correcto.
    """
    err = require_admin('Solo admin puede administrar usuarios')
    if err:
        return err

    user = User.query.get(user_id)
    if not user:
        return jsonify({'error': 'Usuario no encontrado'}), 404

    data = request.get_json(silent=True) or {}

    # Campos de texto libres (None = no tocar; '' = limpiar)
    for field in ('full_name', 'area', 'position', 'factory', 'contact_info'):
        if field in data:
            value = data.get(field)
            setattr(user, field, (value or '').strip() or None)

    # trabajador_id: liga opcional con RRHH. Convenciones del payload:
    #   ausente            → no se toca el valor actual.
    #   null o 0 o ''      → desvincula (queda en None).
    #   int existente      → liga, validando que el trabajador exista.
    # Anti-pisada: cuando ligamos a un trabajador, validamos que NO esté ya
    # ligado a otro usuario (la relación es 1:1 a nivel de negocio: cada
    # empleado tiene un único acceso).
    if 'trabajador_id' in data:
        raw = data.get('trabajador_id')
        if raw in (None, 0, '', '0'):
            user.trabajador_id = None
        else:
            try:
                tid = int(raw)
            except (TypeError, ValueError):
                return jsonify({'error': 'trabajador_id debe ser un número entero'}), 400
            trabajador = Trabajador.query.get(tid)
            if not trabajador:
                return jsonify({'error': f'Trabajador #{tid} no existe'}), 404
            # Validar 1:1: si otro usuario ya está ligado a este trabajador, rechazar.
            existente = User.query.filter(
                User.trabajador_id == tid,
                User.id != user.id,
            ).first()
            if existente:
                return jsonify({
                    'error': (
                        f'El empleado {trabajador.no_empleado} ya está ligado al usuario '
                        f"'{existente.username}'. Desvincula primero."
                    ),
                }), 409
            user.trabajador_id = tid

    # El rol queda explícitamente fuera del payload aceptado. Si alguien lo
    # incluye, se ignora silenciosamente.

    db.session.commit()
    log_action(f"Actualizó perfil del usuario '{user.username}'")
    emit_to_role(['admin', 'super_admin'], 'usuario:changed', {
        'id': user.id, 'action': 'updated',
    })
    return jsonify(_user_to_dict(user))


@bp.route('/<int:user_id>', methods=['DELETE'])
@jwt_required
def eliminar(user_id):
    err = require_admin('Solo admin puede administrar usuarios')
    if err:
        return err

    if user_id == current_user().id:
        return jsonify({'error': 'No puedes eliminar tu propia cuenta'}), 400

    user = User.query.get(user_id)
    if not user:
        return jsonify({'error': 'Usuario no encontrado'}), 404

    if user.username == 'admin':
        return jsonify({'error': 'El usuario administrador no puede ser eliminado'}), 400

    # super_admin queda protegido como última línea de recuperación: solo otro
    # super_admin puede eliminarlo.
    if user.role == 'super_admin' and not is_super_admin():
        return jsonify({'error': 'Solo super_admin puede eliminar cuentas super_admin'}), 403

    try:
        deleted_id = user.id
        db.session.delete(user)
        db.session.commit()
        log_action(f"Eliminó usuario '{user.username}'")
        emit_to_role(['admin', 'super_admin'], 'usuario:changed', {
            'id': deleted_id, 'action': 'deleted',
        })
        return jsonify({'ok': True})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error('Error eliminando usuario: %s', e)
        return jsonify({'error': 'Error al eliminar el usuario'}), 500
