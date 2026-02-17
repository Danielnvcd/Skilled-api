import os
import logging
from functools import wraps
from flask import session, redirect, url_for, flash, request, current_app
from app.extensions import db
from app.models import AuditLog
import filetype
from PIL import Image
import pillow_heif

logger = logging.getLogger(__name__)

# Register HEIF opener
pillow_heif.register_heif_opener()

def log_action(action):
    try:
        ip = request.headers.get("CF-Connecting-IP", request.remote_addr)
        log = AuditLog(
            user=session.get('user', 'anon'),
            action=action,
            ip=ip
        )
        db.session.add(log)
        db.session.commit()
    except Exception as e:
        logger.warning(f"Error guardando log: {e}")

# Strict MIME Type Whitelist
STRICT_MIMETYPES = {
    'pdf': ['application/pdf'],
    'doc': ['application/msword', 'application/x-msi', 'application/vnd.ms-office'],
    'docx': ['application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'application/zip'], 
    'ppt': ['application/vnd.ms-powerpoint', 'application/vnd.ms-office'],
    'pptx': ['application/vnd.openxmlformats-officedocument.presentationml.presentation', 'application/zip'],
    'xls': ['application/vnd.ms-excel', 'application/vnd.ms-office'],
    'xlsx': ['application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'application/zip'],
    'xlsm': ['application/vnd.ms-excel.sheet.macroEnabled.12', 'application/zip'],
    'jpg': ['image/jpeg'],
    'jpeg': ['image/jpeg'],
    'png': ['image/png'],
    'heic': ['image/heic', 'image/heif'],
    'mp4': ['video/mp4'],
    'mp3': ['audio/mpeg'],
    'wav': ['audio/wav', 'audio/x-wav']
}

def allowed_file(file_storage):
    filename = file_storage.filename
    if '.' not in filename:
        return False
    
    ext = filename.rsplit('.', 1)[1].lower()
    if ext not in current_app.config['ALLOWED_EXTENSIONS']:
        return False
    
    # Read first 2048 bytes for magic number check
    header = file_storage.read(2048)
    file_storage.seek(0) # IMPORTANT: Reset stream position
    
    kind = filetype.guess(header)
    
    if kind is None:
        logging.warning(f"File validation failed: could not determine type for {filename}")
        return False
        
    detected_ext = kind.extension
    detected_mime = kind.mime
    
    if detected_ext == 'jpeg': detected_ext = 'jpg'
    
    if ext in STRICT_MIMETYPES:
        if detected_mime not in STRICT_MIMETYPES[ext]:
            logging.warning(f"Security Alert: MIME mismatch for {filename}. Declared: {ext}, Detected MIME: {detected_mime}")
            return False
    else:
        if detected_ext != ext:
             if not (ext in ['docx', 'xlsx', 'pptx'] and detected_ext == 'zip'):
                logging.warning(f"Security Alert: Extension mismatch for {filename}. Declared: {ext}, Detected: {detected_ext}")
                return False

    return True

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if session.get('role') not in ['admin', 'super_admin']:
            flash('Acceso denegado.', 'danger')
            return redirect(url_for('main.home'))
        return f(*args, **kwargs)
    return decorated_function

SUPER_ADMINS = ['Vanesa Rivera', 'Daniel']

def super_admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if session.get('role') == 'super_admin' or \
           (session.get('user') in SUPER_ADMINS):
            return f(*args, **kwargs)
            
        flash('Acceso denegado. Se requieren permisos de Super Administrador.', 'danger')
        return redirect(url_for('main.home'))
    return decorated_function
