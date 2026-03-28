# Sistema de Nóminas 🏢

Un sistema integral basado en web para la gestión de nóminas, empleados y reportes, desarrollado en **Python (Flask)**. Ofrece un entorno seguro y profesional con soporte para bases de datos relacionales, generación de documentos y opciones avanzadas de carga de datos masivos.

---

## 🚀 Características Principales

- **Gestión de Empleados**: Altas, bajas, y edición con múltiples campos (laborales, personales, médicos y financieros).
- **Carga Masiva (Excel)**: Soporte completo para carga y descarga de plantillas en formato `.xlsx` usando `pandas` y `openpyxl`.
- **Generación de Reportes**: Exportación a PDF de recibos y constancias generados en tiempo real con `xhtml2pdf`.
- **Seguridad**:
  - Protección avanzada con `Flask-Talisman` y `Flask-Limiter` (prevención de ataques y rate limiting usando Redis).
  - Manejo de contraseñas y sesiones seguras.
  - Generación de códigos QR y TOTP (`pyotp`, `qrcode`) para autenticación o accesos rápidos.
- **Base de Datos Robusta**: Mapeo ORM con `SQLAlchemy` conectado a **PostgreSQL** y migraciones controladas por `Flask-Migrate`.
- **Procesamiento de Archivos**: Validación y optimización de imágenes (incluidas `.heif`) con `Pillow`.

---

## 🛠️ Requisitos Previos

Asegúrate de tener instalados los siguientes servicios en tu sistema antes de iniciar:

1. [Python 3.9+](https://www.python.org/downloads/)
2. [PostgreSQL](https://www.postgresql.org/download/) (Levantado y corriendo)
3. [Redis](https://redis.io/download/) (Requerido para el control de peticiones y sesiones)
4. Git (Opcional, para versionamiento y clonado)

---

## ⚙️ Instalación y Configuración (Entorno de Desarrollo)

Sigue estos pasos para levantar el proyecto localmente.

### 1. Clonar el repositorio
```bash
git clone <URL_DEL_REPOSITORIO>
cd sistema-de-nominas
```

### 2. Crear y activar el entorno virtual
Se recomienda el uso de un entorno virtual para aislar las dependencias:

**En Windows:**
```bash
python -m venv venv
venv\Scripts\activate
```

**En Linux / macOS:**
```bash
python3 -m venv venv
source venv/bin/activate
```

### 3. Instalar las dependencias
Con el entorno virtual activado, instala todas las librerías necesarias:
```bash
pip install -r requirements.txt
```

### 4. Configurar las Variables de Entorno
El proyecto necesita un archivo `.env` para manejar configuraciones sensibles. Crea un archivo `.env` en la raíz del proyecto basándote en la siguiente estructura:

```ini
# Configuración de Flask
FLASK_APP=run.py
FLASK_ENV=development
SECRET_KEY=tu_super_clave_secreta_aqui

# Conexión a Base de Datos (Modifica usuario, contraseña y base de datos)
DATABASE_URL=postgresql+psycopg2://usuario_db:password_db@localhost:5432/nombre_db

# Conexión a Redis
REDIS_URL=redis://localhost:6379/0
```
> [!IMPORTANT]
> Asegúrate de crear primero la base de datos vacía en PostgreSQL antes de pasar al siguiente paso.

### 5. Aplicar las Migraciones y Crear la Base de Datos
Este proyecto usa `Flask-Migrate` para crear la estructura dentro de tu base de datos configurada:
```bash
flask db upgrade
```

### 6. Arrancar el Servidor
Inicia la aplicación en modo desarrollo:
```bash
python run.py
```
O de forma alternativa:
```bash
flask run --host=0.0.0.0 --port=5000
```
La aplicación estará disponible en `http://localhost:5000`.

---

## 📁 Estructura del Proyecto

```text
/
├── app/                  # Lógica principal de la aplicación Flask (rutas, modelos, vistas)
├── data/                 # Almacenamiento local de datos temporales u otros
├── migrations/           # Archivos e historial de migraciones de la base de datos
├── static/               # Archivos públicos de frontend (CSS, JS, iconos, plantillas .xlsx)
├── templates/            # Archivos HTML renderizados por Jinja2
├── tests/                # Pruebas unitarias de la aplicación con pytest
├── uploads/              # Carpeta de almacenamiento para archivos que sube el usuario
├── .env                  # Variables del entorno (Base de datos, tokens, configuraciones)
├── create_template.py    # Script utilitario para generar la plantilla Excel de empleados
├── requirements.txt      # Dependencias del proyecto Python
└── run.py                # Punto de entrada de la aplicación
```

---

## 🧑‍💻 Otros Comandos Útiles

**Generar la última versión de la plantilla Excel:**
Si necesitas actualizar los valores de la plantilla base para subir usuarios:
```bash
python create_template.py
```
Esto generará un archivo `plantilla_empleados.xlsx` en la ruta `static/downloads/`.

**Ejecutar pruebas unitarias:**
```bash
pytest tests/
```

**Crear una nueva migración (si cambias un Modelo en SQLAlchemy):**
```bash
flask db migrate -m "Mensaje explicando el cambio"
flask db upgrade
```

---

## 🛡️ Soporte y Contacto

Si tienes alguna pregunta o encuentras problemas a la hora de levantar el entorno, por favor revisa que:
1. Las credenciales de la base de datos son correctas en tu archivo `.env`.
2. El servicio de **Redis** se encuentra encendido (puerto `6379`).
3. Te encuentras dentro del entorno virtual al momento de ejecutar `python run.py`.
