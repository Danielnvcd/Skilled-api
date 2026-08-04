import logging
from logging.config import fileConfig

from flask import current_app

from alembic import context

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
fileConfig(config.config_file_name)
logger = logging.getLogger('alembic.env')


def get_engine():
    try:
        # this works with Flask-SQLAlchemy<3 and Alchemical
        return current_app.extensions['migrate'].db.get_engine()
    except (TypeError, AttributeError):
        # this works with Flask-SQLAlchemy>=3
        return current_app.extensions['migrate'].db.engine


def get_engine_url():
    try:
        return get_engine().url.render_as_string(hide_password=False).replace(
            '%', '%%')
    except AttributeError:
        return str(get_engine().url).replace('%', '%%')


# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata
config.set_main_option('sqlalchemy.url', get_engine_url())
target_db = current_app.extensions['migrate'].db

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def get_metadata():
    if hasattr(target_db, 'metadatas'):
        return target_db.metadatas[None]
    return target_db.metadata


def _ajustar_timeouts(connection):
    """Quita el límite de duración que la aplicación impone a cada sentencia.

    Alembic corre sobre el engine de la aplicación (`get_engine()` de arriba), y
    ese engine abre las conexiones con `statement_timeout=30s` y
    `lock_timeout=5s` (ver `SQLALCHEMY_ENGINE_OPTIONS` en app/__init__.py). Para
    las peticiones HTTP está muy bien; para una migración es una trampa:

      · `statement_timeout` — un CREATE INDEX, un backfill o un ALTER TABLE con
        reescritura sobre una tabla grande tarda MÁS de 30 segundos y Postgres
        lo aborta a medias. El DDL es transaccional, así que no queda nada roto,
        pero el despliegue falla y no es evidente por qué. Con la base pequeña
        no se nota; el día que haya volumen, sí.

      · `lock_timeout` — este SE MANTIENE a propósito. Si una consulta larga
        tiene tomada la tabla, es preferible que la migración falle en 5
        segundos a que se quede encolada: mientras un ALTER TABLE espera su
        lock, bloquea a todo el que llegue detrás y tumba la aplicación entera.

    Solo aplica a PostgreSQL; en SQLite (tests) estos parámetros no existen.
    """
    if connection.dialect.name != 'postgresql':
        return

    from sqlalchemy import text

    connection.execute(text('SET statement_timeout = 0'))
    connection.execute(text("SET lock_timeout = '5s'"))

    efectivo = connection.execute(text('SHOW statement_timeout')).scalar()
    bloqueo = connection.execute(text('SHOW lock_timeout')).scalar()
    logger.info(
        'Migraciones con statement_timeout=%s y lock_timeout=%s',
        'sin límite' if efectivo in ('0', None) else efectivo, bloqueo,
    )

    # OBLIGATORIO, no es cosmético. Los `execute` de arriba hacen «autobegin»:
    # SQLAlchemy 2.0 abre una transacción sola en cuanto se ejecuta algo. Si la
    # conexión llega en transacción a `context.configure()`, Alembic la marca
    # como externa (`_in_external_transaction`) y NO comitea nunca: da por hecho
    # que el dueño de la transacción es este env.py. Como aquí no se comitea, al
    # salir del `with connectable.connect()` se hace ROLLBACK y la migración se
    # deshace entera — en silencio, con los logs diciendo que fue bien.
    #
    # El commit cierra esa transacción vacía y devuelve la conexión limpia, así
    # Alembic abre y comitea la suya. Los SET son de sesión (no `SET LOCAL`), de
    # modo que sobreviven al commit y siguen aplicando a la migración.
    connection.commit()


def run_migrations_offline():
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url, target_metadata=get_metadata(), literal_binds=True
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """

    # this callback is used to prevent an auto-migration from being generated
    # when there are no changes to the schema
    # reference: http://alembic.zzzcomputing.com/en/latest/cookbook.html
    def process_revision_directives(context, revision, directives):
        if getattr(config.cmd_opts, 'autogenerate', False):
            script = directives[0]
            if script.upgrade_ops.is_empty():
                directives[:] = []
                logger.info('No changes in schema detected.')

    conf_args = current_app.extensions['migrate'].configure_args
    if conf_args.get("process_revision_directives") is None:
        conf_args["process_revision_directives"] = process_revision_directives

    connectable = get_engine()

    with connectable.connect() as connection:
        _ajustar_timeouts(connection)

        context.configure(
            connection=connection,
            target_metadata=get_metadata(),
            **conf_args
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
