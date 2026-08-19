# Stack tecnológico y decisión de arquitectura — Skilled ERP (Backend)

> **Alcance**: solo backend. El SPA React (`plantilla-frontend/`, Vercel) queda fuera.
> **Pregunta que responde**: qué tecnologías usa el backend, por qué cada una, y
> por qué no se construyó sobre un BaaS tipo Supabase.
> **Fecha**: 2026-08-18 · Versiones tomadas de `requirements.txt`.

---

## Tabla de contenido

1. [Inventario de tecnologías](#1-inventario-de-tecnologías)
2. [Por qué cada pieza](#2-por-qué-cada-pieza)
3. [Comparativa contra Supabase](#3-comparativa-contra-supabase)
4. [Dónde Supabase sí es mejor](#4-dónde-supabase-sí-es-mejor)
5. [Opción híbrida](#5-opción-híbrida)
6. [Cuándo reconsiderar esta decisión](#6-cuándo-reconsiderar-esta-decisión)

---

## 1. Inventario de tecnologías

| Capa | Tecnología | Rol en el sistema |
|---|---|---|
| Framework | Flask 3.1.3 + Werkzeug 3.1.8 (API-only, sin `static_folder`) | 18 blueprints, 216 endpoints bajo `/api/*` |
| ORM | SQLAlchemy 2.0.49 + Flask-SQLAlchemy 3.1.1 | 12 módulos de modelos particionados por dominio |
| Migraciones | Flask-Migrate 4.1.0 (Alembic) | `flask db migrate/upgrade` |
| Base de datos | PostgreSQL 18 + psycopg[binary] 3.3.4 | `pool_pre_ping`, `pool_recycle=1800`, `statement_timeout=30s`, `lock_timeout=5s` |
| Cache / estado efímero | Redis 7 (`redis[hiredis]` 7.4.0) | rate-limit, lockout escalado (10 min → 24 h), anti-replay TOTP (90 s) |
| Realtime | Flask-SocketIO 5.6.1 + python-socketio 5.16.2 + python-engineio 4.13.2 + simple-websocket | salas `user:{id}` / `role:{rol}`, hooks ORM que emiten en mutaciones |
| Servidor de aplicación | Gunicorn 23.0.0 + gevent 24.11.1 + gevent-websocket 0.10.1 | 4 workers `GeventWebSocketWorker` × 1000 conexiones |
| Borde | Nginx → Cloudflare Tunnel | TLS, rate-limit capa 1, `ProxyFix(x_for=2)` |
| Autenticación | PyJWT 2.13.0 (HS256) + pyotp 2.9.0 + qrcode + cryptography 50.0.0 (Fernet) | JWT con `iss`/`aud`/`password_version`, refresh en cookie httpOnly con rotación y detección de replay, 2FA TOTP con secreto cifrado en BD |
| Seguridad web | Flask-Talisman 1.1.0, Flask-Limiter 4.1.1, Flask-Cors 6.0.5, Flask-WTF 1.3.0 | CSP `default-src 'none'`, HSTS, COOP/CORP, CORS por whitelist, CSRF (eximido en `/api/*`, cubierto por JWT + SameSite) |
| Archivos | boto3 1.35.99 → Cloudflare R2, filetype 1.2.0 (magic bytes), Pillow 12.3.0 + pillow-heif 1.3.0, clamd 1.0.2 → ClamAV | subida validada por contenido, normalizada y escaneada por antivirus |
| Documentos | xhtml2pdf 0.2.17 + reportlab 4.5.1 sobre plantillas Jinja | recibos, solicitudes, tomas de inventario, OC express |
| Hojas de cálculo | pandas 3.0.3 + openpyxl 3.1.5 | exportaciones saneadas contra fórmula-injection |
| Validación | marshmallow 3.26.2 + WTForms 3.2.2 + email-validator | schemas de entrada |
| Otros | Flask-Compress 1.24, flask-mail 0.10.0, httpx 0.28.1, python-dotenv, pytest 9.0.3 | gzip, SMTP Gmail, cliente HTTP, config, tests |
| Empaquetado | Docker + docker-compose (`api`, `db`, `redis`, `clamav`) | entorno local idéntico a producción |

Detalle de cómo se ensambla todo esto: [`ARQUITECTURA.md`](./ARQUITECTURA.md).

---

## 2. Por qué cada pieza

- **Flask sobre Django**: el sistema es API JSON pura. El admin, el ORM opinado
  y las plantillas de Django no aportan aquí; Flask deja el control del orden de
  arranque (`create_app()`), que importa porque `ProxyFix` debe correr **antes**
  de `init_socketio` para que el handshake vea los `X-Forwarded-*` correctos.
- **SQLAlchemy 2.0 + Alembic**: el dominio tiene relaciones densas (M:N de
  proyectos, stock por almacén, unidades de herramienta) y necesita bloqueos
  explícitos (`with_for_update`). El ORM da eso sin renunciar a SQL crudo.
- **psycopg v3**: requisito duro tras migrar de eventlet a gevent — eventlet es
  incompatible con psycopg3 (ver [`MIGRACION_EVENTLET_A_GEVENT.md`](./MIGRACION_EVENTLET_A_GEVENT.md)).
- **gevent en vez de gthread**: gthread no implementa el upgrade HTTP→WS que
  Socket.IO necesita (ver [`DEPLOY_GEVENT.md`](./DEPLOY_GEVENT.md)).
- **Redis obligatorio**: `create_app()` aborta si no conecta. Sostiene rate-limit
  distribuido, lockout y anti-replay TOTP; sin él la seguridad se degrada en
  silencio, así que se prefiere fallar en el arranque.
- **R2 en vez de disco local**: egreso gratis y desacople del volumen del
  contenedor (ver [`MIGRACION_ARCHIVOS_A_R2.md`](./MIGRACION_ARCHIVOS_A_R2.md)).
- **ClamAV como servicio aparte**: el escaneo no debe compartir proceso ni
  memoria con la API (ver [`ANTIVIRUS_CLAMAV.md`](./ANTIVIRUS_CLAMAV.md)).

---

## 3. Comparativa contra Supabase

Supabase = PostgreSQL gestionado + PostgREST (CRUD automático sobre tablas) +
RLS + GoTrue (auth) + Storage + Realtime + Edge Functions (Deno). Encaja muy
bien cuando la aplicación es **CRUD con reglas simples**. Este backend no lo es:
el núcleo es cálculo, control transaccional de stock y generación documental.

| Necesidad real del sistema | Con el stack actual | Con Supabase |
|---|---|---|
| **Nómina, prenómina, descuentos, ajuste Inbursa** | Python en `app/utils/` + `app/routes/api_prenomina/` | Edge Functions en Deno, con límites de tiempo/memoria y sin el ecosistema numérico de Python. En la práctica exige un servidor propio igualmente |
| **PDFs** (recibos, OC, tomas) | Jinja + xhtml2pdf/reportlab | No hay equivalente en Deno de calidad comparable; se resuelve con un servicio externo |
| **Excel** (exportaciones saneadas) | pandas + openpyxl | Igual: fuera de Supabase |
| **Stock sin over-selling** | `with_for_update` dentro de `api_transactional`, una transacción por request | PostgREST no abarca varias llamadas en una transacción; habría que escribir la lógica como funciones plpgsql |
| **216 endpoints con lógica de negocio** | Blueprints Python, testeables con pytest | Como RPC plpgsql: el negocio vive en SQL almacenado, difícil de versionar, testear y depurar |
| **Permisos por campo y por propiedad** (coordinador no toca salarios ni PII fiscal; solo ve sus proyectos) | Decoradores + whitelist de campos editables por rol | Políticas RLS + grants por columna sobre una tabla de ~60 columnas: posible, pero la matriz rol × campo × ownership se vuelve SQL frágil y costoso de probar |
| **Realtime semántico** | Salas `user:{id}` / `role:{rol}`, emitidas desde hooks ORM, con auditoría de eventos de seguridad | Realtime difunde cambios de tabla; el "quién ve qué" queda más grueso y otra vez apoyado en RLS |
| **Uploads seguros** | Magic bytes + límite de tamaño + escaneo ClamAV | Storage no escanea archivos; validación por tipo declarado |
| **Auditoría** | `log_action` en toda mutación con usuario + IP, push a admins | Triggers en BD; la IP real del cliente no llega a la BD sin plumbing extra |
| **Datos sensibles** (PII fiscal y médica de trabajadores) | Infraestructura propia expuesta por Cloudflare Tunnel; sin lock-in | Datos en la nube del proveedor; cambiar de proveedor implica reescribir RLS y funciones |
| **Costo y entorno local** | `docker compose up` reproduce producción; costo = hardware propio | Entorno local vía CLI, pero el comportamiento de RLS/funciones en producción es donde aparecen las sorpresas |

**Resumen**: adoptar Supabase no habría eliminado el backend, lo habría partido
en dos (Supabase para datos y auth + un servidor Python para nómina, PDFs, Excel
y transacciones de stock), sumando una frontera de red y una segunda fuente de
verdad para los permisos.

---

## 4. Dónde Supabase sí es mejor

Siendo justos, hay terreno donde la decisión tuvo un costo real:

- **Autenticación**. JWT con `password_version`, rotación de refresh con
  detección de replay, TOTP cifrado con Fernet, códigos de respaldo y lockout
  escalado son varias semanas de trabajo y superficie de riesgo mantenidas a
  mano. GoTrue da todo eso hecho y auditado por terceros.
- **Operación**. Nginx, Gunicorn/gevent, Redis, backups y actualizaciones de
  Postgres son responsabilidad propia. Supabase los absorbe.
- **Backups y PITR gestionados**, sin procedimiento manual que mantener.
- **Velocidad inicial**: un CRUD equivalente habría estado en línea en días.

---

## 5. Opción híbrida

La única pieza donde migrar tendría retorno claro es **identidad**: usar
Supabase Auth (o Auth0/Clerk) como proveedor de identidad y dejar Flask con el
resto. El backend ya valida JWT en cada request, así que el cambio se concentra
en `app/routes/api_auth/` y en la emisión/rotación de tokens; el resto de los 18
blueprints no se entera.

Costo a considerar: se pierde el control fino de `password_version` y del
lockout en Redis, y se añade una dependencia externa en el camino crítico del
login. No es una migración gratuita, solo la menos cara de todas.

Migrar datos o lógica de negocio a Supabase **no** tiene retorno con el diseño
actual.

---

## 6. Cuándo reconsiderar esta decisión

Señales que invalidarían el análisis de arriba:

- La carga operativa (parches, backups, incidentes de Redis o Postgres) empieza a
  consumir más tiempo que el desarrollo de producto.
- Aparece la necesidad de multi-tenant con aislamiento por fila, donde RLS sí es
  la herramienta correcta.
- El equipo crece hacia perfiles frontend y nadie mantiene el backend Python.
- Se requiere certificación (SOC 2, ISO 27001) donde apoyarse en un proveedor
  certificado sale más barato que certificar la infraestructura propia.

---

## Referencias

| Doc | Contenido |
|---|---|
| [`RESUMEN_SISTEMA.md`](./RESUMEN_SISTEMA.md) | Resumen ejecutivo del sistema |
| [`ARQUITECTURA.md`](./ARQUITECTURA.md) | Mapa del código y `create_app()` paso a paso |
| [`SEGURIDAD.md`](./SEGURIDAD.md) | Auditoría de seguridad (8.4/10) |
| [`WEBSOCKETS_Y_DEPLOY.md`](./WEBSOCKETS_Y_DEPLOY.md) | Socket.IO en producción |
| [`MIGRACION_EVENTLET_A_GEVENT.md`](./MIGRACION_EVENTLET_A_GEVENT.md) | Por qué gevent y no eventlet |
| [`MIGRACION_ARCHIVOS_A_R2.md`](./MIGRACION_ARCHIVOS_A_R2.md) | Migración de uploads a Cloudflare R2 |
