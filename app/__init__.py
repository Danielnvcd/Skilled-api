import os
import logging
import traceback
from datetime import timedelta
from flask import Flask, render_template, flash, redirect, url_for, request, jsonify, session
from flask_wtf.csrf import CSRFError
from dotenv import load_dotenv
from app.extensions import db, limiter, csrf, migrate, mail
from werkzeug.middleware.proxy_fix import ProxyFix
from flask_talisman import Talisman
from flask_compress import Compress

def create_app():
    load_dotenv()
    
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    app = Flask(__name__, 
                template_folder=os.path.join(BASE_DIR, 'templates'),
                static_folder=os.path.join(BASE_DIR, 'static'))

    @app.context_processor
    def inject_context():
        from datetime import datetime
        from flask import session, g
        from app.models import User

        current_user = None
        notif_count = 0
        if 'user_id' in session:
            if not hasattr(g, '_current_user'):
                g._current_user = User.query.get(session.get('user_id'))
            current_user = g._current_user

            if session.get('role') in ('admin', 'super_admin'):
                from app.models import Notificacion
                notif_count = Notificacion.query.filter_by(
                    usuario_id=session.get('user_id'),
                    leida=False,
                ).count()

        # Lee la cookie del sidebar para que Jinja aplique la clase collapsed en el
        # servidor — evita el parpadeo (expand→collapse) al navegar entre páginas.
        sidebar_collapsed = request.cookies.get('sidebar_collapsed') == '1'

        return {
            'now': datetime.now,
            'current_user': current_user,
            'notif_count': notif_count,
            'sidebar_collapsed': sidebar_collapsed,
        }
        
    @app.template_filter('fecha_es')
    def fecha_es_filter(dt, format_type='completo'):
        if not dt:
            return ''
        meses = ['Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio', 'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre']
        dias = ['Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo']
        dias_cortos = ['Lun', 'Mar', 'Mié', 'Jue', 'Vie', 'Sáb', 'Dom']
        
        try:
            mes = meses[dt.month - 1]
            if format_type == 'completo':
                return f"{dt.day} de {mes}, {dt.year}"
            elif format_type == 'dia_corto':
                dia = dias_cortos[dt.weekday()]
                return f"{dia} {dt.strftime('%d/%m')}"
            elif format_type == 'mes_corto':
                return f"{dt.day}/{mes[:3]}"
            return dt.strftime('%d/%m/%Y')
        except Exception:
            return str(dt)

    # Intentamos obtener la clave del entorno
    secret_key = os.environ.get('SECRET_KEY')
    
    # Si no existe, DETENEMOS la aplicación con un error claro
    if not secret_key:
        raise RuntimeError("CRÍTICO: No se encontró SECRET_KEY en las variables de entorno. La aplicación no puede arrancar de forma segura.")
    
    app.config['SECRET_KEY'] = secret_key
    app.config['BASE_DIR'] = BASE_DIR
    app.config['UPLOAD_FOLDER'] = os.path.join(BASE_DIR, 'uploads')
    
    _db_uri = os.environ.get('DATABASE_URL', 'sqlite:///app.db')
    app.config['SQLALCHEMY_DATABASE_URI'] = _db_uri
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    # Pool y timeouts: solo para PostgreSQL — SQLite (tests) usa NullPool y no los necesita
    if not _db_uri.startswith('sqlite'):
        _engine_opts = {
            'pool_pre_ping': True,  # descarta conexiones muertas antes de usarlas
            'pool_recycle': 1800,   # renueva conexiones inactivas cada 30 min
            'pool_timeout': 30,     # falla si no hay conexión libre en 30 s
        }
        if _db_uri.startswith('postgresql'):
            _engine_opts['connect_args'] = {
                'options': '-c statement_timeout=30000 -c lock_timeout=5000'
            }
        app.config['SQLALCHEMY_ENGINE_OPTIONS'] = _engine_opts
    app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024 
    app.config['ALLOWED_EXTENSIONS'] = {'pdf', 'doc', 'docx', 'ppt', 'pptx', 'xls', 'xlsx', 'jpg', 'png', 'mp4', 'mp3', 'wav', 'heic'}
    
    app.config['COMPRESS_ALGORITHM'] = ['brotli', 'gzip', 'deflate']
    app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 31536000 # 1 año de caché para estáticos
    app.config['USE_X_ACCEL_REDIRECT'] = os.environ.get('USE_X_ACCEL_REDIRECT', 'false').lower() == 'true'
    
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Strict'
    # Access token corto: 20 minutos. El refresh token (cookie 'rt') extiende la sesión hasta 7 días.
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(minutes=20)
    is_prod = os.environ.get('FLASK_ENV') == 'production'
    app.config['SESSION_COOKIE_SECURE'] = True  # Set True in prod only

    app.config['RATELIMIT_DEFAULT'] = "2000 per day, 500 per hour"

    app.config['MAIL_SERVER'] = 'smtp.gmail.com'
    app.config['MAIL_PORT'] = 587
    app.config['MAIL_USE_TLS'] = True
    app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME')
    app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD')
    app.config['MAIL_DEFAULT_SENDER'] = os.environ.get('MAIL_USERNAME')

    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

    redis_url = os.environ.get('REDIS_URL')
    if redis_url and not redis_url.startswith('memory://'):
        import redis
        import time
        max_retries = 30
        retries = 0
        logging.info(f"Intentando conectar a Redis en: {redis_url}")
        while retries < max_retries:
            try:
                client = redis.from_url(redis_url)
                if client.ping():
                    logging.info("Conexión a Redis exitosa.")
                    break
            except redis.RedisError as e:
                retries += 1
                logging.warning(f"Esperando a que Redis inicie... (Intento {retries}/{max_retries}). Error: {e}")
                time.sleep(2)
        if retries == max_retries:
            raise RuntimeError("CRÍTICO: No se pudo conectar a Redis. La aplicación no puede arrancar.")
            
    app.config['RATELIMIT_STORAGE_URI'] = redis_url or 'memory://'

    db.init_app(app)
    limiter.init_app(app)
    csrf.init_app(app)
    migrate.init_app(app, db)
    mail.init_app(app)
    Compress(app)

    csp = {
        'default-src': '\'self\'',
        'script-src': [
            '\'self\'',
            '\'sha256-GM7IIbUkSXDrnXMRMiFIDiOntytvSUDSsLtYDBaCqEQ=\'',
            '\'sha256-f+AS27PYwphakMuSE5b0u2A4jlG7wBc1PJdgkKE33yM=\'',
            '\'sha256-pxLKgbcWy2PhNHtY70b3+9xM1DaEg9wx0HuSIvhboP0=\'',
            '\'sha256-LR1qZhcrwqC16k6pdfx3zpaEnV1gOIHJjkFpm3G3JgU=\'',
            '\'sha256-HQDTABhTJR5g6ipLHPMSZNn4Zj+TfOpaKNRLQ4+8tH0=\'',  # Tailwind CDN inline script
            'https://static.cloudflareinsights.com',
            'https://cdnjs.cloudflare.com',
            'https://cdn.jsdelivr.net',
            'https://cdn.tailwindcss.com',
            'https://unpkg.com',          # html5-qrcode CDN
        ],
        # SEGURIDAD: 'unsafe-inline' en style-src es necesario temporalmente porque varios
        # templates usan atributos style="" inline en el HTML. Para eliminarlo hay que mover
        # todos los estilos inline a archivos .css externos (tracked en issue de seguridad).
        # Prioridad: media. No bloquea funcionalidad actual.
        'style-src': ['\'self\'', '\'unsafe-inline\'', 'https://cdnjs.cloudflare.com', 'https://fonts.googleapis.com'],
        'img-src': ['\'self\'', 'data:', 'blob:', 'https:'],
        'font-src': ['\'self\'', 'https://cdnjs.cloudflare.com', 'https://fonts.gstatic.com'],
        # blob: necesario para getUserMedia (cámara) y WebWorkers del QR scanner
        'connect-src': ['\'self\'', 'blob:', 'https://cloudflare.com', 'https://cdn.jsdelivr.net'],
        'media-src': ['\'self\'', 'blob:'],           # stream de cámara
        'worker-src': ['\'self\'', 'blob:'],           # WebWorker del scanner QR
        'frame-src': ['\'self\'', 'https://www.youtube.com', 'https://youtube.com'],
        # Bloquea que esta app sea embebida en iframes de otros dominios (anti-clickjacking)
        'frame-ancestors': '\'none\'',
    }
    Talisman(app, content_security_policy=csp, force_https=False)

    from app.routes import auth, main, users, trabajadores, horas, prenomina, proyectos, historico_nominas, prestamos, ficha, proyecto_total, bitacora, info, ajustes, reportes, ausencias, metricas, inventario_ui, notificaciones, manual_uso
    app.register_blueprint(auth.bp)


    app.register_blueprint(main.bp)
    app.register_blueprint(users.bp)
    app.register_blueprint(trabajadores.bp)
    app.register_blueprint(horas.bp)
    app.register_blueprint(prenomina.bp)
    app.register_blueprint(proyectos.bp)
    app.register_blueprint(historico_nominas.bp)
    app.register_blueprint(prestamos.bp)
    app.register_blueprint(ficha.bp)
    app.register_blueprint(proyecto_total.bp)
    app.register_blueprint(bitacora.bp)
    app.register_blueprint(info.bp)
    app.register_blueprint(ajustes.bp)
    app.register_blueprint(reportes.bp)
    app.register_blueprint(ausencias.bp)
    app.register_blueprint(metricas.bp)
    app.register_blueprint(inventario_ui.bp)
    app.register_blueprint(notificaciones.bp)
    app.register_blueprint(manual_uso.bp)

    # ── Handler global de CSRF ─────────────────────────────────────────
    # Se ejecuta en TODA la app cuando un token CSRF es inválido o expiró.
    # Cubre dos escenarios:
    #   1) AJAX/fetch  → responde JSON 419 (interceptado por session_interceptor.js)
    #   2) Formulario HTML →
    #        a) Usuario logueado: flash + regresa al formulario (no destruye sesión)
    #        b) Usuario no logueado: limpia sesión + redirect a login
    # ─────────────────────────────────────────────────────────────────────
    @app.errorhandler(CSRFError)
    def handle_csrf(e):
        # ── AJAX / fetch: JSON con status 419 ──
        if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({
                'error': 'Tu formulario expiró, inténtalo de nuevo.',
                'redirect': url_for('auth.login')
            }), 419

        # ── Formulario HTML ──
        if session.get('user_id'):
            # El usuario sigue logueado; solo el token CSRF expiró.
            # No destruir la sesión — solo redirigir de vuelta al formulario.
            flash('Tu formulario expiró, inténtalo de nuevo.', 'warning')
            return redirect(request.referrer or url_for('main.home'))

        # Usuario no logueado: limpiar sesión y mandar a login.
        session.clear()
        flash('Se expiró tu sesión, inicia sesión de nuevo.', 'warning')
        return redirect(url_for('auth.login'))

    @app.errorhandler(429)
    def ratelimit_handler(e):
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
             return jsonify({'error': "Has excedido el número de intentos permitidos."}), 429
        
        flash("Has excedido el número de intentos permitidos. Por favor espera unos minutos.", "danger")
        return redirect(url_for('main.home'))

    @app.errorhandler(500)
    @app.errorhandler(Exception)
    def handle_500(e):
        try:
            from werkzeug.exceptions import HTTPException
            if isinstance(e, HTTPException) and e.code != 500:
                return e

            # En producción no exponer trazas completas (podrían revelar paths/secretos)
            if is_prod:
                app.logger.error(
                    "Internal Server Error [%s] %s %s",
                    type(e).__name__, request.method, request.path
                )
            else:
                app.logger.error(
                    "Internal Server Error: %s\n%s", str(e), traceback.format_exc()
                )
            
            if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                 return jsonify({'error': "Ocurrió un error interno en el servidor."}), 500
            
            flash("Ocurrió un error inesperado al procesar tu solicitud.", "danger")
            fallback_url = request.referrer if request.referrer else url_for('main.home')
            return redirect(fallback_url)
        except Exception as handler_error:
            app.logger.error("Critical error in 500 handler: %s", str(handler_error)[:200])
            return "Internal Server Error", 500

    # ── Observabilidad: logging de requests lentos y errores ──
    import time as _time

    # Mapa de endpoints de escritorio → móvil para coordinadores
    _MOVIL_REDIRECT = {
        'horas.index':     'horas.movil',
        'ficha.index':     'ficha.movil',
        'proyectos.index': 'proyectos.movil',
        'horas.capturar':  'horas.capturar_movil',
    }

    @app.before_request
    def _redirect_coordinador_movil():
        if request.method != 'GET':
            return
        if request.args.get('desktop'):
            return
        if session.get('role') != 'coordinador':
            return
        dest = _MOVIL_REDIRECT.get(request.endpoint)
        if not dest:
            return
        ua = request.user_agent.string.lower()
        if request.user_agent.platform in ('android', 'iphone', 'ipad') or 'mobi' in ua:
            return redirect(url_for(dest, **(request.view_args or {})))

    @app.before_request
    def _start_timer():
        request._start_time = _time.time()

    @app.after_request
    def _security_headers(response):
        response.headers.setdefault('X-Content-Type-Options', 'nosniff')
        response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
        response.headers.setdefault('Permissions-Policy', 'camera=(self), microphone=(), geolocation=(self)')
        return response

    @app.after_request
    def _log_request(response):
        if request.endpoint and 'static' in request.endpoint:
            return response
        elapsed_ms = (_time.time() - getattr(request, '_start_time', _time.time())) * 1000
        if elapsed_ms > 500 or response.status_code >= 400:
            app.logger.log(
                logging.WARNING if response.status_code >= 400 else logging.INFO,
                "[PERF] %s %s -> %s (%.0fms)",
                request.method, request.path, response.status_code, elapsed_ms
            )
        return response

    with app.app_context():
        os.makedirs(os.path.join(BASE_DIR, 'data'), exist_ok=True)
        # Crear tabla de notificaciones si aún no existe (idempotente, no afecta otras tablas)
        from sqlalchemy import inspect as _sqla_inspect
        if not _sqla_inspect(db.engine).has_table('notificaciones'):
            from app.models import Notificacion
            Notificacion.__table__.create(db.engine)

    app.wsgi_app = ProxyFix(
        app.wsgi_app, 
        x_for=1, 
        x_proto=1, 
        x_host=1, 
        x_prefix=1
    )    

    return app
