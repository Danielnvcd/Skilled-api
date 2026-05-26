"""Tests de Pausa 8a — etiquetas imprimibles (Avery 5160 / 5163).

Endpoint: POST /api/v1/etiquetas/pdf
Auth: JWT real (Bearer).
"""
from decimal import Decimal

import pytest
from werkzeug.security import generate_password_hash

from app.models import User, Producto
from app.routes.api_auth import _encode_access_token


def _hdr(user):
    return {'Authorization': f'Bearer {_encode_access_token(user)}'}


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def inv_admin(db):
    u = User(username='et_admin', password_hash=generate_password_hash('Pass123!'), role='admin')
    db.session.add(u); db.session.commit()
    return u


@pytest.fixture
def outsider(db):
    # Rol no autorizado para inventario (coordinador sí lo está desde 05-25).
    u = User(username='et_out', password_hash=generate_password_hash('Pass123!'), role='visitor')
    db.session.add(u); db.session.commit()
    return u


@pytest.fixture
def producto(db):
    p = Producto(codigo='ETIQ-001', descripcion='Cinta Aislante',
                  categoria='Suministros', unidad='pza',
                  stock_actual=Decimal('10'), stock_minimo=Decimal('0'), activo=True)
    db.session.add(p); db.session.commit()
    return p


@pytest.fixture
def producto_inactivo(db):
    p = Producto(codigo='ETIQ-INA', descripcion='Inactivo',
                  categoria='X', unidad='pza',
                  stock_actual=Decimal('0'), stock_minimo=Decimal('0'), activo=False)
    db.session.add(p); db.session.commit()
    return p


# ═══════════════════════════════════════════════════════════════════════════════

class TestEtiquetasPdf:

    def test_barcode_avery_5160_descarga_ok(self, client, inv_admin, producto):
        r = client.post('/api/v1/etiquetas/pdf', headers=_hdr(inv_admin), json={
            'formato': 'avery_5160',
            'tipo': 'barcode',
            'items': [{'producto_id': producto.id, 'cantidad': 5}],
        })
        assert r.status_code == 200, r.get_json() if r.is_json else r.data[:200]
        assert r.mimetype == 'application/pdf'
        # PDF empieza con %PDF-
        assert r.data.startswith(b'%PDF-')

    def test_qr_avery_5160(self, client, inv_admin, producto):
        r = client.post('/api/v1/etiquetas/pdf', headers=_hdr(inv_admin), json={
            'formato': 'avery_5160',
            'tipo': 'qr',
            'items': [{'producto_id': producto.id, 'cantidad': 3}],
        })
        assert r.status_code == 200
        assert r.data.startswith(b'%PDF-')

    def test_avery_5163(self, client, inv_admin, producto):
        r = client.post('/api/v1/etiquetas/pdf', headers=_hdr(inv_admin), json={
            'formato': 'avery_5163',
            'tipo': 'barcode',
            'items': [{'producto_id': producto.id, 'cantidad': 2}],
        })
        assert r.status_code == 200

    def test_defaults(self, client, inv_admin, producto):
        """Sin formato ni tipo en el payload → defaults (5160, barcode)."""
        r = client.post('/api/v1/etiquetas/pdf', headers=_hdr(inv_admin), json={
            'items': [{'producto_id': producto.id, 'cantidad': 1}],
        })
        assert r.status_code == 200

    def test_multiples_productos(self, client, db, inv_admin, producto):
        p2 = Producto(codigo='ETIQ-002', descripcion='Otro producto',
                       categoria='X', unidad='kg',
                       stock_actual=Decimal('0'), stock_minimo=Decimal('0'), activo=True)
        db.session.add(p2); db.session.commit()
        r = client.post('/api/v1/etiquetas/pdf', headers=_hdr(inv_admin), json={
            'items': [
                {'producto_id': producto.id, 'cantidad': 4},
                {'producto_id': p2.id, 'cantidad': 6},
            ],
        })
        assert r.status_code == 200

    def test_formato_invalido(self, client, inv_admin, producto):
        r = client.post('/api/v1/etiquetas/pdf', headers=_hdr(inv_admin), json={
            'formato': 'xerox_1234',
            'tipo': 'barcode',
            'items': [{'producto_id': producto.id, 'cantidad': 1}],
        })
        assert r.status_code == 422

    def test_tipo_invalido(self, client, inv_admin, producto):
        r = client.post('/api/v1/etiquetas/pdf', headers=_hdr(inv_admin), json={
            'tipo': 'pictograma',
            'items': [{'producto_id': producto.id, 'cantidad': 1}],
        })
        assert r.status_code == 422

    def test_cantidad_cero_rechazada(self, client, inv_admin, producto):
        r = client.post('/api/v1/etiquetas/pdf', headers=_hdr(inv_admin), json={
            'items': [{'producto_id': producto.id, 'cantidad': 0}],
        })
        assert r.status_code == 422

    def test_cantidad_negativa_rechazada(self, client, inv_admin, producto):
        r = client.post('/api/v1/etiquetas/pdf', headers=_hdr(inv_admin), json={
            'items': [{'producto_id': producto.id, 'cantidad': -1}],
        })
        assert r.status_code == 422

    def test_tope_total_etiquetas(self, client, db, inv_admin, producto):
        """600 etiquetas en total (2 × 300) debe rechazarse — tope global 500."""
        p2 = Producto(codigo='ETIQ-T2', descripcion='Otro', categoria='X',
                       unidad='pza', stock_actual=Decimal('0'),
                       stock_minimo=Decimal('0'), activo=True)
        db.session.add(p2); db.session.commit()
        r = client.post('/api/v1/etiquetas/pdf', headers=_hdr(inv_admin), json={
            'items': [
                {'producto_id': producto.id, 'cantidad': 300},
                {'producto_id': p2.id, 'cantidad': 300},
            ],
        })
        assert r.status_code == 422
        body = r.get_json()
        assert isinstance(body.get('detail'), str) and 'tope' in body['detail'].lower()

    def test_producto_no_existe(self, client, inv_admin):
        r = client.post('/api/v1/etiquetas/pdf', headers=_hdr(inv_admin), json={
            'items': [{'producto_id': 999999, 'cantidad': 1}],
        })
        assert r.status_code == 404

    def test_producto_inactivo_no_genera(self, client, inv_admin, producto_inactivo):
        r = client.post('/api/v1/etiquetas/pdf', headers=_hdr(inv_admin), json={
            'items': [{'producto_id': producto_inactivo.id, 'cantidad': 1}],
        })
        assert r.status_code == 404

    def test_payload_sin_items(self, client, inv_admin):
        r = client.post('/api/v1/etiquetas/pdf', headers=_hdr(inv_admin), json={
            'items': [],
        })
        assert r.status_code == 422

    def test_outsider_rechazado(self, client, outsider, producto):
        r = client.post('/api/v1/etiquetas/pdf', headers=_hdr(outsider), json={
            'items': [{'producto_id': producto.id, 'cantidad': 1}],
        })
        assert r.status_code == 403

    def test_sin_token_rechazado(self, client, producto):
        r = client.post('/api/v1/etiquetas/pdf', json={
            'items': [{'producto_id': producto.id, 'cantidad': 1}],
        })
        assert r.status_code == 401
