#!/bin/sh
# ═══════════════════════════════════════════════════════════════════════════
# Arranque del contenedor de la API.
#
# Hace tres cosas y se quita de en medio:
#   1. Comprueba que las variables imprescindibles estén puestas.
#   2. Espera a la base de datos y, en desarrollo, aplica migraciones (en
#      producción son un paso aparte del despliegue — ver más abajo).
#   3. Cede el PID 1 al proceso real (`exec`), para que Gunicorn reciba
#      SIGTERM directo de Docker y pueda cerrar de forma limpia. Sin `exec`,
#      la señal se la queda este shell y Docker termina matando a la fuerza a
#      los 10 segundos, a mitad de las peticiones en curso.
# ═══════════════════════════════════════════════════════════════════════════
set -eu

# ── 0. ¿Qué nos han pedido arrancar? ───────────────────────────────────────
# Tres casos distintos:
#
#   gunicorn  el servidor. Espera la base y, si está permitido, migra.
#
#   flask     típicamente `docker compose run --rm api flask db upgrade`, el
#             paso de migración del despliegue. Necesita que la base esté
#             lista, pero NO debe migrar por su cuenta antes: eso es
#             justamente lo que se le está pidiendo hacer.
#
#   el resto  comandos sueltos (`python -c ...`, `sh`, `pytest`). Se ejecutan
#             tal cual: ni esperas ni migraciones ni exigir SECRET_KEY.
case "${1:-}" in
    gunicorn) es_servidor=si ;;
    flask)    es_servidor=no ;;
    *)
        echo "[entrypoint] Comando suelto ($1): sin espera de base ni migraciones."
        exec "$@"
        ;;
esac

# ── 1. Variables imprescindibles ───────────────────────────────────────────
# create_app() ya revienta sin SECRET_KEY, pero el error se pierde entre el
# ruido de los workers de Gunicorn reiniciándose. Mejor fallar aquí, claro.
if [ -z "${SECRET_KEY:-}" ]; then
    echo "[entrypoint] CRÍTICO: falta SECRET_KEY. Revisa el env_file del compose." >&2
    exit 1
fi

# ── 2. Base de datos ───────────────────────────────────────────────────────
python /usr/local/bin/espera_db.py

# Si esto no es el servidor (o sea: es `flask db upgrade`), ya está. La base
# responde, que es todo lo que necesitaba.
if [ "$es_servidor" = "no" ]; then
    echo "[entrypoint] Ejecutando: $*"
    exec "$@"
fi

# Migrar al arrancar es cómodo en DESARROLLO: levantar el stack en una máquina
# limpia es un solo comando.
#
# En PRODUCCIÓN va en `false` (ver docker-compose.prod.yml) y las migraciones
# son un paso aparte del despliegue. La razón: si una migración falla aquí, el
# contenedor no arranca, y con `restart: unless-stopped` se queda en bucle
# reintentándola con el servicio caído. Como paso separado, una migración que
# falle deja el contenedor viejo sirviendo y da tiempo a decidir.
#
# También hace falta `false` si algún día se corre más de una réplica: dos
# `flask db upgrade` a la vez compiten por la tabla alembic_version.
if [ "${APLICAR_MIGRACIONES:-true}" = "true" ]; then
    # Base recién creada: el esquema sale de los modelos, no de las
    # migraciones (la cadena no arranca desde cero — ver el docstring de
    # bootstrap_esquema.py). No hace nada si la base ya tiene tablas.
    python /usr/local/bin/bootstrap_esquema.py

    # Solo UNA invocación de `flask`. Cada una reconstruye la aplicación entera
    # (varios segundos: conexión a Redis, tablas auxiliares), y como el
    # contenedor no atiende peticiones hasta que termina este script, cada
    # arranque de más es tiempo de caída en cada despliegue.
    flask db upgrade

    # La revisión final se lee del catálogo en vez de con `flask db current`,
    # que costaría otro arranque completo solo para imprimir una línea.
    echo "[entrypoint] Revisión aplicada: $(python /usr/local/bin/revision_actual.py 2>&1 | tail -1)"
else
    echo "[entrypoint] APLICAR_MIGRACIONES=false — las migraciones son un paso"
    echo "             aparte del despliegue. Revisión en la base:" \
         "$(python /usr/local/bin/revision_actual.py 2>&1 | tail -1)"
fi

# ── 3. A trabajar ──────────────────────────────────────────────────────────
echo "[entrypoint] Arrancando: $*"
exec "$@"
