import os
import logging
import traceback
from datetime import timedelta
from flask import Flask, request, jsonify
from flask_wtf.csrf import CSRFError
from dotenv import load_dotenv
from app.extensions import db, limiter, csrf, migrate, mail
from werkzeug.middleware.proxy_fix import ProxyFix
from flask_talisman import Talisman
from flask_compress import Compress
from flask_cors import CORS
from app.realtime import init_socketio, socketio  # noqa: F401  (re-export usado por run.py)

def create_app():
    load_dotenv()

    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    # API-only: sin `static_folder` (el SPA React vive en Vercel), pero
    # `template_folder` SÍ se necesita porque los endpoints de PDF
    # (recibos prenómina/proyecto, orden compra, solicitud pedido, toma de
    # inventario) renderizan HTML con Jinja antes de pasarlo a xhtml2pdf.
    app = Flask(
        __name__,
        static_folder=None,
        template_folder=os.path.join(BASE_DIR, 'templates'),
    )

    # Intentamos obtener la clave del entorno
    secret_key = os.environ.get('SECRET_KEY')
    
    # Si no existe, DETENEMOS la aplicación con un error claro
    if not secret_key:
        raise RuntimeError("CRÍTICO: No se encontró SECRET_KEY en las variables de entorno. La aplicación no puede arrancar de forma segura.")
    
    app.config['SECRET_KEY'] = secret_key
    # Llave dedicada para firmar JWT. Opcional: si no está, se usa SECRET_KEY
    # (comportamiento previo, sin romper tokens ya emitidos). Ver el docstring
    # de `_jwt_secret()` en app/routes/api_auth/_core.py para el porqué y para
    # el procedimiento de rotación.
    app.config['JWT_SECRET_KEY'] = os.environ.get('JWT_SECRET_KEY') or secret_key
    app.config['BASE_DIR'] = BASE_DIR
    app.config['UPLOAD_FOLDER'] = os.path.join(BASE_DIR, 'uploads')
    
    _db_uri = os.environ.get('DATABASE_URL', 'sqlite:///app.db')
    app.config['SQLALCHEMY_DATABASE_URI'] = _db_uri
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    # Pool y timeouts: solo para PostgreSQL — SQLite (tests) usa NullPool y no los necesita
    if not _db_uri.startswith('sqlite'):
        # Tamaño de pool explícito (el default de SQLAlchemy es 5+10=15 por
        # proceso, justo para ráfagas de concurrencia: muchos coordinadores a la
        # vez o un kiosko RFID con varios checadores simultáneos). Con gevent
        # cada greenlet que ejecuta una query retiene una conexión, así que el
        # pool acota cuántas queries corren en paralelo por worker.
        #
        # OJO con max_connections de Postgres (default 100): el total es
        #   workers × (pool_size + max_overflow).
        # Con 4 workers y los defaults de abajo (10+10) = 80 conexiones, deja
        # holgura bajo 100. Si subes estos valores, sube también max_connections
        # en postgresql.conf o quedarás sin conexiones.
        _engine_opts = {
            'pool_pre_ping': True,  # descarta conexiones muertas antes de usarlas
            'pool_recycle': 1800,   # renueva conexiones inactivas cada 30 min
            'pool_timeout': 30,     # falla si no hay conexión libre en 30 s
            'pool_size': int(os.environ.get('DB_POOL_SIZE', '10')),
            'max_overflow': int(os.environ.get('DB_MAX_OVERFLOW', '10')),
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
    # Headers X-RateLimit-*: permiten que el SPA avise ANTES de agotar el
    # límite, en vez de estrellarse contra un 429. El cliente no puede llevar
    # esa cuenta por su lado — las revalidaciones automáticas al recuperar el
    # foco también consumen cupo, así que solo el servidor sabe el número real.
    #
    # OJO: al ser cross-origin (SPA en Vercel → API en skilledmx.cloud), estos
    # headers NO son legibles desde JavaScript salvo que se declaren en
    # `expose_headers` del CORS. Ver la configuración de CORS más abajo.
    app.config['RATELIMIT_HEADERS_ENABLED'] = True

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

    # Filtro Jinja `fecha_es`: formato de fecha en español para los PDFs
    # (recibo_proyecto_pdf renderiza "9 de Junio, 2026"). Vivía en el __init__
    # original; se perdió durante el refactor file→package y rompía el endpoint
    # /api/historico/<fecha>/proyecto/<id>/pdf con TemplateAssertionError.
    @app.template_filter('fecha_es')
    def fecha_es_filter(dt, format_type='completo'):
        if not dt:
            return ''
        meses = ['Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio',
                 'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre']
        dias_cortos = ['Lun', 'Mar', 'Mié', 'Jue', 'Vie', 'Sáb', 'Dom']
        try:
            mes = meses[dt.month - 1]
            if format_type == 'completo':
                return f"{dt.day} de {mes}, {dt.year}"
            if format_type == 'dia_corto':
                return f"{dias_cortos[dt.weekday()]} {dt.strftime('%d/%m')}"
            if format_type == 'mes_corto':
                return f"{dt.day}/{mes[:3]}"
            return dt.strftime('%d/%m/%Y')
        except Exception:
            return str(dt)

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
        # `expose_headers` es lo que hace que el navegador deje leer estos
        # headers desde JS en una respuesta cross-origin. Sin esto, el SPA
        # recibe la respuesta pero `response.headers` llega vacío para todo lo
        # que no sea la lista blanca estándar de CORS.
        expose_headers=[
            'Content-Disposition',
            'X-RateLimit-Limit', 'X-RateLimit-Remaining', 'X-RateLimit-Reset',
            'Retry-After',
        ],
        max_age=600,  # cachea el preflight 10 min (evita preflight en cada request)
    )

    # ── CSP para un backend API-only ────────────────────────────────────────
    # Esta app ya NO sirve HTML a ningún navegador:
    #   - `static_folder=None` (el SPA vive en Vercel y trae su propia CSP).
    #   - Todas las respuestas de `/api/*` son JSON, PDF o Excel.
    #   - Los `render_template` que quedan producen HTML que va DIRECTO a
    #     xhtml2pdf (variable `html_salida`); nunca se devuelve al cliente.
    #
    # La CSP anterior era herencia de cuando el backend sí renderizaba las
    # pantallas: permitía scripts de cdn.tailwindcss.com, unpkg, jsdelivr,
    # cdnjs y varios hashes inline. Ninguno protege nada hoy, pero sí serían
    # permisos reales si algún día se sirviera HTML por error o por una ruta
    # nueva. Se reduce al mínimo: negar todo por defecto.
    #
    # OJO: si en el futuro este backend vuelve a servir una página HTML, esta
    # política la romperá a propósito — hay que abrir explícitamente lo que esa
    # página necesite, en vez de heredar una lista de CDNs sin revisar.
    csp = {
        'default-src': '\'none\'',
        # Anti-clickjacking (redundante con X-Frame-Options, ver _security_headers).
        'frame-ancestors': '\'none\'',
        # Sin <base> ni envío de formularios: no hay documentos que los usen.
        'base-uri': '\'none\'',
        'form-action': '\'none\'',
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

    # API-only: solo se importan/registran los blueprints `api_*` (consumidos por
    # el SPA React en Vercel). Los helpers que antes vivían en módulos UI legacy
    # ahora viven en el `_core.py` del paquete `api_*` correspondiente, o en
    # `app/routes/_api_helpers.py` cuando son compartidos.
    from app.routes import (
        inventario_api, herramientas_api,
        api_auth, api_trabajadores, api_proyectos, api_notificaciones, api_horas,
        api_prenomina, api_prestamos, api_ajustes, api_proyecto_total,
        api_historico, api_users, api_dashboard, api_bitacora, api_metricas,
        api_search, api_sistemas,
    )

    # ── API JWT (siempre activa — es lo que consume el SPA React en Vercel) ──
    # Exenta de CSRF: la protección se logra con JWT en Authorization header (no
    # es enviado automáticamente cross-site) y SameSite=Lax/None en la cookie
    # del refresh token.
    _api_modules = (
        api_auth, api_trabajadores, api_proyectos, api_notificaciones, api_horas,
        api_prenomina, api_prestamos, api_ajustes, api_proyecto_total,
        api_historico, api_users, api_dashboard, api_bitacora, api_metricas,
        inventario_api, herramientas_api, api_search, api_sistemas,
    )
    for mod in _api_modules:
        csrf.exempt(mod.bp)
        app.register_blueprint(mod.bp)

    # ── Handler global de CSRF ─────────────────────────────────────────
    # API-only: siempre responde JSON 419. El SPA React intercepta el código
    # y dispara su propio flujo de re-login.
    # ─────────────────────────────────────────────────────────────────────
    @app.errorhandler(CSRFError)
    def handle_csrf(e):
        return jsonify({'error': 'Tu formulario expiró, inténtalo de nuevo.'}), 419

    @app.errorhandler(429)
    def ratelimit_handler(e):
        return jsonify({'error': "Has excedido el número de intentos permitidos."}), 429

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

            return jsonify({'error': "Ocurrió un error interno en el servidor."}), 500
        except Exception as handler_error:
            app.logger.error("Critical error in 500 handler: %s", str(handler_error)[:200])
            return "Internal Server Error", 500

    # ── Observabilidad: logging de requests lentos y errores ──
    import time as _time

    # Endpoint raíz mínimo: `/` no devuelve 404 (útil para health checks de
    # Cloudflare Tunnel, Render, etc.) y `/health` para el liveness probe.
    # Ambos responden lo mismo — no revelamos stack/service/frontend al
    # mundo (info disclosure es ruido para scanners y pista para atacantes).
    @app.route('/')
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

        # Cache-Control no-store para TODAS las respuestas JSON de la app (todo lo
        # que viene de /api/* y el / con info del servicio). Antes solo `api_auth`
        # lo ponía (su `_no_store_on_auth_responses` es más estricto: agrega
        # `Pragma` y `Expires: 0` para evitar back-button replays con tokens).
        # Aquí sólo cubrimos los endpoints que no son api_auth para que el
        # contrato sea "Flask es la fuente de verdad" y nginx no tenga que
        # duplicar `add_header Cache-Control`. `setdefault` respeta el header
        # más estricto que ya haya puesto `_no_store_on_auth_responses`.
        path = (request.path or '')
        if path.startswith('/api/') or path in ('/', '/health'):
            response.headers.setdefault(
                'Cache-Control', 'no-store, no-cache, must-revalidate'
            )

        # Documentos generados al vuelo (PDF de actas de toma, recibos, órdenes de
        # compra, solicitudes; reportes Excel) cambian con los datos y NO deben
        # cachearse: el navegador/Cloudflare devolvía el archivo VIEJO al reimprimir.
        # `send_file` pone su propio Cache-Control, así que aquí FORZAMOS (override,
        # no setdefault) no-store. Las imágenes/QR/fotos servidas por send_file NO
        # entran aquí — para esas conviene el caché.
        ctype = (response.mimetype or '')
        if ctype == 'application/pdf' or 'spreadsheetml' in ctype or ctype == 'application/vnd.ms-excel':
            response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
            response.headers['Pragma'] = 'no-cache'
            response.headers['Expires'] = '0'

        # NOTA: HSTS no se setea aquí — Talisman ya lo emite en producción con
        # max-age=31536000 e includeSubDomains (ver setup de Talisman arriba).
        # Duplicarlo aquí causaba dos headers idénticos y ruido en logs.
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
        # Buffer circular en Redis que alimenta el panel de sistemas. Va después
        # del log para que, si algo fallara aquí, el registro de siempre ya haya
        # ocurrido. Nunca toca la BD y nunca lanza — ver app/observabilidad.py.
        from app.observabilidad import registrar_peticion
        return registrar_peticion(response)

    with app.app_context():
        os.makedirs(os.path.join(BASE_DIR, 'data'), exist_ok=True)
        # Crear tablas auxiliares si aún no existen (idempotente, no afecta otras).
        #
        # `notificaciones` y `trabajador_notas` no tienen migración propia: nacen
        # aquí. Por eso el bloque sigue siendo necesario.
        #
        # El try/except NO es paranoia: las tres tablas llevan FK a `users`, así
        # que contra una base VACÍA esto falla — y como el CLI de Flask importa
        # la app antes de ejecutar nada, el fallo se llevaba por delante al
        # propio `flask db upgrade`. Resultado: era imposible levantar un
        # entorno desde cero (contenedor nuevo, máquina nueva, restauración).
        # Dejándolo como aviso, `db upgrade` crea `users` y en el siguiente
        # arranque estas tablas se crean sin problema.
        from sqlalchemy import inspect as _sqla_inspect
        _auxiliares = (
            ('notificaciones', 'Notificacion'),
            ('totp_backup_codes', 'TwoFactorBackupCode'),
            ('trabajador_notas', 'NotaTrabajador'),
        )
        for _tabla, _modelo_nombre in _auxiliares:
            try:
                if _sqla_inspect(db.engine).has_table(_tabla):
                    continue
                import app.models as _modelos
                getattr(_modelos, _modelo_nombre).__table__.create(db.engine)
                logging.info("Tabla auxiliar '%s' creada.", _tabla)
            except Exception as _exc:
                logging.warning(
                    "No se pudo crear la tabla auxiliar '%s': %s. "
                    "Normal si la base aún no está migrada — se reintenta en el "
                    "próximo arranque, después de `flask db upgrade`.",
                    _tabla, _exc,
                )

    app.wsgi_app = ProxyFix(
        app.wsgi_app,
        x_for=2,
        x_proto=1,
        x_host=1,
        x_prefix=1
    )

    # Tiempo real (Socket.IO): la inicialización debe ir DESPUÉS de ProxyFix
    # para que SocketIO vea los headers X-Forwarded-* corregidos al validar
    # el origen del handshake.
    init_socketio(app)

    return app
