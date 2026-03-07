import re
import os
import uuid
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, current_app, send_from_directory
from werkzeug.security import generate_password_hash
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash
from app.models import User
from app.extensions import db
from app.utils import login_required, admin_required

bp = Blueprint('users', __name__, url_prefix='/users')

def is_strong_password(password):
    if len(password) < 8:
        return False
    if not re.search(r"[A-Z]", password):
        return False
    if not re.search(r"[a-z]", password):
        return False
    if not re.search(r"[0-9]", password):
        return False
    if not re.search(r"[^A-Za-z0-9]", password):
        return False
    return True

@bp.route('/')
@login_required
@admin_required
def list_users():
    users = User.query.all()
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

    # Default logic: only 'admin' and 'coordinador' for now based on user request
    if role not in ['admin', 'coordinador']:
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
def update_profile(user_id):
    user = User.query.get_or_404(user_id)
    
    user.full_name = request.form.get('full_name')
    user.position = request.form.get('position')
    user.area = request.form.get('area')
    user.contact_info = request.form.get('contact_info')
    
    profile_pic = request.files.get('profile_pic')
    if profile_pic and profile_pic.filename != '':
        allowed_exts = {'jpg', 'jpeg', 'png'}
        ext = profile_pic.filename.rsplit('.', 1)[-1].lower() if '.' in profile_pic.filename else ''
        if ext in allowed_exts:
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
            flash('Formato de imagen no válido. Usa JPG o PNG.', 'warning')
    
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

@bp.route('/profile_pic/<path:filename>')
@login_required
def serve_profile_pic(filename):
    """Serve user profile pictures securely."""
    return send_from_directory(current_app.config['UPLOAD_FOLDER'], filename)
