# ═══════════════════════════════════════════════════════════════════════════
# Imagen de la API (Flask + Gunicorn/gevent).
#
# Construcción en dos etapas:
#
#   builder   instala las dependencias en un virtualenv propio. Trae
#             build-essential porque alguna dependencia puede llegar como
#             sdist y necesitar compilarse; ese compilador NO llega a la
#             imagen final.
#
#   runtime   copia el virtualenv ya armado y el código. Sin compilador, sin
#             caché de pip: menos peso y menos superficie de ataque.
#
# La misma imagen sirve para local y para el VPS. Lo único que cambia entre
# entornos son las variables de entorno y el `command` (ver los compose).
# ═══════════════════════════════════════════════════════════════════════════

# ── Etapa 1: dependencias ──────────────────────────────────────────────────
FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

# cairo NO es opcional aunque no aparezca en requirements.txt:
#   xhtml2pdf → svglib 1.6.0 → rlPyCairo → pycairo
# y pycairo no publica wheels para Linux, así que se compila desde fuente y
# necesita las cabeceras de cairo y pkg-config para encontrarlas. En Windows
# pasaba desapercibido porque ahí sí hay wheel.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libcairo2-dev \
        pkg-config \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copiar SOLO los archivos de dependencias antes que el código: mientras
# requirements.txt no cambie, Docker reutiliza esta capa y el build tarda
# segundos en vez de minutos.
#
# El `-c constraints.txt` es el mismo que usan el CI y el VPS. Sin él, un pin
# transitivo distinto haría que la imagen no sea el entorno que se probó.
COPY requirements.txt constraints.txt ./
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip && \
    pip install -r requirements.txt -c constraints.txt


# ── Etapa 2: runtime ───────────────────────────────────────────────────────
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PATH="/opt/venv/bin:$PATH" \
    FLASK_APP=run.py \
    # En desarrollo el código se monta desde Windows, y ahí vienen carpetas
    # __pycache__ con .pyc compilados por el Python de Windows. Como la versión
    # es la misma (3.12), el intérprete del contenedor los da por buenos y los
    # carga: los tracebacks salían con rutas "C:\Users\...". Mandando la caché
    # a una ruta propia del contenedor, esos archivos se ignoran por completo.
    PYTHONPYCACHEPREFIX=/tmp/pycache

# libcairo2 (la librería, no las cabeceras): pycairo se compiló contra ella en
# la etapa anterior y la carga al importar. Sin esto, generar cualquier PDF con
# SVG revienta con "libcairo.so.2: cannot open shared object file".
RUN apt-get update && apt-get install -y --no-install-recommends \
        libcairo2 \
    && rm -rf /var/lib/apt/lists/*

# Usuario sin privilegios. El unit de systemd ya corría como `sistemanominas`
# con NoNewPrivileges y capabilities vacías; aquí se conserva esa idea: si
# alguien logra ejecución remota, no aterriza como root.
RUN useradd --create-home --uid 1000 nominas

COPY --from=builder /opt/venv /opt/venv

# Los scripts de arranque viven FUERA de /app a propósito: en desarrollo el
# compose monta el código del host sobre /app, y si el entrypoint estuviera
# ahí quedaría tapado por el bind mount (y perdería el bit de ejecución, que
# Windows no conserva).
COPY docker/entrypoint.sh docker/espera_db.py docker/bootstrap_esquema.py \
     docker/revision_actual.py /usr/local/bin/
RUN chmod +x /usr/local/bin/entrypoint.sh

WORKDIR /app
COPY --chown=nominas:nominas . .

# Directorios de estado. En los compose van montados como volúmenes; se crean
# igual para que la imagen arranque sola si alguien la corre sin compose.
RUN mkdir -p /app/uploads /app/data && chown -R nominas:nominas /app/uploads /app/data

USER nominas
EXPOSE 8000

# Sin curl en la imagen (no hace falta y es una dependencia menos): el sondeo
# se hace con la stdlib. `/health` es el mismo endpoint que verifica el CI
# después de cada despliegue.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if b'\"ok\"' in urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).read() else 1)"

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]

# Configuración de producción, calcada del unit de systemd (`gunicorn.serviceee`):
# mismo worker de gevent-websocket —el que sí hace el upgrade a WebSocket—,
# mismos límites de request y mismo reciclado de workers.
#
# Diferencias deliberadas con el unit:
#   --bind 0.0.0.0    dentro del contenedor; quien publica el puerto solo en
#                     127.0.0.1 es el compose, no la app.
#   sin --worker-tmp-dir /dev/shm   el /dev/shm de Docker son 64 MB y el
#                     tmpfs por defecto del contenedor ya vive en RAM.
#   sin --forwarded-allow-ips   el unit confiaba en 127.0.0.1 porque nginx
#                     corría en la misma máquina; dentro de Docker el proxy
#                     llega desde otra IP de la red bridge, así que el valor
#                     se define por entorno (env FORWARDED_ALLOW_IPS en los
#                     compose, donde sí se sabe quién es el proxy).
CMD ["gunicorn", \
     "--workers", "4", \
     "--worker-class", "geventwebsocket.gunicorn.workers.GeventWebSocketWorker", \
     "--worker-connections", "1000", \
     "--bind", "0.0.0.0:8000", \
     "--timeout", "120", \
     "--graceful-timeout", "60", \
     "--keep-alive", "5", \
     "--max-requests", "1000", \
     "--max-requests-jitter", "100", \
     "--limit-request-line", "4094", \
     "--limit-request-fields", "32", \
     "--limit-request-field_size", "8190", \
     "--access-logfile", "-", \
     "--access-logformat", "%({x-real-ip}i)s %(l)s %(u)s %(t)s \"%(r)s\" %(s)s %(b)s \"%(f)s\" \"%(a)s\" %(L)s", \
     "--error-logfile", "-", \
     "--capture-output", \
     "run:app"]
