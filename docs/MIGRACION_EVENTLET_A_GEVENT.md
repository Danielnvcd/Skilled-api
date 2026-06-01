# Migración eventlet → gevent (opcional, para más tarde)

**Estado actual**: la rama tiene `--worker-class eventlet` + `psycopg[binary]==3.3.4` configurado en `Gunicorn .config` y `requirements.txt`, **pendiente de deploy**. Antes de este cambio prod corría con `gthread` (ver comentario en `Gunicorn .config` línea 23).

**Por qué este documento existe**: `eventlet` + `psycopg3` no es la combinación más probada del ecosistema. Si después del deploy vemos en monitoreo queries colgándose, conexiones perdidas, o errores raros en SQLAlchemy bajo carga, la migración correcta es a `gevent` + `psycogreen` (parche oficial para que psycopg respete los greenlets de gevent).

No es bloqueante para el deploy inicial — primero subir el cambio actual a eventlet, observar, y solo migrar si hace falta. Documentado para tenerlo listo si llega el momento.

---

## Cuándo migrar

**Primero hay que hacer el deploy de la versión actual (eventlet)**. Síntomas a vigilar en los primeros días tras ese deploy (`journalctl -u nominas -f`):

- `psycopg.OperationalError: connection ... could not be received`
- Latencias REST de >2 s sin razón aparente
- `engineio.async_drivers.eventlet.RuntimeError`
- Workers reiniciados por `WORKER TIMEOUT` con frecuencia
- WebSocket disconnects con `transport close` cuando hay query a Postgres en curso

Si no aparece nada de esto en 1-2 semanas de uso real, **no migres** — el setup actual está OK para esta carga.

---

## Por qué gevent es más maduro para esta stack

| Aspecto                | eventlet                                | gevent                                       |
|------------------------|------------------------------------------|----------------------------------------------|
| Edad                   | 2008                                     | 2009                                         |
| Mantenimiento activo   | Menos releases, soporte tardío de 3.12+  | Releases regulares, soporte rápido de 3.12+  |
| psycopg2               | Funciona con monkey_patch genérico       | Funciona perfecto con `psycogreen.gevent`    |
| psycopg3               | Funciona en teoría — poca prueba real    | Soportado vía `psycogreen.gevent.patch_psycopg()` |
| WebSocket              | Built-in                                 | Necesita `gevent-websocket`                  |
| Flask-SocketIO docs    | Soporta ambos por igual                  | Mismo                                         |

El punto clave: **psycogreen explícitamente parcha el driver de Postgres** para ceder el control entre operaciones de I/O. Con eventlet dependes de que el monkey-patch genérico de `socket` haga lo correcto a nivel de `libpq` — funciona, pero no es código probado en miles de deploys como gevent + psycogreen.

---

## Cambios necesarios

### 1. `requirements.txt`

```diff
- eventlet==0.36.1
+ gevent==24.11.1
+ gevent-websocket==0.10.1
+ psycogreen==1.0.2
```

### 2. `app/realtime.py`

Cambiar default y aceptar `'gevent'` como modo:

```python
# No requiere cambio si SOCKETIO_ASYNC_MODE viene del env.
# El default sigue siendo 'threading' para dev.
async_mode = os.environ.get('SOCKETIO_ASYNC_MODE', 'threading')
```

### 3. Parchear psycopg al arranque

Agregar al inicio de `run.py` (ANTES de importar la app):

```python
# Solo aplica cuando gunicorn arranca con worker-class gevent.
# En dev (python run.py) este patch no corre porque gevent no se importa.
import os
if os.environ.get('SOCKETIO_ASYNC_MODE') == 'gevent':
    from gevent import monkey
    monkey.patch_all()
    from psycogreen.gevent import patch_psycopg
    patch_psycopg()

from app import create_app, socketio
app = create_app()

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000, debug=False, allow_unsafe_werkzeug=True)
```

**Importante**: el monkey-patch DEBE ejecutarse antes de que cualquier otro módulo importe `socket`, `ssl`, `time`, `select` etc. Por eso va al tope del archivo.

### 4. `Gunicorn .config`

```diff
ExecStart=/opt/nominas/venv/bin/gunicorn \
    --workers 4 \
-   --worker-class eventlet \
+   --worker-class geventwebsocket.gunicorn.workers.GeventWebSocketWorker \
    --worker-connections 1000 \
    ...

-Environment="SOCKETIO_ASYNC_MODE=eventlet"
+Environment="SOCKETIO_ASYNC_MODE=gevent"
```

El worker class de `gevent-websocket` añade soporte nativo para el upgrade WS encima del worker estándar de gevent. Sin él, gevent solo cubre HTTP y el upgrade falla.

### 5. nginx, frontend, vercel.json

**Sin cambios**. La capa de transporte (Socket.IO sobre WS / long-poll) es la misma; solo cambia el motor async del backend.

### 6. Comentario en `app/realtime.py` (líneas ~50-53)

El comentario actual menciona `eventlet` como ejemplo del modo async en prod. Actualizar a `gevent` para que no quede desactualizado:

```diff
- # Dev usa `threading` (Werkzeug `socketio.run`). Prod monta gunicorn con
- # `--worker-class eventlet`, que hace monkey_patch automáticamente; ahí
- # ponemos SOCKETIO_ASYNC_MODE=eventlet en el systemd unit para que la app
- # use las primitivas async correctas.
+ # Dev usa `threading` (Werkzeug `socketio.run`). Prod monta gunicorn con
+ # `--worker-class geventwebsocket.gunicorn.workers.GeventWebSocketWorker`;
+ # el monkey_patch + patch_psycopg se hacen explícitamente en run.py antes
+ # de importar la app. Ponemos SOCKETIO_ASYNC_MODE=gevent en el systemd
+ # unit para que la app use las primitivas async correctas.
```

No es funcional — solo documentación — pero mantiene sincronizada la pista que dejas para el próximo que lea el archivo.

---

## Pasos de deploy de la migración

```bash
# En el server
cd /opt/nominas
git pull  # con los cambios de requirements.txt, run.py, Gunicorn .config
source venv/bin/activate

# Las nuevas deps
pip install -r requirements.txt
pip uninstall eventlet -y  # ya no se usa

# Validar localmente que arranca
SOCKETIO_ASYNC_MODE=gevent python run.py
# Ctrl+C después de ver "Running on http://0.0.0.0:5000"

# Aplicar systemd
sudo cp "Gunicorn .config" /etc/systemd/system/nominas.service
sudo systemctl daemon-reload
sudo systemctl restart nominas

# Smoke test
curl -i "http://localhost:8000/socket.io/?EIO=4&transport=polling"
# 200 + sid

# Mirar logs por 1-2 min
sudo journalctl -u nominas -f
```

Si algo rompe, rollback es trivial: `git revert` el commit, `pip install eventlet==0.36.1`, `systemctl restart nominas`.

---

## Validación post-migración

Después de 30 min de tráfico real:

```bash
# ¿Hay errores de psycopg en logs?
sudo journalctl -u nominas --since "30 min ago" | grep -iE "psycopg|connection|timeout"

# ¿Cuántos workers están vivos?
ps aux | grep gunicorn | wc -l  # debe ser 5 (1 master + 4 workers)

# ¿Latencia de un endpoint pesado?
time curl -H "Authorization: Bearer $TOKEN" https://app.skilledmx.cloud/api/dashboard
```

Si todo sigue sano, la migración fue exitosa. Si aparece algo raro, rollback y abrir issue con los logs.

---

## Por qué no migrar ahora mismo

1. **Si funciona, no lo toques.** El setup actual con eventlet probablemente cubre tu carga sin problema.
2. **Cada cambio de infra introduce riesgo.** Mejor concentrarlo cuando hay evidencia de necesidad.
3. **Aprendes más del comportamiento real.** Ver cómo se comporta eventlet en tu carga te da datos para decidir.

Esta nota queda aquí para tener todo listo si llegado el caso vemos los síntomas listados arriba.
