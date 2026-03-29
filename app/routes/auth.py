from flask import Blueprint, render_template, redirect, url_for, flash, request, session, current_app
from werkzeug.security import generate_password_hash, check_password_hash
from app.extensions import db, limiter, get_redis
from app.models import User
from app.utils import log_action, login_required, is_strong_password
import pyotp
import qrcode
import io
import base64
from datetime import datetime

bp = Blueprint('auth', __name__)

@bp.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    user = User.query.get(session['user_id'])
    
    if not user:
        session.clear()
        flash('Tu sesión es inválida. Por favor inicia sesión de nuevo.', 'danger')
        return redirect(url_for('auth.login'))
    
    if request.method == 'POST':
        new_password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')
        
        if new_password != confirm_password:
            flash('Las contraseñas no coinciden.', 'danger')
        elif not is_strong_password(new_password):
            flash('La nueva contraseña es demasiado débil (usa mayúsculas, minúsculas, números y símbolos).', 'danger')
        else:
            user.password_hash = generate_password_hash(new_password)
            db.session.commit()
            flash('Contraseña actualizada correctamente.', 'success')
            log_action(f"Contraseña actualizada para {user.username}")
            
    return render_template('profile.html', user=user)

@bp.before_app_request
def update_last_seen():
    if 'user_id' not in session:
        return
    if request.endpoint and 'static' in request.endpoint:
        return

    user_id = session['user_id']
    r = get_redis()

    if r:
        # Si la key existe en Redis, significa que ya actualizamos hace menos de 5 min
        cache_key = f"last_seen:{user_id}"
        if r.get(cache_key):
            return  # Aún no toca actualizar

        # Key expiró o no existe → actualizar BD y renovar cache
        try:
            user = User.query.get(user_id)
            if user:
                user.last_seen = datetime.now()
                db.session.commit()
                r.setex(cache_key, 300, "1")  # TTL de 5 minutos
        except Exception as e:
            current_app.logger.warning(f"Error updating last_seen: {e}")
            db.session.rollback()
    else:
        # Fallback sin Redis: throttle via session (cada 5 min)
        import time
        now = time.time()
        last_update = session.get('_last_seen_ts', 0)
        if now - last_update < 300:  # 5 minutos
            return
        try:
            user = User.query.get(user_id)
            if user:
                user.last_seen = datetime.now()
                db.session.commit()
                session['_last_seen_ts'] = now
        except Exception as e:
            current_app.logger.warning(f"Error updating last_seen: {e}")
            db.session.rollback()

@bp.route('/login', methods=['GET', 'POST'])
@limiter.limit("5 per 1 minutes", methods=['POST'])
def login():
    if request.method == 'POST':
        u = User.query.filter_by(username=request.form.get('username')).first()
        
        if u and check_password_hash(u.password_hash, request.form.get('password')):
            session.clear()
            
            if u.totp_secret:
                session['pre_2fa_user_id'] = u.id
                return redirect(url_for('auth.verify_2fa'))
            
            session['user_id'] = u.id
            session['user'] = u.username
            session['role'] = u.role
            session.permanent = True
            u.last_seen = datetime.now()
            db.session.commit()
            log_action("Login exitoso") 
            
            if u.role == 'coordinador':
                return redirect(url_for('horas.index'))
            return redirect(url_for('main.home'))
        
        flash('Credenciales incorrectas', 'danger')
        log_action(f"Login fallido para usuario {request.form.get('username')}")
        
    return render_template('login.html')

@bp.route('/verify-2fa', methods=['GET', 'POST'])
@limiter.limit("5 per 1 minutes", methods=['POST'])
def verify_2fa():
    if 'pre_2fa_user_id' not in session:
        return redirect(url_for('auth.login'))
    
    if request.method == 'POST':
        user = User.query.get(session['pre_2fa_user_id'])
        code = request.form.get('code')
        
        if not user or not user.totp_secret:
            session.pop('pre_2fa_user_id', None)
            flash('Sesión inválida. Inicia sesión de nuevo.', 'danger')
            return redirect(url_for('auth.login'))
        
        totp = pyotp.TOTP(user.totp_secret)
        if totp.verify(code, valid_window=1):
            session.pop('pre_2fa_user_id', None)
            session['user_id'] = user.id
            session['user'] = user.username
            session['role'] = user.role
            session.permanent = True
            log_action(f"Login 2FA exitoso para {user.username}")
            
            if user.role == 'coordinador':
                return redirect(url_for('horas.index'))
            return redirect(url_for('main.home'))
        else:
            log_action(f"2FA fallido para {user.username}")
            flash('Código incorrecto.', 'danger')
            
    return render_template('verify_2fa.html')

@bp.route('/setup-2fa', methods=['GET', 'POST'])
@login_required
def setup_2fa():
    user = User.query.get(session['user_id'])
    
    if request.method == 'POST':
        secret = session.get('totp_secret_setup')
        code = request.form.get('code')
        
        if not secret:
            flash('Error en la sesión. Intenta de nuevo.', 'danger')
            return redirect(url_for('auth.setup_2fa'))
            
        totp = pyotp.TOTP(secret)
        if totp.verify(code, valid_window=1):
            user.totp_secret = secret
            db.session.commit()
            session.pop('totp_secret_setup', None)
            flash('Doble factor de autenticación activado correctamente.', 'success')
            log_action(f"2FA activado para {user.username}")
            return redirect(url_for('main.home'))
        else:
            log_action(f"2FA setup: código incorrecto para {user.username}")
            flash('Código incorrecto.', 'danger')

    if 'totp_secret_setup' not in session:
        session['totp_secret_setup'] = pyotp.random_base32()
    
    secret = session['totp_secret_setup']
    
    totp_uri = pyotp.totp.TOTP(secret).provisioning_uri(
        name=user.username,
        issuer_name="SistemaNominas"
    )
    
    img = qrcode.make(totp_uri)
    buffered = io.BytesIO()
    img.save(buffered, format="PNG")
    qr_base64 = base64.b64encode(buffered.getvalue()).decode('utf-8')
    
    return render_template('setup_2fa.html', qr_code=qr_base64, secret=secret)

@bp.route('/logout', methods=['POST'])
@login_required
def logout():
    # Registrar actividad ANTES de limpiar sesión
    user = User.query.get(session.get('user_id'))
    if user:
        user.last_seen = datetime.now()
        db.session.commit()
    log_action("Logout")
    
    session.clear()
    flash('Sesión cerrada correctamente.', 'info')
    return redirect(url_for('auth.login'))
