"""Fixtures globales para pytest.

La app es **API-only** (JWT en `Authorization: Bearer …`). Ya no hay rutas
HTML, así que no existen fixtures de sesión Flask — quien necesite autenticar
una request usa `auth_headers(user)` o el fixture `auth_headers_factory`.
"""
import os
import shutil
import tempfile

import pytest
from werkzeug.security import generate_password_hash

from app import create_app
from app.extensions import db as _db


def _encode_token_for(user):
    """Wrapper sobre `_encode_access_token` que evita import circular en módulo.

    La función real depende de `current_app.config['SECRET_KEY']`, así que
    debe llamarse dentro de un app_context (el `client` fixture lo asegura).
    """
    from app.routes.api_auth import _encode_access_token
    return _encode_access_token(user)


def auth_headers(user):
    """Devuelve los headers Authorization para `user` (firmados con el SECRET_KEY actual)."""
    return {'Authorization': f'Bearer {_encode_token_for(user)}'}


@pytest.fixture(scope='session')
def app():
    """Crea la app Flask con SQLite en memoria para tests."""
    os.environ['SECRET_KEY'] = 'test-secret-key-do-not-use-in-prod'
    os.environ['DATABASE_URL'] = 'sqlite://'
    os.environ['REDIS_URL'] = ''  # Desactiva Redis (evita colisiones con datos reales)
    from cryptography.fernet import Fernet
    os.environ['TOTP_ENCRYPTION_KEY'] = Fernet.generate_key().decode()

    # El pipeline de imágenes → Cloudflare R2 debe estar APAGADO en tests: nunca
    # subir a un bucket real ni hacer llamadas de red. Vaciamos las credenciales
    # ANTES de create_app; load_dotenv(override=False) respeta estos valores
    # vacíos, así r2_enabled() devuelve False y marcar_para_sync es no-op (los
    # endpoints de catálogo/import se comportan igual que sin la feature).
    # Lo mismo para el bucket PRIVADO (fotos de perfil, documentos, evidencias):
    # con R2_PRIVADO_BUCKET vacío, `archivos.habilitado()` es False y todo se
    # guarda/lee del UPLOAD_FOLDER temporal, sin tocar la red. Importante porque
    # el .env local SÍ trae credenciales reales.
    for _r2 in ('R2_ACCOUNT_ID', 'R2_BUCKET', 'R2_ACCESS_KEY_ID',
                'R2_SECRET_ACCESS_KEY', 'R2_PUBLIC_BASE_URL',
                'R2_PRIVADO_BUCKET', 'R2_PRIVADO_ACCOUNT_ID',
                'R2_PRIVADO_ACCESS_KEY_ID', 'R2_PRIVADO_SECRET_ACCESS_KEY'):
        os.environ[_r2] = ''
    os.environ['R2_FORCE_ENABLE'] = 'false'

    # Resetear el singleton de Redis por si ya se conectó en otro import
    import app.extensions as _ext
    _ext._redis_client = None

    tmp_upload = tempfile.mkdtemp(prefix='test_uploads_')

    application = create_app()
    application.config.update({
        'TESTING': True,
        'WTF_CSRF_ENABLED': False,
        'RATELIMIT_ENABLED': False,
        'SERVER_NAME': 'localhost',
        'UPLOAD_FOLDER': tmp_upload,
    })

    from app.extensions import limiter
    limiter.enabled = False

    with application.app_context():
        _db.create_all()

    yield application

    with application.app_context():
        _db.drop_all()

    shutil.rmtree(tmp_upload, ignore_errors=True)


@pytest.fixture(scope='function')
def db(app):
    """Provee la sesión de BD y limpia tablas tras cada test."""
    with app.app_context():
        _db.session.begin_nested()
        yield _db
        _db.session.rollback()
        for table in reversed(_db.metadata.sorted_tables):
            _db.session.execute(table.delete())
        _db.session.commit()


@pytest.fixture
def client(app, db):
    """Test client sin sesión (los tests autentican con header Bearer)."""
    return app.test_client()


@pytest.fixture
def auth_headers_factory(app):
    """Fixture-factory: `headers = auth_headers_factory(user)`.

    Útil cuando el test necesita varios usuarios distintos en la misma corrida.
    Equivale a llamar `auth_headers(user)` pero garantiza el app_context.
    """
    def _make(user):
        return auth_headers(user)
    return _make


@pytest.fixture
def admin_user(db):
    """Crea un usuario admin de prueba."""
    from app.models import User
    user = User(
        username='admin_test',
        password_hash=generate_password_hash('password123'),
        role='admin',
    )
    db.session.add(user)
    db.session.commit()
    return user


@pytest.fixture
def coordinador_user(db):
    """Crea un usuario coordinador de prueba."""
    from app.models import User
    user = User(
        username='coord_test',
        password_hash=generate_password_hash('password123'),
        role='coordinador',
    )
    db.session.add(user)
    db.session.commit()
    return user


@pytest.fixture
def trabajador(db):
    """Crea un trabajador de prueba."""
    from app.models import Trabajador
    t = Trabajador(
        no_empleado='T001',
        nombre_apellidos='Pérez López',
        nombre='Juan',
        activo=True,
        tipo_nomina='Semanal',
        salario_real_pactado_x_sem=5000,
        hr_extra=100,
        infonavit=200,
        ajuste_inbursa=0,
        viaticos=50,
    )
    db.session.add(t)
    db.session.commit()
    return t


@pytest.fixture
def trabajador2(db):
    """Segundo trabajador de prueba."""
    from app.models import Trabajador
    t = Trabajador(
        no_empleado='T002',
        nombre_apellidos='García Ruiz',
        nombre='María',
        activo=True,
        tipo_nomina='Por hora',
        salario_real_pactado_x_sem=150,
    )
    db.session.add(t)
    db.session.commit()
    return t


@pytest.fixture
def proyecto(db, admin_user, trabajador):
    """Crea un proyecto con un participante."""
    from app.models import Proyecto
    p = Proyecto(
        numero_proyecto='P-001',
        nombre='Proyecto Test',
        activo=True,
        coordinador_id=admin_user.id,
    )
    p.participantes.append(trabajador)
    db.session.add(p)
    db.session.commit()
    return p
