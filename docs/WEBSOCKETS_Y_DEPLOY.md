# WebSockets y Deploy a Producción

Sistema de notificaciones en tiempo real (Socket.IO) que reemplaza el polling
cada 60 s de la campana del SPA y soporta señales en vivo para el kiosko de
asistencias.

---

## Arquitectura

```
                ┌────────────────────────────┐
                │   SPA React (Vercel)       │
                │   app.skilledmx.cloud      │
                └────────┬───────────────────┘
                         │ wss://app.skilledmx.cloud/socket.io/
                         │ (auth: { token: JWT })
                         ▼
                ┌────────────────────────────┐
                │   nginx (Cloudflare Tunnel)│
                │   location /socket.io/     │
                │   proxy_set_header Upgrade │
                └────────┬───────────────────┘
                         │
                         ▼
   ┌─────────────────────────────────────────────────┐
   │   Gunicorn (4 workers, --worker-class eventlet) │
   │   ┌───────────────────────────────────────────┐ │
   │   │  Flask + Flask-SocketIO                   │ │
   │   │   • async_mode=eventlet                   │ │
   │   │   • salas user:{id} y reporte:{id}        │ │
   │   │   • emits desde after_commit de SQLAlchemy│ │
   │   └─────────────┬─────────────────────────────┘ │
   └─────────────────┼───────────────────────────────┘
                     │ pub/sub cross-worker
                     ▼
              ┌────────────────┐
              │  Redis (queue) │
              └────────────────┘
```

### Salas y eventos

| Sala            | Quién entra                                   | Evento                  | Cuándo dispara                                                     |
|-----------------|-----------------------------------------------|-------------------------|--------------------------------------------------------------------|
| `user:{id}`     | Cualquier cliente al hacer handshake con JWT  | `notif:new`             | Insert de `Notificacion` para ese usuario (después del commit)     |
| `user:{id}`     | "                                             | `notif:read`            | `POST /api/notificaciones/{id}/leer`                               |
| `user:{id}`     | "                                             | `notif:read_all`        | `POST /api/notificaciones/marcar_todas`                            |
| `reporte:{id}`  | Cliente que emite `join:reporte` y tiene acceso | `reporte:estado_cambio` | Update de `ReporteSemanal.estado` (después del commit)             |

### Auth

- **Handshake**: el cliente manda `auth: { token: <JWT> }`. El backend decodifica el JWT con `_decode_token(token, 'access')`. Si el token no es válido, el handshake se rechaza (return `False` del handler `connect`).
- **Rotación**: el cliente lee `localStorage.getItem('token')` en cada reconexión (`auth` es función, no objeto), así que cuando axios refresca el JWT el siguiente reconnect usa el token fresco.
- **Cambio de contraseña**: invalida el JWT activo (verificación de `password_version`). El socket abierto sigue vivo hasta el siguiente ping/disconnect (≤60 s).

---

## Backend

### Dependencias (`requirements.txt`)

```
Flask-SocketIO==5.4.1
python-socketio==5.11.4
eventlet==0.36.1
```

### Configuración (`app/realtime.py`)

- `async_mode` se lee de `SOCKETIO_ASYNC_MODE` (env). Default: `'threading'` para `python run.py` en dev.
- `message_queue` se toma de `REDIS_URL`. Sin Redis cae a memoria local (solo un worker).
- `cors_allowed_origins` reusa `CORS_ORIGINS` del `.env` (mismo origen que el CORS REST).
- `manage_session=False`: evita el `AttributeError: property 'session' of 'RequestContext' object has no setter` de Flask 3.1+ ↔ Flask-SocketIO 5.4.x.

### Hooks de emit (`app/realtime.py`)

Patrón: snapshot en `after_insert`/`after_update`, flush en `after_commit`. Si la transacción se hace rollback, el evento nunca se emite.

- `_register_notif_emit_hook` → escucha `Notificacion.after_insert` y emite `notif:new`.
- `_register_reporte_estado_emit_hook` → escucha `ReporteSemanal.after_update` (solo si `estado` cambió) y emite `reporte:estado_cambio`.

### Handlers (`app/realtime.py`)

- `connect`: valida JWT, mete a sala `user:{id}`, guarda `user_id` en la session del socket.
- `disconnect`: log debug, sin side-effects.
- `join:reporte`: el kiosko se suscribe a una sala `reporte:{id}` si tiene acceso (admin/super_admin o coordinador del proyecto). Devuelve ack `{ok, reporte_id, error?}`.

### Variables de entorno requeridas en prod

| Variable                  | Valor                                                                 | Notas                                                |
|---------------------------|-----------------------------------------------------------------------|------------------------------------------------------|
| `SOCKETIO_ASYNC_MODE`     | `eventlet`                                                            | Configurado en el systemd unit                       |
| `CORS_ORIGINS`            | `https://app.skilledmx.cloud,https://dev.skilledmx.cloud,...`         | Mismos que para REST                                 |
| `REDIS_URL`               | `redis://localhost:6379/0`                                            | Imprescindible si hay >1 worker                      |
| `SECRET_KEY`              | (ya existente)                                                        | Firma de JWT                                         |

---

## Gunicorn

`Gunicorn .config` (extracto relevante):

```
ExecStart=/opt/nominas/venv/bin/gunicorn \
    --workers 4 \
    --worker-class eventlet \
    --worker-connections 1000 \
    ...
    run:app

Environment="SOCKETIO_ASYNC_MODE=eventlet"
```

**Por qué eventlet**: cada conexión WS / long-poll ocupa un greenlet (no un hilo del SO). 4 workers × 1000 conexiones = 4 000 clientes concurrentes. Con el `gthread` anterior eran solo 8 (4 workers × 2 threads).

**Trade-off conocido**: los greenlets son cooperativos. Un PDF/Excel de 5 s bloquea todos los demás clientes en ese worker hasta terminar. Como tenemos 4 workers, el load balancer puede meter tráfico en los otros 3 mientras tanto. Si las generaciones de PDF crecen, considerar un pool dedicado.

**Monkey-patching**: el worker class `eventlet` de gunicorn hace `eventlet.monkey_patch()` automáticamente antes de importar `run:app`. No hace falta tocar el código.

---

## nginx

`nginx.config` ya tiene el bloque `/socket.io/` antes de `location /`:

```nginx
location /socket.io/ {
    limit_req zone=api_general burst=300 nodelay;

    proxy_pass http://127.0.0.1:8000;
    proxy_http_version 1.1;
    proxy_set_header Upgrade           $http_upgrade;
    proxy_set_header Connection        $connection_upgrade;
    proxy_set_header Host              $host;
    proxy_set_header X-Real-IP         $remote_addr;
    proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $http_x_forwarded_proto;
    proxy_set_header X-Forwarded-Host  $host;

    proxy_read_timeout 3600s;
    proxy_send_timeout 3600s;
    proxy_connect_timeout 5s;

    proxy_buffering off;

    add_header Cache-Control "no-store" always;
}
```

**Por qué cada directiva**:

- `proxy_http_version 1.1` + `Upgrade` + `Connection $connection_upgrade`: triada estándar para WebSocket. Sin esto Cloudflare reescribiría a HTTP/1.0 y matamos el upgrade.
- `proxy_read_timeout 3600s`: las conexiones WS viven horas. El default (60 s) las mataría a cada minuto.
- `proxy_buffering off`: los chunks de long-poll y WS deben fluir en tiempo real. Con buffering nginx acumula y rompe la entrega.

**Cloudflare**: WebSocket viene activado por default. No hace falta tocar el panel.

---

## Frontend (SPA en Vercel)

### Dependencias (`package.json`)

```json
"dependencies": {
  "socket.io-client": "^4.8.1",
  ...
}
```

### `vercel.json`

```json
"installCommand": "npm install --legacy-peer-deps"
```

Necesario por el peer-dep entre Vite v8 y plugins.

CSP en headers:
```
connect-src 'self' https://*.skilledmx.cloud wss://*.skilledmx.cloud
```

`wss:` es **obligatorio** — el browser bloquea WebSocket si solo aparece `https:`.

### `src/context/SocketContext.jsx`

- Lee `VITE_API_URL` (env var de Vercel) y extrae el origin para el socket.
- `auth` es función → relee el token de `localStorage` en cada reconexión.
- `transports: ['polling', 'websocket']` → polling-first sobrevive mejor a proxies que strippean headers; upgrade a WS pasa una vez establecida la conexión HTTP.
- Reconexión exponencial (1 s → 10 s máx).

### `src/components/NotificacionesBell.jsx`

- Carga inicial 1 vez al montar.
- Escucha `notif:new`, `notif:read`, `notif:read_all` por socket.
- Fallback de polling cada **2 min** solo si el socket está caído (`connected === false`).

### Dev local

Crear `.env.local` (gitignored):
```
VITE_API_URL=http://localhost:5000/api
```

Esto hace que axios y socket.io hablen directo con el backend Flask local (`:5000`) sin pasar por el proxy de Vite (que con v8 tiene bugs en el upgrade WS).

---

## Aplicación de asistencias (kiosko Electron)

El kiosko se conecta con el JWT del operador y se suscribe a cada reporte BORRADOR que tiene cargado localmente.

```js
const socket = io('https://app.skilledmx.cloud', {
  auth: (cb) => cb({ token: jwtDelOperador }),
  path: '/socket.io',
  transports: ['polling', 'websocket'],
  withCredentials: true,
})

socket.on('connect', () => {
  // Suscribirse a cada reporte local
  for (const reporte of reportesLocales) {
    socket.emit('join:reporte', { reporte_id: reporte.id }, (ack) => {
      if (!ack.ok) console.warn('No se pudo suscribir:', ack.error)
    })
  }
})

socket.on('reporte:estado_cambio', ({ reporte_id, estado }) => {
  if (estado !== 'BORRADOR') {
    // Otro operador cerró el reporte. Dejar de aceptar RFIDs.
    detenerCapturaRFID(reporte_id)
    descargarReporte(reporte_id)
  }
})
```

---

## Pasos de deploy a producción

### Orden recomendado

1. **Backend primero** (sin riesgo: el SPA en prod sigue cayendo a polling de 2 min mientras tanto).
2. **Frontend después** (toma ventaja del WS recién desplegado).

Si se invierte el orden tampoco se rompe nada — el SPA queda en polling fallback durante la ventana.

### Backend

```bash
# 1) En el server de producción
cd /opt/nominas
git pull
source venv/bin/activate
pip install -r requirements.txt

# 2) Verificar que el .env tenga lo necesario (ya debería)
grep -E "REDIS_URL|CORS_ORIGINS|SECRET_KEY" .env

# 3) Reinstalar/actualizar systemd unit si cambió `Gunicorn .config`
sudo cp "Gunicorn .config" /etc/systemd/system/nominas.service
sudo systemctl daemon-reload

# 4) Actualizar nginx (ya debe tener el location /socket.io/)
sudo cp nginx.config /etc/nginx/sites-available/nominas.conf
sudo nginx -t
sudo systemctl reload nginx

# 5) Reiniciar Gunicorn
sudo systemctl restart nominas

# 6) Verificar
sudo systemctl status nominas
curl -i "http://localhost:8000/socket.io/?EIO=4&transport=polling"
# Debe devolver 200 + JSON con sid, upgrades, pingTimeout
```

### Frontend

```bash
# Desde tu máquina
cd plantilla-frontend
git add .
git commit -m "feat: WebSockets para notifs y kiosko"
git push origin main
# Vercel hace el build automático con --legacy-peer-deps
```

---

## Checklist final pre-producción

### Backend

- [x] `Flask-SocketIO==5.4.1`, `python-socketio==5.11.4`, `eventlet==0.36.1` en `requirements.txt`
- [x] `app/realtime.py` con `manage_session=False`
- [x] `async_mode` configurable vía `SOCKETIO_ASYNC_MODE` env
- [x] Hook `_register_notif_emit_hook` para notifs
- [x] Hook `_register_reporte_estado_emit_hook` para reportes
- [x] Helpers `emit_to_user`, `emit_to_reporte`
- [x] Handler `connect` con auth JWT
- [x] Handler `join:reporte` con check de acceso
- [x] `Gunicorn .config` con `--worker-class eventlet --worker-connections 1000`
- [x] `Gunicorn .config` con `Environment="SOCKETIO_ASYNC_MODE=eventlet"`
- [x] `nginx.config` con `location /socket.io/` y `proxy_buffering off`
- [x] `app/__init__.py` CSP con `ws:`/`wss:` en `connect-src`
- [x] `app/routes/api_notificaciones.py` emite `notif:read` / `notif:read_all`

### Frontend

- [x] `socket.io-client@^4.8.1` en `package.json`
- [x] `vercel.json` con `installCommand` legacy-peer-deps
- [x] `vercel.json` CSP con `wss://*.skilledmx.cloud` en `connect-src`
- [x] `SocketProvider` envolviendo `<App />` en `main.jsx`
- [x] `getServerOrigin()` lee `VITE_API_URL`
- [x] `auth` como función para refresh de token
- [x] `transports: ['polling', 'websocket']`
- [x] `NotificacionesBell` consume socket + fallback polling 2 min
- [x] `.env.local` en dev (gitignored)

### Producción

- [ ] `pip install -r requirements.txt` ejecutado en `/opt/nominas`
- [ ] `Gunicorn .config` actualizado en systemd (`/etc/systemd/system/`)
- [ ] `nginx.config` actualizado y `nginx -t` ok
- [ ] `systemctl reload nginx`
- [ ] `systemctl restart nominas`
- [ ] `curl http://localhost:8000/socket.io/?EIO=4&transport=polling` devuelve 200
- [ ] Push del frontend a Vercel
- [ ] Verificar handshake en DevTools del browser apuntando a `https://app.skilledmx.cloud/socket.io/`
- [ ] Probar push end-to-end (marcar notif desde otra pestaña)

---

## Diagnóstico de problemas comunes

| Síntoma                                              | Causa probable                                                  | Fix                                                    |
|------------------------------------------------------|-----------------------------------------------------------------|--------------------------------------------------------|
| `connect_error: server error` en consola             | Socket apunta al SPA en vez del backend                         | Verificar `VITE_API_URL` en build de prod              |
| Backend log: `"GET /socket.io/... HTTP/1.1" 400`     | Proxy stripeó headers `Upgrade`/`Sec-WebSocket-Key`             | Añadir bloque `/socket.io/` a nginx con `Upgrade`      |
| `AttributeError: property 'session' has no setter`   | Flask 3.1 + Flask-SocketIO 5.4.x (incompat)                     | `manage_session=False` (ya aplicado)                   |
| Socket se cae cada 60 s                              | `proxy_read_timeout` muy bajo en nginx                          | Subir a 3600 s en `/socket.io/`                        |
| Notifs llegan cada 2 min en vez de instantáneas      | Socket no conectó → fallback polling                            | Revisar `connect_error` en Console                     |
| `Invalid frame header` (WS)                          | Werkzeug dev server tiene problemas con upgrade WS              | OK en dev (cae a polling). En prod no aplica (eventlet)|
| Vercel build falla con `ERESOLVE`                    | Peer-dep entre Vite v8 y plugins                                | `"installCommand": "npm install --legacy-peer-deps"`   |

---

## ¿Está listo para producción?

**Sí**, con dos asuncionrs que conviene validar antes:

1. **Cloudflare**: WebSocket está activado por default; si tu plan/zona tiene algún flag desactivado, hay que prenderlo.
2. **El `.env` de producción ya tiene `REDIS_URL`, `CORS_ORIGINS` y `SECRET_KEY`** (verificado en local — asumimos paridad en prod).

El sistema degrada con gracia:
- Sin backend WS → frontend cae a polling cada 2 min, sigue funcionando.
- Sin nginx Upgrade → polling-only (long-poll), sigue funcionando con más latencia.
- Sin Redis → un solo worker activo, sigue funcionando con menos escalabilidad.
- Token expirado → reconnect automático con token fresco.

No hay punto de falla que tumbe la app.
