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
from flask_cors import CORS

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
    # HIGH-06: case-insensitive — antes "Production" o " production " no
    # activaban HSTS / cookies seguras.
    is_prod = os.environ.get('FLASK_ENV', '').strip().lower() == 'production'
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

    # CORS para el SPA React (dev server de Vite). En producción agregar el origen real.
    # supports_credentials=True habilita el envío de la cookie httpOnly del refresh token.
    #
    # Endurecimiento: especificar methods/allowed_headers explícitos en vez del
    # default '*'. Flask-CORS con default permite cualquier method/header, lo
    # que abre vías de smuggling y deja el preflight cache (Access-Control-Max-Age)
    # con valores diferentes según el browser.
    cors_origins = [o.strip() for o in os.environ.get('CORS_ORIGINS', 'http://localhost:5173,http://127.0.0.1:5173').split(',') if o.strip()]
    CORS(
        app,
        resources={r"/api/*": {"origins": cors_origins}},
        supports_credentials=True,
        methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'],
        allow_headers=['Content-Type', 'Authorization', 'X-Requested-With', 'X-CSRF-Token'],
        expose_headers=['Content-Disposition'],
        max_age=600,  # cachea el preflight 10 min (evita preflight en cada request)
    )

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
        # stream.mux.com: HLS de fondo en Login/Inicio del SPA React
        'connect-src': ['\'self\'', 'blob:', 'https://cloudflare.com', 'https://cdn.jsdelivr.net', 'https://stream.mux.com', 'https://*.mux.com'],
        'media-src': ['\'self\'', 'blob:', 'https://stream.mux.com', 'https://*.mux.com'],  # stream de cámara + HLS de fondo
        'worker-src': ['\'self\'', 'blob:'],           # WebWorker del scanner QR
        'frame-src': ['\'self\'', 'https://www.youtube.com', 'https://youtube.com'],
        # Bloquea que esta app sea embebida en iframes de otros dominios (anti-clickjacking)
        'frame-ancestors': '\'none\'',
    }
    # HSTS: anuncia HTTPS-only por 1 año. force_https=False porque Cloudflare Tunnel
    # termina TLS; la app interna sigue HTTP localmente. Talisman manda STS igual.
    # Preload OFF por defecto: agregar el dominio a la lista de preload de los browsers
    # es semi-irreversible (sacarlo lleva semanas/meses). Activar solo cuando estés
    # 100% seguro de tu setup HTTPS y dispuesto a sostenerlo. Para activarlo: setear
    # HSTS_PRELOAD=true en .env.
    _hsts_preload = os.environ.get('HSTS_PRELOAD', 'false').lower() == 'true'
    Talisman(
        app,
        content_security_policy=csp,
        force_https=False,
        # frame_options='DENY' supera al default SAMEORIGIN. Combinado con
        # frame-ancestors 'none' del CSP, bloquea cualquier intento de iframe
        # incluso desde el propio dominio (anti-clickjacking).
        frame_options='DENY',
        strict_transport_security=is_prod,        # solo emitir STS en producción
        strict_transport_security_max_age=31536000,  # 1 año
        strict_transport_security_include_subdomains=True,
        strict_transport_security_preload=_hsts_preload,
    )

    # IMPORTANTE: los módulos de UI legacy (horas, prenomina, prestamos,
    # ajustes, reportes, ficha, …) se importan SIEMPRE porque los blueprints
    # de la API reusan helpers internos (calcular_preview_prenomina,
    # _aplicar_estilos_y_retornar, _recalcular_prenominas_abiertas, etc.).
    # Lo que se controla con LEGACY_UI_ENABLED es solo el REGISTRO de los
    # blueprints — los endpoints HTML quedan inaccesibles cuando la UI vive
    # en Vercel y este servidor solo expone la API JSON.
    from app.routes import (
        auth, main, users, trabajadores, horas, prenomina, proyectos,
        historico_nominas, prestamos, ficha, proyecto_total, bitacora, info,
        ajustes, reportes, ausencias, metricas, inventario_ui, inventario_api,
        notificaciones, manual_uso, herramientas_api,
        api_auth, api_trabajadores, api_proyectos, api_notificaciones, api_horas,
        api_prenomina, api_prestamos, api_ajustes, api_proyecto_total,
        api_historico, api_users, api_dashboard, api_bitacora, api_metricas,
        api_search,
    )

    # ── API JWT (siempre activa — es lo que consume el SPA React en Vercel) ──
    # Exenta de CSRF: la protección se logra con JWT en Authorization header (no
    # es enviado automáticamente cross-site) y SameSite=Lax/None en la cookie
    # del refresh token.
    csrf.exempt(api_auth.bp);          app.register_blueprint(api_auth.bp)
    csrf.exempt(api_trabajadores.bp);  app.register_blueprint(api_trabajadores.bp)
    csrf.exempt(api_proyectos.bp);     app.register_blueprint(api_proyectos.bp)
    csrf.exempt(api_notificaciones.bp);app.register_blueprint(api_notificaciones.bp)
    csrf.exempt(api_horas.bp);         app.register_blueprint(api_horas.bp)
    csrf.exempt(api_prenomina.bp);     app.register_blueprint(api_prenomina.bp)
    csrf.exempt(api_prestamos.bp);     app.register_blueprint(api_prestamos.bp)
    csrf.exempt(api_ajustes.bp);       app.register_blueprint(api_ajustes.bp)
    csrf.exempt(api_proyecto_total.bp);app.register_blueprint(api_proyecto_total.bp)
    csrf.exempt(api_historico.bp);     app.register_blueprint(api_historico.bp)
    csrf.exempt(api_users.bp);         app.register_blueprint(api_users.bp)
    csrf.exempt(api_dashboard.bp);     app.register_blueprint(api_dashboard.bp)
    csrf.exempt(api_bitacora.bp);     app.register_blueprint(api_bitacora.bp)
    csrf.exempt(api_metricas.bp);      app.register_blueprint(api_metricas.bp)
    csrf.exempt(inventario_api.bp);    app.register_blueprint(inventario_api.bp)
    csrf.exempt(herramientas_api.bp);  app.register_blueprint(herramientas_api.bp)
    csrf.exempt(api_search.bp);        app.register_blueprint(api_search.bp)

    # ── UI legacy Flask + Jinja ──
    # Apagada por defecto en producción (frontend vive en Vercel).
    # Para reactivarla en una rama o entorno de transición: LEGACY_UI_ENABLED=true
    legacy_ui_enabled = os.environ.get('LEGACY_UI_ENABLED', 'false').lower() in ('1', 'true', 'yes')
    app.config['LEGACY_UI_ENABLED'] = legacy_ui_enabled

    if legacy_ui_enabled:
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
        logging.info("LEGACY_UI_ENABLED=true — blueprints UI Flask registrados")
    else:
        logging.info("LEGACY_UI_ENABLED=false — solo se expone /api/* (modo SPA)")

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
        # ── AJAX / fetch / API: JSON 419 ──
        if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.path.startswith('/api/'):
            payload = {'error': 'Tu formulario expiró, inténtalo de nuevo.'}
            if legacy_ui_enabled:
                payload['redirect'] = url_for('auth.login')
            return jsonify(payload), 419

        # ── Formulario HTML — solo aplica si la UI legacy está activa ──
        if legacy_ui_enabled:
            if session.get('user_id'):
                flash('Tu formulario expiró, inténtalo de nuevo.', 'warning')
                return redirect(request.referrer or url_for('main.home'))
            session.clear()
            flash('Se expiró tu sesión, inicia sesión de nuevo.', 'warning')
            return redirect(url_for('auth.login'))

        # Fallback API-only: si llega un request HTML con CSRF inválido sin tener UI.
        return jsonify({'error': 'Token CSRF inválido o expirado'}), 419

    @app.errorhandler(429)
    def ratelimit_handler(e):
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.path.startswith('/api/') or not legacy_ui_enabled:
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
            
            if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.path.startswith('/api/') or not legacy_ui_enabled:
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
        # En modo API-only no hay endpoints UI a los que redirigir.
        if not legacy_ui_enabled:
            return
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

    # Cuando la UI legacy está apagada, agregamos un endpoint raíz mínimo para
    # que `/` no devuelva 404 (útil para health checks de Cloudflare Tunnel,
    # Render, etc.) y para que el operador entienda dónde vive el frontend.
    if not legacy_ui_enabled:
        @app.route('/')
        def _root_info():
            return jsonify({
                'service': 'Skilled ERP API',
                'mode': 'api-only',
                'frontend': 'Hosted on Vercel (separate origin)',
                'health': 'ok',
            })

        @app.route('/health')
        def _health():
            return jsonify({'status': 'ok'})

    @app.before_request
    def _start_timer():
        request._start_time = _time.time()

    @app.after_request
    def _security_headers(response):
        response.headers.setdefault('X-Content-Type-Options', 'nosniff')
        response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
        response.headers.setdefault('Permissions-Policy', 'camera=(self), microphone=(), geolocation=(self)')
        # X-Frame-Options: redundante con frame-ancestors: 'none' del CSP, pero algunos
        # browsers (IE11, viejos Android WebView) no respetan CSP frame-ancestors.
        # Mantener belt + suspenders mientras existan clientes legacy.
        response.headers.setdefault('X-Frame-Options', 'DENY')
        # COOP: aísla el browsing context del documento de cross-origin popups.
        # Previene ataques tipo Spectre via window.opener y XS-Leaks por timing.
        response.headers.setdefault('Cross-Origin-Opener-Policy', 'same-origin')
        # CORP: declara que estos recursos solo deben ser cargados por mismo origen.
        # Bloquea hot-linking y previene que otros sites embedeen nuestras respuestas
        # como recursos (img, script) para inferir información por side-channels.
        response.headers.setdefault('Cross-Origin-Resource-Policy', 'same-origin')
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
        x_for=2, 
        x_proto=1, 
        x_host=1, 
        x_prefix=1
    )    

    return app
