"""API JSON para administración de usuarios (consumida por el SPA React).

Replica `users.py` pero responde JSON y autentica con JWT. Solo accesible para
super_admin. Mantiene las protecciones del flujo clásico: no se puede eliminar
la propia cuenta ni al usuario `admin`; al cambiar la contraseña se revocan
todos los refresh tokens del usuario (cierra sesión en todos sus dispositivos).
"""
import traceback

from flask import Blueprint, current_app, g, jsonify, request
from werkzeug.security import generate_password_hash

from app.extensions import db, limiter
from app.models import RefreshToken, Trabajador, User
from app.routes.api_auth import jwt_required
from app.utils import is_strong_password, log_action

bp = Blueprint('api_users', __name__, url_prefix='/api/users')


_ROLE_ORDER = {
    'super_admin': 0,
    'admin': 1,
    'coordinador': 2,
    'inventario': 3,
    'solicitante_material': 4,
}

_VALID_NEW_ROLES = {'admin', 'coordinador', 'inventario', 'solicitante_material'}

# super_admin nunca se puede crear/asignar desde este endpoint (no está en
# _VALID_NEW_ROLES); esa cuenta solo se crea por seeding/manual.
# Los admins pueden crear y eliminar otros admins por decisión operativa.


def _u():
    return g._jwt_user


def _is_admin() -> bool:
    return _u().role in ('admin', 'super_admin')


def _is_super_admin() -> bool:
    return _u().role == 'super_admin'


def _admin_only():
    if not _is_admin():
        return jsonify({'error': 'Solo admin puede administrar usuarios'}), 403
    return None


def _user_to_dict(u: User) -> dict:
    t = u.trabajador  # FK opcional; relationship lazy='select'
    return {
        'id': u.id,
        'username': u.username,
        'role': u.role,
        'full_name': u.full_name,
        'area': u.area,
        'position': u.position,
        'factory': u.factory,
        'contact_info': u.contact_info,
        'profile_pic': u.profile_pic,
        'totp_enabled': bool(u.totp_secret),
        'last_seen': u.last_seen.isoformat() if u.last_seen else None,
        # Liga opcional a Trabajador (RRHH) — habilita asignaciones de
        # herramienta y filtros "lo mío" basados en empleado.
        'trabajador_id': u.trabajador_id,
        'trabajador_no_empleado': t.no_empleado if t else None,
        'trabajador_nombre': t.nombre_completo if t else None,
    }


@bp.route('', methods=['GET'])
@jwt_required
def listar():
    err = _admin_only()
    if err:
        return err
    users = User.query.all()
    users.sort(key=lambda u: (_ROLE_ORDER.get(u.role, 99), (u.username or '').lower()))
    return jsonify([_user_to_dict(u) for u in users])


@bp.route('', methods=['POST'])
@jwt_required
@limiter.limit('10 per minute')
def crear():
    err = _admin_only()
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

    try:
        new_user = User(
            username=username,
            password_hash=generate_password_hash(password),
            role=role,
        )
        db.session.add(new_user)
        db.session.commit()
        log_action(f"Creó usuario '{username}' con rol '{role}'")
        return jsonify(_user_to_dict(new_user)), 201
    except Exception as e:
        db.session.rollback()
        current_app.logger.error('Error creando usuario: %s\n%s', e, traceback.format_exc())
        return jsonify({'error': 'Error al crear el usuario'}), 500


@bp.route('/<int:user_id>', methods=['PUT'])
@jwt_required
@limiter.limit('20 per minute')
def actualizar(user_id):
    """Admin edita el perfil de otro usuario (full_name, area, position, factory,
    contact_info). Acepta JSON; los campos no enviados se mantienen iguales.

    Nota de seguridad: el rol NO puede modificarse desde este endpoint. Cambiar
    el rol de un usuario en caliente puede romper permisos en sesiones activas
    y abrir caminos de escalación. Si necesitas mover a alguien de rol, elimina
    y recrea la cuenta con el rol correcto.
    """
    err = _admin_only()
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

    try:
        db.session.commit()
        log_action(f"Actualizó perfil del usuario '{user.username}'")
        return jsonify(_user_to_dict(user))
    except Exception as e:
        db.session.rollback()
        current_app.logger.error('Error actualizando usuario: %s\n%s', e, traceback.format_exc())
        return jsonify({'error': 'Error al actualizar el usuario'}), 500


@bp.route('/<int:user_id>/foto', methods=['POST'])
@jwt_required
@limiter.limit('15 per minute')
def subir_foto(user_id):
    """Admin sube/reemplaza la foto de perfil de un usuario. Multipart con campo
    `foto_perfil` o `profile_pic`. Reusa la misma política que `/auth/profile`:
    valida que sea imagen real, genera filename único con uuid, borra la foto
    anterior si no es la default."""
    import os
    import uuid

    from app.utils import allowed_image_file

    err = _admin_only()
    if err:
        return err

    user = User.query.get(user_id)
    if not user:
        return jsonify({'error': 'Usuario no encontrado'}), 404

    foto = request.files.get('foto_perfil') or request.files.get('profile_pic')
    if not foto or not foto.filename:
        return jsonify({'error': 'Adjunta una imagen en el campo foto_perfil'}), 400
    if not allowed_image_file(foto):
        return jsonify({'error': 'Foto rechazada: solo se permiten imágenes JPG o PNG reales.'}), 400

    ext = foto.filename.rsplit('.', 1)[-1].lower() if '.' in foto.filename else 'png'
    unique_filename = f"profile_{user.id}_{uuid.uuid4().hex[:8]}.{ext}"
    upload_path = os.path.join(current_app.config['UPLOAD_FOLDER'], unique_filename)

    try:
        foto.save(upload_path)
    except Exception as e:
        current_app.logger.error('Error guardando foto: %s', e)
        return jsonify({'error': 'No se pudo guardar la imagen'}), 500

    # Borrar foto vieja para no llenar disco
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
        log_action(f"Actualizó la foto del usuario '{user.username}'")
        return jsonify(_user_to_dict(user))
    except Exception as e:
        db.session.rollback()
        current_app.logger.error('Error guardando foto en DB: %s\n%s', e, traceback.format_exc())
        return jsonify({'error': 'Error al actualizar la foto'}), 500


@bp.route('/<int:user_id>', methods=['DELETE'])
@jwt_required
def eliminar(user_id):
    err = _admin_only()
    if err:
        return err

    if user_id == _u().id:
        return jsonify({'error': 'No puedes eliminar tu propia cuenta'}), 400

    user = User.query.get(user_id)
    if not user:
        return jsonify({'error': 'Usuario no encontrado'}), 404

    if user.username == 'admin':
        return jsonify({'error': 'El usuario administrador no puede ser eliminado'}), 400

    # super_admin queda protegido como última línea de recuperación: solo otro
    # super_admin puede eliminarlo.
    if user.role == 'super_admin' and not _is_super_admin():
        return jsonify({'error': 'Solo super_admin puede eliminar cuentas super_admin'}), 403

    try:
        db.session.delete(user)
        db.session.commit()
        log_action(f"Eliminó usuario '{user.username}'")
        return jsonify({'ok': True})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error('Error eliminando usuario: %s', e)
        return jsonify({'error': 'Error al eliminar el usuario'}), 500


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
    err = _admin_only()
    if err:
        return err

    user = User.query.get(user_id)
    if not user:
        return jsonify({'error': 'Usuario no encontrado'}), 404

    # Anti-escalación lateral: solo super_admin puede revocar sesiones de otros admins
    if user.role in ('admin', 'super_admin') and user.id != _u().id and not _is_super_admin():
        return jsonify({
            'error': 'Solo super_admin puede revocar sesiones de otros admins'
        }), 403

    n = RefreshToken.query.filter_by(user_id=user.id, revoked=False).update({'revoked': True})
    db.session.commit()
    log_action(f"Admin revocó {n} sesiones del usuario '{user.username}'")
    return jsonify({'ok': True, 'revocadas': n})


@bp.route('/<int:user_id>/password', methods=['POST'])
@jwt_required
@limiter.limit('10 per minute')
def cambiar_password(user_id):
    err = _admin_only()
    if err:
        return err

    user = User.query.get(user_id)
    if not user:
        return jsonify({'error': 'Usuario no encontrado'}), 404

    # Solo el propio admin puede cambiar la contraseña del usuario 'admin'
    if user.username == 'admin' and user.id != _u().id:
        return jsonify({'error': 'Solo el usuario administrador puede cambiar su propia contraseña'}), 403

    # Anti-escalación lateral: un admin no puede resetear la password de otro
    # admin (eso permitiría takeover de cuentas entre admins). Solo el propio
    # usuario o un super_admin pueden hacerlo.
    if user.role in ('admin', 'super_admin') and user.id != _u().id and not _is_super_admin():
        return jsonify({
            'error': 'Solo super_admin puede resetear la contraseña de otros admins'
        }), 403

    data = request.get_json(silent=True) or {}
    new_password = data.get('new_password') or data.get('newPassword') or ''
    if not new_password:
        return jsonify({'error': 'La contraseña no puede estar vacía'}), 400
    if not is_strong_password(new_password):
        return jsonify({
            'error': 'La contraseña nueva es débil. Asegúrate de incluir 8 caracteres, mayúsculas, minúsculas, números y símbolos.',
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
        if user.id == _u().id:
            log_action('Cambio de contraseña propio')
        else:
            log_action(f"Cambió la contraseña del usuario '{user.username}'")
        return jsonify({'ok': True})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error('Error cambiando contraseña: %s', e)
        return jsonify({'error': 'Error al actualizar la contraseña'}), 500
