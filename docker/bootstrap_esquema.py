"""Crea el esquema desde cero en una base VACÍA, y solo en una base vacía.

Por qué hace falta
------------------
La cadena de migraciones NO puede construir el esquema desde cero: la primera
revisión (`453bd924fe1d_init_workers`) crea `reportes_semanales` con una FK a
`users`, pero ninguna de las 58 migraciones crea `users`. Esa tabla nació de un
`db.create_all()` de las primeras versiones de la app y nunca tuvo revisión
propia. En los entornos que ya existen no se nota —el esquema ya está ahí y
`alembic_version` va al día—, pero un entorno nuevo se queda atascado en la
primera migración.

Qué hace
--------
Si la base está completamente vacía, crea el esquema desde los modelos (que
son la fuente de verdad) y la marca con `stamp head`, de modo que las
migraciones FUTURAS se apliquen con normalidad.

Guardas
-------
Nunca toca una base con datos. Solo actúa si NO hay ni una sola tabla. Si
encuentra tablas sin `alembic_version` (caso raro y ambiguo), se detiene y
avisa: adivinar ahí sería la manera de estropear una base de producción.

Arreglo de fondo pendiente: una migración que cree `users` y las demás tablas
sin revisión, para que la cadena sea autosuficiente y esto sobre.
"""

import os
import sys

# El script vive en /usr/local/bin (fuera de /app, para que el bind mount de
# desarrollo no lo tape), y Python pone ESA carpeta en sys.path, no el
# directorio de trabajo. Sin esto, `import app` no encuentra nada.
sys.path.insert(0, os.getcwd())


def _tablas_existentes() -> set:
    """Lista las tablas SIN construir la aplicación.

    Importa: `create_app()` tarda varios segundos (conexión a Redis, tablas
    auxiliares) y en el 99 % de los arranques este script no tiene nada que
    hacer. Mirar el catálogo con una conexión pelada evita pagar ese coste en
    cada reinicio del contenedor, que es tiempo de caída en cada despliegue.
    """
    from sqlalchemy import create_engine, inspect
    from sqlalchemy.pool import NullPool

    url = os.environ.get('DATABASE_URL', '')
    if not url:
        return set()
    motor = create_engine(url, poolclass=NullPool)
    try:
        return set(inspect(motor).get_table_names())
    finally:
        motor.dispose()


def main() -> int:
    tablas = _tablas_existentes()

    if 'alembic_version' in tablas:
        print('[bootstrap] La base ya está bajo control de Alembic. Nada que hacer.')
        return 0

    if tablas:
        print(
            '[bootstrap] La base tiene tablas pero no `alembic_version`.\n'
            f'            Tablas encontradas: {len(tablas)}.\n'
            '            NO se toca nada: revisa a mano si hace falta un '
            '`flask db stamp <revisión>`.',
            file=sys.stderr,
        )
        return 1

    print('[bootstrap] Base vacía: creando el esquema desde los modelos...')

    from app import create_app
    from app.extensions import db

    app = create_app()
    with app.app_context():
        db.create_all()

        from flask_migrate import stamp
        stamp(revision='head')

    print('[bootstrap] Esquema creado y marcado en la última revisión.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
