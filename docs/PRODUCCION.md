# Producción

Cadena: **Cloudflare Tunnel → Nginx (127.0.0.1:80) → Gunicorn (127.0.0.1:8000) → Flask**.

Archivos de despliegue en la raíz del repo: [`nginx.config`](../nginx.config), [`gunicorn.serviceee`](../gunicorn.serviceee) (se instala como `nominas.service`), [`Dockerfile`](../Dockerfile) y [`docker-compose.prod.yml`](../docker-compose.prod.yml). El VPS todavía corre con systemd y venv; la migración a Docker está en [`PENDIENTE_PRODUCCION.md`](./PENDIENTE_PRODUCCION.md).

---

## Gunicorn

El unit instalado en prod se llama **`nominas.service`** (el archivo del repo es `gunicorn.serviceee`):

```bash
sudo cp gunicorn.serviceee /etc/systemd/system/nominas.service
sudo systemctl daemon-reload
sudo systemctl enable --now nominas
sudo systemctl status nominas
```

Resumen del unit (ver el archivo para los comentarios completos):

- **4 workers gevent (`GeventWebSocketWorker`) × 1000 conexiones**: WebSocket real (upgrade HTTP→WS). gthread no implementa el upgrade y eventlet es incompatible con psycopg3 (ver [`MIGRACION_EVENTLET_A_GEVENT.md`](./MIGRACION_EVENTLET_A_GEVENT.md) y [`DEPLOY_GEVENT.md`](./DEPLOY_GEVENT.md)).
- **`SOCKETIO_ASYNC_MODE=gevent`**: activa el `monkey.patch_all()` al inicio de `run.py` y el modo gevent de Flask-SocketIO.
- **`--max-requests 1000` + jitter**: recicla workers para evitar leaks de pandas/openpyxl/xhtml2pdf.
- **`--forwarded-allow-ips=127.0.0.1`**: solo confía en `X-Forwarded-*` desde nginx local — cierra el spoofing de `CF-Connecting-IP`.
- **`--access-logformat`** custom: imprime `X-Real-IP` (no `127.0.0.1`) y el tiempo de request.
- **Hardening systemd**: `ProtectSystem=strict`, `NoNewPrivileges`, `CapabilityBoundingSet=` (drop all), `MemoryDenyWriteExecute=yes` (si gunicorn no arranca tras tocar gevent, lee el comentario de esa línea en el unit).

---

## Nginx

```bash
sudo cp nginx.config /etc/nginx/sites-available/skilled
sudo ln -s /etc/nginx/sites-available/skilled /etc/nginx/sites-enabled/skilled
sudo nginx -t && sudo systemctl reload nginx
```

Cubre:

- Rate-limit por IP (`api_general` 30/s, `api_auth` 30/min) como segunda capa.
- Real IP de Cloudflare (`set_real_ip_from` + `real_ip_header CF-Connecting-IP`) y confianza en loopback para el túnel.
- Anti-Slowloris (timeouts agresivos) y anti request smuggling (`Connection ""`).
- Bloque dedicado para Socket.IO (`/socket.io/`) con `Upgrade`/`Connection` y `proxy_read_timeout 3600s`.
- Whitelist de métodos HTTP y bloqueo de paths comúnmente escaneados (`.env`, `wp-login.php`, etc.).
- Logs con upstream y tiempos en `/var/log/nginx/skilled_api.access.log`.
- Headers de seguridad delegados a Flask (única fuente de verdad).

CORS se maneja en Flask (`flask_cors` + `CORS_ORIGINS`); no dupliques `Access-Control-*` en nginx.

---

## Frontend en Vercel

1. Importar el repo `plantilla-frontend/` en Vercel (Vite auto-detect vía `vercel.json`).
2. Env vars: `VITE_API_URL=https://api.tu-dominio.com/api`.
3. Push → deploy automático.

El `.env` del backend debe tener:

```env
CORS_ORIGINS=https://<proyecto>.vercel.app,https://app.skilled.com.mx
RT_COOKIE_SAMESITE=None
FLASK_ENV=production
USE_X_ACCEL_REDIRECT=true
```

---

## Checklist post-deploy

```bash
curl https://api.tu-dominio.com/health                          # {"status":"ok"}
sudo journalctl -u nominas -n 20 --no-pager                     # IPs reales, no 127.0.0.1
sudo tail -n 20 /var/log/nginx/skilled_api.access.log           # upstream=127.0.0.1:8000 request_time=...
```

En `/etc/cloudflared/config.yml` usa `protocol: http2`: con QUIC los WebSockets de Socket.IO se degradan (ver [`WEBSOCKETS_Y_DEPLOY.md`](./WEBSOCKETS_Y_DEPLOY.md)).

Para el login end-to-end desde el dominio de Vercel: DevTools → Network → la respuesta de `/api/auth/login` debe incluir `Set-Cookie: skilled_rt=...; SameSite=None; Secure`.
