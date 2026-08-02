"""Espera a que PostgreSQL acepte conexiones antes de seguir con el arranque.

Por qué existe: `depends_on: condition: service_healthy` del compose ya espera
al healthcheck de Postgres, pero eso no cubre el caso del VPS (donde la base
puede vivir fuera del compose) ni el reinicio del contenedor de la base sin
tocar el de la API. Sin esta espera, el `flask db upgrade` del entrypoint
falla al primer intento y el contenedor entra en bucle de reinicio.

Se usa la propia SQLAlchemy en vez de `pg_isready` para no meter el cliente de
Postgres a la imagen, y porque así se valida la URL exactamente como la va a
usar la aplicación (`postgresql+psycopg://...`), no una versión traducida.
"""

import os
import sys
import time

TIEMPO_MAXIMO = int(os.environ.get('DB_ESPERA_SEGUNDOS', '60'))
INTERVALO = 2


def main() -> int:
    url = os.environ.get('DATABASE_URL', '')

    # SQLite (o sin URL) no necesita espera: el archivo está o no está.
    if not url or url.startswith('sqlite'):
        return 0

    from sqlalchemy import create_engine, text

    # Sin pool: es una comprobación de un solo uso y no queremos dejar
    # conexiones colgadas antes de que arranque Gunicorn.
    from sqlalchemy.pool import NullPool
    motor = create_engine(url, poolclass=NullPool)

    limite = time.monotonic() + TIEMPO_MAXIMO
    ultimo_error = None
    while time.monotonic() < limite:
        try:
            with motor.connect() as conexion:
                conexion.execute(text('SELECT 1'))
            print('[entrypoint] La base de datos responde.', flush=True)
            return 0
        except Exception as exc:  # noqa: BLE001 — cualquier fallo aquí es "aún no está"
            ultimo_error = exc
            print('[entrypoint] Esperando a la base de datos...', flush=True)
            time.sleep(INTERVALO)

    print(
        f'[entrypoint] CRÍTICO: la base de datos no respondió en {TIEMPO_MAXIMO}s.\n'
        f'             Último error: {ultimo_error}',
        file=sys.stderr,
        flush=True,
    )
    return 1


if __name__ == '__main__':
    sys.exit(main())
