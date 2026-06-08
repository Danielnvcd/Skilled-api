# Fix — WebSocket no reconecta al abrir la app en la mañana

Diagnóstico y solución del síntoma: **cerrar todo el browser por la noche, abrir la app al día siguiente, el WebSocket muestra "terminado" y hacen falta varios F5 para que reconecte**.

Complemento de [`FIX_WEBSOCKET_TOKEN_EXPIRADO.md`](FIX_WEBSOCKET_TOKEN_EXPIRADO.md), que cubre el caso de **pestaña en background** (no cierre total del browser).

---

## Síntoma

- En la noche: app funciona, WS estable (handshake `101 Switching Protocols`).
- Usuario cierra todas las pestañas / el browser entero.
- Al día siguiente abre la app fresca → indicador muestra `terminado` o `desconectado`.
- Recargar (F5) **una vez no alcanza**; hay que recargar 2-5 veces hasta que el WS conecta.
- Una vez conectado, todo funciona normal.

REST sigue funcionando durante este intervalo (sólo el WS está roto).

---

## Causa raíz — dos bugs combinados

### Bug 1 — Race condition con el token al abrir el socket

`SocketContext.jsx` abría el socket inmediatamente con el token que había en `localStorage` (vencido desde la noche anterior). El backend rechazaba el handshake con `token_expired`. El handler `connect_error` disparaba `performRefresh()` de forma asíncrona, pero **socket.io-client ya había programado el siguiente intento** y mandaba otra vez el token viejo, porque el callback de `auth` lee `localStorage` cada handshake pero el refresh aún no había terminado de escribir el token nuevo.

Si los 10 reintentos se agotaban antes que `performRefresh()` terminara, el socket entraba en `reconnect_failed`. F5 creaba un socket nuevo… que repetía la misma carrera. Por eso "varias F5 hasta que funciona".

### Bug 2 — `Manager` cacheado con `skipReconnect=true` permanente

Documentado en [socket.io-client #733](https://github.com/socketio/socket.io-client/issues/733):

> *"When you call disconnect on a manager, it sets the skipReconnection flag to true, but nothing resets this flag to false, which means your socket will never reconnect when you open new sockets to the server."*

socket.io-client **cachea el `Manager` por URL**. Si un socket entró en `reconnect_failed` y llamó `s.disconnect()`, el Manager queda con `skipReconnect=true` para siempre. El siguiente `io(url)` con la misma URL devuelve un socket sobre el **mismo Manager muerto** — la reconexión sigue rota.

Esto explica por qué a veces incluso después de F5 no funcionaba: el Manager cacheado en el módulo de socket.io-client sobrevive entre re-mounts de React (módulo singleton), aunque no entre F5s completos del browser. Pero combinado con el race del Bug 1, el resultado era impredecible.

---

## Fix aplicado

Tres cambios en `src/context/SocketContext.jsx` (frontend en `C:\Users\ppedo\OneDrive\Documentos\plantilla-frontend`).

### 1. Helper `decodeJwtExp(token)`

Decodifica el `exp` del JWT sin verificar firma (sólo necesitamos saber si está vencido para decidir si refrescar).

```js
function decodeJwtExp(token) {
  if (!token || typeof token !== 'string') return null
  try {
    const payload = token.split('.')[1]
    if (!payload) return null
    const json = atob(payload.replace(/-/g, '+').replace(/_/g, '/'))
    const obj = JSON.parse(json)
    return typeof obj.exp === 'number' ? obj.exp : null
  } catch {
    return null
  }
}
```

### 2. Pre-refresh ANTES de abrir el socket

El `useEffect` envuelve la creación del socket en una función `setup` async. Antes de llamar `io(...)`:

```js
const tokenNow = localStorage.getItem('token')
const exp = decodeJwtExp(tokenNow)
const needsRefresh = !exp || exp * 1000 - Date.now() < 60_000
if (needsRefresh) {
  try {
    await performRefresh()
  } catch {
    return  // RT también murió — no abrimos socket
  }
}
if (cancelled) return
s = io(getServerOrigin(), { ... })
```

Esto elimina el race: cuando llamamos `io(...)`, el token en localStorage ya es nuevo. Primer handshake usa token fresco → 101 OK al primer intento.

Si el refresh falla (RT también vencido), **no abrimos el socket** — el usuario está efectivamente deslogueado y el interceptor de axios manejará el bounce al login.

### 3. `forceNew: true` + recreación en `reconnect_failed`

```js
s = io(getServerOrigin(), {
  // ...resto de opciones...
  reconnectionAttempts: 10,
  timeout: 20_000,
  forceNew: true,  // ← clave
})

s.on('reconnect_failed', () => {
  setConnected(false)
  if (cancelled) return
  setTimeout(() => {
    if (!cancelled) setReconnectTrigger((t) => t + 1)
  }, 2000)
})
```

`forceNew: true` evita el caché del Manager — cada `io(...)` crea uno nuevo, sin heredar `skipReconnect=true`.

`setReconnectTrigger` es un contador en `useState` agregado a las deps del effect. Cuando `reconnect_failed` lo incrementa, el `useEffect` se re-corre: el cleanup destruye el socket podrido, el setup crea uno nuevo con pre-refresh + Manager fresco.

### Patrón completo del `useEffect`

```js
useEffect(() => {
  if (!user) { setSocket(null); setConnected(false); return }
  if (!localStorage.getItem('token')) return

  let cancelled = false
  let s = null
  let heartbeatId = null
  let appPingId = null
  let cleanupListeners = () => {}

  const setup = async () => {
    // Pre-refresh si JWT vence en <60s
    const exp = decodeJwtExp(localStorage.getItem('token'))
    const needsRefresh = !exp || exp * 1000 - Date.now() < 60_000
    if (needsRefresh) {
      try { await performRefresh() } catch { return }
    }
    if (cancelled) return

    s = io(getServerOrigin(), { /* ...opts, forceNew: true... */ })
    // ...listeners (connect, disconnect, auth:force_logout, reconnect_failed,
    //              connect_error)...
    // ...intervals (heartbeat 90s, app:ping 20s con ack timeout 8s)...
    // ...listeners de visibilitychange / online → cleanupListeners...

    setSocket(s)
  }

  setup()

  return () => {
    cancelled = true
    cleanupListeners()
    if (heartbeatId) clearInterval(heartbeatId)
    if (appPingId) clearInterval(appPingId)
    if (s) { s.removeAllListeners(); s.disconnect() }
    setSocket(null)
    setConnected(false)
  }
}, [user?.id, user?.role, reconnectTrigger])
```

---

## Lo que NO es (descartado en la investigación)

| Hipótesis | Estado | Por qué |
|-----------|--------|---------|
| Cloudflare timeout 100s idle | ❌ No aplica | El handshake es **nuevo**, no idle. Y ya existe `app:ping` cada 20s para idle. |
| PostgreSQL pool stale | ❌ Ya configurado | `app/__init__.py:94` tiene `pool_pre_ping=True` + `pool_recycle=1800`. |
| Service Worker intercepta WS | ❌ Improbable | `vite.config.js` tiene `runtimeCaching: []`. WebSocket bypassea el SW por diseño. |
| Eventlet vs gthread | ❌ No es eso | Backend usa `geventwebsocket` correctamente (ver `gunicorn.serviceee`). |
| Bug `Invalid frame header` ([#5404](https://github.com/socketio/socket.io/issues/5404)) | ❌ No aplica | Ese bug requiere socket vivo durante horas; aquí cerramos todo. |

---

## Hipótesis residuales (si el fix no resuelve completamente)

Si tras desplegar el fix el síntoma persiste, descartado el cliente, mirar:

### A) Cloudflared QUIC fallback

[cloudflared #1534](https://github.com/cloudflare/cloudflared/issues/1534): una vez que cloudflared cae a HTTP/2 nunca reintenta QUIC. Si overnight perdió QUIC, primer request en la mañana puede tardar mientras renegocia.

```bash
sudo journalctl -u cloudflared --since "today 06:00" --until "today 09:00"
```

### B) Gunicorn worker cold start

Si systemd recicla los workers overnight, el primer request los inicializa (cold start de gevent + carga de SQLAlchemy + Redis).

```bash
sudo journalctl -u nominas --since "today 06:00" --until "today 09:00" | grep -E "Booting|Worker"
```

### C) Redis connection drop (si `REDIS_URL` está set como message_queue)

`flask-socketio` usa el `message_queue` para emits cross-worker. Si la conexión a Redis murió overnight, los emits fallan pero **el handshake debería seguir funcionando** (no depende de Redis). Si los emits no llegan, mirar `redis-cli` y reiniciar.

---

## Verificación

### Build

```bash
cd C:/Users/ppedo/OneDrive/Documentos/plantilla-frontend
npx vite build
# ✓ built in 3.29s — sin errores
```

### Unit test de `decodeJwtExp`

| Token | exp resultante | needsRefresh |
|-------|---------------|--------------|
| Vence en 10min | `1780887949` | `false` (abre directo) |
| Vencido hace 100s | `1780887249` | `true` (refresca primero) |
| Vence en 30s | `1780887379` | `true` (umbral 60s) |
| Malformado | `null` | `true` (conservador) |
| Empty / null | `null` | (early return) |

### Test en vivo

1. Login normal en la app.
2. Manualmente cambiar el token en localStorage por uno vencido (o esperar 12+ horas).
3. F5 una vez.
4. **Esperado:** el WS conecta al primer intento (no 3-5 F5s).
5. Network tab → `socket.io/?EIO=4&transport=websocket` → status `101`.

---

## Archivos modificados

- `plantilla-frontend/src/context/SocketContext.jsx` — agregado `decodeJwtExp`, `reconnectTrigger` state, `setup async`, `forceNew: true`, recreación en `reconnect_failed`.

Sin cambios en backend.

---

## Referencias

- [socket.io-client #733 — Disconnecting the manager disables reconnects](https://github.com/socketio/socket.io-client/issues/733)
- [socket.io-client #1179 — Automatic reconnect stopped after explicit connection attempt](https://github.com/socketio/socket.io-client/issues/1179)
- [socket.io #3358 — Reconnect never happens (race conditions)](https://github.com/socketio/socket.io/issues/3358)
- [Socket.IO Client Options v4 — forceNew & manager caching](https://socket.io/docs/v4/client-options/)
- [How to use Socket.IO with JSON Web Tokens](https://socket.io/how-to/use-with-jwt)
- [Cloudflare Tunnel — Common errors](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/troubleshoot-tunnels/common-errors/)
- [cloudflared #1534 — QUIC fallback to HTTP/2](https://github.com/cloudflare/cloudflared/issues/1534)
