from flask import Blueprint, render_template
from app.utils import login_required

bp = Blueprint('info', __name__, url_prefix='/info')

@bp.route('/', methods=['GET'])
@login_required
def index():
    tecnologias = [
        {
            "nombre": "Flask",
            "logo": "fa-brands fa-python",
            "color": "#3b82f6",
            "descripcion": "Microframework WSGI modular desarrollado en Python. Actúa como el cerebro principal del sistema (Backend). Sus decoradores y el manejo de contextos HTTP permiten dirigir inteligentemente el tráfico de todas las pantallas, procesar cálculos de nómina pesados, autenticar usuarios y coordinar la creación y edición de horas de todos los proyectos de la constructora."
        },
        {
            "nombre": "PostgreSQL",
            "logo": "fa-solid fa-database",
            "color": "#336791",
            "descripcion": "Motor ORDBMS con soporte nativo para transacciones ACID y relaciones complejas. Es la bóveda acorazada donde se guardan permanentemente los cientos de registros semanales, detalles de empleados, sueldos base y toda la bitácora criptográfica de cambios. Su estricta validación garantiza que nunca se pierda un solo centavo de información por errores de red."
        },
        {
            "nombre": "Gunicorn",
            "logo": "fa-solid fa-server",
            "color": "#4ade80",
            "descripcion": "Servidor WSGI con arquitectura pre-fork para despliegues de grado de producción en entornos UNIX. Su función principal es crear 'clones' (workers) del código de Flask permitiendo que docenas de coordinadores o trabajadores puedan entrar a generar reportes al mismo tiempo sin que el sistema se trabe, repartiendo automáticamente la carga de procesamiento."
        },
        {
            "nombre": "Nginx",
            "logo": "fa-solid fa-network-wired",
            "color": "#009639",
            "descripcion": "Servidor HTTP asíncrono impulsado por eventos y proxy inverso de capa 7. Es el guardián de entrada del sistema; recibe directamente todas las conexiones que vienen del exterior, envía las imágenes de perfil o estilos super rápido desde su memoria caché y frena cualquier tráfico basura protegiendo a Gunicorn de sufrir una sobrecarga directa."
        },
        {
            "nombre": "Cloudflare Tunnel",
            "logo": "fa-solid fa-cloud",
            "color": "#f59e0b",
            "descripcion": "Agente daemon de red (Zero Trust) que conecta servidores locales a la red Edge global. En lugar de exponer directamente la computadora de la empresa a ciberataques, esta herramienta crea un túnel cifrado unidireccional dándole al sistema certificados SSL legítimos (candado verde) y neutralizando por completo el riesgo de denegación de servicios masiva (DDoS)."
        },
        {
            "nombre": "Docker",
            "logo": "fa-brands fa-docker",
            "color": "#0db7ed",
            "descripcion": "Plataforma de virtualización a nivel de sistema operativo basada en contenedores. Empaqueta el backend, la base de datos y todos sus pre-requisitos en silos de software inmutables, asegurando que la plataforma de nóminas corra exactamente igual en la computadora del desarrollador que en el clúster de producción sin conflictos de librerías."
        },
        {
            "nombre": "Ubuntu Server",
            "logo": "fa-brands fa-ubuntu",
            "color": "#e95420",
            "descripcion": "Sistema operativo Linux empresarial de núcleo monolítico. Sirve como la roca fundamental (Host OS) que asigna la memoria RAM, previene bloqueos de hardware y asegura que el sistema pueda mantenerse levantado 24/7 de forma súper estable sin reinicios forzados ni ventanas de error inesperadas."
        },
        {
            "nombre": "Redis",
            "logo": "fa-solid fa-bolt", # Since there's no official Redis FA icon, we use a bolt
            "color": "#dc382d",
            "descripcion": "Base de datos en memoria y bróker de mensajes que opera a velocidad de la luz. Funciona como la memoria a corto plazo ultra-rápida del sistema para almacenar sesiones activas, controlar los límites de peticiones (Rate Limiting) para bloquear ataques de fuerza bruta y manejar colas de trabajos en segundo plano instantáneamente."
        },
        {
            "nombre": "JavaScript & UI Dinámica",
            "logo": "fa-brands fa-js",
            "color": "#f7df1e",
            "descripcion": "Ecosistema Frontend que corre dentro del navegador del usuario. Utiliza peticiones XHR/Fetch para guardar los registros de nómina sin recargar la página, manipula el DOM para mostrar modales de perfiles instantáneamente y renderiza gráficos matemáticos de los totales económicos usando variables CSS fluidas."
        }
    ]
    
    return render_template('info.html', tecnologias=tecnologias)
