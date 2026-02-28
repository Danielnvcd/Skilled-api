import os
import logging
from datetime import timedelta
from flask import Flask, render_template, flash, redirect, url_for, request, jsonify
from flask_wtf.csrf import CSRFError
from dotenv import load_dotenv
from app.extensions import db, limiter, csrf, migrate
from werkzeug.middleware.proxy_fix import ProxyFix
from flask_talisman import Talisman

def create_app():
    load_dotenv()
    
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    app = Flask(__name__, 
                template_folder=os.path.join(BASE_DIR, 'templates'),
                static_folder=os.path.join(BASE_DIR, 'static'))

    @app.context_processor
    def inject_now():
        from datetime import datetime
        return {'now': datetime.now}
        
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
    
    app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///app.db')
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024 
    app.config['ALLOWED_EXTENSIONS'] = {'pdf', 'doc', 'docx', 'ppt', 'pptx', 'xls', 'xlsx', 'xlsm', 'jpg', 'png', 'mp4', 'mp3', 'wav', 'heic'}
    
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(minutes=60)
    app.config['SESSION_COOKIE_SECURE'] = True # Set to True in Prod

    app.config['RATELIMIT_STORAGE_URI'] = os.environ.get('REDIS_URL', 'memory://')
    app.config['RATELIMIT_DEFAULT'] = "2000 per day, 500 per hour"

    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

    db.init_app(app)
    limiter.init_app(app)
    csrf.init_app(app)
    migrate.init_app(app, db)

    csp = {
        'default-src': '\'self\'',
        'script-src': ['\'self\'', 'https://static.cloudflareinsights.com', 'https://cdnjs.cloudflare.com'],
        'style-src': ['\'self\'', '\'unsafe-inline\'', 'https://cdnjs.cloudflare.com'],
        'img-src': ['\'self\'', 'data:', 'blob:'],
        'font-src': ['\'self\'', 'https://cdnjs.cloudflare.com'],
        'connect-src': ['\'self\'', 'https://cloudflare.com'],
        'frame-src': ['\'self\'', 'https://www.youtube.com', 'https://youtube.com'],
        'media-src': '\'self\''
    }
    Talisman(app, content_security_policy=csp, force_https=False)

    from app.routes import auth, main, users, trabajadores, horas, prenomina, proyectos, historico_nominas, prestamos, ficha
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

    @app.errorhandler(CSRFError)
    def handle_csrf(e):
        return render_template("base.html", content="<h1>Sesión expirada</h1><p>Por seguridad, el formulario ha caducado. Recarga la página e intenta de nuevo.</p>"), 400

    @app.errorhandler(429)
    def ratelimit_handler(e):
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
             return jsonify({'error': "Has excedido el número de intentos permitidos."}), 429
        
        flash("Has excedido el número de intentos permitidos. Por favor espera unos minutos.", "danger")
        return redirect(url_for('main.home'))

    with app.app_context():
        os.makedirs(os.path.join(BASE_DIR, 'data'), exist_ok=True)
        # db.create_all() # User should use migrations

    app.wsgi_app = ProxyFix(
        app.wsgi_app, 
        x_for=1, 
        x_proto=1, 
        x_host=1, 
        x_prefix=1
    )    

    return app
