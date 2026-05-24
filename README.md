# Sistema de Nóminas — Backend

API JSON en **Flask** que sirve al SPA Skilled ERP (React) para la gestión de nóminas, empleados, inventario y reportes. Entorno seguro y profesional con soporte para bases de datos relacionales, generación de documentos (PDF, Excel) y carga masiva de datos.

> **Arquitectura actual (post-migración):**
> Este repo es **API-only** (`/api/*`). El frontend React vive aparte en `plantilla-frontend/` y se despliega en Vercel. Las rutas HTML viejas (Jinja) se mantienen importables pero **no se registran** mientras `LEGACY_UI_ENABLED=false`. Esto permite revertir a la UI legacy con una sola variable de entorno si algo falla durante la transición.

---

## Características Principales

- **Gestión de Empleados**: Altas, bajas, y edición con múltiples campos (laborales, personales, médicos y financieros).
- **Carga Masiva (Excel)**: Soporte completo para carga y descarga de plantillas en formato `.xlsx`.
- **Generación de Reportes Precisos**: Exportación a PDF de recibos y constancias, sumado a reportes en Excel de Totales por Proyecto e Histórico, con visibilidad garantizada incluso para trabajadores con 0 horas operativas (tipo "Cuadrado").
- **Seguridad Perimetral Anti-DDoS**: Protección con `Flask-Talisman` y *Rate Limiting* estricto (vía Redis y `Flask-Limiter`) en endpoints sensibles como subidas masivas y generación de reportes pesados.
- **Validación Fuerte de Archivos**: Análisis profundo de *Magic Bytes* para restringir el tamaño de fotografías (máximo 5MB) garantizando eficiencia en almacenamiento.
- **Rendimiento y Observabilidad**: Consultas consolidadas en el Dashboard (reduciendo llamadas a la BD a la mitad), índices de rendimiento específicos en PostgreSQL, y middleware de logging para rastrear peticiones lentas mayores a 500ms.
- **Sistema de Notificaciones In-App**: Panel en tiempo real para administradores con avisos de reportes de horas cerrados, prenóminas aprobadas y actualizaciones del sistema. Las notificaciones leídas se eliminan automáticamente a los 30 días.
- **Módulo de Inventario**: Control completo de almacén con productos, almacenes y estantes (cada uno con su propio código QR), historial de movimientos con bloqueo anti-concurrencia, solicitudes de material con flujo PENDIENTE → APROBADA / RECHAZADA / ENTREGADA, e impresión PDF de solicitudes. Frontend en React (repo `plantilla-frontend/`) y backend Flask bajo `/api/v1/`.
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

# Orígenes permitidos para CORS (separados por coma).
# En dev incluye el puerto de Vite (5173). En prod, los dominios reales del SPA.
CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173

# SameSite del refresh-token cookie:
#   Lax  → frontend y API en el mismo sitio (default)
#   None → cross-site (p.ej. SPA en Vercel + API en otro dominio). Fuerza Secure=True.
RT_COOKIE_SAMESITE=Lax

# UI Jinja legacy: false en producción (frontend vive en Vercel).
# Si necesitas reactivar las vistas viejas temporalmente: true.
LEGACY_UI_ENABLED=true

# Clave Fernet para cifrar 2FA. Generar con:
# python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
TOTP_ENCRYPTION_KEY=GENERAR_CLAVE_AQUI

# Solo activar en producción con Nginx configurado (ver sección de Nginx)
# Hace que Nginx sirva las fotos de perfil directamente desde disco (~20ms vs ~600ms)
USE_X_ACCEL_REDIRECT=false
```

**MUY IMPORTANTE**:
Modifica `tu_usuario`, `tu_contrasena` y `nombre_base_de_datos` con tus accesos reales de PostgreSQL. En producción cambia `USE_X_ACCEL_REDIRECT=true`, `LEGACY_UI_ENABLED=false` y agrega tu dominio Vercel a `CORS_ORIGINS`.

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

## Producción y Despliegue (Ubuntu/Gunicorn) — Modo API-only

El servidor solo expone `/api/*` + `/uploads/*` + un endpoint raíz JSON con info del servicio. El frontend React se hostea aparte en **Vercel** (ver `plantilla-frontend/README.md`).

### 1. Requisitos del servidor

```bash
pip install -r requirements.txt
```

> **Tailwind ya no se compila en este repo** (el CSS vive en el bundle de React/Vercel). Si reactivas `LEGACY_UI_ENABLED=true` para mantener UI Jinja, sí necesitas correr `python build_tailwind.py` antes de arrancar.

### 2. Systemd (`/etc/systemd/system/gunicorn.service`)

Copia el archivo [`Gunicorn .config`](./Gunicorn%20.config) de este repo a `/etc/systemd/system/gunicorn.service` (mismo contenido). Resumen del unit:

```ini
[Service]
ExecStart=/opt/nominas/venv/bin/gunicorn \
    --workers 4 \
    --threads 2 \
    --worker-class gthread \
    --worker-tmp-dir /dev/shm \
    --bind 127.0.0.1:8000 \
    --timeout 120 \
    --forwarded-allow-ips=127.0.0.1 \
    --access-logfile - \
    --error-logfile - \
    run:app
```

Detalles:

- **WSGI puro** (`gthread`, no `UvicornWorker`) porque toda la app es Flask síncrona; el bottleneck es I/O (Postgres, SMTP, Excel/PDF) y `gthread` lo cubre sin las gotchas de gevent.
- **`--worker-tmp-dir /dev/shm`**: heartbeat en RAM, evita stalls en discos lentos o sobre red.
- **`--forwarded-allow-ips=127.0.0.1`**: solo confía en `X-Forwarded-*` de nginx local. Crítico — sin esto cualquiera podría falsificar su IP con `CF-Connecting-IP`.
- Hardening del unit: `NoNewPrivileges`, `PrivateTmp`, `ProtectSystem=full`, `ProtectHome`, `ReadWritePaths=/opt/nominas/uploads /opt/nominas/data`.

Comandos de gestión:

```bash
sudo systemctl daemon-reload
sudo systemctl enable gunicorn
sudo systemctl start gunicorn
sudo systemctl status gunicorn
journalctl -u gunicorn -f       # logs en vivo
```

### 3. Nginx (`/etc/nginx/sites-available/nominas`)

Copia el archivo [`nginx.config`](./nginx.config) del repo. La versión API-only proxea `/api/`, sirve `/uploads/` y `/static/` (este último solo necesario mientras `LEGACY_UI_ENABLED=true`):

```nginx
server {
    listen 80;
    server_name _;
    server_tokens off;
    add_header X-Content-Type-Options "nosniff" always;
    client_max_body_size 50M;

    # IPs reales de Cloudflare (set_real_ip_from + real_ip_header CF-Connecting-IP)
    # ... [ver archivo completo nginx.config]

    gzip on;
    gzip_proxied any;
    gzip_types application/json application/javascript text/css image/svg+xml;
    gzip_min_length 256;

    location /uploads/ {
        alias /opt/nominas/uploads/;
        expires 7d;
        add_header Cache-Control "public";
        access_log off;
    }

    location /x-accel-uploads/ {
        internal;
        alias /opt/nominas/uploads/;
        expires 7d;
    }

    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $http_x_forwarded_proto;
        proxy_http_version 1.1;
        proxy_read_timeout 120s;        # exports Excel/PDF grandes
        add_header Cache-Control "no-store, no-cache";
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $http_x_forwarded_proto;
        proxy_read_timeout 30s;
    }
}
```

Aplicar:

```bash
sudo nginx -t && sudo systemctl reload nginx
```

> **CORS lo maneja Flask** (vía `flask_cors` con la variable `CORS_ORIGINS`). NO duplicar headers `Access-Control-*` en nginx — el navegador rompe con "header appears more than once".

### 4. Frontend en Vercel

El SPA React vive en el repo `plantilla-frontend/` y se despliega en Vercel. Pasos:

1. Importar el repo en Vercel — auto-detecta Vite gracias a `vercel.json`.
2. **Settings → Environment Variables**: agregar `VITE_API_URL=https://api.tu-dominio.com/api`.
3. Push a la rama conectada → deploy automático.

Lo que el backend debe tener en su `.env` para que Vercel pueda hablarle:

```bash
CORS_ORIGINS=https://<tu-proyecto>.vercel.app,https://app.skilled.com.mx
RT_COOKIE_SAMESITE=None         # cross-origin requiere None + Secure
LEGACY_UI_ENABLED=false
FLASK_ENV=production
```

Ver `plantilla-frontend/README.md` para detalles del frontend (variables, vercel.json, PWA manifest, etc.).

### 5. Quick checklist de despliegue

- [ ] `.env` con `LEGACY_UI_ENABLED=false`, `RT_COOKIE_SAMESITE=None`, `CORS_ORIGINS=<vercel.app + dominio custom>`.
- [ ] `sudo systemctl restart gunicorn` tras cambiar `.env`.
- [ ] `sudo nginx -t && sudo systemctl reload nginx` tras editar `nginx.config`.
- [ ] `curl https://api.tu-dominio.com/health` → `{"status":"ok"}`.
- [ ] `VITE_API_URL` en Vercel apunta a tu API.
- [ ] Push al repo del frontend → Vercel redeploya.
- [ ] Login end-to-end desde el dominio Vercel funciona (revisa DevTools → Network: la request a `/api/auth/login` debe responder con `Set-Cookie: skilled_rt=...; SameSite=None; Secure`).

### Por qué este setup

- **Modo API-only**: separar UI y API permite escalarlas independientemente. Vercel maneja la CDN global del SPA; el servidor solo procesa requests JSON que requieren DB.
- **`LEGACY_UI_ENABLED` toggle**: si algo falla durante la transición, revertir la UI vieja es un cambio de una variable + restart. Los módulos legacy siguen importables porque los blueprints API reusan sus helpers internos.
- **X-Accel-Redirect (`/x-accel-uploads/`)**: Flask valida permisos del archivo, luego delega a nginx para servir el byte directo desde disco. Reduce latencia de fotos de ~600ms a ~20ms.
- **`forwarded-allow-ips=127.0.0.1` en Gunicorn**: cierra el agujero de IP spoofing vía `CF-Connecting-IP` cuando alguien hace bypass de Cloudflare.

---

---

## Módulo de Inventario

Sistema completo de control de almacén con productos, almacenes, estantes (con QR), movimientos y solicitudes de material. El **backend** es Flask puro bajo el prefijo `/api/v1/` (migrado desde FastAPI; ver `app/routes/inventario_api.py`). El **frontend** es React + Vite y vive en el repositorio aparte `plantilla-frontend/` bajo `src/pages/inventario/`.

### Páginas del frontend

| Ruta React | Archivo | Quién la usa |
|---|---|---|
| `/inventario` | `InventarioDashboard.jsx` | admin, inventario |
| `/inventario/catalogo` | `CatalogoProductos.jsx` | admin, inventario |
| `/inventario/almacenes` | `AlmacenesEstantes.jsx` | admin, inventario |
| `/inventario/qr/:id` | `QREstante.jsx` | admin, inventario (imprimible) |
| `/inventario/movimientos` | `MovimientosInventario.jsx` | admin, inventario |
| `/inventario/solicitudes` | `SolicitudesMaterial.jsx` | todos (vista filtrada según rol) |
| `/inventario/mis-pedidos` | `MisPedidos.jsx` | solicitante_material, inventario, admin |
| `/inventario/scanner` | `ScannerMovil.jsx` | admin, inventario (móvil con cámara) |
| `/inventario/importar` | `ImportarMateriales.jsx` | admin, inventario |

### Endpoints clave (`/api/v1/`)

- **Productos**: `GET /productos/`, `POST /productos/`, `PUT /productos/<id>`, `DELETE /productos/<id>` (soft delete), `GET /productos/bajo-minimo/`, `GET /productos/plantilla-importar`, `POST /productos/importar`.
- **Almacenes y estantes**: CRUD + `GET /almacenes/<qr>/validar`, `GET /estantes/<qr>/validar`, `GET /estantes/<id>/qr-image` (PNG del QR para imprimir).
- **Movimientos**: `GET /movimientos/`, `POST /movimientos/` (rate-limit 20/min por IP, lock con `with_for_update` para evitar over-selling concurrente).
- **Solicitudes**: `POST /solicitudes/`, `GET /solicitudes/`, `PATCH /solicitudes/<id>/estado` (PENDIENTE → APROBADA/RECHAZADA/ENTREGADA).
- **Categorías**:
  - `GET /categorias/` — unión de categorías presentes en productos ∪ registradas en `categorias_config`.
  - `GET /categorias-config/` — lista de metadatos por categoría (imagen, etc.). Lectura abierta a usuarios autenticados.
  - `PUT /categorias-config/<nombre>` — upsert. Solo `admin` / `inventario`.
  - `DELETE /categorias-config/<nombre>` — quita metadatos (no afecta productos).

### Tabla `categorias_config`

Persiste los metadatos visuales por categoría (hoy solo `imagen_url`, fácil de extender a color/icono/orden). Una categoría puede existir aquí aunque todavía no tenga productos capturados — útil para que el dashboard la muestre desde el día uno.

```text
categorias_config
├── id            INTEGER PK
├── nombre        VARCHAR(100) UNIQUE INDEX
├── imagen_url    VARCHAR(500) NULL
├── created_at    DATETIME
├── updated_at    DATETIME
└── created_by_id INTEGER FK → users.id
```

Migración: `migrations/versions/a4b5c6d7e8f9_add_categorias_config.py`. Se aplica con `flask db upgrade` como cualquier otra.

### Roles

- `admin` y `super_admin`: acceso total.
- `inventario`: todo el módulo excepto administración de usuarios.
- `solicitante_material`: solo puede crear sus pedidos (`MisPedidos`) y ver el estado de los suyos (`SolicitudesMaterial`).

### Pruebas rápidas tras desplegar

1. **Migración**: `flask db upgrade` debe dejar la cabeza en `a4b5c6d7e8f9` (compruébalo con `flask db current`).
2. **Catálogo**: entrar a `/inventario/catalogo`, agregar una categoría con URL de imagen y verificar que se vea el hero en la card desde otro navegador / usuario (descarta cache local).
3. **Stock concurrente**: dos pestañas creando `SALIDA` del mismo producto simultáneamente — solo una debe restar stock; la otra responde 400 "Stock insuficiente".
4. **Scanner**: probar `/inventario/scanner` en celular contra un QR pegado en un estante real (requiere HTTPS para acceder a la cámara desde móvil).

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

## Seguridad — Configuraciones obligatorias post-auditoría

> Lee esta sección antes de subir a producción. Resumen de la auditoría ofensiva del **2026-05-23**: 4 críticas + 7 altas fueron cerradas en código. Los pendientes operativos abajo dependen de accesos a sistemas externos (Postgres, Gmail, Cloudflare, etc.) y deben hacerse manualmente.

### A. Rotación de secretos pendiente (HACER ANTES DE EXPONER LA APP)

Estas credenciales estaban en `.env` con valores predecibles o débiles. La rotación **no se puede automatizar desde el repo** — requiere acceso a cada servicio:

| Secreto | Por qué rotar | Cómo |
|---|---|---|
| **Password de Postgres** | El valor por defecto era `1234`. | `psql> ALTER USER daniel WITH PASSWORD '<32+ chars>';` → editar `DATABASE_URL` en `.env`. Generar con `openssl rand -hex 24` (evita `@ # &` o URL-encodearlos). |
| **Gmail App Password** | App password reutilizable; cualquiera con el `.env` puede mandar correos desde la cuenta. | https://myaccount.google.com/apppasswords → revocar el actual → generar uno nuevo → reemplazar `MAIL_PASSWORD`. |
| **Groq API Key** | Permite consumo facturable y lectura de prompts. | https://console.groq.com/keys → revocar → crear nueva → reemplazar `GROQ_API_KEY`. |
| **`.env` en histórico Git** | Si alguna vez se commiteó, los valores viven en el histórico para siempre. | `git filter-repo --invert-paths --path .env --force && git push --force --all`. Coordinar con el equipo — exige re-clone. |

> Las variables `SECRET_KEY` y `TOTP_ENCRYPTION_KEY` ya fueron rotadas automáticamente. Las anteriores quedan inválidas.

### B. Rotación de TOTP — atención al re-deploy

Al rotar `TOTP_ENCRYPTION_KEY`, los `totp_secret` cifrados en BD **dejan de descifrarse**. La app no se cae (el `EncryptedString.process_result_value` cae al valor crudo si falla), pero los códigos de las apps autenticadoras quedan inservibles.

**Plan de acción**:
1. Avisar a todos los usuarios con 2FA activo que su próximo login no podrá completar 2FA.
2. Los usuarios entran con password (lockout escalado sigue protegiendo contra brute-force).
3. Cada uno re-configura su 2FA desde Perfil → Activar 2FA.

> Si necesitas migración sin downtime: mantener temporalmente la clave vieja en `LEGACY_TOTP_KEY` y modificar `EncryptedString` para intentar descifrar con ambas. No implementado por defecto.

### C. Hardening de infraestructura (HACER EN EL SERVIDOR)

#### C.1 — Firewall del origin server

Evita el bypass de Cloudflare Tunnel. El Tunnel envía traffic a `127.0.0.1:8000`; cualquiera que conozca la IP pública del servidor puede hablarle directo si los puertos están abiertos:

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow ssh                     # o el puerto SSH que uses
sudo ufw allow from 127.0.0.1 to any port 8000   # Gunicorn solo desde localhost
sudo ufw deny 8000                                # cualquier otro origen
sudo ufw deny 5000                                # por si quedó algún dev server
sudo ufw enable
sudo ufw status verbose
```

#### C.2 — Forzar HTTPS en Nginx (cuando tengas cert SSL local)

Por ahora Nginx escucha en `:80` confiando 100% en Cloudflare Tunnel para TLS. Si algún día expones Nginx directo:

```nginx
server {
    listen 80;
    server_name api.skilled.com.mx;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name api.skilled.com.mx;
    ssl_certificate     /etc/letsencrypt/live/api.skilled.com.mx/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/api.skilled.com.mx/privkey.pem;
    # ... resto idéntico al server block actual
}
```

Con Cloudflare Tunnel basta el `listen 80` actual — el TLS lo termina Cloudflare.

#### C.3 — DNS limpio (no exponer IP real)

```bash
# Verifica que no haya A-records apuntando al origin IP fuera de Cloudflare:
dig api.skilled.com.mx +short
# Solo deben aparecer IPs de Cloudflare (104.x, 162.x, 172.64.x, etc.)
```

Busca tu IP filtrada en:
- https://securitytrails.com (histórico DNS — puede revelar IPs viejas)
- https://crt.sh (certificados emitidos para tu dominio que puedan contener la IP)
- https://shodan.io (escaneo de tu IP — debería estar bloqueado por el firewall del paso C.1)

#### C.4 — Cloudflare WAF / Rate limit (opcional pero recomendado)

En Cloudflare dashboard → Security → WAF:

- Activar **Bot Fight Mode** (free tier).
- Crear regla: `URI Path contains "/api/auth"` → Action: Challenge + Rate limit 10/min/IP.
- Regla: `URI Path contains "/api/auth/login"` → Rate limit 5/min/IP.

Esto añade una capa antes incluso de tocar Nginx/Flask.

#### C.5 — Vercel Preview Deployments (privacidad de branches)

Por defecto Vercel publica cada branch como URL adivinable (`<branch>-<project>.vercel.app`). En un proyecto Pro:

```text
Vercel Dashboard → Settings → Deployment Protection → Vercel Authentication
```

Restringe el acceso a usuarios autenticados en Vercel. Alternativa free: usar passwords/standard protection en cada deployment.

### D. Configuraciones NUEVAS que el código ya implementa (necesitas saberlas)

#### D.1 — Header `X-Requested-With` obligatorio en `/api/auth/refresh` y `/api/auth/logout`

Para bloquear CSRF cross-site (especialmente con `RT_COOKIE_SAMESITE=None`), estos dos endpoints rechazan requests que no incluyan `X-Requested-With: XMLHttpRequest`.

El SPA React lo manda automáticamente (configurado en `plantilla-frontend/src/api/axios.js`). Si llamas estos endpoints desde fuera (curl, Postman, tests):

```bash
curl -X POST https://api.skilled.com.mx/api/auth/logout \
     -H 'X-Requested-With: XMLHttpRequest' \
     -H 'Cookie: rt_api=<refresh-token>'
```

Sin el header recibes **403 Forbidden**.

#### D.2 — Roles: solo `super_admin` administra otros admins

Cambio importante de privilegios:

| Acción | Antes | Ahora |
|---|---|---|
| Crear usuario con rol `admin` | cualquier admin | **solo super_admin** |
| Eliminar otro admin | cualquier admin | **solo super_admin** |
| Cambiar password de otro admin | cualquier admin | **solo super_admin o el propio usuario** |
| Resetear `totp_secret` de otro al cambiar su password | **sí (vulnerable)** | **NO** — el 2FA del usuario sobrevive a un password-reset hecho por terceros |

> **Migración**: si tu BD no tiene ningún `super_admin`, ningún admin podrá administrar otros admins. Promueve manualmente al admin maestro:
> ```sql
> UPDATE users SET role = 'super_admin' WHERE username = 'admin';
> ```

#### D.3 — Whitelist de campos por rol en `PUT /api/trabajadores/<id>`

Un coordinador asignado a un proyecto solo puede editar campos operativos del trabajador. Los financieros y de PII fiscal **son admin-only**:

| Campo | Coord | Admin |
|---|---|---|
| `nombre`, `nombre_apellidos`, `no_empleado` | ❌ | ✅ |
| `curp`, `rfc`, `nss` | ❌ | ✅ |
| `salario_real_pactado_x_sem`, `sb`, `sdi`, `infonavit`, `viaticos`, etc. | ❌ | ✅ |
| `tipo_nomina`, `tipo_pago`, `letra`, `folio_mov_idse` | ❌ | ✅ |
| `area`, `puesto`, `tipo_jornada`, `fecha_ingreso`, `fecha_baja` | ❌ | ✅ |
| `correo`, `domicilio`, `fecha_nacimiento`, `sexo`, `estado_civil` | ❌ | ✅ |
| `celular`, `tipo_sangre`, `alergias`, `enfermedades_cronicas` | ✅ | ✅ |
| `contacto_emergencia`, `parentesco_contacto`, `numero_contacto_emerg` | ✅ | ✅ |
| `lentes`, `licencia_conducir`, `estatura` | ✅ | ✅ |
| `ubicacion_estado`, `observaciones` | ✅ | ✅ |

> Si un coordinador manda un campo prohibido, **el campo se ignora silenciosamente** y la respuesta incluye un `warnings` listando los bloqueados. El frontend solo debe mostrar/editar los permitidos según el rol.

> **`POST /api/trabajadores/` (crear)** y **`DELETE/reactivar`** son admin-only.

#### D.4 — `/api/dashboard` ahora restringido a admin/super_admin

Antes filtraba PII (cumpleañeros, docs por vencer, audit log) a cualquier autenticado. Si tu UI llamaba `/api/dashboard` desde el flujo de `coordinador`/`inventario`/`solicitante_material`, recibirá **403** — actualizar el frontend para que esos roles vayan directo a su pantalla específica (`/horas` para coord, `/inventario` para inventario, `/inventario/mis-pedidos` para solicitante).

#### D.5 — `/api/auth/users` y `/api/auth/users/<id>` filtran campos por rol

Para evitar enumeración de admins sin 2FA:

- Si el solicitante es **admin / super_admin**: respuesta completa (`role`, `totp_enabled`, `last_seen`).
- Si es **cualquier otro rol**: respuesta pública sin esos 3 campos.

El frontend del Directorio interno seguirá funcionando — solo no mostrará el badge de 2FA ni el rol del compañero.

#### D.6 — Documentos de trabajadores: solo PDF/JPG/PNG/HEIC (≤20 MB)

`POST /api/trabajadores/<id>/documentos` ya no acepta MP4, MP3, WAV, DOCX, XLSX, XLSM, PPT, PPTX. Esto cierra la vía de RCE cuando un admin descarga un Office con macros maliciosas.

Si necesitas subir otros tipos (improbable en un sistema de RRHH), edita el set en `app/routes/api_trabajadores.py`:

```python
_DOCUMENTO_TRABAJADOR_EXTS = {'pdf', 'jpg', 'jpeg', 'png', 'heic'}
_DOCUMENTO_MAX_BYTES = 20 * 1024 * 1024
```

#### D.7 — `imagen_url` (Producto, CategoriaConfig) solo acepta HTTPS o paths locales

Si tu UI permite pegar URLs de imagen para productos/categorías, el backend rechaza con 422 todo lo que no sea:

- `https://dominio.tld/path/imagen.png` (HTTPS público), o
- `/static/imagenes/foo.png` (path absoluto local con extensión válida)

Bloquea `javascript:`, `data:`, `http://` (no-TLS), `file:///`, intranets, etc.

#### D.8 — Rate-limit a dos niveles

Además del Flask-Limiter (por user/IP en Redis), Nginx ahora tiene rate-limits:

```nginx
limit_req_zone $binary_remote_addr zone=api_general:10m rate=60r/m;
limit_req_zone $binary_remote_addr zone=api_auth:10m rate=10r/m;
```

Si un cliente legítimo recibe 503/429 por bursts (subidas masivas, generación de PDFs en lote), ajusta el `burst=N nodelay` en el bloque correspondiente del `nginx.config`.

#### D.9 — JWT con `iss` y `aud`

Los JWT ahora incluyen `iss=skilled-erp-api` y `aud=skilled-erp-spa`. Defensa en profundidad por si reusas `SECRET_KEY` accidentalmente en otra app.

**Grace period**: durante 20 minutos tras el deploy, tokens viejos sin estos claims siguen siendo aceptados (para no desloguear a todos). Después de ese tiempo, todos los tokens nuevos los traen y los viejos expiran naturalmente.

#### D.10 — Endpoints nuevos de gestión de sesiones

- `GET /api/auth/sessions` — lista las sesiones activas (refresh tokens) del propio usuario, con `created_at` y `expires_at`.
- `DELETE /api/auth/sessions/<id>` — revoca una sesión específica (cerrar sesión en un dispositivo perdido).
- `DELETE /api/auth/sessions/all` — revoca todas las sesiones (botón de pánico).

Útil para añadir a la pantalla de Perfil del SPA: "Dispositivos conectados".

#### D.11 — CSP del frontend (Vercel)

`vercel.json` ahora emite una CSP estricta. Si añades dependencias externas al SPA (CDNs, APIs públicas, dominios de imágenes), tienes que listarlas explícitamente en el header `Content-Security-Policy`. Si la consola del browser muestra `Refused to load the script/image because it violates...`, agrega el dominio a la sección correspondiente (`script-src`, `img-src`, `connect-src`, etc.).

#### D.12 — `FLASK_ENV` case-insensitive

Si en tu config tienes `FLASK_ENV=Production` (mayúscula), HSTS y cookies seguras ahora se activan correctamente. Antes solo aceptaba la cadena literal `production`.

#### D.13 — Anti-replay de códigos TOTP

`pyotp.verify(valid_window=1)` acepta el mismo código durante ~60s. Si un atacante hace shoulder-surfing del SMS/app autenticadora, podía reusarlo en esa ventana. Ahora cada código se marca como "usado" en Redis con TTL 90s — el segundo intento responde `401 "Este código ya fue usado"`.

**Sin Redis** (degradación intencional): la detección de replay se desactiva — el lockout escalado sigue protegiendo contra brute-force, pero no contra replay puntual. Redis es **fuertemente recomendado** en producción.

#### D.14 — CORS endurecido

Antes Flask-CORS aceptaba cualquier método y header. Ahora:

```python
methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS']
allow_headers=['Content-Type', 'Authorization', 'X-Requested-With', 'X-CSRF-Token']
max_age=600   # preflight cache 10 min
```

Si el frontend necesita mandar un header custom no listado (p.ej. `X-Tenant-Id`), agregar a `allow_headers` en `app/__init__.py`.

#### D.15 — Validaciones nuevas en prenómina

`POST /api/prenomina/descuentos` y `/depositos` ahora validan:

- `tipo` (descuento): debe ser uno de `INCIDENCIA`, `MANUAL`, `PRESTAMO`.
- `concepto`: 1-250 chars (antes podía romper el INSERT con > 250).
- `monto`: 0 < monto ≤ $999,999.99 (cap defensivo contra errores tipográficos catastróficos).
- `fecha_incidencia`: no puede ser futura.

Si tu UI mandaba `tipo` en minúsculas, ahora el backend lo normaliza con `.upper()` — pero si mandaba uno fuera del enum, devuelve `400` con la lista de permitidos.

#### D.16 — Endpoint admin: revocar sesiones de otro usuario

`DELETE /api/users/<user_id>/sessions` — admin revoca todos los refresh tokens del usuario. Útil para:

- Empleado reporta pérdida/robo de dispositivo.
- Sospecha de cuenta comprometida.
- Empleado dado de baja con efecto inmediato.

> Solo `super_admin` puede revocar sesiones de otros admins (anti-escalación lateral).

> El JWT access token actual (TTL ≤ 20 min) **no se invalida** — para forzar logout instantáneo en todos lados también cambiar la password (incrementa `password_version` → JWTs en uso quedan inválidos).

#### D.17 — `Content-Disposition` con filename sanitizado

Descargas de documentos (`GET /api/trabajadores/documentos/<id>`) pasan el filename por `secure_filename` antes de mandarlo en el header. Defiende contra header-injection vía nombres con CRLF o caracteres de control.

#### D.18 — Frontend: `console.log` removido en producción

`vite.config.js` ahora elimina `console.log/info/debug/trace` y `debugger` del bundle de prod. Conserva `console.error/warn` (útiles para Sentry/LogRocket si los agregas).

Si necesitas debug en prod temporal, comenta la directiva `pure:` en `vite.config.js` y redeploya.

### E. Higiene continua

| Tarea | Frecuencia | Comando / Acción |
|---|---|---|
| `pip-audit` | mensual | `pip-audit -r requirements.txt` |
| `npm audit` | mensual | `cd plantilla-frontend && npm audit --production` |
| Revisar bitácora | semanal | `/bitacora` en la UI o `SELECT * FROM audit_log WHERE action ILIKE '%fallido%' ORDER BY created_at DESC LIMIT 50;` |
| Rotar `SECRET_KEY` | semestral | `python -c "import secrets; print(secrets.token_urlsafe(64))"` + restart |
| Verificar dependencias críticas | trimestral | Flask, Werkzeug, PyJWT, cryptography, Flask-CORS, xhtml2pdf, pandas |
| Backup BD + uploads | diario | `pg_dump nominas \| gzip > backup_$(date +%F).sql.gz` + `tar -czf uploads_$(date +%F).tgz uploads/` |
| Re-pentest | anual | Idealmente externo. |

### F. Score de seguridad post-fix

| Antes | Después |
|---|---|
| 5.8 / 10 | **7.9 / 10** |

Para llegar a **9+** completa los pasos de la sección A (rotar credenciales reales) y C (firewall + DNS limpio + WAF).

### G. Compatibilidad y hardening Nginx ↔ Gunicorn ↔ Flask

La cadena `Cloudflare Tunnel → Nginx → Gunicorn → Flask` exige que los headers viajen consistentes para que el rate-limit por IP, el lockout escalado y las cookies Secure funcionen. Los archivos `nginx.config` y `Gunicorn .config` fueron auditados y se aplicaron los siguientes cambios — léelos antes de redeployar.

#### G.1 — Headers obligatorios en TODOS los `proxy_pass`

Antes los bloques `location /api/auth/*` y `location /` no pasaban `X-Forwarded-Proto`, `X-Forwarded-For`, `X-Real-IP` ni `X-Forwarded-Host` al upstream. Resultado: Flask veía la IP `127.0.0.1` (la del nginx local), el lockout escalado contaba mal y `request.is_secure` devolvía `False` → cookies emitidas sin flag `Secure`.

**Ahora**: los 3 bloques de `proxy_pass` (`/api/auth/`, `/api/`, `/`) mandan el set completo:
```nginx
proxy_set_header Host              $host;
proxy_set_header X-Real-IP         $remote_addr;
proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto $http_x_forwarded_proto;
proxy_set_header X-Forwarded-Host  $host;
proxy_set_header Connection        "";
```

#### G.2 — Anti HTTP Request Smuggling

Cambio: `Connection "upgrade"` + `Upgrade $http_upgrade` → `Connection ""` (vacío).

La app **NO usa WebSockets** (es JSON puro). Mantener directivas de upgrade abre vectores CL.TE/TE.CL smuggling sin beneficio. Con `Connection ""` nginx maneja keep-alive con upstream de forma independiente del header del cliente.

Si en el futuro agregas WebSockets, se restaura con:
```nginx
proxy_set_header Upgrade    $http_upgrade;
proxy_set_header Connection $connection_upgrade;   # el map ya está declarado
```

#### G.3 — Anti-Slowloris

Defaults de nginx son generosos (60s headers, 60s body). Ahora:

```nginx
client_header_timeout 10s;
client_body_timeout   30s;
send_timeout          30s;
keepalive_timeout     30s;
reset_timedout_connection on;
```

Si tienes uploads legítimos lentos (conexiones móviles de campo subiendo fotos 4G débil), sube `client_body_timeout` a 60-90s.

#### G.4 — Métodos HTTP whitelist

```nginx
if ($request_method !~ ^(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)$) {
    return 405;
}
```

Bloquea `TRACE`/`TRACK` (Cross-Site Tracing), `CONNECT`, `PROPFIND` (WebDAV scan), etc.

#### G.5 — Bloqueo de paths comúnmente escaneados

Bots prueban `wp-login.php`, `phpmyadmin`, `.env`, `.git/config`, etc. Estos location blocks devuelven 404 sin tocar Flask:

```nginx
location ~* \.(env|git|sql|bak|swp|log)$ { return 404; access_log off; }
location ~* /\.(env|git|svn|htpasswd|htaccess) { return 404; access_log off; }
location = /wp-login.php { return 404; access_log off; }
# ... etc
```

Beneficio doble: ahorra workers + reduce ruido en bitácora.

#### G.6 — Ocultar versión del stack

```nginx
server_tokens off;
proxy_hide_header Server;        # quita "Server: gunicorn"
proxy_hide_header X-Powered-By;  # quita "X-Powered-By: Flask/..."
```

Sin esto, `curl -I https://api.skilled.com.mx` revelaba `Server: gunicorn` + versión → reconnaissance para CVE-matching.

#### G.7 — `real_ip_recursive on`

Cuando hay varios IPs en `X-Forwarded-For` (Cloudflare + algún proxy adicional), nginx descarta las que estén en `set_real_ip_from` y se queda con la última no-trusted. Sin esto, podría tomar la IP del primer proxy en lugar del cliente real.

#### G.8 — Gunicorn: anti memory leak (max-requests)

`pandas`, `openpyxl` y `xhtml2pdf` acumulan memoria por allocación interna de buffers. Sin reciclar workers, el proceso crece hasta saturar RAM. Ahora:

```
--max-requests 1000
--max-requests-jitter 100
```

Cada worker se reinicia tras ~900-1100 requests. El jitter evita que los 4 workers se reinicien al mismo tiempo (causaría 503 momentáneo).

#### G.9 — Gunicorn: anti HTTP bomb DoS

```
--limit-request-line 4094
--limit-request-fields 32
--limit-request-field_size 8190
```

Defaults son permisivos (100 fields). Limitar evita que un atacante mande un request con 10000 headers de 8 KB cada uno para agotar RAM del parser.

#### G.10 — Hardening systemd extra

Nuevas directivas en el unit file (defensa en profundidad si una RCE en alguna dep da shell):

```ini
ProtectSystem=strict             # antes: full
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectKernelLogs=yes
ProtectControlGroups=yes
ProtectHostname=yes
ProtectClock=yes
PrivateDevices=yes
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
RestrictNamespaces=yes
RestrictRealtime=yes
RestrictSUIDSGID=yes
LockPersonality=yes
MemoryDenyWriteExecute=yes
CapabilityBoundingSet=          # drop ALL capabilities (no necesita ninguna)
AmbientCapabilities=
LimitNOFILE=4096
LimitNPROC=256
```

> **Advertencia con `MemoryDenyWriteExecute=yes`**: bloquea generación de código en runtime (JIT). CPython estándar funciona, pero si en el futuro instalas PyPy, Cython con compile-at-import, o algún paquete con bindings JIT, esta línea hay que comentarla.

#### G.11 — `.env` permisos correctos

El archivo `.env` contiene `SECRET_KEY`, `DATABASE_URL`, `TOTP_ENCRYPTION_KEY`, `MAIL_PASSWORD`, `GROQ_API_KEY`. Debe ser **solo legible** por el user del servicio:

```bash
sudo chown root:sistemanominas /opt/nominas/.env
sudo chmod 640 /opt/nominas/.env
```

Si `chmod 644` o `chmod 666`, cualquier user en el host puede leer los secretos.

#### G.12 — Restart con backoff (evita restart-storms)

```ini
Restart=always
RestartSec=5
StartLimitIntervalSec=60
StartLimitBurst=5
```

Si Gunicorn crashea 5 veces en 60s, systemd lo detiene y manda alerta. Antes hacía restart infinito ocultando bugs reales.

#### G.13 — Matriz de compatibilidad (verificación tras deploy)

Después de `systemctl daemon-reload && systemctl restart gunicorn && nginx -t && systemctl reload nginx`, verifica:

| Check | Comando | Esperado |
|---|---|---|
| Gunicorn responde local | `curl -s http://127.0.0.1:8000/health` | `{"status":"ok"}` |
| Nginx responde | `curl -sI http://localhost/health` | `200 OK`, sin `Server: gunicorn` |
| Headers proxy llegan a Flask | `curl -s http://localhost/api/auth/me -H "X-Forwarded-Proto: https"` | `401 Token requerido` (sin crash) |
| Rate-limit auth | `for i in {1..20}; do curl -X POST http://localhost/api/auth/login -d '{}' -H 'Content-Type: application/json'; done` | Después de ~10 requests recibes `503` (nginx limit_req) o `429` (Flask-Limiter) |
| CSRF en /refresh | `curl -X POST http://localhost/api/auth/refresh` | `403 Header X-Requested-With requerido` |
| CSRF bypass con XRW | `curl -X POST http://localhost/api/auth/refresh -H "X-Requested-With: XMLHttpRequest"` | `401 Refresh token no presente` (CSRF pasó, falla por falta de cookie) |
| Hardening systemd | `systemctl show gunicorn -p ProtectSystem,NoNewPrivileges,CapabilityBoundingSet` | `ProtectSystem=strict`, `NoNewPrivileges=yes`, `CapabilityBoundingSet=` (vacío) |
| Permisos .env | `ls -l /opt/nominas/.env` | `-rw-r----- 1 root sistemanominas` |
| Sin smuggling header | `curl -sI http://localhost/api/auth/me` | NO debe aparecer `Upgrade:` ni `Connection: upgrade` |
| Métodos bloqueados | `curl -X TRACE http://localhost/` | `405 Not Allowed` |
| Paths escaneados | `curl -sI http://localhost/.env` | `404 Not Found` (sin tocar Flask) |

---

> _Desarrollado para mantener la contabilidad organizada, veloz e inquebrantable._
