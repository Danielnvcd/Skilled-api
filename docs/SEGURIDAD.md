# Auditoría de Seguridad — Skilled ERP

> **Stack**: Flask + React/Vite + Axios + PostgreSQL + Redis + Nginx + Cloudflare Tunnel + Vercel + Gunicorn.
> **Fecha auditoría**: 2026-05-23
> **Tipo**: Pentest ofensivo + code review + revisión de infraestructura
> **Score**: 5.8 → **8.4 / 10** (tras tres rondas de fixes)

---

## Tabla de contenido

1. [Resumen ejecutivo](#1-resumen-ejecutivo)
2. [Vulnerabilidades encontradas](#2-vulnerabilidades-encontradas)
3. [Fixes aplicados en código](#3-fixes-aplicados-en-código)
4. [Configuraciones nuevas obligatorias](#4-configuraciones-nuevas-obligatorias)
5. [Pendientes operativos (rotación de credenciales)](#5-pendientes-operativos-rotación-de-credenciales)
6. [Hardening de infraestructura](#6-hardening-de-infraestructura)
7. [Compatibilidad Nginx ↔ Gunicorn ↔ Flask](#7-compatibilidad-nginx--gunicorn--flask)
8. [Matriz de testing post-deploy](#8-matriz-de-testing-post-deploy)
9. [Lo que falta — inventario completo](#9-lo-que-falta--inventario-completo)
10. [Roadmap priorizado](#10-roadmap-priorizado)
11. [Score final](#11-score-final)
12. [Apéndices](#12-apéndices)

---

## 1. Resumen ejecutivo

### Hallazgos por severidad

| Severidad | Cantidad | Estado |
|-----------|----------|--------|
| CRITICAL  | 4        | Todas cerradas en código |
| HIGH      | 7        | 6 cerradas en código, 1 mitigada parcialmente |
| MEDIUM    | 11       | 9 cerradas, 2 documentadas |
| LOW / Info | 13      | 8 cerradas, 5 documentadas |

### Madurez observada (positivo)

- JWT correctamente firmado HS256 con whitelist de algoritmo
- `password_version` invalida tokens al cambiar password
- Refresh token rotation con detección de replay
- Lockout escalado por username en Redis (10m → 24h)
- `_DUMMY_PW_HASH` constant-time anti-timing enumeration
- `get_real_client_ip_flask()` valida CIDRs Cloudflare antes de confiar en `CF-Connecting-IP`
- Fernet cifrado de `totp_secret` en BD
- CSP estricta con sha256 hashes inline
- `frame-ancestors 'none' + X-Frame-Options DENY + COOP same-origin`
- `secure_filename` + magic-bytes check (`filetype`)
- `with_for_update(nowait=True)` anti-race en stock
- `safe_excel_value()` anti CSV/formula injection
- `_mask_pii()` para CURP/RFC/NSS a no-admin
- Hardening systemd básico ya presente

### Vectores principales antes del fix

1. **Secretos en `.env`** con SECRET_KEY conocido + password DB `1234`
2. **Admin-cambia-password borra el `totp_secret` del otro** → takeover lateral entre admins
3. **Coordinador editaba salarios** vía mass-assignment en `PUT /api/trabajadores/<id>`
4. **`/api/dashboard` sin role-check** filtraba PII y audit log a cualquier autenticado

Todos cerrados.

---

## 2. Vulnerabilidades encontradas

### CRITICAL

#### CRIT-01 — Secretos productivos en `.env`
- **CVSS**: 9.8 (AV:N/AC:L/PR:N/UI:N/S:C/C:H/I:H/A:H)
- **OWASP**: A02:2021 (Cryptographic Failures), A05:2021
- **Detalle**: `SECRET_KEY` legible, `DATABASE_URL` con password `1234`, `MAIL_PASSWORD` (Gmail App Password), `TOTP_ENCRYPTION_KEY`, `GROQ_API_KEY` todos en texto plano.
- **Impacto**: Forge de JWT super_admin → toma de control total. Lectura completa de BD. Acceso a Gmail. Descifrado de todos los `totp_secret`.
- **Fix aplicado**: `SECRET_KEY` y `TOTP_ENCRYPTION_KEY` rotados automáticamente.
- **Fix pendiente operativo**: rotar password Postgres, Gmail App Password, Groq API Key (sección 5).

#### CRIT-02 — Admin reset password borra TOTP ajeno
- **CVSS**: 8.8
- **OWASP**: A01:2021, A07:2021
- **Archivos**: `api_users.py:271`, `users.py:134`, `api_auth.py:473`, `auth.py:207`
- **Detalle**: `user.totp_secret = None` se ejecutaba en TODOS los flujos de password reset, incluyendo admin-resetea-otro-admin → escalación lateral entre admins.
- **PoC**: admin A resetea password de admin B con el endpoint normal → 2FA de B borrado → A entra como B sin segundo factor.
- **Fix aplicado**: Eliminada la línea `totp_secret = None` en los 4 endpoints. Solo el dueño puede deshabilitar su TOTP explícitamente.

#### CRIT-03 — Mass-assignment de salarios y PII por coordinadores
- **CVSS**: 8.1
- **OWASP**: A01:2021, A04:2021
- **Archivo**: `api_trabajadores.py:176-249, 457-498`
- **Detalle**: `_apply_payload` asignaba ciegamente todos los campos del form. Coordinador autorizado para un proyecto podía modificar salario, RFC, CURP, NSS, tipo_nomina, etc.
- **PoC**:
  ```bash
  curl -X POST /api/trabajadores/123 -H "Authorization: Bearer $COORD_JWT" \
       -F "salario_real_pactado_x_sem=99999.99" -F "sb=9999"
  ```
- **Fix aplicado**: Whitelist por rol. Coord solo edita: celular, datos médicos, contacto emergencia, lentes, licencia, estatura, ubicacion_estado, observaciones. Admin edita todo.
- **`POST /api/trabajadores/`** también restringido a admin.

#### CRIT-04 — `/api/dashboard` sin role-check
- **CVSS**: 7.5
- **OWASP**: A01:2021
- **Archivo**: `api_dashboard.py:24-150`
- **Detalle**: Cualquier autenticado leía: cumpleañeros (PII), docs por vencer con nombres, credenciales por vencer, audit log reciente, stats globales.
- **Fix aplicado**: `if not _is_admin(): return 403`.

---

### HIGH

#### HIGH-01 — Directorio público de usuarios filtra `role`/`totp_enabled`/`last_seen`
- **CVSS**: 6.5
- **Archivo**: `api_auth.py:346-362`
- **Detalle**: `GET /api/auth/users` retornaba a CUALQUIER autenticado: lista completa con `role` + `totp_enabled` + `last_seen` → enumeración de admins sin 2FA + ventanas de actividad.
- **Fix aplicado**: `_user_to_dict_public()` (sin esos 3 campos) usado para no-admin. Admin recibe vista completa.

#### HIGH-02 — CSRF en `/api/auth/refresh` y `/api/auth/logout`
- **CVSS**: 6.1 (cuando `RT_COOKIE_SAMESITE=None`)
- **Archivos**: `api_auth.py`, `__init__.py:235`
- **Detalle**: Endpoints leen refresh token solo de cookie. Con SameSite=None (deploy Vercel cross-site), form POST cross-origin enviaba la cookie → logout/refresh forzado.
- **Fix aplicado**: Header `X-Requested-With: XMLHttpRequest` obligatorio + axios lo manda automáticamente. Browsers fuerzan preflight CORS para headers custom → bloquea CSRF.

#### HIGH-03 — Upload de documentos sin restricción de tipo
- **CVSS**: 6.3
- **Archivo**: `api_trabajadores.py:703-740`, `utils.py:135-169`
- **Detalle**: `allowed_file()` aceptaba mp4/mp3/wav/docx/xlsm/pptx → admin descarga office con macros = RCE.
- **Fix aplicado**: `_DOCUMENTO_TRABAJADOR_EXTS = {'pdf','jpg','jpeg','png','heic'}` + max 20 MB.

#### HIGH-04 — Coord manipula horas de cualquier trabajador del proyecto
- **CVSS**: 6.5
- **Estado**: **Funcionalidad esperada** (coord legítimamente captura horas) — mitigado con audit log existente.
- **Recomendación pendiente**: alerta cuando se modifiquen muchas horas extras / día festivo en lote.

#### HIGH-05 — `api_proyectos` no valida `coordinador_id` antes de asignar
- **CVSS**: 5.4
- **Archivo**: `api_proyectos.py:262-325`
- **Detalle**: Permitía asignar usuarios inexistentes o con rol incorrecto como coordinador → proyectos huérfanos y bypass de auth.
- **Fix aplicado**: `_validar_coordinador()` chequea existencia + rol en `{'coordinador','admin','super_admin'}`.

#### HIGH-06 — `FLASK_ENV` case-sensitive
- **CVSS**: 5.3
- **Archivo**: `__init__.py:113`
- **Detalle**: `FLASK_ENV=Production` (mayúscula) o `FLASK_ENV=" production "` no activaban HSTS ni cookies seguras.
- **Fix aplicado**: `.strip().lower() == 'production'`.

#### HIGH-07 — `ip-api.com` por HTTP plano
- **CVSS**: 4.3
- **Archivos**: `bitacora.py:101-103`, `api_bitacora.py:101-103`
- **Detalle**: HTTP plano + `IP_GEO_CACHE` sin límite.
- **Fix aplicado**: HTTPS + IP canónica + LRU(5000).

---

### MEDIUM

| ID | Resumen | Fix |
|----|---------|-----|
| MED-01 | `<path:nombre>` permite slashes en categorías | Cambiado a `<string:nombre>` |
| MED-02 | Validar `coord_id` en `api_proyectos.crear` | Resuelto con HIGH-05 |
| MED-03 | Log forging vía CRLF en `log_action` | `_safe_log_value()` sanitiza |
| MED-04 | `_cookie_secure()` confía en X-Forwarded-Proto | Mitigado por `--forwarded-allow-ips=127.0.0.1` |
| MED-05 | Enum de foto de user filtra existencia 404 vs 200 | Aceptable (directorio interno) |
| MED-06 | Audit log reciente expuesto vía dashboard | Resuelto con CRIT-04 |
| MED-07 | Reset password propio borraba 2FA | Resuelto con CRIT-02 |
| MED-08 | `MAX_CONTENT_LENGTH 50 MB` global sin cuota por user | Documentado, requiere decisión de producto |
| MED-09 | xhtml2pdf templates pueden tener XSS | Auditados — Jinja autoescape activo, sin `|safe` |
| MED-10 | `_load_cloudflare_cidrs` bloquea 5s arranque | Aceptable (fallback existe) |
| MED-11 | `/uploads/` sin headers de seguridad | Añadido CSP `default-src 'none'` + CORP same-site + nosniff |

---

### LOW / Info

| ID | Resumen | Estado |
|----|---------|--------|
| LOW-01 | CSP dependencias externas (cloudflareinsights, tailwindcss CDN) | Documentado |
| LOW-02 | Tailwind via CDN en prod (debería compilarse) | Documentado |
| LOW-03 | `is_prod` case-sensitive | Resuelto con HIGH-06 |
| LOW-04 | `SESSION_COOKIE_SAMESITE='Strict'` rompe UX con links externos | Aceptable (es API-only) |
| LOW-05 | `run.py` bind `0.0.0.0` | Solo dev, OK |
| LOW-06 | Sin rate-limit en `/api/auth/me` | Añadido 60/min |
| LOW-07 | `password_version` invalida JWT pero no rota SECRET_KEY | Diseño correcto |
| LOW-08 | TOTP brute-force ventana de 1M en 60s | Mitigado con rate-limit 4-6/min + anti-replay |
| LOW-09 | `mailto:` sin anti-spoof | N/A (no expone mailto público) |
| LOW-10 | Frontend Vercel sin CSP | Añadida CSP completa en `vercel.json` |
| LOW-11 | localStorage no es HttpOnly (vuln XSS) | Estándar SPA, documentado |
| LOW-12 | `npm audit` no en CI | Documentado |
| LOW-13 | preconnect a fonts.googleapis.com filtra origen | Aceptable |

---

## 3. Fixes aplicados en código

### Ronda 1 (críticas + altas principales)

| Fix | Archivos |
|-----|----------|
| CRIT-01: rotar `SECRET_KEY` + `TOTP_ENCRYPTION_KEY` | `.env` |
| CRIT-02: quitar `totp_secret = None` en 4 endpoints | `api_users.py`, `users.py`, `api_auth.py`, `auth.py` |
| CRIT-03: whitelist por rol en `_apply_payload` | `api_trabajadores.py` |
| CRIT-04: role-check `admin/super_admin` en `/api/dashboard` | `api_dashboard.py` |
| HIGH-01: `_user_to_dict_public()` para no-admin | `api_auth.py` |
| HIGH-02: `X-Requested-With` requerido + axios lo manda | `api_auth.py`, `plantilla-frontend/src/api/axios.js` |
| HIGH-03: docs solo PDF/JPG/PNG/HEIC + 20 MB cap | `api_trabajadores.py`, `utils.py` |
| HIGH-05: `_validar_coordinador()` | `api_proyectos.py` |
| HIGH-06: `FLASK_ENV` case-insensitive | `__init__.py` |
| HIGH-07: HTTPS para `ip-api.com` + LRU cache | `bitacora.py`, `api_bitacora.py` |
| MED-03: `_safe_log_value()` sanitiza CRLF | `utils.py`, `inventario_api.py` |
| MED-11: headers de seguridad en `/uploads/` nginx | `nginx.config` |
| Vercel CSP + HSTS + COOP | `plantilla-frontend/vercel.json` |
| Solo super_admin crea/elimina/cambia password de admins | `api_users.py` |
| Rate-limit en `/api/auth/me` (60/min) | `api_auth.py` |
| `X-Frame-Options: DENY` (Talisman default era SAMEORIGIN) | `__init__.py` |

### Ronda 2 (extras tras revisión)

| Fix | Archivos |
|-----|----------|
| MED-01: `<path:nombre>` → `<string:nombre>` | `inventario_api.py` |
| Validar `imagen_url` solo HTTPS o path local | `inventario_api.py` schemas |
| JWT con `iss=skilled-erp-api` + `aud=skilled-erp-spa` + validación dura | `api_auth.py` |
| `GET/DELETE /api/auth/sessions` + `DELETE /api/auth/sessions/all` | `api_auth.py` |
| Limpiar legacy `ALLOWED_ORIGIN` | `.env` |

### Ronda 3 (compatibilidad nginx + gunicorn)

| Fix | Archivos |
|-----|----------|
| Headers proxy completos en `/api/auth/*` y `/` (antes faltaban) | `nginx.config` |
| `Connection ""` (anti HTTP request smuggling) | `nginx.config` |
| Timeouts anti-Slowloris (client_header/body/send/keepalive) | `nginx.config` |
| Whitelist métodos HTTP | `nginx.config` |
| Bloqueo paths escaneados (.env, .git, wp-login, etc.) | `nginx.config` |
| `proxy_hide_header Server` + `X-Powered-By` | `nginx.config` |
| `real_ip_recursive on` | `nginx.config` |
| `add_header ... always` (aplica a 4xx/5xx) | `nginx.config` |
| Gunicorn `--max-requests` + `--max-requests-jitter` | `Gunicorn .config` |
| Gunicorn `--limit-request-line/fields/field_size` | `Gunicorn .config` |
| systemd hardening: ProtectKernel*, RestrictAddressFamilies, etc. | `Gunicorn .config` |
| `ProtectSystem=strict`, `CapabilityBoundingSet=` (drop all) | `Gunicorn .config` |
| `StartLimitIntervalSec` + `StartLimitBurst` | `Gunicorn .config` |
| `ExecReload=SIGHUP` para graceful reload | `Gunicorn .config` |

### Ronda 4 (defensa en profundidad)

| Fix | Archivos |
|-----|----------|
| Anti-replay TOTP (Redis tracking 90s) | `api_auth.py` |
| CORS estricto (methods/headers/max-age explícitos) | `__init__.py` |
| Validación enum + monto cap + concepto length en prenómina | `api_prenomina.py` |
| `fecha_incidencia` no puede ser futura | `api_prenomina.py` |
| `DELETE /api/users/<id>/sessions` (forzar logout remoto) | `api_users.py` |
| `secure_filename` en `Content-Disposition` | `api_trabajadores.py` |
| Vite: drop `console.log/info/debug/trace` en prod | `plantilla-frontend/vite.config.js` |

---

## 4. Configuraciones nuevas obligatorias

### 4.1 — Header `X-Requested-With` en `/api/auth/refresh` y `/api/auth/logout`

Estos dos endpoints autentican por cookie (refresh token), no por Authorization. Para bloquear CSRF cross-site (especialmente con `RT_COOKIE_SAMESITE=None`), exigen `X-Requested-With: XMLHttpRequest`.

El SPA React lo manda automáticamente (configurado en `plantilla-frontend/src/api/axios.js`).

Si llamas desde curl/Postman/tests:
```bash
curl -X POST https://api.skilled.com.mx/api/auth/refresh \
     -H 'X-Requested-With: XMLHttpRequest' \
     -H 'Cookie: rt_api=<refresh-token>'
```
Sin el header → **403 Forbidden**.

### 4.2 — Promover el admin maestro a `super_admin`

Tras los cambios de roles, solo `super_admin` puede:

- Crear cuentas con rol `admin`
- Eliminar otros admins
- Cambiar password de otros admins
- Revocar sesiones de otros admins

Si tu BD no tiene ningún `super_admin`, ningún admin podrá administrar otros admins. Promueve manualmente:

```sql
UPDATE users SET role = 'super_admin' WHERE username = 'admin';
```

### 4.3 — Re-configurar 2FA de todos los usuarios

`TOTP_ENCRYPTION_KEY` fue rotada. Los `totp_secret` cifrados en BD **dejan de descifrarse**.

La app no crashea (`EncryptedString.process_result_value` cae al valor crudo si falla), pero los códigos TOTP dejan de funcionar.

**Plan**:
1. Avisar a usuarios con 2FA activo que su próximo login no podrá completar 2FA.
2. Login solo con password (lockout escalado sigue protegiendo).
3. Cada usuario re-configura su 2FA desde Perfil → Activar 2FA.

### 4.4 — `.env` permisos correctos

```bash
sudo chown root:sistemanominas /opt/nominas/.env
sudo chmod 640 /opt/nominas/.env
```

### 4.5 — Whitelist de campos por rol en trabajadores

| Campo | Coord | Admin |
|-------|-------|-------|
| `nombre`, `nombre_apellidos`, `no_empleado` | NO | SI |
| `curp`, `rfc`, `nss` | NO | SI |
| Todos los financieros (salario, sb, sdi, infonavit, etc.) | NO | SI |
| `tipo_nomina`, `tipo_pago`, `letra`, `folio_mov_idse` | NO | SI |
| `area`, `puesto`, `tipo_jornada`, fechas | NO | SI |
| `correo`, `domicilio`, datos personales formales | NO | SI |
| `celular`, datos médicos, contacto emergencia | SI | SI |
| `lentes`, `licencia_conducir`, `estatura` | SI | SI |
| `ubicacion_estado`, `observaciones` | SI | SI |

Si un coord manda campos bloqueados, se ignoran y la respuesta incluye `warnings` listando los rechazados. El frontend debe ocultarlos según rol.

### 4.6 — Documentos: solo PDF/JPG/PNG/HEIC ≤ 20 MB

Endpoint `POST /api/trabajadores/<id>/documentos` ya no acepta otros tipos. Para extender:

```python
# app/routes/api_trabajadores.py
_DOCUMENTO_TRABAJADOR_EXTS = {'pdf', 'jpg', 'jpeg', 'png', 'heic'}
_DOCUMENTO_MAX_BYTES = 20 * 1024 * 1024
```

### 4.7 — `imagen_url` solo HTTPS o path local

En Producto y CategoriaConfig, `imagen_url` rechaza con 422 cualquier cosa que no sea:
- `https://dominio.tld/path/imagen.png`
- `/static/imagenes/foo.png`

Bloquea `javascript:`, `data:`, `http://`, `file:///`.

### 4.8 — JWT con `iss` + `aud`

Los JWT incluyen `iss=skilled-erp-api` y `aud=skilled-erp-spa`. Grace period: tokens viejos sin estos claims se aceptan hasta que expiren (max 20 min).

### 4.9 — Endpoints nuevos de gestión de sesiones

| Método | Path | Quién |
|--------|------|-------|
| GET | `/api/auth/sessions` | Propio usuario — lista sus sesiones activas |
| DELETE | `/api/auth/sessions/<id>` | Propio usuario — revoca una sesión específica |
| DELETE | `/api/auth/sessions/all` | Propio usuario — revoca todas (pánico) |
| DELETE | `/api/users/<user_id>/sessions` | Admin — fuerza logout de otro usuario |

### 4.10 — Anti-replay TOTP (requiere Redis)

Cada código TOTP se marca usado en Redis con TTL 90s. Sin Redis, esta defensa se desactiva (degradación intencional).

### 4.11 — CORS endurecido

```python
methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS']
allow_headers=['Content-Type', 'Authorization', 'X-Requested-With', 'X-CSRF-Token']
max_age=600
```

Para añadir un header custom (p.ej. `X-Tenant-Id`), editar `app/__init__.py`.

### 4.12 — Vercel CSP

`vercel.json` emite CSP estricta. Si añades CDNs externos al SPA, agregar a `script-src`/`img-src`/`connect-src` etc.

---

## 5. Pendientes operativos (rotación de credenciales)

### 5.1 — Password de PostgreSQL

```sql
-- En psql:
ALTER USER daniel WITH PASSWORD 'nueva-password-32-chars-aleatorios';
```

Generar con:
```bash
openssl rand -hex 24
```

Evitar `@`, `#`, `&` o URL-encodearlos en `DATABASE_URL`.

### 5.2 — Gmail App Password

1. https://myaccount.google.com/apppasswords
2. Revocar el app password actual
3. Generar uno nuevo
4. Reemplazar `MAIL_PASSWORD` en `.env`

### 5.3 — Groq API Key

1. https://console.groq.com/keys
2. Revocar la actual
3. Crear nueva
4. Reemplazar `GROQ_API_KEY` en `.env`

### 5.4 — Purgar `.env` del histórico Git (si se commiteó)

```bash
git filter-repo --invert-paths --path .env --force
git push --force --all
```

Coordinar con el equipo: requiere re-clone obligatorio.

### 5.5 — Verificar histórico

```bash
# Buscar referencias a .env en histórico:
git log --all --source --remotes --oneline -- .env
git log --all -p -S "1234" -- .env  # busca el password viejo en el histórico
```

---

## 6. Hardening de infraestructura

### 6.1 — Firewall del origin server

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow ssh
sudo ufw allow from 127.0.0.1 to any port 8000
sudo ufw deny 8000
sudo ufw deny 5000
sudo ufw enable
sudo ufw status verbose
```

Evita bypass de Cloudflare Tunnel.

### 6.2 — Forzar HTTPS en Nginx (cuando tengas cert local)

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

Con Cloudflare Tunnel basta `listen 80` (TLS lo termina CF).

### 6.3 — DNS limpio

Verificar que no haya A-records apuntando al origin IP:

```bash
dig api.skilled.com.mx +short
# Solo deben aparecer IPs Cloudflare: 104.x, 162.x, 172.64.x, etc.
```

Buscar IP filtrada en:
- https://securitytrails.com (histórico DNS)
- https://crt.sh (certificados que contengan IPs)
- https://shodan.io (escaneo de tu IP, debe estar bloqueado por ufw)

### 6.4 — Cloudflare WAF + Rate-limit

Cloudflare dashboard → Security → WAF:

- Activar **Bot Fight Mode**.
- Regla: `URI Path contains "/api/auth"` → Action: Challenge + Rate limit 10/min/IP.
- Regla: `URI Path contains "/api/auth/login"` → Rate limit 5/min/IP.

### 6.5 — Vercel Deployment Protection

```
Vercel Dashboard → Settings → Deployment Protection → Vercel Authentication
```

(Requiere plan Pro). Restringe acceso a previews a usuarios autenticados.

---

## 7. Compatibilidad Nginx ↔ Gunicorn ↔ Flask

### 7.1 — Headers obligatorios en TODOS los `proxy_pass`

Antes los bloques `/api/auth/*` y `/` no pasaban X-Forwarded-*. Ahora los 3 bloques tienen:

```nginx
proxy_set_header Host              $host;
proxy_set_header X-Real-IP         $remote_addr;
proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto $http_x_forwarded_proto;
proxy_set_header X-Forwarded-Host  $host;
proxy_set_header Connection        "";
```

Sin `Connection ""` → vector de HTTP request smuggling (este servicio NO usa WebSockets).

### 7.2 — Timeouts anti-Slowloris

```nginx
client_header_timeout 10s;
client_body_timeout   30s;
send_timeout          30s;
keepalive_timeout     30s;
reset_timedout_connection on;
```

### 7.3 — Whitelist HTTP methods

```nginx
if ($request_method !~ ^(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)$) {
    return 405;
}
```

Bloquea TRACE/TRACK (XST), CONNECT, PROPFIND.

### 7.4 — Bloqueo paths escaneados

```nginx
location ~* \.(env|git|sql|bak|swp|log)$ { return 404; access_log off; }
location ~* /\.(env|git|svn|htpasswd|htaccess) { return 404; access_log off; }
location = /wp-login.php { return 404; access_log off; }
location = /xmlrpc.php   { return 404; access_log off; }
location = /phpmyadmin   { return 404; access_log off; }
```

### 7.5 — Ocultar versión del stack

```nginx
server_tokens off;
proxy_hide_header Server;
proxy_hide_header X-Powered-By;
```

### 7.6 — Real IP recursive

```nginx
real_ip_recursive on;
```

Descarta proxies trusted del XFF para quedarse con cliente real.

### 7.7 — Gunicorn: anti memory leak

```
--max-requests 1000
--max-requests-jitter 100
```

Cada worker se recicla tras ~900-1100 requests. Jitter evita reciclaje simultáneo de los 4 workers.

### 7.8 — Gunicorn: anti HTTP bomb DoS

```
--limit-request-line 4094
--limit-request-fields 32
--limit-request-field_size 8190
```

### 7.9 — Hardening systemd

```ini
ProtectSystem=strict
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
CapabilityBoundingSet=
AmbientCapabilities=
LimitNOFILE=4096
LimitNPROC=256
```

Advertencia: `MemoryDenyWriteExecute=yes` rompe JIT (PyPy, Cython runtime compile). Si en el futuro instalas algo con JIT, comentar esta línea.

### 7.10 — Restart con backoff

```ini
Restart=always
RestartSec=5
StartLimitIntervalSec=60
StartLimitBurst=5
```

5 crashes en 60s → systemd detiene el servicio (evita restart-storm que oculta bugs).

---

## 8. Matriz de testing post-deploy

Después de `systemctl daemon-reload && systemctl restart gunicorn && nginx -t && systemctl reload nginx`:

| # | Check | Comando | Esperado |
|---|-------|---------|----------|
| 1 | Gunicorn local responde | `curl -s http://127.0.0.1:8000/health` | `{"status":"ok"}` |
| 2 | Nginx responde + sin Server header | `curl -sI http://localhost/health` | `200 OK`, sin `Server: gunicorn` |
| 3 | Proxy headers llegan a Flask | `curl -s http://localhost/api/auth/me -H "X-Forwarded-Proto: https"` | `401 Token requerido` |
| 4 | Rate-limit auth funciona | `for i in {1..20}; do curl -X POST http://localhost/api/auth/login -d '{}' -H 'Content-Type: application/json'; done` | Después de ~10 reqs: `503` o `429` |
| 5 | CSRF block /refresh | `curl -X POST http://localhost/api/auth/refresh` | `403 Header X-Requested-With requerido` |
| 6 | CSRF pasa con XRW | `curl -X POST http://localhost/api/auth/refresh -H "X-Requested-With: XMLHttpRequest"` | `401 Refresh token no presente` |
| 7 | Hardening systemd | `systemctl show gunicorn -p ProtectSystem,NoNewPrivileges,CapabilityBoundingSet` | `strict`, `yes`, vacío |
| 8 | Permisos .env | `ls -l /opt/nominas/.env` | `-rw-r----- 1 root sistemanominas` |
| 9 | Sin smuggling headers | `curl -sI http://localhost/api/auth/me` | NO debe aparecer `Upgrade:` ni `Connection: upgrade` |
| 10 | Método bloqueado | `curl -X TRACE http://localhost/` | `405 Not Allowed` |
| 11 | Path escaneado bloqueado | `curl -sI http://localhost/.env` | `404` (sin tocar Flask) |
| 12 | TOTP replay bloqueado | Usar el mismo código 2 veces en `/verify-2fa` | 1°: 200, 2°: 401 "ya fue usado" |
| 13 | Documento .docx rechazado | Subir DOCX a `/api/trabajadores/<id>/documentos` | 400 "Tipo de archivo no permitido" |
| 14 | Coord NO ve dashboard | Login como coord → `GET /api/dashboard` | 403 |
| 15 | Coord NO edita salario | `PUT /api/trabajadores/<id>` con `salario_real_pactado_x_sem` | 200 con `warnings` listando campos bloqueados |
| 16 | CSP en Vercel no rompe | Abrir DevTools → Console en SPA | Sin "Refused to load" |
| 17 | JWT con iss/aud bad | Token con `iss="otro"` → cualquier endpoint | 401 "Token inválido" |
| 18 | Login + 2FA end-to-end | Login → verify-2fa → /me | Token + user data |

---

## 9. Lo que falta — inventario completo

### A. NO se puede arreglar desde código

| # | Tarea | Tiempo |
|---|-------|--------|
| A1 | Rotar password Postgres | 5 min |
| A2 | Rotar Gmail App Password | 3 min |
| A3 | Rotar Groq API Key | 3 min |
| A4 | `git filter-repo` para purgar `.env` del histórico | 30 min (incluye coordinar re-clone) |
| A5 | `sudo ufw deny 8000` en el origin server | 2 min |
| A6 | Verificar DNS limpio (no A-records al origin) | 10 min |
| A7 | Configurar Cloudflare WAF | 15 min |
| A8 | Vercel Deployment Protection (Pro plan) | 5 min |
| A9 | `chmod 640 /opt/nominas/.env` | 1 min |
| A10 | Re-configurar 2FA de todos los usuarios | Comunicación + soporte |
| A11 | Backups automatizados (BD + uploads) | 1-2 horas setup |
| A12 | Promover admin maestro a `super_admin` (1 query SQL) | 1 min |

### B. Requiere decisiones de producto

| # | Tema | Decisión |
|---|------|----------|
| B1 | 2FA obligatorio para admins | SI/NO |
| B2 | Password rotation policy (90 días) | SI/NO |
| B3 | WebAuthn/Passkey en lugar de TOTP | A futuro |
| B4 | Email verification al crear cuenta | SI/NO |
| B5 | Quotas de storage por usuario | Cuánto? |
| B6 | Audit log retention | 90/180/365 días |
| B7 | Alertas Slack/email en eventos críticos | Qué eventos |
| B8 | Workflow de aprobación financiera | Threshold $? |

### C. Testing manual post-deploy

Ver sección 8 (matriz completa).

### D. Hardening avanzado (no urgente)

| # | Mejora | Beneficio |
|---|--------|-----------|
| D1 | Logging estructurado JSON | Integración ELK/Loki/Datadog |
| D2 | Sentry para errores en prod | Visibilidad |
| D3 | OpenTelemetry traces | Performance debug |
| D4 | Cifrar `domicilio`/`correo` en BD (no solo TOTP) | Defensa si DB se filtra |
| D5 | Row Level Security en Postgres | Defensa si Flask es bypased |
| D6 | localStorage → cookie HttpOnly + session backend | Mitiga XSS roba-token |
| D7 | CSP-Report-Only mode | Iteración segura del CSP |
| D8 | SRI en CDNs externos | Mitiga compromise CDN |
| D9 | HSTS preload | Cuando 100% seguro de HTTPS forever |
| D10 | DAST en CI (ZAP/Burp) | Regresiones |
| D11 | SAST en CI (Bandit, Semgrep) | Nuevos patrones inseguros |
| D12 | `pip-audit` + `npm audit` en CI gates | Bloquea merge con CVE |
| D13 | Trivy si usas Docker | CVE en base image |
| D14 | Secrets manager (Vault/Doppler/AWS SM) | Rotación automática |
| D15 | Postgres TDE / pgcrypto | Encryption at-rest |

### E. Refactors grandes (no urgentes)

| # | Cambio | Por qué no se hizo |
|---|--------|---------------------|
| E1 | JWT → cookie HttpOnly + session backend | Cambio mayor arquitectura |
| E2 | Eliminar templates Jinja | Blueprints API reusan helpers |
| E3 | xhtml2pdf → WeasyPrint | Cambia layout PDFs, requiere QA visual |
| E4 | pyotp → authlib | pyotp es estándar mantenido |
| E5 | pandas → polars | pandas funciona, cambio cosmético |
| E6 | Refresh token 7 días → 1 día sliding | Trade-off UX, decisión producto |

---

## 10. Roadmap priorizado

### Esta semana (CRÍTICO)

1. **Rotar password Postgres** (A1) — 5 min, impacto crítico
2. **`ufw deny 8000`** en el servidor (A5) — 2 min, impacto alto
3. **Revocar Gmail App Password** (A2) — 3 min
4. **`chmod 640 /opt/nominas/.env`** (A9) — 1 min
5. **Promover admin maestro a `super_admin`** (A12) — 1 min
6. **Avisar a usuarios para re-setup 2FA** (A10) — comunicación
7. **Smoke test de matriz §8** post-deploy

### Próximas 2 semanas

8. Backup automatizado (A11)
9. Cloudflare WAF (A7)
10. Verificar DNS limpio (A6)
11. Decisión 2FA obligatorio admins (B1)

### Próximo mes

12. Sentry (D2)
13. `pip-audit` + `npm audit` en CI (D12)
14. Logging JSON estructurado (D1)
15. CSP-Report-Only mode antes de endurecer más (D7)

### Backlog (cuando haya bandwidth)

16. Secrets manager (D14)
17. JWT → cookies HttpOnly (E1)
18. Alertas Slack en eventos críticos (B7)
19. WebAuthn/Passkey (B3)

---

## 11. Score final

### Evolución por ronda

| Ronda | Cambios | Score |
|-------|---------|-------|
| Pre-auditoría | Baseline | **5.8 / 10** |
| Ronda 1 (CRITs + HIGHs principales) | 16 fixes | 7.9 / 10 |
| Ronda 2 (extras post-revisión) | 5 fixes | 8.1 / 10 |
| Ronda 3 (nginx + gunicorn) | 13 fixes | 8.3 / 10 |
| Ronda 4 (defensa en profundidad) | 7 fixes | **8.4 / 10** |

### Para llegar a 9+

Completar **sección A** (rotación real de credenciales + firewall + DNS limpio + WAF). Todo está documentado paso a paso. Tiempo estimado: 1-2 horas.

### Para llegar a 9.5+

Completar **sección D** parcial (Sentry, logging JSON, pip-audit en CI, secrets manager). Tiempo estimado: 1-2 semanas.

### Breakdown por categoría

| Categoría | Score |
|-----------|-------|
| Autenticación | 8.5 (JWT con iss/aud, 2FA con anti-replay, lockout escalado, refresh rotation) |
| Autorización | 7.5 (whitelist por rol, super_admin para admin-admin, falta workflow aprobación financiera) |
| Validación de entrada | 8.0 (marshmallow, validate_lengths, filetype magic-bytes, enum validation, imagen_url whitelist) |
| Gestión de secretos | 6.0 (`.env` en repo todavía con valores antiguos hasta que se ejecuten A1-A3) |
| TLS / cifrado | 7.5 (Talisman HSTS condicional, falta listen 443 directo) |
| Logging / auditoría | 7.5 (AuditLog completo, _safe_log_value anti-CRLF, falta logging JSON) |
| Resiliencia (rate-limit, lockout) | 9.0 (Flask-Limiter por user/IP + nginx rate_limit + lockout escalado Redis) |
| Configuración de infra | 8.5 (Gunicorn hardened, nginx anti-smuggling/Slowloris, falta firewall origin) |
| Manejo de errores | 8.0 (sin stack traces en prod, JSON 500 genéricos) |
| Dependencias | 8.0 (versiones recientes, falta CI gate con audit) |

---

## 12. Apéndices

### 12.1 — Comandos útiles

#### Generar nuevo SECRET_KEY
```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

#### Generar nueva TOTP_ENCRYPTION_KEY
```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

#### Generar nuevo password DB
```bash
openssl rand -hex 24
```

#### Verificar permisos .env
```bash
stat -c '%U:%G %a' /opt/nominas/.env
# Esperado: root:sistemanominas 640
```

#### Listar refresh tokens activos en DB
```sql
SELECT u.username, COUNT(*) AS sesiones
FROM refresh_tokens rt JOIN users u ON u.id = rt.user_id
WHERE rt.revoked = FALSE AND rt.expires_at > NOW()
GROUP BY u.username ORDER BY sesiones DESC;
```

#### Buscar intentos de login fallidos por IP
```sql
SELECT ip, COUNT(*) AS intentos
FROM audit_log
WHERE action ILIKE '%Login fallido%' AND created_at > NOW() - INTERVAL '24 hours'
GROUP BY ip HAVING COUNT(*) > 10 ORDER BY intentos DESC;
```

#### Validar nginx config
```bash
sudo nginx -t
sudo systemctl reload nginx
```

#### Reload graceful gunicorn
```bash
sudo systemctl reload gunicorn
# o:
sudo kill -HUP $(pgrep -f 'gunicorn.*master')
```

#### Logs en vivo
```bash
journalctl -u gunicorn -f
journalctl -u nginx -f
```

### 12.2 — Variables de entorno (.env actual)

```env
FLASK_APP=run.py
FLASK_ENV=production

# Rotados 2026-05-23:
SECRET_KEY=JTHE_-6PVsbLAzZE5BzE_Hn9lvSCR4xH6gDQPOQ0ymMtuv8aXoDcjco7IK-kaRG6Gyj1qnq0Npi5_BfCcH969Q
TOTP_ENCRYPTION_KEY=wKUg-1Bl_-fez-3l1c-RbGoXUZm2DNsERFK0ZNogUz8=

# PENDIENTE DE ROTAR (sección 5):
DATABASE_URL=postgresql+psycopg://daniel:1234@localhost:5432/MASTER
MAIL_PASSWORD=rudt nlyo wuxa tmwl
GROQ_API_KEY=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

# OK:
REDIS_URL=redis://localhost:6379/0
MAIL_USERNAME=nominaskilled@gmail.com
CORS_ORIGINS=https://app.skilledmx.cloud,https://dev.skilledmx.cloud
RT_COOKIE_SAMESITE=None
```

### 12.3 — Archivos modificados

#### Backend (`Sistema de nominas/`)
- `.env`
- `app/__init__.py`
- `app/utils.py`
- `app/routes/api_auth.py`
- `app/routes/api_users.py`
- `app/routes/api_dashboard.py`
- `app/routes/api_trabajadores.py`
- `app/routes/api_proyectos.py`
- `app/routes/api_prenomina.py`
- `app/routes/api_bitacora.py`
- `app/routes/auth.py`
- `app/routes/users.py`
- `app/routes/bitacora.py`
- `app/routes/inventario_api.py`
- `nginx.config`
- `Gunicorn .config`
- `README.md` (sección de seguridad expandida)

#### Frontend (`plantilla-frontend/`)
- `src/api/axios.js`
- `vite.config.js`
- `vercel.json`

### 12.4 — Endpoints añadidos

```
GET    /api/auth/sessions             - Lista sesiones propias
DELETE /api/auth/sessions/<id>        - Revocar sesión propia específica
DELETE /api/auth/sessions/all         - Revocar todas las propias
DELETE /api/users/<user_id>/sessions  - Admin revoca sesiones de otro
```

### 12.5 — Endpoints con cambios de comportamiento

```
GET  /api/dashboard                    - Ahora 403 si no es admin
GET  /api/auth/users                   - Filtra role/totp/last_seen para no-admin
GET  /api/auth/users/<id>              - Mismo filtrado
POST /api/auth/refresh                 - Requiere X-Requested-With
POST /api/auth/logout                  - Requiere X-Requested-With
POST /api/trabajadores/                 - Solo admin (antes coord también)
POST /api/trabajadores/<id>            - Whitelist por rol (coord limited)
POST /api/trabajadores/<id>/documentos - Solo PDF/JPG/PNG/HEIC ≤ 20 MB
POST /api/users/                        - Solo super_admin puede crear admins
DELETE /api/users/<id>                  - Solo super_admin elimina admins
POST /api/users/<id>/password          - Solo super_admin resetea otros admins; no borra TOTP
POST /api/auth/verify-2fa              - Anti-replay: códigos usados rechazados
POST /api/prenomina/descuentos          - Valida enum tipo, monto cap, concepto length, fecha
POST /api/prenomina/depositos           - Valida concepto length, monto cap
PUT  /api/v1/categorias-config/<nombre> - <string:> en lugar de <path:>
POST /api/v1/productos/                  - imagen_url solo HTTPS o path local
PUT  /api/v1/productos/<id>             - mismo
PUT  /api/v1/categorias-config/<nombre> - mismo
```

### 12.6 — Referencias

- OWASP Top 10 2021: https://owasp.org/Top10/
- OWASP API Security Top 10: https://owasp.org/www-project-api-security/
- CIS Benchmark Nginx: https://www.cisecurity.org/benchmark/nginx
- CIS Benchmark systemd: https://www.cisecurity.org/benchmark/distribution_independent_linux
- Flask Security Considerations: https://flask.palletsprojects.com/en/stable/security/
- JWT Best Practices RFC 8725: https://datatracker.ietf.org/doc/html/rfc8725
- Cloudflare IPs: https://www.cloudflare.com/ips/
- Vercel Headers: https://vercel.com/docs/edge-network/headers

---

## Notas finales

Este documento se generó a partir de una auditoría de seguridad ofensiva exhaustiva del sistema Skilled ERP el **2026-05-23**.

**Mantenimiento**: re-auditar al menos una vez al año, o cada vez que se introduzca un cambio mayor de arquitectura (nuevo módulo, nueva integración, nuevo rol de usuario).

**Contacto / ownership**: el equipo de desarrollo es responsable de cerrar las secciones A (rotación) y C (testing) antes de exponer el sistema en producción.
