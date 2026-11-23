# Instalación y configuración

Cómo levantar el backend en local, qué variables necesita el `.env` y cómo está organizado el repo. Para el despliegue en el VPS ver [`PRODUCCION.md`](./PRODUCCION.md).

---

## Requisitos

- Python 3.12 (las imágenes usan `python:3.12-slim`; 3.11 es el mínimo que exige pandas 3)
- PostgreSQL 14+ (los contenedores traen 18)
- Redis — obligatorio: `create_app()` aborta el arranque si no conecta
- Docker + Docker Compose si vas por el camino recomendado
- Git

---

## Con Docker (recomendado)

```bash
docker compose up --build      # api en http://localhost:5000
```

Levanta la API con su propio PostgreSQL, Redis y ClamAV usando el `.env` de la raíz y una copia de la base `MASTER`. Es la única forma de probar en Windows el camino real de WebSockets con gevent y el antivirus, que en el venv nativo no arrancan. Guía del día a día: [`DIA_A_DIA.md`](./DIA_A_DIA.md). Montaje y despliegue: [`DOCKER.md`](./DOCKER.md).

El VPS **todavía no corre en Docker**; lo que falta y qué lo bloquea está en [`PENDIENTE_PRODUCCION.md`](./PENDIENTE_PRODUCCION.md).

---

## En un venv

```bash
git clone <repo-url>
cd Skilled-api

python -m venv venv
venv\Scripts\activate           # Windows
source venv/bin/activate        # Linux / Mac

pip install -r requirements.txt

flask db upgrade
python run.py                   # http://localhost:5000
```

Crea la BD en Postgres antes de migrar y deja `SOCKETIO_ASYNC_MODE=threading`: gevent crashea con psycopg en Windows.

---

## `.env`

Copia [`.env.example`](../.env.example) —documenta todas las variables con sus comandos de generación— y rellena los valores:

```bash
cp .env.example .env
```

Las que importan:

| Variable | Notas |
|---|---|
| `SECRET_KEY` | Obligatoria, la app no arranca sin ella |
| `DATABASE_URL` | Driver psycopg v3: `postgresql+psycopg://...` |
| `TOTP_ENCRYPTION_KEY` | Clave Fernet para cifrar secretos 2FA en BD |
| `REDIS_URL` | Rate-limit, lockout, anti-replay TOTP y message queue de Socket.IO |
| `CORS_ORIGINS` | Dev: Vite (5173). Prod: dominios Vercel/custom |
| `RT_COOKIE_SAMESITE` | `Lax` same-origin (dev); `None` cross-origin (prod) |
| `SOCKETIO_ASYNC_MODE` | `threading` en dev (default); `gevent` en prod y en los contenedores |
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` | Pool por proceso (10 + 10 por default). Total = `workers × (pool+overflow) ≤ max_connections` |
| `R2_*` | Bucket público del catálogo + dominio que lo sirve |
| `R2_PRIVADO_*` | Bucket privado de documentos y fotos. Si `R2_PRIVADO_BUCKET` va vacía, todo se queda en disco (`uploads/`) |
| `CLAMAV_HOST` / `CLAMAV_SOCKET` | Demonio del antivirus. `CLAMAV_FAIL_CLOSED=true` en prod |
| `IMG_MAX_DOWNLOAD_BYTES` / `IMG_MAX_PIXELS` | Topes de la descarga de imágenes externas |
| `USE_X_ACCEL_REDIRECT` | `true` solo en prod con nginx configurado |
| `HSTS_PRELOAD` | `false` salvo que estés seguro (semi-irreversible) |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | Solo para el compose: con ellas crea la base del contenedor |

---

## Estructura del repo

```text
Skilled-api/
├── run.py                  # Entry point; monkey-patch de gevent si SOCKETIO_ASYNC_MODE=gevent
├── requirements.txt        # + constraints.txt
├── Dockerfile              # Imagen de la API (builder + runtime)
├── docker-compose.yml      # Stack de desarrollo (api, db, redis, clamav)
├── docker-compose.prod.yml # Stack del VPS
├── docker/                 # entrypoint, espera de la BD, bootstrap del esquema
├── nginx.config            # Config de Nginx
├── gunicorn.serviceee      # Unit de systemd (se instala como nominas.service)
├── app/
│   ├── __init__.py         # create_app(): CORS, Talisman, Limiter, blueprints, Socket.IO
│   ├── extensions.py       # db, limiter, mail; IP real tras Cloudflare; EncryptedString
│   ├── realtime.py         # Socket.IO: handlers, hooks ORM, emit_to_*
│   ├── observabilidad.py   # after_request: contadores, histograma y detalle en Redis
│   ├── constants.py        # constantes compartidas
│   ├── models/             # SQLAlchemy, 12 módulos por dominio
│   ├── routes/             # 18 blueprints, cada uno como sub-paquete con _core.py
│   └── utils/              # seguridad, archivos, R2, antivirus, imágenes, horas, nómina
├── templates/              # Jinja SOLO para PDFs (xhtml2pdf)
├── migrations/             # Alembic
├── static/                 # Imágenes del catálogo servidas localmente y capturas del README
├── docs/                   # Documentación
└── tests/                  # 36 módulos de pytest
```

Detalle del paquete `app/` en [`ARQUITECTURA.md`](./ARQUITECTURA.md).

---

## Comandos útiles

```bash
# Migraciones
flask db migrate -m "descripción"
flask db upgrade
flask db current

# Tests (correrlos tras cualquier cambio)
pytest tests/

# Docker
docker compose up -d               # levantar en segundo plano
docker compose logs -f api         # logs de la API
docker compose exec api sh         # entrar al contenedor
docker compose exec api pytest     # tests dentro del contenedor
```

Las plantillas de Excel no se generan con ningún script: se descargan de `GET /api/trabajadores/plantilla-importar` y `GET /api/v1/productos/plantilla-importar`.
