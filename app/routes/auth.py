from flask import Blueprint, render_template, redirect, url_for, flash, request, session, current_app
from werkzeug.security import generate_password_hash, check_password_hash
from urllib.parse import urlparse, urljoin
from app.extensions import db, limiter, get_redis
from app.models import User
from app.utils import log_action, login_required, is_strong_password
import pyotp
import qrcode
import io
import base64
from datetime import datetime

def _is_safe_url(target):
    """Verifica que el URL de redirección sea del mismo dominio (anti open-redirect)."""
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ('http', 'https') and ref_url.netloc == test_url.netloc

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
        
        if new_password:
            if new_password != confirm_password:
                flash('Las contraseñas no coinciden.', 'danger')
            elif not is_strong_password(new_password):
                flash('La nueva contraseña es demasiado débil (usa mayúsculas, minúsculas, números y símbolos).', 'danger')
            else:
                user.password_hash = generate_password_hash(new_password)
                # Incrementar versión invalida todas las sesiones abiertas en otros dispositivos
                user.password_version = (user.password_version or 1) + 1
                db.session.commit()
                # Actualizar la versión en la sesión activa para no desloguear al propio usuario
                session['password_version'] = user.password_version
                flash('Contraseña actualizada correctamente.', 'success')
                log_action(f"Contraseña actualizada para {user.username}")
            
    if request.method == 'POST':
        profile_pic = request.files.get('profile_pic')
        if profile_pic and profile_pic.filename != '':
            from werkzeug.utils import secure_filename
            from app.utils import allowed_image_file
            import os, uuid
            if allowed_image_file(profile_pic):
                ext = profile_pic.filename.rsplit('.', 1)[-1].lower() if '.' in profile_pic.filename else ''
                filename = secure_filename(profile_pic.filename)
                unique_filename = f"profile_{user.id}_{uuid.uuid4().hex[:8]}.{ext}"
                upload_path = os.path.join(current_app.config['UPLOAD_FOLDER'], unique_filename)
                profile_pic.save(upload_path)
                
                if user.profile_pic and user.profile_pic != 'default.png':
                    old_pic_path = os.path.join(current_app.config['UPLOAD_FOLDER'], user.profile_pic)
                    if os.path.exists(old_pic_path):
                        try:
                            os.remove(old_pic_path)
                        except Exception as e:
                            current_app.logger.warning(f"No se pudo eliminar foto antigua: {e}")
                
                user.profile_pic = unique_filename
                db.session.commit()
                flash('Foto de perfil actualizada correctamente.', 'success')
                log_action(f"Foto de perfil actualizada para {user.username}")
            else:
                flash('Foto rechazada: solo se permiten imágenes JPG o PNG reales.', 'warning')
            
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
    # Si ya tiene sesión activa, regresarlo a donde estaba o al home
    if 'user_id' in session:
        flash('Ya tienes una sesión activa.', 'info')
        next_url = request.args.get('next') or request.referrer
        if next_url and _is_safe_url(next_url) and '/login' not in next_url:
            fallback = next_url
        else:
            fallback = url_for('main.home')
        return redirect(fallback)
        
    if request.method == 'POST':
        u = User.query.filter_by(username=request.form.get('username')).first()
        
        if u and check_password_hash(u.password_hash, request.form.get('password')):
            remember = request.form.get('remember') == 'on'
            session.clear()
            
            if u.totp_secret:
                session['pre_2fa_user_id'] = u.id
                session['remember'] = remember
                return redirect(url_for('auth.verify_2fa'))
            
            session['user_id'] = u.id
            session['user'] = u.username
            session['role'] = u.role
            session['password_version'] = u.password_version or 1
            session.permanent = remember
            u.last_seen = datetime.now()
            db.session.commit()
            log_action("Login exitoso")
            
            is_mobile = request.user_agent.platform in ['android', 'iphone', 'ipad'] or 'mobi' in request.user_agent.string.lower()
            if u.role == 'inventario':
                if is_mobile:
                    return redirect(url_for('inventario_ui.movil'))
                else:
                    return redirect(url_for('inventario_ui.web'))
            elif u.role == 'solicitante_material':
                return redirect(url_for('inventario_ui.solicitar'))
                
            if u.role == 'coordinador':
                return redirect(url_for('horas.index'))
            return redirect(url_for('main.home'))
        
        flash('Credenciales incorrectas', 'danger')
        # Registrar fallo con IP real (Cloudflare Tunnel envía CF-Connecting-IP)
        _attempted_user = (request.form.get('username') or '')[:80]  # truncar, nunca loguear contraseña
        _real_ip = request.headers.get('CF-Connecting-IP', request.remote_addr)
        log_action(f"Login fallido para usuario '{_attempted_user}' desde IP {_real_ip}")
        
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
            remember = session.get('remember', False)
            session.pop('pre_2fa_user_id', None)
            session.pop('remember', None)
            session['user_id'] = user.id
            session['user'] = user.username
            session['role'] = user.role
            session['password_version'] = user.password_version or 1
            session.permanent = remember
            log_action(f"Login 2FA exitoso para {user.username}")
            
            is_mobile = request.user_agent.platform in ['android', 'iphone', 'ipad'] or 'mobi' in request.user_agent.string.lower()
            if user.role == 'inventario':
                if is_mobile:
                    return redirect(url_for('inventario_ui.movil'))
                else:
                    return redirect(url_for('inventario_ui.web'))
            elif user.role == 'solicitante_material':
                return redirect(url_for('inventario_ui.solicitar'))
                
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
