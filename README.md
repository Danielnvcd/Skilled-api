# Sistema de Nóminas — Backend (Skilled ERP)

API JSON en **Flask** que sirve al SPA Skilled ERP (React) para nóminas, empleados, inventario y reportes. Despliegue **API-only**: este repo expone `/api/*`; el frontend React vive en el repo `plantilla-frontend/` y se hostea en Vercel.

---

## Stack

- **API**: Flask + SQLAlchemy + Flask-JWT-Extended + Flask-Limiter + Flask-Talisman
- **DB**: PostgreSQL (psycopg v3)
- **Cache / Rate-limit / Anti-replay TOTP**: Redis
- **Realtime**: Socket.IO (`async_mode=threading`, compatible con gthread)
- **Documentos**: pandas + openpyxl (Excel), xhtml2pdf (PDF)
- **Despliegue**: Gunicorn (gthread, 4 workers × 6 threads) detrás de Nginx → Cloudflare Tunnel

---

## Características Principales

- **Empleados**: alta, baja, edición; campos laborales, personales, médicos y financieros con whitelist por rol.
- **Carga masiva**: import/export de plantillas `.xlsx`.
- **Reportes**: PDF (recibos, constancias) y Excel (totales por proyecto, histórico).
- **Inventario**: productos, almacenes y estantes con QR; movimientos con lock anti-concurrencia; flujo de solicitudes `PENDIENTE → APROBADA/RECHAZADA/ENTREGADA`; PDF de solicitudes.
- **Realtime**: notificaciones in-app vía Socket.IO; expiración automática a 30 días de las leídas.
- **Seguridad**: rate-limit a dos niveles (Nginx + Flask-Limiter), JWT con `iss`/`aud`, refresh-token rotation en cookie, lockout escalado, anti-replay TOTP en Redis, validación de magic bytes en uploads.

---

## Requisitos

- Python 3.9+
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

```env
FLASK_APP=run.py
FLASK_ENV=development

# Generar con: python -c "import secrets; print(secrets.token_hex(64))"
SECRET_KEY=<32+ hex>

# Driver psycopg v3
DATABASE_URL=postgresql+psycopg://user:pass@localhost:5432/nominas

REDIS_URL=redis://localhost:6379/0

# Recuperación de password, etc.
MAIL_USERNAME=tu_correo@gmail.com
MAIL_PASSWORD=<gmail app password>

# Orígenes del SPA. En dev: Vite (5173). En prod: dominio Vercel + custom.
CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173

# Same-origin en dev → Lax. Cross-origin en prod (Vercel + API) → None + Secure.
RT_COOKIE_SAMESITE=Lax

# Cifrado de TOTP secrets. Generar:
# python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
TOTP_ENCRYPTION_KEY=<fernet key>

# Solo true en prod con nginx + X-Accel-Redirect habilitado
USE_X_ACCEL_REDIRECT=false
```

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
├── run.py                  # Entry point (dev y referenciado por Gunicorn)
├── requirements.txt
├── create_template.py      # Genera plantilla Excel de empleados
├── nginx.config            # Config de Nginx
├── gunicorn.service        # Unit de systemd
├── app/
│   ├── __init__.py         # Factory: Flask, CORS, JWT, Talisman, Limiter, Socket.IO
│   ├── models/             # SQLAlchemy
│   ├── routes/             # Blueprints (api_*, auth, inventario_api, etc.)
│   └── realtime.py         # Eventos Socket.IO
├── migrations/             # Alembic
├── uploads/                # Fotos y documentos (writeable en prod)
└── tests/
```

---

## Producción

Cadena: **Cloudflare Tunnel → Nginx (127.0.0.1:80) → Gunicorn (127.0.0.1:8000) → Flask**.

### Gunicorn

```bash
sudo cp gunicorn.service /etc/systemd/system/gunicorn.service
sudo systemctl daemon-reload
sudo systemctl enable --now gunicorn
sudo systemctl status gunicorn
```

Resumen del unit (ver archivo para comentarios completos):

- **4 workers × 6 threads (gthread)** → ~24 conexiones simultáneas. Eventlet incompatible con psycopg3 (ver `docs/MIGRACION_EVENTLET_A_GEVENT.md`).
- **`--max-requests 1000` + jitter**: recicla workers para evitar leaks de pandas/openpyxl/xhtml2pdf.
- **`--forwarded-allow-ips=127.0.0.1`**: solo confía en `X-Forwarded-*` desde nginx local — cierra spoofing de `CF-Connecting-IP`.
- **`--access-logformat`** custom: imprime `X-Real-IP` (no `127.0.0.1`) + tiempo de request.
- **Hardening systemd**: `ProtectSystem=strict`, `NoNewPrivileges`, `CapabilityBoundingSet=` (drop all), `MemoryDenyWriteExecute=yes`.

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
sudo journalctl -u gunicorn -n 20 --no-pager                    # IPs reales, no 127.0.0.1
sudo tail -n 20 /var/log/nginx/skilled_api.access.log           # upstream=127.0.0.1:8000 request_time=...
```

Login end-to-end desde el dominio Vercel: DevTools → Network → la respuesta de `/api/auth/login` debe incluir `Set-Cookie: skilled_rt=...; SameSite=None; Secure`.

---

## Módulo de Inventario

Backend Flask bajo `/api/v1/` (`app/routes/inventario_api.py`). Frontend en `plantilla-frontend/src/pages/inventario/`.

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

Panel en sidebar para `admin` / `super_admin`. Polling cada 45 s (o push vía Socket.IO).

Tipos:

| Tipo | Origen |
|---|---|
| `REPORTE_CERRADO` | Al cerrar un reporte de horas |
| `PRENOMINA_CERRADA` | Al aprobar/cerrar una prenómina |
| `ACTUALIZACION` | Entradas nuevas en el `CHANGELOG` de `app/routes/notificaciones.py` |

**Limpieza automática**: notificaciones leídas se eliminan a los 30 días en cada `GET /resumen` (sin cron externo). Edita `DIAS_EXPIRACION` para cambiar.

La tabla `notificaciones` se autocrea en el arranque (`inspect().has_table()`); no requiere `flask db upgrade`.

---

## Seguridad

Estado actual tras la auditoría del 2026-05-23 (4 críticas + 7 altas cerradas en código). Ver [`SEGURIDAD.md`](./SEGURIDAD.md) para el detalle completo.

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
- Hardening systemd completo en `gunicorn.service` (ProtectKernel*, RestrictAddressFamilies, CapabilityBoundingSet vacío, MemoryDenyWriteExecute).

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
# Recrear plantilla Excel de empleados
python create_template.py

# Migraciones
flask db migrate -m "descripción"
flask db upgrade
flask db current

# Tests (algunos legacy — ver memoria del proyecto antes de correrlos)
pytest tests/
```

---

## Referencias

- Archivos de despliegue: [`nginx.config`](./nginx.config), [`gunicorn.service`](./gunicorn.service)
- Seguridad detallada: [`SEGURIDAD.md`](./SEGURIDAD.md)
- Frontend: repo `plantilla-frontend/` (deploy en Vercel)
- Docs adicionales en [`docs/`](./docs/):
  - `WEBSOCKETS_Y_DEPLOY.md` — setup de Socket.IO y consideraciones
  - `MIGRACION_EVENTLET_A_GEVENT.md` — por qué gthread y no eventlet
  - `MIGRACION_USERESOURCE_REALTIME.md` — migración del módulo realtime

---

> _Desarrollado para mantener la contabilidad organizada, veloz e inquebrantable._
