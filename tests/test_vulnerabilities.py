import pytest
import io
import os
from app import create_app
from app.extensions import db
from app.models import User, Trabajador, DocumentoTrabajador

@pytest.fixture
def app():
    os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
    app = create_app()
    app.config.update({
        "TESTING": True,
        "WTF_CSRF_ENABLED": False,
        "UPLOAD_FOLDER": "/tmp"
    })
    
    with app.app_context():
        db.create_all()
        # Seed user
        admin = User(username='admin', password_hash='hash', role='admin')
        coord = User(username='coord', password_hash='hash', role='coordinador')
        worker = Trabajador(no_empleado='123', nombre='Test', nombre_apellidos='Worker')
        db.session.add_all([admin, coord, worker])
        db.session.commit()
        
        # Add doc
        doc = DocumentoTrabajador(trabajador_id=worker.id, nombre_archivo='test.pdf', ruta_archivo='test.pdf')
        db.session.add(doc)
        db.session.commit()
        
    yield app

@pytest.fixture
def client(app):
    return app.test_client()

def test_file_upload_invalid_mime(client, app):
    with client.session_transaction() as sess:
        sess['user_id'] = 1
        sess['role'] = 'admin'
        
    # File with correct extension but invalid mime (fake PDF)
    data = {
        'documento': (io.BytesIO(b'Fake content not PDF'), 'test.pdf')
    }
    response = client.post('/trabajadores/1/documentos', data=data, content_type='multipart/form-data')
    assert response.status_code == 400
    assert b'File format not allowed' in response.data

def test_idor_document_access(client, app):
    # Logged as normal user vs admin vs coordinator who doesn't own worker
    with client.session_transaction() as sess:
        sess['user_id'] = 2 # Coord
        sess['role'] = 'coordinador'
        
    # Trying to get worker's doc without being their coordinator
    response = client.get('/trabajadores/documento/1')
    assert response.status_code == 403

def test_password_policy(client, app):
    with client.session_transaction() as sess:
        sess['user_id'] = 1
        sess['role'] = 'admin'
        
    # Test weak password
    response = client.post('/profile', data={'new_password': 'weak', 'confirm_password': 'weak'})
    # It renders the template directly with the flash message
    assert response.status_code == 200
    assert b'demasiado d\xc3\xa9bil' in response.data or b'weak' in response.data

