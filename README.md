# Sistema de Nóminas — Backend (Skilled ERP)

API JSON en **Flask** que sirve al SPA Skilled ERP (React) para nóminas, empleados, inventario y reportes. Despliegue **API-only**: este repo expone `/api/*`; el frontend React vive en el repo `plantilla-frontend/` y se hostea en Vercel.

---

## Stack

- **API**: Flask + SQLAlchemy + Flask-JWT-Extended + Flask-Limiter + Flask-Talisman
- **DB**: PostgreSQL (psycopg v3)
- **Cache / Rate-limit / Anti-replay TOTP**: Redis
- **Realtime**: Socket.IO — `gevent` en prod (WebSocket real), `threading` en dev; `message_queue` en Redis para emitir entre workers
- **Documentos**: pandas + openpyxl (Excel), xhtml2pdf (PDF)
- **Despliegue**: Gunicorn (gevent + `GeventWebSocketWorker`, 4 workers × 1000 conexiones) detrás de Nginx → Cloudflare Tunnel (`protocol: http2`, requerido para WS estable)

---

## Características Principales

- **Empleados**: alta, baja, edición; campos laborales, personales, médicos y financieros con whitelist por rol. Notas internas tipo "chatter" en la ficha (ver `docs/NOTAS_TRABAJADOR.md`).
- **Listados ordenables**: trabajadores y préstamos aceptan `?sort=<campo>&dir=asc|desc` con whitelist de columnas (ver `docs/ORDENAMIENTO_LISTADOS.md`).
- **Carga masiva**: import/export de plantillas `.xlsx`.
- **Reportes**: PDF (recibos, constancias) y Excel (totales por proyecto, histórico).
- **Inventario**: productos, almacenes y estantes con QR; movimientos con lock anti-concurrencia; flujo de solicitudes `PENDIENTE → APROBADA/RECHAZADA/ENTREGADA`; PDF de solicitudes; tomas físicas con ajustes automáticos; etiquetas Avery y órdenes de compra express.
- **Herramientas**: catálogo + unidades físicas rastreables (serie/QR); asignaciones a trabajadores, mantenimientos, incidencias y flujo de baja con autorización.
- **Realtime**: notificaciones in-app vía Socket.IO; expiración automática a 30 días de las leídas.
- **Seguridad**: rate-limit a dos niveles (Nginx + Flask-Limiter), JWT con `iss`/`aud`, refresh-token rotation en cookie, lockout escalado, anti-replay TOTP en Redis, validación de magic bytes en uploads.

---

## Requisitos

- Python 3.11+ (pandas 3.x lo exige; local corre 3.12)
- PostgreSQL 14+
- Redis (recomendado en prod; sin él la anti-replay TOTP se degrada)
- Git

---

## Instalación Local (Dev)

```bash
git clone <repo-url>
cd "Sistema de nominas"

# Entorno virtual
python -m venv venv
venv\Scripts\activate           # Windows
source venv/bin/activate        # Linux / Mac

pip install -r requirements.txt
```

### `.env` (crear en la raíz)

Copia [`.env.example`](./.env.example) — documenta todas las variables con sus
comandos de generación — y rellena los valores:

```bash
cp .env.example .env
```

Las críticas:

| Variable | Notas |
|---|---|
| `SECRET_KEY` | Obligatoria, la app no arranca sin ella |
| `DATABASE_URL` | Driver psycopg v3: `postgresql+psycopg://...` |
| `TOTP_ENCRYPTION_KEY` | Clave Fernet para cifrar secretos 2FA en BD |
| `REDIS_URL` | Rate-limit, lockout, anti-replay TOTP y message queue de Socket.IO |
| `CORS_ORIGINS` | Dev: Vite (5173). Prod: dominios Vercel/custom |
| `RT_COOKIE_SAMESITE` | `Lax` same-origin (dev); `None` cross-origin (prod) |
| `SOCKETIO_ASYNC_MODE` | `threading` en dev (default); `gevent` SOLO en prod — gevent crashea con psycopg en Windows local |
| `USE_X_ACCEL_REDIRECT` | `true` solo en prod con nginx configurado |
| `HSTS_PRELOAD` | `false` salvo que estés seguro (semi-irreversible) |

> Crea la BD `nominas` en Postgres antes de migrar.

### Migrar e iniciar

```bash
flask db upgrade
python run.py            # http://localhost:5000
```

---

## Estructura del Repo

```text
Sistema de nominas/
├── run.py                  # Entry point; monkey-patch de gevent si SOCKETIO_ASYNC_MODE=gevent
├── requirements.txt
├── nginx.config            # Config de Nginx
├── gunicorn.serviceee      # Unit de systemd (se instala como nominas.service)
├── app/
│   ├── __init__.py         # create_app(): CORS, Talisman, Limiter, blueprints, Socket.IO
│   ├── extensions.py       # db, limiter, mail; IP real tras Cloudflare; EncryptedString
│   ├── realtime.py         # Socket.IO: handlers, hooks ORM, emit_to_*
│   ├── models/             # SQLAlchemy, particionado por dominio
│   ├── routes/             # 17 blueprints api_*, cada uno como sub-paquete con _core.py
│   └── utils/              # seguridad, archivos, imágenes, horas, payroll
├── templates/              # Jinja SOLO para PDFs (xhtml2pdf)
├── migrations/             # Alembic
├── uploads/                # Fotos y documentos (writeable en prod)
├── scripts/                # Generadores de inventario de docs y utilidades
├── docs/                   # Documentación (ver Referencias)
└── tests/
```

Detalle completo en [`docs/ARQUITECTURA.md`](./docs/ARQUITECTURA.md).

---

## Producción

Cadena: **Cloudflare Tunnel → Nginx (127.0.0.1:80) → Gunicorn (127.0.0.1:8000) → Flask**.

### Gunicorn

El unit instalado en prod se llama **`nominas.service`** (el archivo del repo
es `gunicorn.serviceee`):

```bash
sudo cp gunicorn.serviceee /etc/systemd/system/nominas.service
sudo systemctl daemon-reload
sudo systemctl enable --now nominas
sudo systemctl status nominas
```

Resumen del unit (ver archivo para comentarios completos):

- **4 workers gevent (`GeventWebSocketWorker`) × 1000 conexiones**: WebSocket real (upgrade HTTP→WS). gthread no implementa el upgrade y eventlet es incompatible con psycopg3 (ver `docs/MIGRACION_EVENTLET_A_GEVENT.md` y `docs/DEPLOY_GEVENT.md`).
- **`SOCKETIO_ASYNC_MODE=gevent`** en el unit: activa el `monkey.patch_all()` al inicio de `run.py` y el modo gevent de Flask-SocketIO.
- **`--max-requests 1000` + jitter**: recicla workers para evitar leaks de pandas/openpyxl/xhtml2pdf.
- **`--forwarded-allow-ips=127.0.0.1`**: solo confía en `X-Forwarded-*` desde nginx local — cierra spoofing de `CF-Connecting-IP`.
- **`--access-logformat`** custom: imprime `X-Real-IP` (no `127.0.0.1`) + tiempo de request.
- **Hardening systemd**: `ProtectSystem=strict`, `NoNewPrivileges`, `CapabilityBoundingSet=` (drop all), `MemoryDenyWriteExecute=yes` (si gunicorn no arranca tras tocar gevent, ver el comentario de esa línea en el unit).

### Nginx

```bash
sudo cp nginx.config /etc/nginx/sites-available/skilled
sudo ln -s /etc/nginx/sites-available/skilled /etc/nginx/sites-enabled/skilled
sudo nginx -t && sudo systemctl reload nginx
```

Cubre:

- Rate-limit por IP (`api_general` 30/s, `api_auth` 30/min) como segunda capa.
- Real IP de Cloudflare (`set_real_ip_from` + `real_ip_header CF-Connecting-IP`).
- Anti-Slowloris (timeouts agresivos), anti HTTP request smuggling (`Connection ""`).
- Bloque dedicado para Socket.IO (`/socket.io/`) con `Upgrade`/`Connection` y `proxy_read_timeout 3600s`.
- Whitelist de métodos HTTP + bloqueo de paths comúnmente escaneados (`.env`, `wp-login.php`, etc.).
- Logs con upstream y tiempos en `/var/log/nginx/skilled_api.access.log`.
- Headers de seguridad delegados a Flask (única fuente de verdad).

> **CORS se maneja en Flask** (`flask_cors` + `CORS_ORIGINS`). No duplicar `Access-Control-*` en nginx.

### Frontend en Vercel

1. Importar el repo `plantilla-frontend/` en Vercel (Vite auto-detect via `vercel.json`).
2. Env vars: `VITE_API_URL=https://api.tu-dominio.com/api`.
3. Push → deploy automático.

Backend `.env` debe tener:

```env
CORS_ORIGINS=https://<proyecto>.vercel.app,https://app.skilled.com.mx
RT_COOKIE_SAMESITE=None
FLASK_ENV=production
USE_X_ACCEL_REDIRECT=true
```

### Checklist post-deploy

```bash
curl https://api.tu-dominio.com/health                          # {"status":"ok"}
sudo journalctl -u nominas -n 20 --no-pager                     # IPs reales, no 127.0.0.1
sudo tail -n 20 /var/log/nginx/skilled_api.access.log           # upstream=127.0.0.1:8000 request_time=...
```

> **Cloudflare Tunnel**: usar `protocol: http2` en `/etc/cloudflared/config.yml` —
> con QUIC los WebSockets de Socket.IO se degradan (ver `docs/WEBSOCKETS_Y_DEPLOY.md`).

Login end-to-end desde el dominio Vercel: DevTools → Network → la respuesta de `/api/auth/login` debe incluir `Set-Cookie: skilled_rt=...; SameSite=None; Secure`.

---

## Módulo de Inventario

Backend Flask bajo `/api/v1/`: paquetes `app/routes/inventario_api/` (materiales) y `app/routes/herramientas_api/` (herramientas). Frontend en `plantilla-frontend/src/pages/inventario/`. Referencia completa de endpoints en [`docs/API_REFERENCE.md`](./docs/API_REFERENCE.md).

### Endpoints clave

- **Productos**: CRUD + `GET /productos/bajo-minimo/`, `GET /productos/plantilla-importar`, `POST /productos/importar`.
- **Almacenes / estantes**: CRUD + validación por QR (`GET /almacenes/<qr>/validar`), generación PNG del QR (`GET /estantes/<id>/qr-image`).
- **Movimientos**: `POST /movimientos/` con `with_for_update` para evitar over-selling concurrente; rate-limit 20/min por IP.
- **Solicitudes**: `PATCH /solicitudes/<id>/estado` mueve por el flujo PENDIENTE → APROBADA / RECHAZADA / ENTREGADA.
- **Categorías**: `categorias_config` persiste metadatos visuales (imagen, etc.) por categoría, aunque aún no tenga productos.

### Roles

| Rol | Acceso |
|---|---|
| `super_admin` | Todo. Único que administra otros admins. |
| `admin` | Todo excepto crear/eliminar otros admins. |
| `inventario` | Módulo de inventario completo (sin admin de usuarios). |
| `coordinador` | Solo `/horas` y campos médicos/contacto de sus trabajadores. |
| `solicitante_material` | Solo `/inventario/mis-pedidos`. |

---

## Sistema de Notificaciones

Panel en sidebar para `admin` / `super_admin`. Push en tiempo real vía Socket.IO (evento `notif:new`, emitido post-commit por hook ORM), con polling como fallback.

Tipos:

| Tipo | Origen |
|---|---|
| `REPORTE_CERRADO` | Al cerrar un reporte de horas |
| `PRENOMINA_CERRADA` | Al aprobar/cerrar una prenómina |
| `ACTUALIZACION` | Entradas nuevas en el `CHANGELOG` de `app/routes/api_notificaciones/_core.py` |

**Limpieza automática**: notificaciones leídas se eliminan a los 30 días en cada `GET /resumen` (sin cron externo). Edita `DIAS_EXPIRACION` para cambiar.

Las tablas `notificaciones`, `totp_backup_codes` y `trabajador_notas` se autocrean en el arranque (`inspect().has_table()`); no requieren `flask db upgrade`.

---

## Seguridad

Estado actual tras la auditoría del 2026-05-23 (4 críticas + 7 altas cerradas en código). Ver [`docs/SEGURIDAD.md`](./docs/SEGURIDAD.md) para el detalle completo.

### Autenticación

- **JWT** con `iss=skilled-erp-api`, `aud=skilled-erp-spa`. Access token corto + refresh token rotativo en cookie httpOnly.
- **Refresh proactivo en el SPA**: el frontend renueva el access token 60 s antes de expirar; al volver foco a una pestaña inactiva se re-evalúa (ver `plantilla-frontend/src/api/axios.js`).
- **CSRF**: `/api/auth/refresh` y `/api/auth/logout` exigen `X-Requested-With: XMLHttpRequest` (header custom fuerza preflight, bloquea POST cross-site desde `<form>`).
- **Lockout escalado** por IP/user en Redis. **Anti-replay TOTP** (90 s en Redis).
- **2FA**: `totp_secret` cifrado con Fernet (`TOTP_ENCRYPTION_KEY`).
- **Password rotation**: cambio de password invalida JWTs en uso vía `password_version`.

### Autorización

- **Whitelist de campos por rol** en `PUT /api/trabajadores/<id>` (coord solo edita médicos/contacto; admin edita financieros/PII fiscal — campos prohibidos se ignoran y vuelven en `warnings`).
- **`/api/dashboard`**: solo admin/super_admin (filtra PII).
- **`/api/auth/users`**: oculta `role`, `totp_enabled`, `last_seen` a roles no-admin.
- **Solo `super_admin`** crea/elimina otros admins. Cambio de password de un admin **no resetea** su 2FA.

### Inputs

- Documentos de trabajador: solo PDF / JPG / PNG / HEIC, ≤ 20 MB, validación de magic bytes (no extensión).
- `imagen_url` solo acepta `https://` o paths `/static/...` locales (bloquea `javascript:`, `data:`, `http://`, `file:///`).
- Prenómina valida `tipo` enum, `concepto` 1-250 chars, `monto` ≤ $999,999.99, `fecha_incidencia` no futura.

### Infraestructura

- Rate-limit Nginx (`api_general` 30/s, `api_auth` 30/min) + Flask-Limiter (por user/IP en Redis).
- Gunicorn `--forwarded-allow-ips=127.0.0.1` cierra spoofing de `CF-Connecting-IP`.
- `.env` con `chown root:sistemanominas` + `chmod 640` (no legible por otros users del host).
- Hardening systemd completo en `gunicorn.serviceee` / `nominas.service` (ProtectKernel*, RestrictAddressFamilies, CapabilityBoundingSet vacío, MemoryDenyWriteExecute).

### Pendientes operativos (no automatizables desde el repo)

1. **Rotar credenciales**:
   - Postgres: `ALTER USER ... WITH PASSWORD '...'` → editar `DATABASE_URL`.
   - Gmail App Password: https://myaccount.google.com/apppasswords.
   - Si `.env` se commiteó alguna vez: `git filter-repo --invert-paths --path .env --force && git push --force --all`.
2. **Firewall del origin server** (ufw): `deny 8000` desde cualquier origen no-localhost — evita bypass de Cloudflare si filtran la IP real.
3. **DNS limpio**: `dig api.skilled.com.mx +short` debe devolver solo IPs de Cloudflare.
4. **Cloudflare WAF**: Bot Fight Mode + rate-limit en `/api/auth/login` (5/min/IP).

### Higiene continua

| Tarea | Frecuencia | Comando |
|---|---|---|
| `pip-audit` | mensual | `pip-audit -r requirements.txt` |
| `npm audit` | mensual | `cd plantilla-frontend && npm audit --production` |
| Revisar bitácora | semanal | UI `/bitacora` o `SELECT * FROM audit_log WHERE action ILIKE '%fallido%' ORDER BY created_at DESC LIMIT 50;` |
| Backup BD + uploads | diario | `pg_dump nominas \| gzip > backup_$(date +%F).sql.gz` + `tar -czf uploads_$(date +%F).tgz uploads/` |
| Rotar `SECRET_KEY` | semestral | `python -c "import secrets; print(secrets.token_urlsafe(64))"` + restart |
| Re-pentest externo | anual | — |

---

## Comandos Útiles

```bash
# Migraciones
flask db migrate -m "descripción"
flask db upgrade
flask db current

# Tests (suite completa en verde; correrla tras cualquier cambio)
pytest tests/

# Regenerar inventarios para la documentación (rutas y modelos)
python scripts/gen_api_inventory.py
python scripts/gen_models_inventory.py
```

> Las plantillas Excel (empleados y productos) ya no se generan con un script:
> se descargan de `GET /api/trabajadores/plantilla-importar` y
> `GET /api/v1/productos/plantilla-importar`.

---

## Referencias

- Archivos de despliegue: [`nginx.config`](./nginx.config), [`gunicorn.serviceee`](./gunicorn.serviceee) (se instala como `nominas.service`)
- Seguridad detallada: [`docs/SEGURIDAD.md`](./docs/SEGURIDAD.md)
- Frontend: repo `plantilla-frontend/` (deploy en Vercel)
- Docs adicionales en [`docs/`](./docs/):
  - `ARQUITECTURA.md` — mapa del código: factory, blueprints, realtime, utils
  - `API_REFERENCE.md` — referencia completa de los 216 endpoints
  - `MODELOS_BD.md` — todas las tablas, columnas y relaciones
  - `WEBSOCKETS_Y_DEPLOY.md` — setup de Socket.IO y consideraciones (incluye fix de Cloudflare QUIC)
  - `DEPLOY_GEVENT.md` — despliegue con gevent + GeventWebSocketWorker
  - `MIGRACION_EVENTLET_A_GEVENT.md` — por qué gevent y no eventlet
  - `MIGRACION_USERESOURCE_REALTIME.md` — migración del módulo realtime
  - `FIX_WEBSOCKET_TOKEN_EXPIRADO.md` / `FIX_WEBSOCKET_RECONEXION_MATUTINA.md` — fixes de reconexión WS
  - `MANUAL_USO_PRENOMINA_PRESTAMOS_INBURSA.md` — manual funcional de prenómina/préstamos/ajustes
  - `REFACTOR_ARCHIVOS_GRANDES.md` — plan/estado del refactor a sub-paquetes
  - `NOTAS_TRABAJADOR.md` — notas internas ("chatter") en la ficha del trabajador
  - `ORDENAMIENTO_LISTADOS.md` — orden por columna (`sort`/`dir`) en trabajadores y préstamos

---

> _Desarrollado para mantener la contabilidad organizada, veloz e inquebrantable._
