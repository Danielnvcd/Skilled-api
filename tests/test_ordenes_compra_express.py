"""Tests de Pausa 9 — Compras express.

Endpoints:
  POST /api/v1/ordenes-compra/express/sugerencia
  POST /api/v1/ordenes-compra/express/pdf

Auth: JWT real (Bearer).
"""
import datetime
from decimal import Decimal

import pytest
from werkzeug.security import generate_password_hash

from app.extensions import db as _db
from app.models import User, Producto, MovimientoInventario
from app.routes.api_auth import _encode_access_token


def _hdr(user):
    return {'Authorization': f'Bearer {_encode_access_token(user)}'}


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def inv_admin(db):
    u = User(username='oc_admin', password_hash=generate_password_hash('Pass123!'), role='admin')
    db.session.add(u); db.session.commit()
    return u


@pytest.fixture
def outsider(db):
    # visitor no está en _require_inventario.
    u = User(username='oc_out', password_hash=generate_password_hash('Pass123!'), role='visitor')
    db.session.add(u); db.session.commit()
    return u


@pytest.fixture
def producto_con_proveedor(db):
    p = Producto(
        codigo='OC-001', descripcion='Cemento Portland', categoria='Construcción',
        unidad='saco', stock_actual=Decimal('5'), stock_minimo=Decimal('20'),
        proveedor_default_nombre='Cementos del Norte',
        proveedor_default_contacto='5512345678',
        activo=True,
    )
    db.session.add(p); db.session.commit()
    return p


@pytest.fixture
def producto_sin_proveedor(db):
    p = Producto(
        codigo='OC-002', descripcion='Cinta métrica', categoria='Herramientas',
        unidad='pza', stock_actual=Decimal('1'), stock_minimo=Decimal('10'),
        activo=True,
    )
    db.session.add(p); db.session.commit()
    return p


@pytest.fixture
def producto_con_consumo(db, inv_admin):
    """Producto con 6 unidades vendidas en los últimos 30 días → 0.2/día."""
    p = Producto(
        codigo='OC-003', descripcion='Tornillo M6', categoria='Ferretería',
        unidad='pza', stock_actual=Decimal('15'), stock_minimo=Decimal('5'),
        proveedor_default_nombre='Cementos del Norte',
        proveedor_default_contacto='5512345678',
        activo=True,
    )
    db.session.add(p); db.session.flush()

    hace_10 = datetime.datetime.now() - datetime.timedelta(days=10)
    db.session.add(MovimientoInventario(
        tipo='SALIDA', producto_id=p.id, cantidad=Decimal('6'),
        usuario_id=inv_admin.id, fecha=hace_10, motivo='test',
    ))
    db.session.commit()
    return p


@pytest.fixture
def producto_inactivo(db):
    p = Producto(
        codigo='OC-INA', descripcion='Inactivo', categoria='X', unidad='pza',
        stock_actual=Decimal('0'), stock_minimo=Decimal('0'), activo=False,
    )
    db.session.add(p); db.session.commit()
    return p


# ═══════════════════════════════════════════════════════════════════════════════

class TestSugerencia:

    def test_sugerencia_sin_consumo(self, client, inv_admin, producto_con_proveedor):
        """Sin consumo: cantidad_sugerida = max(0, stock_minimo - stock_actual) = 15."""
        r = client.post('/api/v1/ordenes-compra/express/sugerencia',
                        headers=_hdr(inv_admin),
                        json={'producto_ids': [producto_con_proveedor.id]})
        assert r.status_code == 200, r.get_json()
        data = r.get_json()
        assert 'grupos' in data
        assert len(data['grupos']) == 1
        g = data['grupos'][0]
        assert g['proveedor'] == 'Cementos del Norte'
        assert g['contacto'] == '5512345678'
        assert len(g['items']) == 1
        it = g['items'][0]
        assert it['codigo'] == 'OC-001'
        assert it['consumo_promedio_30d'] == 0
        # stock 5, mínimo 20 → faltante 15
        assert it['cantidad_sugerida'] == 15.0

    def test_sugerencia_con_consumo(self, client, inv_admin, producto_con_consumo):
        """Con 6/30=0.2 por día: (0.2*30) - 15 + 5 = -4 → fallback a max(0,5-15)=0."""
        r = client.post('/api/v1/ordenes-compra/express/sugerencia',
                        headers=_hdr(inv_admin),
                        json={'producto_ids': [producto_con_consumo.id]})
        assert r.status_code == 200
        it = r.get_json()['grupos'][0]['items'][0]
        assert it['consumo_promedio_30d'] == 0.2
        assert it['cantidad_sugerida'] == 0.0

    def test_sugerencia_consumo_alto(self, client, db, inv_admin):
        """Consumo alto que sí dispara la fórmula principal:
        stock=2, min=1, ventas=60 en 30d → consumo=2/día → 2*30 - 2 + 1 = 59."""
        p = Producto(codigo='OC-X', descripcion='Hot', categoria='X', unidad='pza',
                     stock_actual=Decimal('2'), stock_minimo=Decimal('1'),
                     proveedor_default_nombre='ProveX', activo=True)
        db.session.add(p); db.session.flush()
        db.session.add(MovimientoInventario(
            tipo='SALIDA', producto_id=p.id, cantidad=Decimal('60'),
            usuario_id=inv_admin.id,
            fecha=datetime.datetime.now() - datetime.timedelta(days=5),
            motivo='test',
        ))
        db.session.commit()
        r = client.post('/api/v1/ordenes-compra/express/sugerencia',
                        headers=_hdr(inv_admin),
                        json={'producto_ids': [p.id]})
        assert r.status_code == 200
        it = r.get_json()['grupos'][0]['items'][0]
        assert it['consumo_promedio_30d'] == 2.0
        assert it['cantidad_sugerida'] == 59.0

    def test_agrupacion_por_proveedor(self, client, db, inv_admin,
                                       producto_con_proveedor, producto_sin_proveedor):
        """Dos productos: uno con proveedor, otro sin → 2 grupos."""
        # Agregar un tercero del mismo proveedor para verificar agrupación.
        p3 = Producto(codigo='OC-004', descripcion='Otro', categoria='X', unidad='pza',
                      stock_actual=Decimal('0'), stock_minimo=Decimal('3'),
                      proveedor_default_nombre='Cementos del Norte',
                      activo=True)
        db.session.add(p3); db.session.commit()

        r = client.post('/api/v1/ordenes-compra/express/sugerencia',
                        headers=_hdr(inv_admin),
                        json={'producto_ids': [
                            producto_con_proveedor.id,
                            producto_sin_proveedor.id,
                            p3.id,
                        ]})
        assert r.status_code == 200
        grupos = {g['proveedor']: g for g in r.get_json()['grupos']}
        assert 'Cementos del Norte' in grupos
        assert 'Sin proveedor' in grupos
        assert len(grupos['Cementos del Norte']['items']) == 2
        assert len(grupos['Sin proveedor']['items']) == 1

    def test_producto_inexistente_404(self, client, inv_admin):
        r = client.post('/api/v1/ordenes-compra/express/sugerencia',
                        headers=_hdr(inv_admin),
                        json={'producto_ids': [9999]})
        assert r.status_code == 404

    def test_producto_inactivo_404(self, client, inv_admin, producto_inactivo):
        r = client.post('/api/v1/ordenes-compra/express/sugerencia',
                        headers=_hdr(inv_admin),
                        json={'producto_ids': [producto_inactivo.id]})
        assert r.status_code == 404

    def test_dedupe_de_ids(self, client, inv_admin, producto_con_proveedor):
        """Producto repetido en la lista → debe aparecer una sola vez."""
        r = client.post('/api/v1/ordenes-compra/express/sugerencia',
                        headers=_hdr(inv_admin),
                        json={'producto_ids': [
                            producto_con_proveedor.id,
                            producto_con_proveedor.id,
                        ]})
        assert r.status_code == 200
        items = r.get_json()['grupos'][0]['items']
        assert len(items) == 1

    def test_lista_vacia_422(self, client, inv_admin):
        r = client.post('/api/v1/ordenes-compra/express/sugerencia',
                        headers=_hdr(inv_admin),
                        json={'producto_ids': []})
        assert r.status_code == 422

    def test_sin_token_401(self, client, producto_con_proveedor):
        r = client.post('/api/v1/ordenes-compra/express/sugerencia',
                        json={'producto_ids': [producto_con_proveedor.id]})
        assert r.status_code == 401

    def test_outsider_403(self, client, outsider, producto_con_proveedor):
        r = client.post('/api/v1/ordenes-compra/express/sugerencia',
                        headers=_hdr(outsider),
                        json={'producto_ids': [producto_con_proveedor.id]})
        assert r.status_code == 403


# ═══════════════════════════════════════════════════════════════════════════════

class TestPdfOC:

    def test_pdf_descarga_ok(self, client, inv_admin, producto_con_proveedor):
        r = client.post('/api/v1/ordenes-compra/express/pdf',
                        headers=_hdr(inv_admin),
                        json={
                            'proveedor': 'Cementos del Norte',
                            'contacto': '5512345678',
                            'items': [{'producto_id': producto_con_proveedor.id,
                                       'cantidad': 15}],
                        })
        assert r.status_code == 200, (r.get_json() if r.is_json else r.data[:200])
        assert r.mimetype == 'application/pdf'
        assert r.data.startswith(b'%PDF-')
        # Header con link de WhatsApp
        wa = r.headers.get('X-Whatsapp-Link')
        assert wa is not None
        assert wa.startswith('https://wa.me/')
        # El número 10 dígitos se promueve con prefijo MX (52)
        assert '525512345678' in wa
        # Header con folio
        folio = r.headers.get('X-Folio')
        assert folio is not None
        assert folio.startswith('OCE-')

    def test_pdf_sin_contacto(self, client, inv_admin, producto_sin_proveedor):
        r = client.post('/api/v1/ordenes-compra/express/pdf',
                        headers=_hdr(inv_admin),
                        json={
                            'proveedor': 'ProveedorX',
                            'items': [{'producto_id': producto_sin_proveedor.id,
                                       'cantidad': 5}],
                        })
        assert r.status_code == 200
        # Sin contacto → wa.me/?text=...
        wa = r.headers.get('X-Whatsapp-Link')
        assert wa.startswith('https://wa.me/?text=')

    def test_pdf_cantidad_decimal(self, client, inv_admin, producto_con_proveedor):
        r = client.post('/api/v1/ordenes-compra/express/pdf',
                        headers=_hdr(inv_admin),
                        json={
                            'proveedor': 'X',
                            'items': [{'producto_id': producto_con_proveedor.id,
                                       'cantidad': 2.5}],
                        })
        assert r.status_code == 200

    def test_pdf_multilinea(self, client, db, inv_admin,
                            producto_con_proveedor, producto_sin_proveedor):
        r = client.post('/api/v1/ordenes-compra/express/pdf',
                        headers=_hdr(inv_admin),
                        json={
                            'proveedor': 'Mixto',
                            'items': [
                                {'producto_id': producto_con_proveedor.id, 'cantidad': 3},
                                {'producto_id': producto_sin_proveedor.id, 'cantidad': 7},
                            ],
                        })
        assert r.status_code == 200

    def test_pdf_producto_inexistente_404(self, client, inv_admin):
        r = client.post('/api/v1/ordenes-compra/express/pdf',
                        headers=_hdr(inv_admin),
                        json={
                            'proveedor': 'X',
                            'items': [{'producto_id': 9999, 'cantidad': 1}],
                        })
        assert r.status_code == 404

    def test_pdf_producto_inactivo_404(self, client, inv_admin, producto_inactivo):
        r = client.post('/api/v1/ordenes-compra/express/pdf',
                        headers=_hdr(inv_admin),
                        json={
                            'proveedor': 'X',
                            'items': [{'producto_id': producto_inactivo.id, 'cantidad': 1}],
                        })
        assert r.status_code == 404

    def test_pdf_sin_proveedor_422(self, client, inv_admin, producto_con_proveedor):
        r = client.post('/api/v1/ordenes-compra/express/pdf',
                        headers=_hdr(inv_admin),
                        json={
                            'items': [{'producto_id': producto_con_proveedor.id,
                                       'cantidad': 1}],
                        })
        assert r.status_code == 422

    def test_pdf_items_vacios_422(self, client, inv_admin):
        r = client.post('/api/v1/ordenes-compra/express/pdf',
                        headers=_hdr(inv_admin),
                        json={'proveedor': 'X', 'items': []})
        assert r.status_code == 422

    def test_pdf_cantidad_negativa_422(self, client, inv_admin, producto_con_proveedor):
        r = client.post('/api/v1/ordenes-compra/express/pdf',
                        headers=_hdr(inv_admin),
                        json={
                            'proveedor': 'X',
                            'items': [{'producto_id': producto_con_proveedor.id,
                                       'cantidad': -3}],
                        })
        assert r.status_code == 422

    def test_pdf_duplicados_422(self, client, inv_admin, producto_con_proveedor):
        r = client.post('/api/v1/ordenes-compra/express/pdf',
                        headers=_hdr(inv_admin),
                        json={
                            'proveedor': 'X',
                            'items': [
                                {'producto_id': producto_con_proveedor.id, 'cantidad': 5},
                                {'producto_id': producto_con_proveedor.id, 'cantidad': 5},
                            ],
                        })
        assert r.status_code == 422

    def test_pdf_sin_token_401(self, client, producto_con_proveedor):
        r = client.post('/api/v1/ordenes-compra/express/pdf',
                        json={'proveedor': 'X',
                              'items': [{'producto_id': producto_con_proveedor.id,
                                         'cantidad': 1}]})
        assert r.status_code == 401

    def test_pdf_outsider_403(self, client, outsider, producto_con_proveedor):
        r = client.post('/api/v1/ordenes-compra/express/pdf',
                        headers=_hdr(outsider),
                        json={'proveedor': 'X',
                              'items': [{'producto_id': producto_con_proveedor.id,
                                         'cantidad': 1}]})
        assert r.status_code == 403


# ═══════════════════════════════════════════════════════════════════════════════

class TestProductoConProveedorDefault:
    """El CRUD de Producto debe persistir y devolver los nuevos campos."""

    def test_create_con_proveedor(self, client, inv_admin):
        r = client.post('/api/v1/productos/', headers=_hdr(inv_admin), json={
            'codigo': 'NEW-001',
            'descripcion': 'Producto nuevo con proveedor',
            'categoria': 'Test',
            'unidad': 'pza',
            'stock_actual': 0,
            'stock_minimo': 5,
            'proveedor_default_nombre': 'Mi Proveedor',
            'proveedor_default_contacto': '5599887766',
        })
        assert r.status_code == 200, r.get_json()
        body = r.get_json()
        assert body['proveedor_default_nombre'] == 'Mi Proveedor'
        assert body['proveedor_default_contacto'] == '5599887766'

    def test_update_proveedor(self, client, inv_admin, producto_sin_proveedor):
        r = client.put(f'/api/v1/productos/{producto_sin_proveedor.id}',
                       headers=_hdr(inv_admin),
                       json={'proveedor_default_nombre': 'Nuevo Prov',
                             'proveedor_default_contacto': '5511112222'})
        assert r.status_code == 200
        body = r.get_json()
        assert body['proveedor_default_nombre'] == 'Nuevo Prov'
        assert body['proveedor_default_contacto'] == '5511112222'

    def test_serializer_expone_campos(self, client, inv_admin, producto_con_proveedor):
        """El listado de productos debe incluir los nuevos campos de proveedor."""
        r = client.get('/api/v1/productos/', headers=_hdr(inv_admin))
        assert r.status_code == 200
        items = r.get_json()
        target = next(x for x in items if x['id'] == producto_con_proveedor.id)
        assert target['proveedor_default_nombre'] == 'Cementos del Norte'
        assert target['proveedor_default_contacto'] == '5512345678'
