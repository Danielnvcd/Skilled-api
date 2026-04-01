# Sistema de Nóminas 🏢

Un sistema integral basado en web para la gestión de nóminas, empleados y reportes, desarrollado en **Python (Flask)**. Ofrece un entorno seguro y profesional con soporte para bases de datos relacionales, generación de documentos (PDF, Excel) y opciones avanzadas de carga de datos masivos.

---

## 🚀 Características Principales

- **Gestión de Empleados**: Altas, bajas, y edición con múltiples campos (laborales, personales, médicos y financieros).
- **Carga Masiva (Excel)**: Soporte completo para carga y descarga de plantillas en formato `.xlsx`.
- **Generación de Reportes Precisos**: Exportación a PDF de recibos y constancias, sumado a reportes en Excel de Totales por Proyecto e Histórico, con visibilidad garantizada incluso para trabajadores con 0 horas operativas (tipo "Cuadrado").
- **Seguridad Perimetral Anti-DDoS**: Protección con `Flask-Talisman` y *Rate Limiting* estricto (vía Redis y `Flask-Limiter`) en endpoints sensibles como subidas masivas y generación de reportes pesados.
- **Validación Fuerte de Archivos**: Análisis profundo de *Magic Bytes* para restringir el tamaño de fotografías (máximo 5MB) garantizando eficiencia en almacenamiento.
- **Rendimiento y Observabilidad**: Consultas consolidadas en el Dashboard (reduciendo llamadas a la BD a la mitad), índices de rendimiento específicos en PostgreSQL, y middleware de logging para rastrear peticiones lentas mayores a 500ms.
- **Base de Datos Robusta**: Mapeo ORM con `SQLAlchemy` conectado a **PostgreSQL**.

---

## 🛠️ Requisitos del Sistema

Para ejecutar el proyecto sin problemas, necesitas tener instalados en tu computadora (haz clic sobre ellos si necesitas instalarlos):

1. [Python 3.9 o superior](https://www.python.org/downloads/)
2. [Git](https://git-scm.com/downloads)
3. [PostgreSQL](https://www.postgresql.org/download/)
4. [Redis](https://redis.io/download/) - *(Opcionalmente, puedes levantar Redis y Postgres utilizando [Docker](https://www.docker.com/products/docker-desktop/))*

---

## ⚙️ Pasos de Instalación Rápida (Estilo Copiar y Pegar)

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

**En Windows (PowerShell) / Mac / Linux:**
```bash
echo FLASK_APP=run.py >> .env
echo FLASK_ENV=development >> .env
echo SECRET_KEY=una_clave_secreta_super_segura12345 >> .env
echo "DATABASE_URL=postgresql+psycopg2://tu_usuario:tu_contrasena@localhost:5432/nombre_base_de_datos" >> .env
echo REDIS_URL=redis://localhost:6379/0 >> .env
```

⚠️ **MUY IMPORTANTE**:
Abre el nuevo archivo `.env` que se creó. Asegúrate de modificar `tu_usuario`, `tu_contrasena` y `nombre_base_de_datos` con tus accesos reales configurados de PostgreSQL.

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

> ¡Listo! Abre tu navegador favorito y accede a: **http://localhost:5000** 🚀

---

## 🧑‍💻 Comandos Útiles para el Día a Día

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

---

## 📁 Arquitectura del Código del Proyecto

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

> _Desarrollado para mantener la contabilidad organizada, veloz e inquebrantable._
