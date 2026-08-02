"""Fotos de unidades de herramienta: validación, re-encode a WebP y descarga.

El endpoint no tenía cobertura. Lo que se fija aquí es que lo ALMACENADO sea
siempre un WebP recién renderizado por Pillow: validar los magic bytes dice que
el archivo ES una imagen, no que sea inofensiva — el re-encode es lo que destruye
cualquier payload embebido en el original.
"""
import io

import pytest
from PIL import Image
from werkzeug.security import generate_password_hash

from app.models import Herramienta, HerramientaUnidad, MediaHerramienta, User
from app.routes.api_auth import _encode_access_token
from app.utils import archivos


def _hdr(user):
    return {'Authorization': f'Bearer {_encode_access_token(user)}'}


def _png(size=(60, 40), color=(200, 30, 30)):
    buf = io.BytesIO()
    Image.new('RGB', size, color).save(buf, format='PNG')
    buf.seek(0)
    return buf


@pytest.fixture
def inv(db):
    u = User(username='fotos_inv', password_hash=generate_password_hash('Pass123!'),
             role='inventario')
    db.session.add(u); db.session.commit()
    return u


@pytest.fixture
def unidad(db, inv):
    h = Herramienta(sku='HRR-F001', descripcion='Rotomartillo', clasificacion='Eléctrica',
                    unidad='pieza', piezas=1, serializada=True, activo=True,
                    created_by_id=inv.id)
    db.session.add(h); db.session.commit()
    u = HerramientaUnidad(herramienta_id=h.id, no_serie='SN-F1',
                          codigo_interno='HRR-000900', qr_code='qr-fotos-1',
                          estado='DISPONIBLE', cantidad=1)
    db.session.add(u); db.session.commit()
    return u


def _subir(client, inv, unidad, contenido, nombre='foto.png'):
    return client.post(
        f'/api/v1/herramientas-unidades/{unidad.id}/fotos',
        headers=_hdr(inv),
        data={'foto': (contenido, nombre)},
        content_type='multipart/form-data',
    )


def test_la_foto_se_almacena_como_webp(app, client, db, inv, unidad):
    r = _subir(client, inv, unidad, _png())
    assert r.status_code == 201, r.get_json()

    media = MediaHerramienta.query.filter_by(unidad_id=unidad.id).one()
    assert media.ruta_archivo.endswith('.webp')
    assert media.mime == 'image/webp'
    # El nombre que subió el usuario se conserva: es como reconoce su foto.
    assert media.nombre_original == 'foto.png'

    with app.app_context():
        with open(archivos.ruta_local(media.ruta_archivo), 'rb') as f:
            crudo = f.read()
    # Firma real de WebP: 'RIFF' .... 'WEBP'. Confirma que se re-renderizó y no
    # se guardó el PNG original con otro nombre.
    assert crudo[:4] == b'RIFF' and crudo[8:12] == b'WEBP'
    assert media.tamano_bytes == len(crudo)


def test_un_jpeg_tambien_termina_en_webp(app, client, db, inv, unidad):
    buf = io.BytesIO()
    Image.new('RGB', (50, 50), (10, 90, 200)).save(buf, format='JPEG')
    buf.seek(0)

    r = _subir(client, inv, unidad, buf, 'campo.jpg')
    assert r.status_code == 201

    media = MediaHerramienta.query.filter_by(unidad_id=unidad.id).one()
    assert media.mime == 'image/webp'


def test_un_no_imagen_se_rechaza(client, db, inv, unidad):
    """Magic bytes: un ejecutable renombrado a .png no entra."""
    falso = io.BytesIO(b'MZ\x90\x00' + b'\x00' * 600)   # cabecera PE
    r = _subir(client, inv, unidad, falso, 'troyano.png')
    assert r.status_code == 422
    assert MediaHerramienta.query.count() == 0


def test_una_imagen_corrupta_da_422_y_no_500(client, db, inv, unidad):
    """Cabecera PNG válida pero cuerpo basura: Pillow falla al abrirla."""
    roto = io.BytesIO(b'\x89PNG\r\n\x1a\n' + b'\xff' * 600)
    r = _subir(client, inv, unidad, roto, 'rota.png')
    assert r.status_code == 422
    assert MediaHerramienta.query.count() == 0


def test_la_foto_se_puede_descargar(client, db, inv, unidad):
    _subir(client, inv, unidad, _png())
    media = MediaHerramienta.query.filter_by(unidad_id=unidad.id).one()

    r = client.get(f'/api/v1/herramientas-unidades/{unidad.id}/media/{media.id}',
                   headers=_hdr(inv))
    assert r.status_code == 200
    assert r.mimetype == 'image/webp'


def test_descargar_sin_permiso_es_403(client, db, inv, unidad):
    _subir(client, inv, unidad, _png())
    media = MediaHerramienta.query.filter_by(unidad_id=unidad.id).one()

    ajeno = User(username='fotos_out', password_hash=generate_password_hash('Pass123!'),
                 role='visitor')
    db.session.add(ajeno); db.session.commit()

    r = client.get(f'/api/v1/herramientas-unidades/{unidad.id}/media/{media.id}',
                   headers=_hdr(ajeno))
    assert r.status_code == 403
