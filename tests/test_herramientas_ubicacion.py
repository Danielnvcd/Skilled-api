"""
Coherencia bodega ↔ estante al editar una unidad de herramienta.

`PUT /herramientas-unidades/<id>` puede cambiar la bodega, el estante o ambos.
El invariante es que el estante donde queda colocada la unidad pertenezca a su
bodega. No hay constraint en base que lo imponga (`estante_id` solo tiene FK a
`estantes`), así que el guard de la vista es lo único que lo sostiene.

Bug que cubren estos tests: mandando SOLO `almacen_id` la validación se saltaba
por completo y la unidad quedaba en la bodega nueva conservando un estante de la
bodega vieja.
"""
import uuid

import pytest
from werkzeug.security import generate_password_hash

from app.models import Almacen, Estante, Herramienta, HerramientaUnidad, User
from app.routes.api_auth import _encode_access_token

URL = '/api/v1/herramientas-unidades'


def _hdr(user):
    return {'Authorization': f'Bearer {_encode_access_token(user)}'}


@pytest.fixture
def admin(db):
    u = User(username='ubic_admin', password_hash=generate_password_hash('Pass123!'),
             role='admin')
    db.session.add(u)
    db.session.commit()
    return u


@pytest.fixture
def bodegas(db):
    """Dos bodegas; la A con un estante, la B con otro."""
    a = Almacen(nombre='Bodega A', qr_code=str(uuid.uuid4()), activo=True)
    b = Almacen(nombre='Bodega B', qr_code=str(uuid.uuid4()), activo=True)
    db.session.add_all([a, b])
    db.session.commit()
    est_a = Estante(nombre='A-1', almacen_id=a.id, qr_code=str(uuid.uuid4()), activo=True)
    est_b = Estante(nombre='B-1', almacen_id=b.id, qr_code=str(uuid.uuid4()), activo=True)
    db.session.add_all([est_a, est_b])
    db.session.commit()
    return a, b, est_a, est_b


@pytest.fixture
def unidad(db, admin, bodegas):
    """Unidad colocada en la bodega A, en un estante de la bodega A."""
    bodega_a, _, est_a, _ = bodegas
    h = Herramienta(sku='HRR-UBI', descripcion='Taladro', clasificacion='Eléctrica',
                    unidad='pieza', piezas=1, serializada=True, activo=True,
                    created_by_id=admin.id)
    db.session.add(h)
    db.session.commit()
    u = HerramientaUnidad(
        herramienta_id=h.id, no_serie='SN-UBI', codigo_interno='HRR-UBI001',
        qr_code=str(uuid.uuid4()), estado='DISPONIBLE', cantidad=1,
        almacen_id=bodega_a.id, estante_id=est_a.id,
    )
    db.session.add(u)
    db.session.commit()
    return u


class TestCoherenciaBodegaEstante:

    def test_mover_de_bodega_sin_estante_es_rechazado(self, client, admin, unidad, bodegas, db):
        """El bug: solo `almacen_id` dejaba la unidad en B con un estante de A."""
        _, bodega_b, est_a, _ = bodegas
        r = client.put(f'{URL}/{unidad.id}', headers=_hdr(admin),
                       json={'almacen_id': bodega_b.id})
        assert r.status_code == 422, r.get_json()
        assert 'estante' in r.get_json()['detail'].lower()

        # Y no se persistió nada: la unidad sigue coherente en la bodega A.
        db.session.refresh(unidad)
        assert unidad.almacen_id == est_a.almacen_id

    def test_mover_bodega_y_estante_juntos_funciona(self, client, admin, unidad, bodegas, db):
        """El camino correcto: mover ambos a la vez sí se permite."""
        _, bodega_b, _, est_b = bodegas
        r = client.put(f'{URL}/{unidad.id}', headers=_hdr(admin),
                       json={'almacen_id': bodega_b.id, 'estante_id': est_b.id})
        assert r.status_code == 200, r.get_json()
        db.session.refresh(unidad)
        assert (unidad.almacen_id, unidad.estante_id) == (bodega_b.id, est_b.id)

    def test_estante_de_otra_bodega_sigue_rechazado(self, client, admin, unidad, bodegas):
        """Comportamiento previo que NO debe cambiar: estante ajeno → 422."""
        _, _, _, est_b = bodegas
        r = client.put(f'{URL}/{unidad.id}', headers=_hdr(admin),
                       json={'estante_id': est_b.id})
        assert r.status_code == 422, r.get_json()
        assert r.get_json()['detail'] == 'estante_id no pertenece al almacen_id indicado'

    def test_unidad_sin_estante_puede_cambiar_de_bodega(self, client, admin, unidad, bodegas, db):
        """Sin estante colocado no hay nada que validar: el cambio pasa."""
        _, bodega_b, _, _ = bodegas
        unidad.estante_id = None
        db.session.commit()
        r = client.put(f'{URL}/{unidad.id}', headers=_hdr(admin),
                       json={'almacen_id': bodega_b.id})
        assert r.status_code == 200, r.get_json()
        db.session.refresh(unidad)
        assert unidad.almacen_id == bodega_b.id

    def test_editar_otros_campos_no_valida_la_ubicacion(self, client, admin, unidad, bodegas, db):
        """Una fila ya incoherente (guardada antes del arreglo) no debe impedir
        editar campos que no son de ubicación."""
        _, bodega_b, _, _ = bodegas
        unidad.almacen_id = bodega_b.id   # incoherente a propósito: estante es de A
        db.session.commit()
        r = client.put(f'{URL}/{unidad.id}', headers=_hdr(admin),
                       json={'observaciones': 'Revisada en campo'})
        assert r.status_code == 200, r.get_json()
        db.session.refresh(unidad)
        assert unidad.observaciones == 'Revisada en campo'

    def test_almacen_inexistente_da_404(self, client, admin, unidad):
        r = client.put(f'{URL}/{unidad.id}', headers=_hdr(admin),
                       json={'almacen_id': 999999})
        assert r.status_code == 404, r.get_json()
        assert r.get_json()['detail'] == 'almacen_id no existe'

    def test_estante_inexistente_da_404(self, client, admin, unidad):
        r = client.put(f'{URL}/{unidad.id}', headers=_hdr(admin),
                       json={'estante_id': 999999})
        assert r.status_code == 404, r.get_json()
        assert r.get_json()['detail'] == 'estante_id no existe'
