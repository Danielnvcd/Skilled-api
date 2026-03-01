import os
import pytest
from werkzeug.security import generate_password_hash
from app import create_app
from app.extensions import db as _db
from app.models import (
    User, Trabajador, Proyecto, Prestamo, AbonoPrestamo,
    ReporteSemanal, RegistroDiarioHoras, Prenomina,
    DescuentoPrenomina, DepositoExtra
)


@pytest.fixture(scope='session')
def app():
    """Crea la app Flask con SQLite en memoria para tests."""
    os.environ['SECRET_KEY'] = 'test-secret-key-do-not-use-in-prod'
    os.environ['DATABASE_URL'] = 'sqlite://'  # In-memory SQLite

    application = create_app()
    application.config.update({
        'TESTING': True,
        'WTF_CSRF_ENABLED': False,   # Desactivar CSRF en tests
        'SERVER_NAME': 'localhost',
    })

    with application.app_context():
        _db.create_all()

    yield application

    with application.app_context():
        _db.drop_all()


@pytest.fixture(scope='function')
def db(app):
    """Provee la sesión de BD y hace rollback tras cada test."""
    with app.app_context():
        _db.session.begin_nested()
        yield _db
        _db.session.rollback()
        # Limpiar todas las tablas para el siguiente test
        for table in reversed(_db.metadata.sorted_tables):
            _db.session.execute(table.delete())
        _db.session.commit()


@pytest.fixture
def client(app, db):
    """Test client con sesión limpia."""
    return app.test_client()


@pytest.fixture
def admin_user(db):
    """Crea un usuario admin de prueba."""
    user = User(
        username='admin_test',
        password_hash=generate_password_hash('password123'),
        role='admin'
    )
    db.session.add(user)
    db.session.commit()
    return user


@pytest.fixture
def coordinador_user(db):
    """Crea un usuario coordinador de prueba."""
    user = User(
        username='coord_test',
        password_hash=generate_password_hash('password123'),
        role='coordinador'
    )
    db.session.add(user)
    db.session.commit()
    return user


@pytest.fixture
def trabajador(db):
    """Crea un trabajador de prueba."""
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
        viaticos=50
    )
    db.session.add(t)
    db.session.commit()
    return t


@pytest.fixture
def trabajador2(db):
    """Segundo trabajador de prueba."""
    t = Trabajador(
        no_empleado='T002',
        nombre_apellidos='García Ruiz',
        nombre='María',
        activo=True,
        tipo_nomina='Por hora',
        salario_real_pactado_x_sem=150
    )
    db.session.add(t)
    db.session.commit()
    return t


@pytest.fixture
def proyecto(db, admin_user, trabajador):
    """Crea un proyecto con un participante."""
    p = Proyecto(
        numero_proyecto='P-001',
        nombre='Proyecto Test',
        activo=True,
        coordinador_id=admin_user.id
    )
    p.participantes.append(trabajador)
    db.session.add(p)
    db.session.commit()
    return p


@pytest.fixture
def logged_in_admin(client, admin_user):
    """Simula login de admin."""
    with client.session_transaction() as sess:
        sess['user_id'] = admin_user.id
        sess['user'] = admin_user.username
        sess['role'] = 'admin'
    return client


@pytest.fixture
def logged_in_coordinador(client, coordinador_user):
    """Simula login de coordinador."""
    with client.session_transaction() as sess:
        sess['user_id'] = coordinador_user.id
        sess['user'] = coordinador_user.username
        sess['role'] = 'coordinador'
    return client
