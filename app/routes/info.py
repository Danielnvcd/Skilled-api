from flask import Blueprint, render_template
from app.utils import login_required

bp = Blueprint('info', __name__, url_prefix='/info')

@bp.route('/', methods=['GET'])
@login_required
def index():
    tecnologias = [
        {
            "nombre": "Spring Boot",
            "logo": "fa-solid fa-leaf",
            "color": "#6db33f",
            "descripcion": "Backend principal del sistema. Maneja rutas, autenticación, sesiones y el procesamiento de nómina."
        },
        {
            "nombre": "FastAPI",
            "logo": "fa-solid fa-rocket",
            "color": "#05998b",
            "descripcion": "API REST del módulo de inventario. Ofrece validación automática de datos y documentación integrada."
        },
        {
            "nombre": "PostgreSQL",
            "logo": "fa-solid fa-database",
            "color": "#336791",
            "descripcion": "Base de datos principal. Guarda empleados, horas, nóminas, préstamos y toda la bitácora de cambios."
        },
        {
            "nombre": "Gunicorn",
            "logo": "fa-solid fa-server",
            "color": "#4ade80",
            "descripcion": "Servidor de producción para FastAPI. Permite que múltiples usuarios usen el sistema al mismo tiempo."
        },
        {
            "nombre": "Nginx",
            "logo": "fa-solid fa-network-wired",
            "color": "#009639",
            "descripcion": "Proxy inverso que recibe las conexiones del exterior y sirve archivos estáticos directamente."
        },
        {
            "nombre": "Cloudflare Tunnel",
            "logo": "fa-solid fa-cloud",
            "color": "#f59e0b",
            "descripcion": "Expone el servidor de forma segura sin abrir puertos. Incluye protección DDoS y certificado SSL."
        },
        {
            "nombre": "Docker",
            "logo": "fa-brands fa-docker",
            "color": "#0db7ed",
            "descripcion": "Empaqueta la aplicación y sus dependencias para que corra igual en cualquier entorno."
        },
        {
            "nombre": "Ubuntu Server",
            "logo": "fa-brands fa-ubuntu",
            "color": "#e95420",
            "descripcion": "Sistema operativo del servidor donde corre todo el sistema."
        },
        {
            "nombre": "Redis",
            "logo": "fa-solid fa-bolt",
            "color": "#dc382d",
            "descripcion": "Almacena sesiones activas y controla el rate limiting para bloquear ataques de fuerza bruta."
        },
        {
            "nombre": "JavaScript",
            "logo": "fa-brands fa-js",
            "color": "#f7df1e",
            "descripcion": "Maneja la interactividad del frontend: modales, capturas de horas y actualizaciones sin recargar la página."
        }
    ]
    
    return render_template('info.html', tecnologias=tecnologias)
