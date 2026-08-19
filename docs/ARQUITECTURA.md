# Arquitectura del código

Mapa de cómo está organizado el backend (Flask, API-only). Complementa al
`README.md` (instalación/deploy) y se acompaña de:

- [`API_REFERENCE.md`](./API_REFERENCE.md) — los 216 endpoints, por blueprint.
- [`MODELOS_BD.md`](./MODELOS_BD.md) — todas las tablas y relaciones.

Generado a partir del código el 2026-06-09. Para regenerar los inventarios:
`python scripts/gen_api_inventory.py` y `python scripts/gen_models_inventory.py`.

---

## Vista general

```text
app/
├── __init__.py        # create_app(): factory de la aplicación
├── extensions.py      # db, limiter, csrf, migrate, mail + helpers de IP/Redis
├── realtime.py        # Socket.IO: init, handlers, hooks ORM, emit_to_*
├── constants.py       # constantes compartidas
├── models/            # SQLAlchemy, particionado por dominio (12 módulos)
├── routes/            # 18 blueprints, cada uno como sub-paquete
│   └── _api_helpers.py  # current_user, is_admin, api_transactional, Excel común
└── utils/             # helpers por dominio (seguridad, archivos, imágenes…)

run.py                 # entry point dev; expone `app` y `socketio` a Gunicorn
templates/             # Jinja SOLO para PDFs (xhtml2pdf) — no hay UI server-side
migrations/            # Alembic (flask db ...)
uploads/               # fotos y documentos (writeable en prod)
tests/                 # pytest (ver memoria: suite verde 2026-06-08)
scripts/               # utilidades de mantenimiento / generadores de inventario
```

El SPA React vive en el repo `plantilla-frontend/` (Vercel) y consume
exclusivamente `/api/*` + Socket.IO.

---

## `create_app()` — orden de inicialización

`app/__init__.py` arma la app en este orden (el orden importa):

1. **Flask sin `static_folder`** (API-only) pero **con `template_folder`**:
   los endpoints de PDF (recibos, solicitudes, tomas, OC express) renderizan
   Jinja → xhtml2pdf.
2. **Config**: `SECRET_KEY` obligatoria (aborta si falta), `DATABASE_URL`
   (default sqlite para tests), pool Postgres (`pool_pre_ping`,
   `pool_recycle=1800`, `statement_timeout=30s`, `lock_timeout=5s`),
   `MAX_CONTENT_LENGTH=50MB`, mail Gmail SMTP, rate-limit default
   `2000/día, 500/hora`.
3. **Espera de Redis**: si hay `REDIS_URL`, reintenta ping hasta 30 veces
   (2 s c/u) y aborta si no conecta. Redis respalda Flask-Limiter, lockout
   y anti-replay TOTP.
4. **Extensiones**: `db`, `limiter`, `csrf`, `migrate`, `mail`, `Compress`.
5. **Filtro Jinja `fecha_es`**: fechas en español para los PDFs.
6. **CORS** (solo `/api/*`): orígenes de `CORS_ORIGINS`,
   `supports_credentials=True` (cookie del refresh token), methods/headers
   explícitos, preflight cacheado 10 min.
7. **Talisman**: CSP completa (ver comentarios en el archivo), `frame_options
   DENY`, HSTS solo en producción, `force_https=False` (Cloudflare termina TLS).
8. **Registro de blueprints**: los 18 módulos de rutas se registran en bucle y
   **se eximen de CSRF** (la protección la da el JWT en header + SameSite de
   la cookie `rt`).
9. **Error handlers globales**: CSRF→419 JSON, 429 JSON, y un handler de
   `Exception` que deja pasar `HTTPException`, loggea (sin traza en prod) y
   responde 500 JSON genérico.
10. **`/` y `/health`**: liveness probe, ambos responden `{"status":"ok"}`.
11. **`after_request`**: headers de seguridad (nosniff, Referrer-Policy,
    Permissions-Policy, X-Frame-Options, COOP, CORP) + `Cache-Control:
    no-store` en todo `/api/*`; y log `[PERF]` de requests >500 ms o ≥400.
12. **Tablas autocreadas** (idempotente, sin migración): `notificaciones`,
    `totp_backup_codes`, `trabajador_notas` — `inspect().has_table()` +
    `__table__.create()`.
13. **ProxyFix** (`x_for=2`: Cloudflare → nginx) y, DESPUÉS de ProxyFix,
    **`init_socketio(app)`** para que el handshake valide orígenes con los
    headers `X-Forwarded-*` ya corregidos.

---

## `extensions.py`

- **`EncryptedString`**: `TypeDecorator` que cifra con Fernet
  (`TOTP_ENCRYPTION_KEY`) al persistir y descifra al leer; tolera valores
  legacy sin cifrar. Lo usa `User.totp_secret`.
- **IP real detrás de Cloudflare**: descarga los CIDR oficiales de CF al
  arranque (fallback hardcodeado) y `get_real_client_ip_flask()` solo confía
  en `CF-Connecting-IP` si la conexión directa viene de una IP de Cloudflare.
- **`rate_limit_key()`**: key del Limiter = IP real. El bucketing por usuario
  lo hacen `key_func` locales en `api_auth` (login, verify-2fa, refresh).
- **`get_redis()`**: singleton del cliente Redis; devuelve `None` si no hay.

---

## `routes/` — patrón de paquete por blueprint

Cada módulo grande se partió en sub-paquete (ver
`REFACTOR_ARCHIVOS_GRANDES.md`). Convención:

```text
api_x/
├── __init__.py   # importa los sub-módulos (side-effect: registra rutas) y re-exporta bp
├── _core.py      # Blueprint + decoradores + schemas/serializers + helpers privados
└── <tema>.py     # endpoints agrupados por tema
```

Los 18 blueprints y sus prefijos:

| Paquete | Prefijo | Dominio |
|---|---|---|
| `api_auth` | `/api/auth` | Login, JWT, refresh, 2FA, perfil, sesiones |
| `api_trabajadores` | `/api/trabajadores` | Empleados: CRUD, ficha, fotos/docs, notas, import/export, timeline |
| `api_proyectos` | `/api/proyectos` | Proyectos y asignación de coordinador |
| `api_horas` | `/api/horas` | Reportes semanales de horas, registros, QR/RFID, móvil |
| `api_prenomina` | `/api/prenomina` | Cálculo semanal, ajustes, PDF/Excel, envío por correo |
| `api_prestamos` | `/api/prestamos` | Préstamos, abonos, liquidación, Excel |
| `api_ajustes` | `/api/ajustes` | Periodos de ajuste Inbursa y sus descuentos |
| `api_proyecto_total` | `/api/proyecto-total` | Totales acumulados por proyecto + Excel |
| `api_historico` | `/api/historico` | Semanas cerradas: consulta, PDF, Excel |
| `api_users` | `/api/users` | Administración de usuarios del sistema |
| `api_dashboard` | `/api/dashboard` | KPIs y alertas (solo admin) |
| `api_bitacora` | `/api/bitacora` | Audit log (lectura) |
| `api_metricas` | `/api/metricas` | Métricas generales |
| `api_notificaciones` | `/api/notificaciones` | Notificaciones in-app |
| `api_search` | `/api/v1/buscar` | Búsqueda global |
| `api_sistemas` | `/api/sistemas` | Panel de TI: infraestructura, sesiones, bloqueos, eventos de seguridad, archivos |
| `inventario_api` | `/api/v1` | Materiales: productos, almacenes, movimientos, solicitudes, tomas, reportes |
| `herramientas_api` | `/api/v1` | Herramientas: catálogo, unidades, asignaciones, mantenimientos, bajas |

### Autenticación y autorización

- **`@jwt_required`** (`api_auth/jwt_required.py`): valida el Bearer token
  (`iss`/`aud`, `password_version`) y deja el `User` en `flask.g._jwt_user`.
- **`_api_helpers.py`** (compartido): `current_user()`, `is_admin()`,
  `is_super_admin()`, `require_admin()`, `require_roles()`, y el decorador
  **`api_transactional`** (rollback + log + 500 ante excepciones no-HTTP;
  deja pasar `HTTPException`). También centraliza el saneo anti
  fórmula-injection y los estilos de los Excel exportados.
- **Inventario/herramientas** usan sus propios decoradores en `_core.py`:
  - `_require_login` — cualquier usuario autenticado.
  - `_require_inventario` — lectura: `inventario`, `solicitante_material`,
    `coordinador`, `admin`, `super_admin`.
  - `_require_inventario_admin` — escritura: `inventario`, `admin`,
    `super_admin`.
- **Coordinador** tiene ownership: solo ve/edita trabajadores y reportes de
  SUS proyectos (`_authorized` en `api_trabajadores/_core.py` y equivalentes
  en `api_horas`).

---

## `models/` — particionado por dominio

`app/models/__init__.py` re-exporta todo (la API `from app.models import X`
se preserva). Módulos: `auth`, `trabajador`, `proyecto`, `horas`, `prenomina`,
`prestamos`, `ajustes`, `notificaciones`, `inventario`, `solicitudes`,
`herramientas`. Detalle completo de tablas en
[`MODELOS_BD.md`](./MODELOS_BD.md).

---

## `realtime.py` — Socket.IO

`async_mode` por env var `SOCKETIO_ASYNC_MODE`: **gevent en prod** (Gunicorn
con `GeventWebSocketWorker`; `run.py` aplica `monkey.patch_all()` al boot) y
**threading en dev** (Werkzeug). Con Redis, Socket.IO usa `message_queue` para
emitir entre workers. Ver `DEPLOY_GEVENT.md` y `MIGRACION_EVENTLET_A_GEVENT.md`.
Piezas:

- **`init_socketio(app)`**: se llama al final de `create_app()`; valida CORS
  del handshake con los mismos orígenes del SPA.
- **Handshake autenticado**: el evento `connect` valida el JWT; cada conexión
  se une a las salas `user:{id}` y `role:{rol}`. `join:reporte` agrega la
  sala `reporte:{id}` (kiosko de captura).
- **Hooks ORM** (`after_insert`/`after_commit` — el emit sale SOLO si el
  commit fue exitoso):
  - `notif:new` — al insertar `Notificacion`.
  - `reporte:estado_cambio` — cambio de estado de `ReporteSemanal` (sala del reporte).
  - `reporte:registros_cambio` — cambios en `RegistroDiarioHoras` (reemplazó el polling del Grid del kiosko).
  - `bitacora:new` — inserción de `AuditLog` (a admin/super_admin).
  - `abono:new` — inserción de `AbonoPrestamo` (manuales y por prenómina).
- **Emits manuales desde endpoints** (post-commit): p. ej. `nota:changed`
  (notas de trabajador). Política del proyecto: toda feature nueva integra
  Socket.IO (emits backend + `invalidateOn`/listeners en el SPA).
- **API pública**: `emit_to_user(user_id, ...)`, `emit_to_role(roles, ...)`,
  `emit_to_reporte(reporte_id, ...)`, `force_logout_user(user_id)` (cierra
  los WebSockets activos en panic-revoke / borrado de usuario).

---

## `utils/` — helpers por dominio

Re-exportados planos desde `app/utils/__init__.py`:

| Módulo | Símbolos | Para qué |
|---|---|---|
| `security.py` | `is_strong_password`, `_safe_log_value` | Política de passwords; saneo de valores en logs |
| `validaciones.py` | `TRABAJADOR_LENGTHS`, `validate_lengths` | Límites de longitud por campo de Trabajador |
| `audit.py` | `log_action` | Escribe en `audit_log` (usuario + IP + acción) |
| `files.py` | `allowed_file`, `STRICT_MIMETYPES`, `safe_excel_value` | Validación de uploads (magic bytes) y anti fórmula-injection en Excel |
| `images.py` | `allowed_image_file`, `image_to_webp`, … | Validación + conversión a WebP de fotos |
| `horas.py` | `calcular_horas_productivas`, `turnos_se_traslapan` | Reglas de cálculo de horas |
| `payroll.py` | `to_dec`, `recalcular_totales_prenomina` | Decimales y totales de prenómina |

---

## Patrones transversales

- **Auditoría**: toda mutación relevante llama `log_action(...)`; el insert
  dispara además el push `bitacora:new`.
- **Excel**: pandas + openpyxl con `_sanitize_rows` (anti `=fórmula`) y
  `_aplicar_estilos_y_retornar` compartidos (5 paquetes los usan).
- **PDF**: Jinja (`templates/`) → xhtml2pdf. Por eso existe `template_folder`
  aunque no haya UI server-side.
- **Concurrencia de stock**: `POST /api/v1/movimientos/` usa
  `with_for_update` para evitar over-selling.
- **Paginación estándar**: `page`/`per_page` + respuesta `{items, total,
  pages, ...}`. Listados de trabajadores y préstamos aceptan además
  `sort`/`dir` con whitelist (ver `ORDENAMIENTO_LISTADOS.md`).
- **Rate-limit por endpoint**: `@limiter.limit(...)` en endpoints sensibles
  (login, uploads, generación de PDFs/QR, movimientos), sobre el default
  global y la capa de Nginx.
