import os
import uuid
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, current_app, send_from_directory, jsonify, make_response
from werkzeug.security import generate_password_hash
from werkzeug.utils import secure_filename
from app.models import User
from app.extensions import db, limiter
from app.utils import login_required, admin_required, is_strong_password, allowed_image_file

bp = Blueprint('users', __name__, url_prefix='/users')

_ROLE_ORDER = {
    'super_admin': 0,
    'admin': 1,
    'coordinador': 2,
    'inventario': 3,
    'solicitante_material': 4,
}

@bp.route('/')
@login_required
@admin_required
def list_users():
    users = User.query.all()
    users = sorted(users, key=lambda u: (_ROLE_ORDER.get(u.role, 99), u.username or ''))
    return render_template('users.html', users=users)

@bp.route('/add', methods=['POST'])
@login_required
@admin_required
def add_user():
    username = request.form.get('username')
    password = request.form.get('password')
    role = request.form.get('role')

    if not username or not password or not role:
        flash('Todos los campos son obligatorios.', 'danger')
        return redirect(url_for('users.list_users'))

    if User.query.filter_by(username=username).first():
        flash('El nombre de usuario ya existe.', 'danger')
        return redirect(url_for('users.list_users'))

    # Default logic: expanded with inventory roles
    if role not in ['admin', 'coordinador', 'inventario', 'solicitante_material']:
        flash('Rol no válido.', 'danger')
        return redirect(url_for('users.list_users'))

    if not is_strong_password(password):
        flash('La contraseña es demasiado débil (usa mayúsculas, minúsculas, números y símbolos).', 'danger')
        return redirect(url_for('users.list_users'))

    hashed_password = generate_password_hash(password)
    new_user = User(username=username, password_hash=hashed_password, role=role)
    
    try:
        db.session.add(new_user)
        db.session.commit()
        flash('Usuario creado exitosamente.', 'success')
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f'Error al crear usuario: {e}')
        flash('Ocurrió un error al crear el usuario. Intenta de nuevo.', 'danger')

    return redirect(url_for('users.list_users'))

@bp.route('/update_profile/<int:user_id>', methods=['POST'])
@login_required
@admin_required
@limiter.limit("10 per minute")
def update_profile(user_id):
    user = User.query.get_or_404(user_id)
    
    user.full_name = request.form.get('full_name')
    user.position = request.form.get('position')
    user.area = request.form.get('area')
    user.contact_info = request.form.get('contact_info')
    
    profile_pic = request.files.get('profile_pic')
    if profile_pic and profile_pic.filename != '':
        if allowed_image_file(profile_pic):
            ext = profile_pic.filename.rsplit('.', 1)[-1].lower() if '.' in profile_pic.filename else ''
            filename = secure_filename(profile_pic.filename)
            unique_filename = f"profile_{user.id}_{uuid.uuid4().hex[:8]}.{ext}"
            upload_path = os.path.join(current_app.config['UPLOAD_FOLDER'], unique_filename)
            profile_pic.save(upload_path)
            
            # Remove old pic if not default.png to save space
            if user.profile_pic and user.profile_pic != 'default.png':
                old_pic_path = os.path.join(current_app.config['UPLOAD_FOLDER'], user.profile_pic)
                if os.path.exists(old_pic_path):
                    try:
                        os.remove(old_pic_path)
                    except Exception as e:
                        current_app.logger.warning(f"No se pudo eliminar foto antigua: {e}")
            
            user.profile_pic = unique_filename
        else:
            flash('Foto rechazada: solo se permiten imágenes JPG o PNG reales.', 'warning')
    
    try:
        db.session.commit()
        flash(f'Perfil actualizado para {user.username}.', 'success')
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f'Error al actualizar perfil: {e}')
        flash('Ocurrió un error al actualizar el perfil. Intenta de nuevo.', 'danger')

    return redirect(url_for('users.list_users'))

@bp.route('/update_password/<int:user_id>', methods=['POST'])
@login_required
@admin_required
def update_password(user_id):
    user = User.query.get_or_404(user_id)
    new_password = request.form.get('new_password')

    if not new_password:
        flash('La contraseña no puede estar vacía.', 'danger')
        return redirect(url_for('users.list_users'))

    if not is_strong_password(new_password):
        flash('La contraseña nueva es débil. Asegúrate de incluir 8 caracteres, números y símbolos.', 'danger')
        return redirect(url_for('users.list_users'))

    user.password_hash = generate_password_hash(new_password)
    
    try:
        db.session.commit()
        flash(f'Contraseña actualizada para {user.username}.', 'success')
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f'Error al actualizar contraseña: {e}')
        flash('Ocurrió un error al actualizar la contraseña. Intenta de nuevo.', 'danger')

    return redirect(url_for('users.list_users'))

@bp.route('/delete/<int:user_id>', methods=['POST'])
@login_required
@admin_required
def delete_user(user_id):
    # Bloquear auto-eliminación
    if user_id == session.get('user_id'):
        flash('No puedes eliminar tu propia cuenta.', 'danger')
        return redirect(url_for('users.list_users'))
    
    user = User.query.get_or_404(user_id)
    
    try:
        db.session.delete(user)
        db.session.commit()
        flash(f'Usuario {user.username} eliminado.', 'success')
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f'Error al eliminar usuario: {e}')
        flash('Ocurrió un error al eliminar el usuario.', 'danger')

    return redirect(url_for('users.list_users'))

@bp.route('/profile_pic/<string:filename>')
@login_required
def serve_profile_pic(filename):
    """Serve user profile pictures securely (IDOR + path traversal protection)."""
    if '/' in filename or '\\' in filename or '..' in filename:
        return jsonify({'error': 'Ruta inválida'}), 400

    user_id = session.get('user_id')
    user = User.query.get(user_id)

    if not user:
        return jsonify({'error': 'No autorizado'}), 403

    if user.role not in ['admin', 'super_admin'] and user.profile_pic != filename and filename != 'default.png':
        return jsonify({'error': 'Acceso denegado'}), 403

    if current_app.config.get('USE_X_ACCEL_REDIRECT'):
        response = make_response('')
        response.headers['X-Accel-Redirect'] = f'/x-accel-uploads/{filename}'
        response.headers['Cache-Control'] = 'private, max-age=86400'
        return response

    response = make_response(send_from_directory(current_app.config['UPLOAD_FOLDER'], filename))
    response.headers['Cache-Control'] = 'private, max-age=86400'
    return response
