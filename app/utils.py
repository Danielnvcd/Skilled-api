import os
import logging
from functools import wraps
from flask import session, redirect, url_for, flash, request, current_app
from app.extensions import db
from app.models import AuditLog
import filetype
from PIL import Image
import pillow_heif
import re

logger = logging.getLogger(__name__)

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

# ──────────────────────────────────────────────
# Validación de longitudes de campos en Trabajadores
# ──────────────────────────────────────────────
TRABAJADOR_LENGTHS = {
    'no_empleado': 50,
    'nombre': 250,
    'nombre_apellidos': 250,
    'tipo_mov': 100,
    'tipo_cont': 100,
    'area': 150,
    'puesto': 150,
    'tipo_jornada': 100,
    'curp': 18,
    'rfc': 13,
    'nss': 20,
    'letra_fecha_nac': 50,
    'sexo': 20,
    'nacionalidad': 100,
    'estado_civil': 50,
    'correo': 150,
    'celular': 20,
    'tipo_sangre': 10,
    'contacto_emergencia': 200,
    'parentesco_contacto': 100,
    'numero_contacto_emerg': 20,
    'lentes': 20,
    'licencia_conducir': 50,
    'estatura': 20,
    'letra': 100,
    'folio_mov_idse': 100,
    'tipo_pago': 100,
    'tipo_nomina': 50,
    'no_proyecto': 100,
    'coord_a_cargo': 150,
    'ubicacion_actual': 150,
    'ubicacion_estado': 100,
    'planta': 100,
    'credencial_id': 40
}

def validate_lengths(data, lengths_dict=TRABAJADOR_LENGTHS):
    """
    Valida las longitudes recibidas en `data` contra el `lengths_dict`.
    Realiza strip() sobre los valores validables. Retorna lista de errores.
    """
    errores = []
    # Procesar solo campos declarados en el lengths_dict que lleguen en data
    for campo, max_len in lengths_dict.items():
        if campo in data:
            val = data[campo]
            # Solo validamos si es string
            if isinstance(val, str):
                val_stripped = val.strip()
                if len(val_stripped) > max_len:
                    errores.append(f"El campo '{campo}' excede el límite de {max_len} caracteres (recibidos: {len(val_stripped)}).")
    return errores

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

# ──────────────────────────────────────────────
# Validación estricta para fotos de perfil (solo imágenes reales)
# ──────────────────────────────────────────────
ALLOWED_IMAGE_EXTENSIONS = {'jpg', 'jpeg', 'png'}
ALLOWED_IMAGE_MIMES = {'image/jpeg', 'image/png'}
MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5 MB máximo para fotos de perfil

def allowed_image_file(file_storage):
    """Valida que el archivo sea una imagen real (JPG/PNG) de máximo 5MB.
    
    A diferencia de allowed_file(), esta función:
    - Solo acepta extensiones de imagen (jpg, jpeg, png)
    - Valida magic bytes con filetype
    - Rechaza archivos > 5MB
    - Rechaza cualquier otro tipo aunque tenga extensión de imagen
    
    Usar para: fotos de perfil de trabajadores y usuarios.
    """
    filename = file_storage.filename
    if not filename or '.' not in filename:
        return False

    ext = filename.rsplit('.', 1)[1].lower()
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        logging.warning(f"Foto rechazada: extensión '{ext}' no permitida para {filename}")
        return False

    # Verificar tamaño: leer todo el contenido para medir, luego resetear
    file_storage.seek(0, 2)  # Ir al final del archivo
    file_size = file_storage.tell()
    file_storage.seek(0)  # Resetear al inicio
    
    if file_size > MAX_IMAGE_SIZE:
        logging.warning(f"Foto rechazada: {filename} excede el límite de {MAX_IMAGE_SIZE // (1024*1024)}MB ({file_size} bytes)")
        return False

    # Leer primeros bytes para verificar magic number
    header = file_storage.read(2048)
    file_storage.seek(0)  # IMPORTANTE: resetear posición del stream

    kind = filetype.guess(header)
    if kind is None:
        logging.warning(f"Foto rechazada: no se pudo determinar el tipo de {filename}")
        return False

    if kind.mime not in ALLOWED_IMAGE_MIMES:
        logging.warning(
            f"Security Alert: Foto rechazada por MIME inválido para {filename}. "
            f"Extensión: {ext}, MIME detectado: {kind.mime}"
        )
        return False

    return True

# ──────────────────────────────────────────────
# Matriz declarativa: rol → blueprints permitidos
# '*' = acceso total.  Agregar/quitar permisos aquí.
# ──────────────────────────────────────────────
ROLE_PERMISSIONS = {
    'super_admin': {
        'allowed': '*',
        'default_redirect': 'main.home',
    },
    'admin': {
        'allowed': '*',
        'default_redirect': 'main.home',
    },
    'coordinador': {
        'allowed': ['horas.', 'auth.', 'ficha.', 'trabajadores.', 'main.credenciales', 'users.serve_profile_pic'],
        'default_redirect': 'horas.index',
        'deny_message': 'Acceso denegado. Tu rol solo permite acceder a Horas, Ficha Técnica y Credenciales.',
    },
}

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('auth.login'))

        role = session.get('role', '')
        perms = ROLE_PERMISSIONS.get(role)

        # Rol desconocido → denegar por defecto
        if not perms:
            flash('Acceso denegado. Rol no reconocido.', 'danger')
            return redirect(url_for('auth.login'))

        allowed = perms['allowed']

        # '*' = acceso total, no requiere más validación
        if allowed != '*' and request.endpoint:
            if not any(request.endpoint.startswith(p) for p in allowed):
                flash(perms.get('deny_message', 'Acceso denegado.'), 'warning')
                return redirect(url_for(perms['default_redirect']))

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

from datetime import datetime, date, timedelta

def _time_to_minutes(t):
    """Convierte un time a minutos desde 00:00."""
    return t.hour * 60 + t.minute

def turnos_se_traslapan(entrada_a, salida_a, entrada_b, salida_b):
    """
    Detecta traslape entre dos turnos usando objetos time.
    Maneja correctamente turnos que cruzan medianoche (ej. 22:00 → 02:00).
    """
    a_start = _time_to_minutes(entrada_a)
    a_end = _time_to_minutes(salida_a)
    b_start = _time_to_minutes(entrada_b)
    b_end = _time_to_minutes(salida_b)
    
    # Si el turno cruza medianoche, extender fin +24h
    if a_end <= a_start:
        a_end += 1440
    if b_end <= b_start:
        b_end += 1440

    # Chequeo directo
    if a_start < b_end and a_end > b_start:
        return True
    
    # Chequeo con turno B desplazado +24h (cubre caso donde ambos cruzan medianoche
    # o están en "días" distintos dentro de la ventana de 48h)
    if a_start < (b_end + 1440) and a_end > (b_start + 1440):
        return True
    if (a_start + 1440) < b_end and (a_end + 1440) > b_start:
        return True
    
    return False


def calcular_horas_productivas(hora_entrada, hora_salida, tipo_nomina, tomo_comida):
    """
    Calcula las horas productivas basado en las reglas del diagrama de flujo.
    Args:
        hora_entrada (datetime.time): Hora de inicio de labores
        hora_salida (datetime.time): Hora de fin de labores
        tipo_nomina (str): 'Semanal', 'Por hora', o 'Cuadrado'
        tomo_comida (bool): Si el trabajador tomó la hora de comida o no.
    Returns:
        float: Total de horas productivas.
    """
    if not hora_entrada or not hora_salida:
        return 0.0
        
    # Crear datetimes auxiliares el mismo día para calcular la diferencia usando atributos time
    h_in = datetime.combine(date.today(), hora_entrada)
    h_out = datetime.combine(date.today(), hora_salida)
    
    # Si la hora de salida es menor, asumimos que cruzó la medianoche
    if h_out < h_in:
        from datetime import timedelta
        h_out += timedelta(days=1)
        
    diff = h_out - h_in
    total_hours = diff.total_seconds() / 3600.0
    
    # Regla: Si es 'Por hora' y sí tomó comida (asumimos 1 hora por defecto, como en sistemas estándar)
    # según el diagrama: "MENOS la hora de la comida cuando el trabajador goce de ella"
    if tipo_nomina == 'Por hora' and tomo_comida:
        total_hours -= 1.0
        
    # 'Semanal' y 'Cuadrado' no descuentan comida según la descripción:
    # "Goce o no de la hora de la comida el total de las horas productivas es = a la Resta..."
    
    return max(0.0, round(total_hours, 2))


# ──────────────────────────────────────────────
# Helpers financieros compartidos
# ──────────────────────────────────────────────
from decimal import Decimal

def to_dec(value):
    """Convierte un valor a Decimal de forma segura."""
    if value is None:
        return Decimal('0')
    return Decimal(str(value))


def recalcular_totales_prenomina(prenomina):
    """Recalcula los totales de una prenómina individual basándose en sus descuentos, depósitos y préstamos activos.
    
    Esta función es usada tanto por prenomina.py como por prestamos.py para mantener
    una sola fuente de verdad para el cálculo financiero.
    """
    from app.models import Prestamo

    # Descuentos granulares
    total_desc_detalle = sum((to_dec(d.monto) for d in prenomina.descuentos_detalle), Decimal('0')) if prenomina.descuentos_detalle else Decimal('0')
    # Depósitos extras
    total_dep_extra = sum((to_dec(d.monto) for d in prenomina.depositos_detalle), Decimal('0')) if prenomina.depositos_detalle else Decimal('0')

    # Cuotas de préstamos activos
    prestamos_activos = Prestamo.query.filter_by(trabajador_id=prenomina.trabajador_id, estado='ACTIVO').all()
    total_prestamos = sum((to_dec(pr.descuento_semanal) for pr in prestamos_activos), Decimal('0'))

    prenomina.descuento_prestamos = total_prestamos
    prenomina.depositos_otros = total_dep_extra
    prenomina.descuentos_otros = total_desc_detalle

    prenomina.total_percepciones = to_dec(prenomina.salario_base) + to_dec(prenomina.pago_horas_extras) + to_dec(prenomina.pago_viaticos) + to_dec(prenomina.pago_festivos) + to_dec(prenomina.depositos_otros)
    prenomina.total_deducciones = to_dec(prenomina.descuento_infonavit) + to_dec(prenomina.ajuste_inbursa) + to_dec(prenomina.descuento_incidencias) + to_dec(prenomina.descuento_prestamos) + to_dec(prenomina.descuentos_otros)
    prenomina.total_a_pagar = prenomina.total_percepciones - prenomina.total_deducciones

    db.session.commit()
