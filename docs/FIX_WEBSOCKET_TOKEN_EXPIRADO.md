# Fix — WebSocket falla tras inactividad (token expirado en reconnect)

Diagnóstico y solución de los errores `WebSocket connection to 'wss://app.skilledmx.cloud/socket.io/...' failed` que aparecen **después de tiempo de inactividad** en la pestaña del SPA, aunque el WS funcione bien al abrir la app.

---

## Síntoma

```
index-Xwuz2LkV.js:192 WebSocket connection to 'wss://app.skilledmx.cloud/socket.io/?EIO=4&transport=websocket' failed:
```

…en loop, repetido cada pocos segundos. Patrón observado: al abrir la SPA todo funciona; tras un rato (mins/horas) en background, empieza el spam.

---

## Lo que NO es

Confirmado por los siguientes chequeos en el server:

```bash
# 1. Worker class correcto
sudo systemctl cat nominas | grep -E "worker-class|SOCKETIO_ASYNC_MODE"
#   --worker-class geventwebsocket.gunicorn.workers.GeventWebSocketWorker
#   Environment="SOCKETIO_ASYNC_MODE=gevent"

# 2. Backend responde Socket.IO directo
curl -i "http://127.0.0.1:8000/socket.io/?EIO=4&transport=polling"
#   HTTP/1.1 200 OK
#   0{"sid":"...","upgrades":["websocket"],...}

# 3. Nginx reenvía bien (con Host del dominio público)
curl -i "http://127.0.0.1/socket.io/?EIO=4&transport=polling" -H "Host: app.skilledmx.cloud"
#   HTTP/1.1 200 OK + sid

# 4. Nginx tiene el location
sudo nginx -T | grep -A2 "location /socket.io"
#   location /socket.io/ { ... }
```

Por tanto **no es** problema de gunicorn, nginx ni del worker class. El backend está bien configurado tras la migración a gevent (ver `DEPLOY_GEVENT.md`).

---

## Causa raíz (hipótesis principal)

**Token JWT expirado en el momento del reconnect del WebSocket.**

Secuencia:

1. SPA carga → JWT fresco en `localStorage.token` → handshake OK → WS conectado.
2. Usuario deja la pestaña en background (cambia de pestaña, no minimiza).
3. Chrome **throttlea `setTimeout`** en pestañas inactivas (típicamente a 1/min).
4. El refresh proactivo de `axios.js` (programado ~60s antes de expirar) no dispara a tiempo.
5. Access token expira (TTL = **20 min**, `app/routes/api_auth.py:33`).
6. El WS se cae por algún motivo (CF Tunnel recicla, ping perdido, red).
7. socket.io-client intenta reconectar. El callback `auth: (cb) => cb({ token: localStorage.getItem('token') })` lee el token **viejo expirado**.
8. Backend rechaza el handshake en `app/realtime.py:_on_connect` (era `return False`, ahora `ConnectionRefusedError`).
9. Como `transports: ['websocket']` (sin polling fallback), no hay transport alternativo → loop infinito de `WebSocket failed`.

### Evidencia que la respalda

- Los logs de nginx **no muestran** ningún request a `/socket.io/` durante el fallo → el cliente está fallando antes de que el upgrade complete, consistente con handshake rechazado.
- El refresh proactivo funciona en pestaña activa (token fresco al volver) pero falla en background por el throttling.
- `withCredentials: true` + setup cross-site (Vercel `erp.skilledmx.cloud` + API `app.skilledmx.cloud`) no afecta porque la auth del socket va por `auth.token`, no por cookie.

### Grieta de la hipótesis

`axios.js:99` ya tiene un listener `visibilitychange` que refresca el token al volver a la pestaña. **Si funciona bien**, el bug debería autocurarse en 1-2 reintentos del socket — no quedarse en loop infinito.

Posible explicación: race condition entre el refresh proactivo (async) y los reintentos automáticos de socket.io (cada 1-10s). Si el socket reintenta más rápido que `performRefresh()` resuelve, los primeros intentos fallan con el token viejo todavía. Con el fix aplicado, el `connect_error` listener dispara el refresh explícitamente y queda más robusto.

---

## Fix aplicado

### 1. Frontend — `plantilla-frontend/src/api/axios.js`

Exportar `performRefresh` (era función interna del módulo):

```js
export function performRefresh() { ... }
```

### 2. Frontend — `plantilla-frontend/src/context/SocketContext.jsx`

Tres cambios:

**a)** Import:
```js
import { performRefresh } from '../api/axios'
```

**b)** `connect_error` ahora detecta motivos de auth y dispara refresh con guard anti-thundering-herd:
```js
let refreshingFromSocket = false
s.on('connect_error', async (err) => {
  setConnected(false)
  const msg = (err && (err.message || err.data)) || ''
  const looksLikeAuth =
    typeof msg === 'string' &&
    /token|auth|unauth|forbidden|refused/i.test(msg)
  if (!looksLikeAuth || refreshingFromSocket) return
  refreshingFromSocket = true
  try {
    await performRefresh()
  } catch {
    // Refresh falló — el interceptor de axios bouncea al login en el próximo HTTP.
  } finally {
    refreshingFromSocket = false
  }
})
```

La reconexión automática (`reconnection: true`, ya configurado) toma el nuevo token vía el callback de `auth` ya existente.

**c)** `visibilitychange` para reconectar manualmente al volver la pestaña:
```js
const onVisible = async () => {
  if (document.visibilityState !== 'visible') return
  if (s.connected) return
  try { await performRefresh() } catch {}
  try { s.connect() } catch {}
}
document.addEventListener('visibilitychange', onVisible)
```

Cleanup actualizado para remover el listener.

### 3. Backend — `app/realtime.py`

Reemplazar `return False` (rechazo silencioso) por `ConnectionRefusedError` con motivo identificable:

```python
from socketio.exceptions import ConnectionRefusedError as SocketIOConnectionRefusedError

@socketio.on('connect')
def _on_connect(auth):
    token = (auth or {}).get('token') if isinstance(auth, dict) else None
    if not token:
        raise SocketIOConnectionRefusedError('token_missing')

    payload = _decode_token(token, 'access')
    if not payload:
        raise SocketIOConnectionRefusedError('token_expired')

    try:
        user_id = int(payload['sub'])
    except (KeyError, TypeError, ValueError):
        raise SocketIOConnectionRefusedError('token_invalid')

    user = User.query.get(user_id)
    if not user:
        raise SocketIOConnectionRefusedError('user_not_found')
    if (user.password_version or 1) != payload.get('pv', 1):
        raise SocketIOConnectionRefusedError('token_revoked')
```

El motivo llega al cliente como `err.message`, y la regex `/token|auth|unauth|forbidden|refused/i` del SocketContext matchea cualquiera de ellos para disparar el refresh.

---

## Deploy

```bash
# Backend
cd /opt/nominas
sudo -u sistemanominas git pull
sudo systemctl restart nominas

# Frontend
# Push a la rama conectada con Vercel; auto-deploy.
```

Verificación de import del backend:
```bash
/opt/nominas/venv/bin/python -c "from socketio.exceptions import ConnectionRefusedError; print('OK')"
/opt/nominas/venv/bin/python -c "import app.realtime; print('OK')"
```

---

## Validación honesta (recomendada después del deploy)

Aún no se confirmó al 100% la causa raíz con un test reproducible. Para validar:

1. Abrir la SPA, dejar DevTools abierto (Network + Console).
2. Cambiar a otra pestaña (no minimizar — eso garantiza throttling de `setTimeout`).
3. Esperar **30 min** (token expira a los 20).
4. Volver a la pestaña.
5. Ejecutar en consola **inmediatamente**:

   ```js
   const t = localStorage.getItem('token').split('.')[1]
   const exp = JSON.parse(atob(t)).exp * 1000
   console.log('expirado:', exp < Date.now(), 'hace', Math.round((Date.now()-exp)/1000), 's')
   ```

6. Mirar Network: ¿hay `POST /api/auth/refresh`? ¿`socket.io/?EIO=4&transport=websocket` con status `101`?
7. En el server: `sudo journalctl -u nominas -f | grep -i socket` para confirmar que `socket: rechazado (token inválido)` ya no aparece, o aparece una vez y luego viene un connect exitoso.

### Tabla de diagnóstico

| Síntoma tras volver | Causa probable | Acción |
|---|---|---|
| Token expirado + POST refresh exitoso + WS 101 | El fix funciona | ✅ Listo |
| Token expirado + no hay POST refresh | El fix no se aplicó (revisar deploy) | Reverificar Vercel build |
| Token NO expirado pero loop de WS failed | No era token; ver "causas alternativas" abajo | Investigar CF Tunnel |
| Token expirado + POST refresh con 401 | Cookie `rt` también expiró (30d normal) | Re-login esperado |

---

## Causas alternativas no descartadas

Si tras validar resulta que **no era token**, considerar:

### a) Cloudflare Tunnel Free reciclando la conexión WS

CF Tunnel Free mantiene un canal HTTP/2 al edge de Cloudflare. Esporádicamente lo recicla (cada varias horas). Cuando ocurre, todos los WS activos se cortan.

**Mitigación**: bajar `ping_interval` a 10s en `app/realtime.py` (más chatter pero la conexión se mantiene viva):
```python
socketio.init_app(
    ...
    ping_interval=10,
    ping_timeout=30,
    ...
)
```

**Cuándo escalar a CF Pro**: no necesario en el caso típico. Free soporta WS oficialmente. Solo considerar si tras todos los fixes gratuitos sigue habiendo desconexiones frecuentes y el negocio lo justifica.

### b) `max-requests=1000` en gunicorn recicla workers

Cada vez que un worker recicla por `--max-requests`, los WS se cortan. Se ve como desconexiones esporádicas no relacionadas con tiempo en background.

**Mitigación**: subir a `--max-requests 5000 --max-requests-jitter 500` en `gunicorn.serviceee`.

### c) Bundle frontend cacheado en el navegador

`index-Xwuz2LkV.js` es un hash; si Vercel sirvió un bundle viejo o el browser lo cacheó, el código que falla puede ser de una versión previa.

**Mitigación**: hard refresh (Ctrl+F5), o verificar en Vercel que el último deploy tomó.

### d) Cookie `rt` cross-site bloqueada

Setup actual: SPA en `erp.skilledmx.cloud` + API en `app.skilledmx.cloud` = cross-site. La cookie del refresh token debe estar marcada `SameSite=None; Secure`. Si no, `performRefresh()` falla silenciosamente.

**Verificar**: `.env` en server debe tener `RT_COOKIE_SAMESITE=None`. Si dice `Lax`, los refreshes cross-site no envían la cookie.

---

## Referencias

- `docs/DEPLOY_GEVENT.md` — migración gunicorn gthread → geventwebsocket
- `docs/WEBSOCKETS_Y_DEPLOY.md` — arquitectura general de Socket.IO en el sistema
- `app/realtime.py` — capa Socket.IO server-side
- `plantilla-frontend/src/context/SocketContext.jsx` — provider del socket en el SPA
- `plantilla-frontend/src/api/axios.js` — refresh proactivo + interceptor reactivo
