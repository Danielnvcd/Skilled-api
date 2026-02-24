from flask import Blueprint, render_template, request, redirect, url_for, flash
from werkzeug.security import generate_password_hash
from app.models import User
from app.extensions import db
from app.utils import login_required, admin_required

bp = Blueprint('users', __name__, url_prefix='/users')

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

    hashed_password = generate_password_hash(password)
    new_user = User(username=username, password_hash=hashed_password, role=role)
    
    try:
        db.session.add(new_user)
        db.session.commit()
        flash('Usuario creado exitosamente.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error al crear usuario: {e}', 'danger')

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

    user.password_hash = generate_password_hash(new_password)
    
    try:
        db.session.commit()
        flash(f'Contraseña actualizada para {user.username}.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error al actualizar contraseña: {e}', 'danger')

    return redirect(url_for('users.list_users'))

@bp.route('/delete/<int:user_id>', methods=['POST'])
@login_required
@admin_required
def delete_user(user_id):
    user = User.query.get_or_404(user_id)
    
    # Prevent self-deletion if desired, though not explicitly requested, it's good practice.
    # But let's stick to "eliminarlos" request.
    
    try:
        db.session.delete(user)
        db.session.commit()
        flash('Usuario eliminado exitosamente.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error al eliminar usuario: {e}', 'danger')

    return redirect(url_for('users.list_users'))
