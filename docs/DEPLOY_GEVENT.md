# Deploy — Migración a Gunicorn + gevent (WebSocket fix)

Pasos para subir a producción el cambio que arregla el WebSocket de Socket.IO,
migrando el worker de Gunicorn de `gthread` → `geventwebsocket`.

---

## Contexto

Síntoma observado en prod (`https://app.skilledmx.cloud`):

```
WebSocket connection to 'wss://app.skilledmx.cloud/socket.io/?EIO=4&transport=websocket' failed
```

…en loop infinito de reconexión, sin errores visibles en `journalctl -u gunicorn`.

**Causa raíz:** `--worker-class gthread` no implementa el upgrade HTTP → WebSocket.
Acepta el GET inicial y responde HTTP normal, pero no hace el switch de
protocolo. El navegador reporta "failed" y el frontend (que usa
`transports: ['websocket']`) queda sin transporte viable (el polling tampoco
sirve porque 4 workers sin sticky sessions rebotan los `sid`).

**Fix:** worker `geventwebsocket.gunicorn.workers.GeventWebSocketWorker` +
`SOCKETIO_ASYNC_MODE=gevent` + monkey-patch en `run.py`.

**Por qué gevent y no eventlet:** eventlet 0.36+ rompe psycopg3 al boot
(`TypeError: type 'Queue' is not subscriptable`). gevent no tiene ese problema
y psycopg3 ≥ 3.1.14 lo soporta nativamente (la app usa 3.3.4) — **no se
necesita psycogreen**.

---

## Cambios incluidos en el repo

| Archivo | Cambio |
|---|---|
| `requirements.txt` | `+ gevent==24.11.1`, `+ gevent-websocket==0.10.1` |
| `run.py` | Monkey-patch al inicio, gateado por `SOCKETIO_ASYNC_MODE=gevent` |
| `gunicorn.serviceee` | Worker class → `GeventWebSocketWorker`, sin `--threads`, `--worker-connections 1000`, `--graceful-timeout 60`, env `SOCKETIO_ASYNC_MODE=gevent` |
| `app/realtime.py` | Docstring actualizado |
| `plantilla-frontend/src/context/SocketContext.jsx` | Comentario actualizado |

---

## Pre-deploy — verificaciones rápidas

```bash
# En el servidor:
# 1. Confirmar versión de psycopg (debe ser ≥ 3.1.14)
sudo -u sistemanominas /opt/nominas/venv/bin/pip show psycopg | grep Version
# → Version: 3.3.4  ✅

# 2. Backup del unit actual por si algo sale mal
sudo cp /etc/systemd/system/gunicorn.service /etc/systemd/system/gunicorn.service.bak-pregevent
```

---

## Pasos de deploy

### 1. Pull del código

```bash
cd /opt/nominas
sudo -u sistemanominas git fetch origin
sudo -u sistemanominas git checkout Inventario   # o la rama que mergees a main
sudo -u sistemanominas git pull
```

### 2. Instalar dependencias nuevas

```bash
sudo -u sistemanominas /opt/nominas/venv/bin/pip install \
    gevent==24.11.1 \
    gevent-websocket==0.10.1
```

Verificar que la instalación no rompió otras deps:

```bash
sudo -u sistemanominas /opt/nominas/venv/bin/pip check
# debe imprimir "No broken requirements found."
```

### 3. Copiar el unit y recargar systemd

```bash
sudo cp /opt/nominas/gunicorn.serviceee /etc/systemd/system/gunicorn.service
sudo systemctl daemon-reload
sudo systemctl restart gunicorn
```

### 4. Validar arranque

```bash
sudo systemctl status gunicorn --no-pager
sudo journalctl -u gunicorn -n 50 --no-pager
```

Buscar estas líneas en los logs (todo correcto):

- `Starting gunicorn 23.0.0`
- `Using worker: geventwebsocket.gunicorn.workers.GeventWebSocketWorker`
- `Booting worker with pid: NNNN` (×4)
- `Conexión a Redis exitosa`
- `LEGACY_UI_ENABLED=false — solo se expone /api/* (modo SPA)`

**Sin** errores tipo:

- `ModuleNotFoundError: No module named 'gevent'` → la instalación de pip falló
- `PermissionError` o `mmap: Operation not permitted` → ver troubleshooting `MemoryDenyWriteExecute`
- `TypeError: ... not subscriptable` → no debería pasar, pero implicaría conflicto eventlet/psycopg

### 5. Validar desde el navegador

1. Abrir `https://erp.skilledmx.cloud` (la SPA)
2. DevTools → **Network** → filtro **WS**
3. Recargar (Ctrl+F5)
4. Debe aparecer una entrada `socket.io/?EIO=4&transport=websocket`
   con **Status 101 Switching Protocols** y un timer corriendo
5. DevTools → **Console**: **no** debe haber spam de
   `WebSocket connection failed`
6. Probar feature realtime: por ejemplo, abrir dos pestañas, modificar un
   trabajador y ver si la otra pestaña actualiza sin recargar

---

## Troubleshooting

### Síntoma: `Failed at step EXEC ... Permission denied` al arrancar

Causa: `MemoryDenyWriteExecute=yes` en el unit bloquea el `mmap` que
`greenlet` necesita para los stacks de coroutines.

Fix:

```bash
sudo systemctl edit gunicorn
```

Pegar:

```ini
[Service]
MemoryDenyWriteExecute=no
```

```bash
sudo systemctl restart gunicorn
```

(Pérdida de hardening aceptable a cambio de WebSocket funcional.)

### Síntoma: gunicorn arranca pero los logs muestran latencia altísima en queries

Causa posible (issue [psycopg#919](https://github.com/psycopg/psycopg/issues/919)):
degradación de psycopg3 + gevent en cargas pesadas.

Diagnóstico:

```bash
# Verificar que el monkey-patch sí se aplicó
sudo journalctl -u gunicorn | grep -i monkey
```

Si el problema persiste, considerar bajar a `--workers 1` con muchos
greenlets o abrir un issue. Como mitigación inmediata, rollback (ver abajo).

### Síntoma: el WS conecta pero se cae cada N requests

Causa: `--max-requests 1000` cuenta cada request como una unidad, incluido
WebSockets de larga duración. Cuando el worker recicla, los WS se cortan.

Si lo notas frecuente, subir el límite o desactivar:

```
--max-requests 5000 \
--max-requests-jitter 500 \
```

---

## Rollback de emergencia

Si algo sale mal y no logras diagnosticarlo en minutos:

```bash
# 1. Restaurar el unit anterior
sudo cp /etc/systemd/system/gunicorn.service.bak-pregevent \
        /etc/systemd/system/gunicorn.service
sudo systemctl daemon-reload
sudo systemctl restart gunicorn

# 2. Revertir el commit en el repo (opcional, el unit viejo ya
#    apunta a gthread; el monkey-patch en run.py solo se activa
#    si SOCKETIO_ASYNC_MODE=gevent, así que con el unit viejo
#    queda inerte. Pero por limpieza:)
cd /opt/nominas
sudo -u sistemanominas git revert HEAD --no-edit
sudo -u sistemanominas git push
```

Tras rollback, el WebSocket volverá a fallar (estado original) pero el
resto del API JSON funciona normal.

---

## Post-deploy — qué monitorear las primeras 24h

- `journalctl -u gunicorn -f` durante el primer arranque y un par de horas
- Métricas en Grafana / dashboard interno (latencia p95 de `/api/*`)
- Reportes manuales de usuarios sobre lentitud o desconexiones
- Consumo de RAM: `systemctl status gunicorn` (cada worker debería estar
  entre 200–400 MB; si crece sin parar, hay leak)

---

## Referencias

- [`docs/MIGRACION_EVENTLET_A_GEVENT.md`](MIGRACION_EVENTLET_A_GEVENT.md) — historia previa, por qué eventlet no es opción
- [`docs/WEBSOCKETS_Y_DEPLOY.md`](WEBSOCKETS_Y_DEPLOY.md) — arquitectura general de Socket.IO en este sistema
- [psycogreen README](https://github.com/psycopg/psycogreen) — confirma que psycopg3 ≥ 3.1.14 soporta gevent nativo
