# Sistema de Nóminas

Un sistema integral basado en web para la gestión de nóminas, empleados y reportes, desarrollado en **Python (Flask)**. Ofrece un entorno seguro y profesional con soporte para bases de datos relacionales, generación de documentos (PDF, Excel) y opciones avanzadas de carga de datos masivos.

---

## Características Principales

- **Gestión de Empleados**: Altas, bajas, y edición con múltiples campos (laborales, personales, médicos y financieros).
- **Carga Masiva (Excel)**: Soporte completo para carga y descarga de plantillas en formato `.xlsx`.
- **Generación de Reportes Precisos**: Exportación a PDF de recibos y constancias, sumado a reportes en Excel de Totales por Proyecto e Histórico, con visibilidad garantizada incluso para trabajadores con 0 horas operativas (tipo "Cuadrado").
- **Seguridad Perimetral Anti-DDoS**: Protección con `Flask-Talisman` y *Rate Limiting* estricto (vía Redis y `Flask-Limiter`) en endpoints sensibles como subidas masivas y generación de reportes pesados.
- **Validación Fuerte de Archivos**: Análisis profundo de *Magic Bytes* para restringir el tamaño de fotografías (máximo 5MB) garantizando eficiencia en almacenamiento.
- **Rendimiento y Observabilidad**: Consultas consolidadas en el Dashboard (reduciendo llamadas a la BD a la mitad), índices de rendimiento específicos en PostgreSQL, y middleware de logging para rastrear peticiones lentas mayores a 500ms.
- **Sistema de Notificaciones In-App**: Panel en tiempo real para administradores con avisos de reportes de horas cerrados, prenóminas aprobadas y actualizaciones del sistema. Las notificaciones leídas se eliminan automáticamente a los 30 días.
- **Base de Datos Robusta**: Mapeo ORM con `SQLAlchemy` conectado a **PostgreSQL**.

---

## Requisitos del Sistema

Para ejecutar el proyecto sin problemas, necesitas tener instalados en tu computadora (haz clic sobre ellos si necesitas instalarlos):

1. [Python 3.9 o superior](https://www.python.org/downloads/)
2. [Git](https://git-scm.com/downloads)
3. [PostgreSQL](https://www.postgresql.org/download/)
4. [Redis](https://redis.io/download/) - *(Opcionalmente, puedes levantar Redis y Postgres utilizando [Docker](https://www.docker.com/products/docker-desktop/))*

---

## Pasos de Instalación Rápida (Estilo Copiar y Pegar)

Sigue estas instrucciones al pie de la letra, copiando y pegando en tu terminal para iniciar todo el sistema localmente.

### 1. Clonar el Repositorio

Abre tu terminal y ejecuta el siguiente comando donde desees guardar el proyecto:

```bash
git clone https://github.com/TU_USUARIO/TU_REPOSITORIO.git
```

Una vez clonado, ingresa a la carpeta del proyecto:

```bash
cd SISTEMA_DE_NOMINAS
```

**(Nota: Reemplaza la URL anterior por el enlace directo de tu repositorio GitHub si estás clonando desde internet y la ruta en el comando `cd` si la carpeta difiere).**

### 2. Creación del Entorno Virtual

Para evitar conflictos con otras versiones de Python, crea un entorno virtual y actívalo. *(Copia el bloque según tu sistema operativo:)*

**En WINDOWS (CMD o PowerShell):**
```bash
python -m venv venv
venv\Scripts\activate
```

**En MAC / LINUX:**
```bash
python3 -m venv venv
source venv/bin/activate
```

Tu terminal ahora debería mostrar un `(venv)` al inicio de la línea. Esto significa que estás dentro del entorno aislado.

### 3. Instalación de Dependencias

Con el entorno virtual ya activo, instala todas las librerías necesarias ejecutando:

```bash
pip install -r requirements.txt
```

### 4. Configurar el Entorno (Archivo `.env`)

El sistema utiliza variables de entorno secretas. En la carpeta raíz del proyecto, debes crear un archivo que se llame exactamente `.env`.

> [!NOTE]
> Puedes usar el siguiente comando rápido en la terminal (PowerShell o bash) para crear tu archivo `.env`:

Crea el archivo `.env` en la raíz del proyecto con el siguiente contenido como base:

```env
FLASK_APP=run.py

# 'development' en local, 'production' en el servidor real
FLASK_ENV=development

# Generar con: python -c "import secrets; print(secrets.token_hex(64))"
SECRET_KEY=una_clave_secreta_super_segura12345

# Reemplaza con tus credenciales reales de PostgreSQL
DATABASE_URL=postgresql+psycopg://tu_usuario:tu_contrasena@localhost:5432/nombre_base_de_datos

REDIS_URL=redis://localhost:6379/0

MAIL_USERNAME=tu_correo@gmail.com
MAIL_PASSWORD=tu_app_password_de_gmail

# Dominios permitidos para CORS (separados por coma)
ALLOWED_ORIGIN=https://tu-dominio.com

# Clave Fernet para cifrar 2FA. Generar con:
# python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
TOTP_ENCRYPTION_KEY=GENERAR_CLAVE_AQUI

# Solo activar en producción con Nginx configurado (ver sección de Nginx)
# Hace que Nginx sirva las fotos de perfil directamente desde disco (~20ms vs ~600ms)
USE_X_ACCEL_REDIRECT=false
```

**MUY IMPORTANTE**:
Modifica `tu_usuario`, `tu_contrasena` y `nombre_base_de_datos` con tus accesos reales de PostgreSQL. En producción cambia `USE_X_ACCEL_REDIRECT=true`.

---

### 5. Configurar PostgreSQL y Redis (Base de Datos)

Antes del siguiente paso, debes confirmar que abriste tu gestor de base de datos Postgres (como pgAdmin o la consola SQL) y creaste una base de datos vacía. 

**Levantar Base de Datos y Redis por Docker (Modo muy simple - opcional):**
Si tienes *Docker* instalado y prefieres no instalar servicios en la PC principal, pega esto:
```bash
docker run --name nominas-postgres -e POSTGRES_USER=tu_usuario -e POSTGRES_PASSWORD=tu_contrasena -e POSTGRES_DB=nombre_base_de_datos -p 5432:5432 -d postgres
docker run --name nominas-redis -p 6379:6379 -d redis
```

---

### 6. Ejecutar Migraciones e Iniciar Base de Datos

Una vez que Postgres y Redis están funcionando y configurados en tu `.env`, crea la estructura de las tablas ejecutando este único comando:

```bash
flask db upgrade
```

### 7. Arrancar el Servidor Principal

Ahora simplemente levanta la aplicación:

```bash
python run.py
```

> Listo. Abre tu navegador favorito y accede a: **http://localhost:5000**

---

## Comandos Útiles para el Día a Día

Aquí tienes un listado de comandos a mano de utilidades extra cuando desarrollas. Asegúrate de tener activado siempre tu entorno virtual `(venv)` antes de usarlos.

**1. Actualizar/Recrear Plantilla de Empleados en Excel**
Este comando vuelve a generar o actualizar un archivo `plantilla_empleados.xlsx` con todas las listas actualizadas de trabajadores para carga masiva:
```bash
python create_template.py
```

**2. Ejecutar Pruebas (Tests)**
Levanta todo el módulo `pytest` para verificar si las funciones como creación de ausencias siguen funcionando bien:
```bash
pytest tests/
```

**3. Registrar Modelos de Base de Datos Nuevos**
Si en un futuro cambias código dentro de las carpetas de `app/models/`, deberás aplicar una migración para sincronizar el código con PostgreSQL:
```bash
flask db migrate -m "Modifique la tabla empleados"
flask db upgrade
```

> **Excepción — tabla `notificaciones`:** Esta tabla se crea automáticamente al arrancar la aplicación si no existe (usando `inspect().has_table()`). No requiere `flask db upgrade` ni en instalaciones nuevas ni al actualizar a producción. Basta con subir el código y reiniciar el servidor.

---

## Arquitectura del Código del Proyecto

Si quieres curiosear qué hace cada archivo y dónde va cada módulo, guíate por este mapa:

```text
SISTEMA DE NOMINAS/
├── .env                  # Ubicación de Variables maestras secretas (claves, DB, etc.)
├── run.py                # Punto de ENTRADA. Archivo con que se inicia todo el sistema.
├── requirements.txt      # Paquetes y librerías pre-instaladas.
├── create_template.py    # Generador automatizado del Excel de importación.
├── app/                  
│   ├── models/           # Definiciones de bases de datos.
│   ├── routes/           # Rutas y Endpoints (URL) en Python y plantillas HTML.
│   └── __init__.py      # Declarador de inicio de la aplicación y configuraciones.
├── migrations/           # Historial e instrucciones de actualizaciones de columnas de Tablas.
├── static/               
│   ├── css/              # Hojas de estilo y diseños.
│   ├── js/               # Funciones de Front-End reactivos (interfaz, graficas, validaciones).
│   └── downloads/        # Carpetas públicas donde caen descargas como exportación excel.
├── templates/            # Todo tu código HTML puro (plantillas jinja2).
├── tests/                # Pruebas automatizadas.
└── uploads/              # Cargas directas desde el portal por el usuario.
```

---

## Producción y Despliegue (Ubuntu/Gunicorn)

Para entornos de producción, se recomienda utilizar **Gunicorn** con workers de **Uvicorn** para manejar tanto Flask como FastAPI de forma simultánea.

### 1. Requisitos para el Servidor
Asegúrate de instalar las dependencias y tener `uvicorn` y `gunicorn` en tu entorno virtual:
```bash
pip install -r requirements.txt
```

### 2. Compilar Tailwind CSS
El archivo `static/css/tailwind.css` **no se incluye en el repositorio** (está en `.gitignore`) porque se genera a partir del código fuente. Debes compilarlo en el servidor antes de arrancar la aplicación:

```bash
# Generar el CSS minificado de producción
python build_tailwind.py
```

Esto escanea todos los templates HTML y archivos JS, y genera `static/css/tailwind.css` (~40 KB minificado).

> Si en el futuro modificas clases de Tailwind en los templates, vuelve a ejecutar `python build_tailwind.py` y reinicia el servidor.
>
> Para desarrollo local con recarga automática al guardar cambios: `python build_tailwind.py --watch`

### 3. Configuración de Systemd (`/etc/systemd/system/nominas.service`)
Utiliza la siguiente plantilla para mantener el servidor siempre encendido:

```ini
[Unit]
Description=Sistema de Nominas - Gunicorn (Flask + FastAPI)
After=network.target postgresql.service redis.service

[Service]
User=sistemanominas
Group=www-data
WorkingDirectory=/opt/nominas
Environment="PATH=/opt/nominas/venv/bin"
EnvironmentFile=/opt/nominas/.env
# Ejecución con UvicornWorker para cargar root_app (FastAPI + Flask)
ExecStart=/opt/nominas/venv/bin/gunicorn --workers 4 --worker-class uvicorn.workers.UvicornWorker --bind 127.0.0.1:8000 --timeout 120 --access-logfile - run:root_app
Restart=always

[Install]
WantedBy=multi-user.target
```

### 4. Comandos de Gestión
```bash
sudo systemctl daemon-reload
sudo systemctl enable nominas
sudo systemctl start nominas
sudo systemctl status nominas
```

### 5. Configuración de Nginx (`/etc/nginx/sites-available/nominas`)
Para un correcto funcionamiento (especialmente con HTTPS/Cloudflare), usa la siguiente configuración completa:

```nginx
server {
    listen 80;
    server_name _;
    client_max_body_size 50M;

    # IPs reales de Cloudflare
    set_real_ip_from 173.245.48.0/20;
    set_real_ip_from 103.21.244.0/22;
    set_real_ip_from 103.22.200.0/22;
    set_real_ip_from 103.31.4.0/22;
    set_real_ip_from 141.101.64.0/18;
    set_real_ip_from 108.162.192.0/18;
    set_real_ip_from 190.93.240.0/20;
    set_real_ip_from 188.114.96.0/20;
    set_real_ip_from 197.234.240.0/22;
    set_real_ip_from 198.41.128.0/17;
    set_real_ip_from 162.158.0.0/15;
    set_real_ip_from 104.16.0.0/13;
    set_real_ip_from 104.24.0.0/14;
    set_real_ip_from 172.64.0.0/13;
    set_real_ip_from 131.0.72.0/22;
    real_ip_header CF-Connecting-IP;

    # Compresión gzip
    gzip on;
    gzip_types text/css application/javascript application/json image/svg+xml;
    gzip_min_length 256;

    # Archivos estáticos (CSS, JS, imágenes)
    location /static/ {
        alias /opt/nominas/static/;
        expires 1y;
        add_header Cache-Control "public, immutable";
        access_log off;
    }

    # Uploads directos (no fotos de perfil)
    location /uploads/ {
        alias /opt/nominas/uploads/;
        expires 7d;
        add_header Cache-Control "public";
        access_log off;
    }

    # Internal: Nginx sirve fotos de perfil directamente tras validación de Flask
    # Requiere USE_X_ACCEL_REDIRECT=true en el .env de producción
    location /x-accel-uploads/ {
        internal;
        alias /opt/nominas/uploads/;
        expires 7d;
        add_header Cache-Control "private, max-age=86400";
    }

    # Todo lo demás (HTML, API)
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $http_x_forwarded_proto;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 120s;
        add_header Cache-Control "no-store, no-cache";
    }
}
```

Aplicar cambios:
```bash
sudo nginx -t && sudo systemctl reload nginx
```

### ¿Por qué es necesaria esta configuración?

- **ASGI + WSGI**: Gunicorn por defecto solo entiende Flask (WSGI). Al agregar `uvicorn.workers.UvicornWorker`, le permitimos procesar también FastAPI (ASGI) de forma eficiente.
- **Protocolo HTTPS (X-Forwarded-Proto)**: FastAPI es estricto con la seguridad. Sin el encabezado `$http_x_forwarded_proto`, la documentación de la API (`/api/docs`) y las redirecciones intentarían usar `http`, provocando errores de **Contenido Mixto** que el navegador bloquearía.
- **WebSockets**: Los encabezados de `Upgrade` y `Connection` permiten que FastAPI maneje conexiones persistentes en tiempo real si se requieren en el futuro.
- **X-Accel-Redirect (`/x-accel-uploads/`)**: Flask valida permisos de foto de perfil (IDOR), luego delega a Nginx para servir el archivo directo desde disco. Reduce la latencia de ~600ms a ~20ms en producción.

---

---

## Sistema de Notificaciones

El panel de notificaciones aparece en la barra lateral para los roles `admin` y `super_admin`. Se actualiza automáticamente cada 45 segundos sin recargar la página.

### Tipos de notificación

| Tipo | Cuándo se genera |
|---|---|
| `REPORTE_CERRADO` | Al cerrar un reporte de horas desde el módulo Reporte de Horas |
| `PRENOMINA_CERRADA` | Al aprobar/cerrar una prenómina |
| `ACTUALIZACION` | Al arrancar la app cuando hay entradas nuevas en el `CHANGELOG` del código |

### Expiración automática

Las notificaciones **ya leídas** se eliminan de la base de datos automáticamente después de **30 días**. Las no leídas se conservan hasta que el administrador las abra. No se necesita ningún cron job ni tarea programada externa — la limpieza ocurre en cada llamada al endpoint de resumen.

Para cambiar el período de retención, edita la constante en `app/routes/notificaciones.py`:
```python
DIAS_EXPIRACION = 30  # días hasta eliminar notificaciones leídas
```

### Agregar nuevas actualizaciones al changelog

Cuando liberes una funcionalidad nueva, agrega una entrada al listado `CHANGELOG` en `app/routes/notificaciones.py`. La próxima vez que un admin abra la app verá la notificación automáticamente:

```python
CHANGELOG = [
    {
        'referencia': 'update_YYYY-MM-DD_nombre_unico',  # clave única, no cambiar después
        'titulo': 'Título corto de la actualización',
        'mensaje': 'Descripción de qué se agregó o mejoró.',
        'url': '/ruta/opcional',  # o None si no aplica
    },
    # ... entradas anteriores
]
```

### Despliegue a producción

La tabla `notificaciones` **no requiere `flask db upgrade`**. Se crea sola al arrancar si no existe. Solo sube el código y reinicia el servidor.

---

> _Desarrollado para mantener la contabilidad organizada, veloz e inquebrantable._
