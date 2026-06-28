"""Tests del módulo Solicitudes de Compra (procura).

Cubre:
  - POST   /api/v1/solicitudes-compra/                 (crear, producto + texto libre)
  - GET    /api/v1/solicitudes-compra/                 (listar + filtro estatus)
  - PATCH  /api/v1/solicitudes-compra/<id>/estado      (transiciones)
  - POST   /api/v1/solicitudes-compra/<id>/recibir     (recepción parcial/total → ENTRADA)
  - GET    /api/v1/solicitudes-compra/productos-activos
  - DELETE /api/v1/solicitudes-compra/<id>             (cancelar soft)
  - AuthZ: admin (RH) NO puede entrar; inventario sí.

Auth: JWT real (Bearer), igual que el resto de tests de inventario.
"""
import uuid
from decimal import Decimal

import pytest
from werkzeug.security import generate_password_hash

from app.models import (
    User, Almacen, Producto, StockPorAlmacen, MovimientoInventario,
    SolicitudCompra,
)
from app.routes.api_auth import _encode_access_token


def _hdr(user):
    return {'Authorization': f'Bearer {_encode_access_token(user)}'}


@pytest.fixture
def inv_user(db):
    u = User(username='c_inv', password_hash=generate_password_hash('Pass123!'), role='inventario')
    db.session.add(u); db.session.commit()
    return u


@pytest.fixture
def admin_rh(db):
    u = User(username='c_admin', password_hash=generate_password_hash('Pass123!'), role='admin')
    db.session.add(u); db.session.commit()
    return u


@pytest.fixture
def almacen(db):
    a = Almacen(nombre='Bodega C', qr_code=str(uuid.uuid4()), activo=True)
    db.session.add(a); db.session.commit()
    return a


@pytest.fixture
def producto(db, almacen):
    p = Producto(
        codigo='C-001', descripcion='Tubo PVC', categoria='Suministros',
        unidad='pza', stock_actual=Decimal('10'), stock_reservado=Decimal('0'),
        stock_minimo=0, precio_unitario=Decimal('25'), activo=True,
    )
    db.session.add(p); db.session.flush()
    db.session.add(StockPorAlmacen(producto_id=p.id, almacen_id=almacen.id, cantidad=Decimal('10')))
    db.session.commit()
    return p


def _crear(client, user, producto, **extra):
    payload = {
        'proveedor_sugerido': 'Ferretería X',
        'prioridad': 'ALTA',
        'detalles': [
            {'producto_id': producto.id, 'cantidad_solicitada': 8, 'precio_estimado': 25},
            {'descripcion_libre': 'Llave de paso especial', 'unidad': 'pza', 'cantidad_solicitada': 2},
        ],
    }
    payload.update(extra)
    return client.post('/api/v1/solicitudes-compra/', headers=_hdr(user), json=payload)


# ─── AuthZ ────────────────────────────────────────────────────────────────────

def test_admin_rh_no_entra(client, admin_rh, producto):
    r = _crear(client, admin_rh, producto)
    assert r.status_code == 403


def test_inventario_crea(client, inv_user, producto):
    r = _crear(client, inv_user, producto)
    assert r.status_code == 200, r.get_json()
    data = r.get_json()
    assert data['folio'].startswith('SC-')
    assert data['estatus'] == 'PENDIENTE'
    assert len(data['detalles']) == 2
    assert round(data['total_estimado'], 2) == 200.0  # 8 * 25


# ─── Recepción → ENTRADA ──────────────────────────────────────────────────────

def test_recepcion_parcial_y_total(client, inv_user, producto, almacen, db):
    sol = _crear(client, inv_user, producto).get_json()
    sol_id = sol['id']
    det_prod = next(d for d in sol['detalles'] if d['producto_id'])
    det_libre = next(d for d in sol['detalles'] if not d['producto_id'])

    stock_inicial = float(producto.stock_actual)

    # Recepción parcial: 5 de 8 del producto.
    r = client.post(f'/api/v1/solicitudes-compra/{sol_id}/recibir', headers=_hdr(inv_user), json={
        'almacen_destino_id': almacen.id,
        'recepciones': [{'detalle_id': det_prod['id'], 'cantidad_recibida': 5}],
    })
    assert r.status_code == 200, r.get_json()
    data = r.get_json()
    assert data['estatus'] == 'ORDENADA'   # parcial sobre PENDIENTE → avanza

    db.session.expire_all()
    assert float(Producto.query.get(producto.id).stock_actual) == stock_inicial + 5
    assert MovimientoInventario.query.filter_by(producto_id=producto.id, tipo='ENTRADA').count() == 1

    # Recepción del resto del producto + el ítem libre → RECIBIDA.
    r = client.post(f'/api/v1/solicitudes-compra/{sol_id}/recibir', headers=_hdr(inv_user), json={
        'almacen_destino_id': almacen.id,
        'recepciones': [
            {'detalle_id': det_prod['id'], 'cantidad_recibida': 3},
            {'detalle_id': det_libre['id'], 'cantidad_recibida': 2},
        ],
    })
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['estatus'] == 'RECIBIDA'
    db.session.expire_all()
    assert float(Producto.query.get(producto.id).stock_actual) == stock_inicial + 8


@pytest.fixture
def producto_kg(db, almacen):
    p = Producto(
        codigo='C-KG', descripcion='Estaño en rollo', categoria='Suministros',
        unidad='kg', stock_actual=Decimal('5'), stock_reservado=Decimal('0'),
        stock_minimo=0, precio_unitario=Decimal('300'), activo=True,
    )
    db.session.add(p); db.session.flush()
    db.session.add(StockPorAlmacen(producto_id=p.id, almacen_id=almacen.id, cantidad=Decimal('5')))
    db.session.commit()
    return p


def test_pieza_rechaza_decimal(client, inv_user, producto):
    r = client.post('/api/v1/solicitudes-compra/', headers=_hdr(inv_user), json={
        'detalles': [{'producto_id': producto.id, 'cantidad_solicitada': 2.5}],
    })
    assert r.status_code == 400, r.get_json()


def test_kg_acepta_decimal(client, inv_user, producto_kg, almacen):
    r = client.post('/api/v1/solicitudes-compra/', headers=_hdr(inv_user), json={
        'detalles': [{'producto_id': producto_kg.id, 'cantidad_solicitada': 2.5}],
    })
    assert r.status_code == 200, r.get_json()
    sol = r.get_json()
    det = sol['detalles'][0]
    # Recepción decimal también permitida para kg → ENTRADA con decimal.
    r = client.post(f"/api/v1/solicitudes-compra/{sol['id']}/recibir", headers=_hdr(inv_user), json={
        'almacen_destino_id': almacen.id,
        'recepciones': [{'detalle_id': det['id'], 'cantidad_recibida': 2.5}],
    })
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['estatus'] == 'RECIBIDA'


def test_recibir_mas_de_pendiente_falla(client, inv_user, producto, almacen):
    sol = _crear(client, inv_user, producto).get_json()
    det = next(d for d in sol['detalles'] if d['producto_id'])
    r = client.post(f"/api/v1/solicitudes-compra/{sol['id']}/recibir", headers=_hdr(inv_user), json={
        'almacen_destino_id': almacen.id,
        'recepciones': [{'detalle_id': det['id'], 'cantidad_recibida': 99}],
    })
    assert r.status_code == 422


# ─── Estado + listado + productos activos ─────────────────────────────────────

def test_transicion_estado(client, inv_user, producto):
    sol = _crear(client, inv_user, producto).get_json()
    sol_id = sol['id']
    r = client.patch(f'/api/v1/solicitudes-compra/{sol_id}/estado', headers=_hdr(inv_user),
                     json={'estatus': 'ORDENADA'})
    assert r.status_code == 200
    assert r.get_json()['estatus'] == 'ORDENADA'
    # Transición inválida: ORDENADA → RECIBIDA no se permite por este endpoint.
    r = client.patch(f'/api/v1/solicitudes-compra/{sol_id}/estado', headers=_hdr(inv_user),
                     json={'estatus': 'RECIBIDA'})
    assert r.status_code == 422  # OneOf del schema rechaza RECIBIDA


def test_productos_activos_y_listado(client, inv_user, producto):
    _crear(client, inv_user, producto)
    r = client.get('/api/v1/solicitudes-compra/productos-activos', headers=_hdr(inv_user))
    assert r.status_code == 200
    activos = r.get_json()
    assert any(a['producto_id'] == producto.id for a in activos)

    r = client.get('/api/v1/solicitudes-compra/?estatus=PENDIENTE', headers=_hdr(inv_user))
    assert r.status_code == 200
    assert len(r.get_json()) >= 1


def test_pdf_y_whatsapp_header(client, inv_user, producto):
    sol = _crear(client, inv_user, producto, proveedor_contacto='5512345678').get_json()
    r = client.get(f"/api/v1/solicitudes-compra/{sol['id']}/pdf", headers=_hdr(inv_user))
    assert r.status_code == 200, r.get_json() if r.is_json else r.status_code
    assert r.mimetype == 'application/pdf'
    assert r.headers.get('X-Whatsapp-Link', '').startswith('https://wa.me/')
    assert r.headers.get('X-Folio') == sol['folio']


def test_cancelar(client, inv_user, producto):
    sol = _crear(client, inv_user, producto).get_json()
    r = client.delete(f"/api/v1/solicitudes-compra/{sol['id']}", headers=_hdr(inv_user))
    assert r.status_code == 200
    assert SolicitudCompra.query.get(sol['id']).estatus == 'CANCELADA'
    # Ya cancelada → no aparece en productos-activos.
    r = client.get('/api/v1/solicitudes-compra/productos-activos', headers=_hdr(inv_user))
    assert all(a['producto_id'] != producto.id for a in r.get_json())
