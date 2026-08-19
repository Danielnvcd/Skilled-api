# Resumen del sistema — Skilled ERP (Backend)

Resumen ejecutivo de una sola página: qué es, cómo está armado, cómo se protege
y cómo está organizada la base de datos y el código. Para el detalle completo
ver los docs enlazados en cada sección.

---

## 1. Qué es

API JSON en **Flask** (API-only, sin HTML) que sirve a un SPA en React
(repo aparte `plantilla-frontend/`, hosteado en Vercel). Cubre: nóminas,
empleados, proyectos, horas trabajadas, préstamos, inventario de materiales
y herramientas, con notificaciones y auditoría en tiempo real.

---

## 2. Arquitectura

**Stack**: Flask + SQLAlchemy + PostgreSQL (psycopg v3) + Redis + Socket.IO
(gevent en prod) + Gunicorn detrás de Nginx → Cloudflare Tunnel. PDFs con
Jinja + xhtml2pdf, Excel con pandas + openpyxl.

```text
app/
├── __init__.py     # create_app(): config, CORS, Talisman, blueprints, Socket.IO
├── extensions.py   # db, limiter, csrf, migrate, mail + IP real tras Cloudflare
├── realtime.py     # Socket.IO: auth del handshake, salas, hooks ORM, emit_to_*
├── models/         # SQLAlchemy, 12 módulos por dominio
├── routes/         # 18 blueprints (cada uno: _core.py + módulos por tema)
└── utils/          # seguridad, archivos, imágenes, horas, nómina, R2, antivirus
```

- **Sin `static_folder`**: no hay UI server-side; solo `/api/*` + Socket.IO.
- **`create_app()`** arranca en orden fijo: config → espera Redis → extensiones
  → CORS (solo `/api/*`) → Talisman/CSP → registra los 18 blueprints (exentos
  de CSRF, protegidos por JWT) → error handlers globales → headers de
  seguridad + `Cache-Control: no-store` → tablas auxiliares autocreadas →
  `ProxyFix` → Socket.IO (al final, para ver los headers ya corregidos).
- **Realtime (Socket.IO)**: cada conexión autenticada se une a salas
  `user:{id}` / `role:{rol}`; hooks ORM emiten automáticamente al insertar
  notificaciones, cambios de estado de reportes, entradas de bitácora y
  abonos de préstamos.
- **Patrones transversales**: paginación estándar (`page`/`per_page` +
  `sort`/`dir` con whitelist), auditoría (`log_action` en toda mutación),
  Excel con saneo anti fórmula-injection, `with_for_update` para evitar
  over-selling de stock.

Detalle completo: [`ARQUITECTURA.md`](./ARQUITECTURA.md) ·
[`API_REFERENCE.md`](./API_REFERENCE.md) (216 endpoints).

---

## 3. Base de datos

**PostgreSQL** vía SQLAlchemy + Alembic (`flask db ...`). Modelos
particionados por dominio en `app/models/`:

| Dominio | Tablas clave |
|---|---|
| **Auth** | `users` (roles: super_admin/admin/inventario/coordinador/solicitante_material/user), `refresh_tokens`, `totp_backup_codes`, `audit_log` |
| **Empleados** | `trabajadores` (~60 columnas: identificación, laboral, PII fiscal, médico, financiero), `credenciales_plantas`, `documentos_trabajador`, `trabajador_notas` |
| **Proyectos** | `proyectos`, M:N con trabajadores vía `proyecto_trabajador` |
| **Horas** | `reportes_semanales`, `registros_diarios_horas`, `saldo_vacaciones`, `ausencias` |
| **Nómina** | `prenominas`, `descuentos_prenomina`, `depositos_extra` |
| **Préstamos** | `prestamos`, `abonos_prestamo` |
| **Ajuste Inbursa** | `ajuste_periodos`, `ajuste_trabajadores_periodo`, `ajuste_descuentos` |
| **Inventario** | `almacenes`, `estantes`, `productos`, `stock_por_almacen`, `movimientos_inventario`, `tomas_inventario`, `solicitudes_material(_detalle)` |
| **Herramientas** | `herramientas`, `herramienta_unidades`, `asignaciones_herramienta`, `mantenimientos_herramienta`, `incidencias_herramienta`, `solicitudes_baja_herramienta` |
| **Notificaciones** | `notificaciones` (in-app, purga a 30 días) |

Convenciones: PK `id` autoincremental (salvo mapeos M:N con PK compuesta),
timestamps en UTC, montos en `Numeric(10,2)`, estados como `String` en
mayúsculas. Tres tablas se autocrean al arranque sin migración:
`notificaciones`, `totp_backup_codes`, `trabajador_notas`.

Detalle completo + diagrama de relaciones: [`MODELOS_BD.md`](./MODELOS_BD.md).

---

## 4. Seguridad

**Autenticación**: JWT (HS256, `iss`/`aud`, `password_version` invalida
tokens al cambiar contraseña) + refresh token en cookie httpOnly con
rotación y detección de replay. 2FA con TOTP (secreto cifrado con Fernet en
BD, códigos de respaldo, anti-replay vía Redis 90s). Lockout escalado por
usuario en Redis (10 min → 24 h). Comparación constant-time anti-timing en
login.

**Autorización**: por rol vía decoradores (`require_admin`, `require_roles`,
`_require_inventario`, etc.) + whitelist de campos editables por rol en
trabajadores (coordinador no puede tocar salarios/PII fiscal). Coordinador
tiene "ownership": solo ve/edita lo de sus propios proyectos.

**Infraestructura**: rate-limit en dos capas (Nginx + Flask-Limiter, IP real
validada contra CIDRs oficiales de Cloudflare), CSP estricta (`default-src
'none'`), Talisman con HSTS/`frame-ancestors: none`/COOP/CORP, CORS
restringido a orígenes conocidos con headers explícitos, `Cache-Control:
no-store` en todo `/api/*`. Uploads validados por magic bytes (no solo
extensión) con límite de tamaño. Excel exportado saneado contra
CSV/fórmula-injection.

**Auditoría**: toda mutación relevante escribe en `audit_log` (usuario + IP +
acción), con push en tiempo real a admins.

El sistema pasó por una auditoría ofensiva completa (pentest + code review +
infraestructura) con score final **8.4/10**, 4 críticas y 7 altas cerradas.
Detalle de hallazgos, fixes y pendientes operativos:
[`SEGURIDAD.md`](./SEGURIDAD.md).

---

## 5. Código y funciones — organización

- **`routes/`**: 18 blueprints, uno por dominio (`api_auth`, `api_trabajadores`,
  `api_proyectos`, `api_horas`, `api_prenomina`, `api_prestamos`, `api_ajustes`,
  `inventario_api`, `herramientas_api`, etc.), cada uno como sub-paquete con
  `_core.py` (blueprint + decoradores + serializers) y módulos por tema.
- **`_api_helpers.py`** (compartido): `current_user()`, `is_admin()`,
  `require_admin()`, `require_roles()`, decorador `api_transactional`
  (rollback + log automático ante excepciones).
- **`utils/`**: helpers reutilizables por dominio —
  `security.py` (política de contraseñas, saneo de logs),
  `validaciones.py` (longitudes de campos), `audit.py` (`log_action`),
  `files.py`/`images.py` (validación de uploads, conversión a WebP),
  `horas.py` (cálculo de horas productivas), `payroll.py` (totales de
  nómina), `r2.py` (almacenamiento de archivos en Cloudflare R2),
  `antivirus.py` (escaneo con ClamAV).
- **`models/`**: SQLAlchemy puro, sin lógica de negocio; re-exportado plano
  desde `app/models/__init__.py`.

---

## 6. Referencias

| Doc | Contenido |
|---|---|
| [`ARQUITECTURA.md`](./ARQUITECTURA.md) | Mapa completo del código, `create_app()` paso a paso |
| [`API_REFERENCE.md`](./API_REFERENCE.md) | Los 216 endpoints por blueprint |
| [`MODELOS_BD.md`](./MODELOS_BD.md) | Todas las tablas, columnas y relaciones |
| [`SEGURIDAD.md`](./SEGURIDAD.md) | Auditoría de seguridad completa |
| [`STACK_Y_ALTERNATIVAS.md`](./STACK_Y_ALTERNATIVAS.md) | Tecnologías del backend, por qué cada una y comparativa vs Supabase |
| [`DOCKER.md`](./DOCKER.md) / [`DIA_A_DIA.md`](./DIA_A_DIA.md) | Desarrollo local y despliegue |
| [`WEBSOCKETS_Y_DEPLOY.md`](./WEBSOCKETS_Y_DEPLOY.md) | Socket.IO en producción |
