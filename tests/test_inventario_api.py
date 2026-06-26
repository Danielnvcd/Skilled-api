"""
Tests del blueprint `inventario_api` (Flask, JWT API-only).

Cobertura: auth, autorización por rol, CRUD, lógica de negocio (stock,
ajustes, traspasos), solicitudes, auditoría, edge cases.

Notas:
  - CSRF está desactivado vía conftest (WTF_CSRF_ENABLED=False).
  - Rate limiter está desactivado vía conftest (RATELIMIT_ENABLED=False).
  - Auth: JWT real en `Authorization: Bearer …` — el helper `_login` setea
    `environ_base['HTTP_AUTHORIZATION']` así que todas las requests siguientes
    del mismo client ya van firmadas.
  - Códigos esperados: 400 (regla de negocio), 422 (validación), 404 (no existe),
    403 (sin rol), 401 (sin token), 204 (DELETE OK), 200 (resto OK).
"""
import uuid
import pytest
from werkzeug.security import generate_password_hash

from app.extensions import db as flask_db
from app.models import (
    User, Almacen, Estante, Producto, StockPorAlmacen, MovimientoInventario,
    SolicitudMaterial, SolicitudMaterialDetalle, AuditLog
)
from app.routes.api_auth import _encode_access_token


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _login(client, user_id: int, role: str = None, username: str = None):
    """Firma el client con un JWT real del usuario indicado.

    Compat con el flujo antiguo de sesión: se llama igual (`_login(client, u.id, 'role')`)
    pero ahora emite un access token y lo deja en `environ_base`, de modo que TODAS
    las requests subsiguientes del mismo `client` viajan con `Authorization: Bearer …`.

    `role` y `username` se ignoran — el JWT toma el rol real del User en BD; los
    parámetros se conservan solo para no romper las llamadas existentes.
    """
    user = User.query.get(user_id)
    assert user is not None, f"_login: user_id={user_id} no existe en BD"
    token = _encode_access_token(user)
    client.environ_base['HTTP_AUTHORIZATION'] = f'Bearer {token}'


def _logout(client):
    client.environ_base.pop('HTTP_AUTHORIZATION', None)


# ─── Fixtures locales ─────────────────────────────────────────────────────────
# Se aprovecha conftest.py: `client`, `db`, `app`. Cada test corre dentro de una
# transacción aislada (rollback automático).

@pytest.fixture
def inv_admin(db):
    u = User(username='inv_admin', password_hash=generate_password_hash('Pass123!'), role='admin')
    db.session.add(u)
    db.session.commit()
    return u


@pytest.fixture
def inv_user(db):
    u = User(username='inv_user', password_hash=generate_password_hash('Pass123!'), role='inventario')
    db.session.add(u)
    db.session.commit()
    return u


@pytest.fixture
def inv_solicitante(db):
    u = User(username='inv_sol', password_hash=generate_password_hash('Pass123!'), role='solicitante_material')
    db.session.add(u)
    db.session.commit()
    return u


@pytest.fixture
def inv_outsider(db):
    """Usuario sin rol de inventario (coordinador sí puede leer inventario
    desde 05-25 — usamos `visitor` que no figura en ninguna whitelist)."""
    u = User(username='inv_out', password_hash=generate_password_hash('Pass123!'), role='visitor')
    db.session.add(u)
    db.session.commit()
    return u


# ═══════════════════════════════════════════════════════════════════════════════
# 1. HEALTH CHECK
# ═══════════════════════════════════════════════════════════════════════════════

class TestHealth:
    def test_health_ok(self, client):
        resp = client.get('/api/v1/health')
        assert resp.status_code == 200
        assert resp.get_json() == {'status': 'ok'}


# ═══════════════════════════════════════════════════════════════════════════════
# 2. AUTENTICACIÓN Y AUTORIZACIÓN
# ═══════════════════════════════════════════════════════════════════════════════

class TestAutenticacion:

    def test_sin_sesion_retorna_401(self, client):
        resp = client.get('/api/v1/productos/')
        assert resp.status_code == 401

    def test_rol_sin_permiso_retorna_403(self, client, inv_outsider):
        _login(client, inv_outsider.id, 'coordinador')
        resp = client.get('/api/v1/productos/')
        assert resp.status_code == 403

    def test_admin_puede_acceder(self, client, inv_admin):
        _login(client, inv_admin.id, 'admin')
        resp = client.get('/api/v1/productos/')
        assert resp.status_code == 200

    def test_inventario_puede_leer(self, client, inv_user):
        _login(client, inv_user.id, 'inventario')
        resp = client.get('/api/v1/productos/')
        assert resp.status_code == 200

    def test_solicitante_puede_leer_productos(self, client, inv_solicitante):
        _login(client, inv_solicitante.id, 'solicitante_material')
        resp = client.get('/api/v1/productos/')
        assert resp.status_code == 200

    def test_solicitante_no_puede_crear_producto(self, client, inv_solicitante):
        _login(client, inv_solicitante.id, 'solicitante_material')
        resp = client.post('/api/v1/productos/', json={
            'codigo': 'HACK-001', 'descripcion': 'X', 'categoria': 'T', 'unidad': 'pza',
            'stock_actual': 0, 'stock_minimo': 0,
        })
        assert resp.status_code == 403

    def test_solicitante_no_puede_borrar_almacen(self, client, inv_solicitante):
        _login(client, inv_solicitante.id, 'solicitante_material')
        resp = client.delete('/api/v1/almacenes/1')
        assert resp.status_code == 403


# ═══════════════════════════════════════════════════════════════════════════════
# 3. PRODUCTOS — CRUD
# ═══════════════════════════════════════════════════════════════════════════════

class TestProductos:

    def test_crear_producto_valido(self, client, inv_admin):
        _login(client, inv_admin.id, 'admin')
        resp = client.post('/api/v1/productos/', json={
            'codigo': 'PROD-001', 'descripcion': 'Test', 'categoria': 'Herramientas',
            'unidad': 'pza', 'stock_actual': 50.0, 'stock_minimo': 5.0,
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['codigo'] == 'PROD-001'
        assert data['stock_actual'] == 50.0
        assert data['activo'] is True

    def test_crear_producto_codigo_duplicado(self, client, inv_admin, db):
        _login(client, inv_admin.id, 'admin')
        db.session.add(Producto(codigo='DUP-001', descripcion='Existente',
                                categoria='T', unidad='pza', stock_actual=0, stock_minimo=0))
        db.session.commit()
        resp = client.post('/api/v1/productos/', json={
            'codigo': 'DUP-001', 'descripcion': 'Duplicado', 'categoria': 'T', 'unidad': 'pza',
            'stock_actual': 0, 'stock_minimo': 0,
        })
        assert resp.status_code == 400
        assert 'ya existe' in resp.get_json()['detail'].lower()

    def test_crear_producto_codigo_invalido(self, client, inv_admin):
        _login(client, inv_admin.id, 'admin')
        resp = client.post('/api/v1/productos/', json={
            'codigo': 'PROD TEST 001!', 'descripcion': 'X', 'categoria': 'T', 'unidad': 'pza',
            'stock_actual': 0, 'stock_minimo': 0,
        })
        assert resp.status_code == 422

    def test_crear_producto_stock_negativo(self, client, inv_admin):
        _login(client, inv_admin.id, 'admin')
        resp = client.post('/api/v1/productos/', json={
            'codigo': 'NEG-001', 'descripcion': 'X', 'categoria': 'T', 'unidad': 'pza',
            'stock_actual': -10.0, 'stock_minimo': 0,
        })
        assert resp.status_code == 422

    def test_crear_producto_sin_campo_requerido(self, client, inv_admin):
        _login(client, inv_admin.id, 'admin')
        resp = client.post('/api/v1/productos/', json={
            'codigo': 'MISS-001', 'categoria': 'T', 'unidad': 'pza',
        })
        assert resp.status_code == 422

    def test_crear_producto_codigo_demasiado_largo(self, client, inv_admin):
        _login(client, inv_admin.id, 'admin')
        resp = client.post('/api/v1/productos/', json={
            'codigo': 'A' * 51, 'descripcion': 'X', 'categoria': 'T', 'unidad': 'pza',
            'stock_actual': 0, 'stock_minimo': 0,
        })
        assert resp.status_code == 422

    def test_listar_productos(self, client, inv_admin, db):
        _login(client, inv_admin.id, 'admin')
        db.session.add(Producto(codigo='LIST-001', descripcion='Test', categoria='T',
                                unidad='pza', stock_actual=0, stock_minimo=0))
        db.session.commit()
        resp = client.get('/api/v1/productos/')
        assert resp.status_code == 200
        codigos = [p['codigo'] for p in resp.get_json()]
        assert 'LIST-001' in codigos

    def test_listar_paginacion(self, client, inv_admin):
        _login(client, inv_admin.id, 'admin')
        resp = client.get('/api/v1/productos/?skip=0&limit=1')
        assert resp.status_code == 200
        assert len(resp.get_json()) <= 1

    def test_actualizar_producto(self, client, inv_admin, db):
        _login(client, inv_admin.id, 'admin')
        p = Producto(codigo='UPD-001', descripcion='Orig', categoria='T',
                     unidad='pza', stock_actual=0, stock_minimo=0)
        db.session.add(p); db.session.commit()
        resp = client.put(f'/api/v1/productos/{p.id}', json={'descripcion': 'Actualizada'})
        assert resp.status_code == 200
        assert resp.get_json()['descripcion'] == 'Actualizada'

    def test_actualizar_producto_inexistente(self, client, inv_admin):
        _login(client, inv_admin.id, 'admin')
        resp = client.put('/api/v1/productos/999999', json={'descripcion': 'x'})
        assert resp.status_code == 404

    def test_actualizar_codigo_a_uno_duplicado(self, client, inv_admin, db):
        _login(client, inv_admin.id, 'admin')
        a = Producto(codigo='A-001', descripcion='A', categoria='T', unidad='pza',
                     stock_actual=0, stock_minimo=0)
        b = Producto(codigo='B-001', descripcion='B', categoria='T', unidad='pza',
                     stock_actual=0, stock_minimo=0)
        db.session.add_all([a, b]); db.session.commit()
        resp = client.put(f'/api/v1/productos/{b.id}', json={'codigo': 'A-001'})
        assert resp.status_code == 400

    def test_borrar_producto_soft_delete(self, client, inv_admin, db):
        _login(client, inv_admin.id, 'admin')
        p = Producto(codigo='DEL-001', descripcion='X', categoria='T', unidad='pza',
                     stock_actual=0, stock_minimo=0)
        db.session.add(p); db.session.commit()
        pid = p.id
        resp = client.delete(f'/api/v1/productos/{pid}')
        assert resp.status_code == 204
        p_db = db.session.get(Producto, pid)
        assert p_db.activo is False

    def test_borrar_producto_inexistente(self, client, inv_admin):
        _login(client, inv_admin.id, 'admin')
        resp = client.delete('/api/v1/productos/999999')
        assert resp.status_code == 404

    def test_stock_minimo_supera_maximo(self, client, inv_admin):
        _login(client, inv_admin.id, 'admin')
        resp = client.post('/api/v1/productos/', json={
            'codigo': 'STOCK-OVER', 'descripcion': 'X', 'categoria': 'T', 'unidad': 'pza',
            'stock_actual': 0, 'stock_minimo': 2_000_000,
        })
        assert resp.status_code == 422


# ═══════════════════════════════════════════════════════════════════════════════
# 4. ALMACENES — CRUD
# ═══════════════════════════════════════════════════════════════════════════════

class TestAlmacenes:

    def test_crear_almacen(self, client, inv_admin):
        _login(client, inv_admin.id, 'admin')
        resp = client.post('/api/v1/almacenes/', json={
            'nombre': 'Almacén Central', 'ubicacion': 'Planta 1', 'activo': True,
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['nombre'] == 'Almacén Central'
        assert data['qr_code']

    def test_crear_almacen_sin_nombre(self, client, inv_admin):
        _login(client, inv_admin.id, 'admin')
        resp = client.post('/api/v1/almacenes/', json={'ubicacion': 'P2'})
        assert resp.status_code == 422

    def test_crear_almacen_nombre_demasiado_largo(self, client, inv_admin):
        _login(client, inv_admin.id, 'admin')
        resp = client.post('/api/v1/almacenes/', json={'nombre': 'A' * 101, 'activo': True})
        assert resp.status_code == 422

    def test_listar_almacenes(self, client, inv_admin, db):
        _login(client, inv_admin.id, 'admin')
        db.session.add(Almacen(nombre='X', qr_code=str(uuid.uuid4()), activo=True))
        db.session.commit()
        resp = client.get('/api/v1/almacenes/')
        assert resp.status_code == 200
        assert isinstance(resp.get_json(), list)

    def test_validar_qr_almacen(self, client, inv_admin, db):
        _login(client, inv_admin.id, 'admin')
        qr = str(uuid.uuid4())
        db.session.add(Almacen(nombre='QR Test', qr_code=qr, activo=True))
        db.session.commit()
        resp = client.get(f'/api/v1/almacenes/{qr}/validar')
        assert resp.status_code == 200

    def test_validar_qr_inexistente(self, client, inv_admin):
        _login(client, inv_admin.id, 'admin')
        resp = client.get(f'/api/v1/almacenes/{uuid.uuid4()}/validar')
        assert resp.status_code == 404

    def test_actualizar_almacen(self, client, inv_admin, db):
        _login(client, inv_admin.id, 'admin')
        a = Almacen(nombre='Orig', qr_code=str(uuid.uuid4()), activo=True)
        db.session.add(a); db.session.commit()
        resp = client.put(f'/api/v1/almacenes/{a.id}', json={'ubicacion': 'P-X'})
        assert resp.status_code == 200
        assert resp.get_json()['ubicacion'] == 'P-X'

    def test_actualizar_almacen_inexistente(self, client, inv_admin):
        _login(client, inv_admin.id, 'admin')
        resp = client.put('/api/v1/almacenes/999999', json={'nombre': 'X'})
        assert resp.status_code == 404

    def test_borrar_almacen_soft_delete(self, client, inv_admin, db):
        _login(client, inv_admin.id, 'admin')
        a = Almacen(nombre='Borrar', qr_code=str(uuid.uuid4()), activo=True)
        db.session.add(a); db.session.commit()
        aid = a.id
        resp = client.delete(f'/api/v1/almacenes/{aid}')
        assert resp.status_code == 204
        assert db.session.get(Almacen, aid).activo is False

    def test_borrar_almacen_inexistente(self, client, inv_admin):
        _login(client, inv_admin.id, 'admin')
        resp = client.delete('/api/v1/almacenes/999999')
        assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# 5. ESTANTES — CRUD
# ═══════════════════════════════════════════════════════════════════════════════

class TestEstantes:

    def _crear_almacen(self, db):
        a = Almacen(nombre='Alm Est', qr_code=str(uuid.uuid4()), activo=True)
        db.session.add(a); db.session.commit()
        return a

    def test_crear_estante_valido(self, client, inv_admin, db):
        _login(client, inv_admin.id, 'admin')
        a = self._crear_almacen(db)
        resp = client.post('/api/v1/estantes/', json={
            'nombre': 'Estante A-1', 'descripcion': 'Primero', 'almacen_id': a.id,
        })
        assert resp.status_code == 200
        assert resp.get_json()['nombre'] == 'Estante A-1'

    def test_crear_estante_almacen_inexistente(self, client, inv_admin):
        _login(client, inv_admin.id, 'admin')
        resp = client.post('/api/v1/estantes/', json={'nombre': 'X', 'almacen_id': 999999})
        assert resp.status_code == 404

    def test_crear_estante_sin_almacen_id(self, client, inv_admin):
        _login(client, inv_admin.id, 'admin')
        resp = client.post('/api/v1/estantes/', json={'nombre': 'X'})
        assert resp.status_code == 422

    def test_qr_image_estante_es_png(self, client, inv_admin, db):
        _login(client, inv_admin.id, 'admin')
        a = self._crear_almacen(db)
        e = Estante(nombre='E1', almacen_id=a.id, qr_code=str(uuid.uuid4()), activo=True)
        db.session.add(e); db.session.commit()
        resp = client.get(f'/api/v1/estantes/{e.id}/qr-image')
        assert resp.status_code == 200
        assert resp.headers['Content-Type'] == 'image/png'
        assert resp.data[:4] == b'\x89PNG'

    def test_qr_image_estante_inexistente(self, client, inv_admin):
        _login(client, inv_admin.id, 'admin')
        resp = client.get('/api/v1/estantes/999999/qr-image')
        assert resp.status_code == 404

    def test_validar_qr_estante(self, client, inv_admin, db):
        _login(client, inv_admin.id, 'admin')
        a = self._crear_almacen(db)
        qr = str(uuid.uuid4())
        e = Estante(nombre='E1', almacen_id=a.id, qr_code=qr, activo=True)
        db.session.add(e); db.session.commit()
        resp = client.get(f'/api/v1/estantes/{qr}/validar')
        assert resp.status_code == 200

    def test_validar_qr_estante_inexistente(self, client, inv_admin):
        _login(client, inv_admin.id, 'admin')
        resp = client.get(f'/api/v1/estantes/{uuid.uuid4()}/validar')
        assert resp.status_code == 404

    def test_actualizar_estante_almacen_destino_invalido(self, client, inv_admin, db):
        _login(client, inv_admin.id, 'admin')
        a = self._crear_almacen(db)
        e = Estante(nombre='E1', almacen_id=a.id, qr_code=str(uuid.uuid4()), activo=True)
        db.session.add(e); db.session.commit()
        resp = client.put(f'/api/v1/estantes/{e.id}', json={'almacen_id': 999999})
        assert resp.status_code == 404

    def test_get_estantes_por_almacen(self, client, inv_admin, db):
        _login(client, inv_admin.id, 'admin')
        a = self._crear_almacen(db)
        resp = client.get(f'/api/v1/almacenes/{a.id}/estantes')
        assert resp.status_code == 200
        assert isinstance(resp.get_json(), list)


# ═══════════════════════════════════════════════════════════════════════════════
# 6. MOVIMIENTOS
# ═══════════════════════════════════════════════════════════════════════════════

class TestMovimientos:

    @pytest.fixture(autouse=True)
    def _bodega_default(self, db):
        """Movimientos requieren al menos un Almacén activo (fallback
        cuando el payload no manda almacén_origen/destino). Creamos DOS
        bodegas activas: la primera es la default (origen), la segunda se
        usa como destino en los TRASPASOs. IDs expuestos vía
        `self._bodega_id` y `self._bodega_dest_id`."""
        a = Almacen(nombre='Bodega Tests', qr_code=str(uuid.uuid4()), activo=True)
        b = Almacen(nombre='Bodega Tests B', qr_code=str(uuid.uuid4()), activo=True)
        db.session.add_all([a, b]); db.session.commit()
        self._bodega_id = a.id
        self._bodega_dest_id = b.id
        return a

    def _crear_producto(self, db, stock=100):
        from decimal import Decimal
        p = Producto(
            codigo=f'MOV-{uuid.uuid4().hex[:6]}', descripcion='X', categoria='T',
            unidad='pza', stock_actual=Decimal(str(stock)), stock_minimo=10,
        )
        db.session.add(p); db.session.flush()
        # Refactor 'stock por almacén': la cache `Producto.stock_actual` se
        # recalcula desde StockPorAlmacen, así que necesitamos sembrar el
        # registro inicial en la bodega default. Sin esto, todo movimiento
        # parte de stock=0 y los asserts del test no cuadran.
        db.session.add(StockPorAlmacen(
            producto_id=p.id, almacen_id=self._bodega_id,
            cantidad=Decimal(str(stock)),
        ))
        db.session.commit()
        return p

    def test_entrada_incrementa_stock(self, client, inv_admin, db):
        _login(client, inv_admin.id, 'admin')
        p = self._crear_producto(db, stock=100)
        resp = client.post('/api/v1/movimientos/', json={
            'tipo': 'ENTRADA', 'producto_id': p.id, 'cantidad': 25.0,
        })
        assert resp.status_code == 200
        db.session.refresh(p)
        assert float(p.stock_actual) == 125.0

    def test_entrada_cantidad_cero_falla(self, client, inv_admin, db):
        _login(client, inv_admin.id, 'admin')
        p = self._crear_producto(db)
        resp = client.post('/api/v1/movimientos/', json={
            'tipo': 'ENTRADA', 'producto_id': p.id, 'cantidad': 0.0,
        })
        assert resp.status_code in (400, 422)

    def test_entrada_cantidad_negativa_falla(self, client, inv_admin, db):
        _login(client, inv_admin.id, 'admin')
        p = self._crear_producto(db)
        resp = client.post('/api/v1/movimientos/', json={
            'tipo': 'ENTRADA', 'producto_id': p.id, 'cantidad': -10.0,
        })
        assert resp.status_code in (400, 422)

    def test_salida_reduce_stock(self, client, inv_admin, db):
        _login(client, inv_admin.id, 'admin')
        p = self._crear_producto(db, stock=100)
        resp = client.post('/api/v1/movimientos/', json={
            'tipo': 'SALIDA', 'producto_id': p.id, 'cantidad': 10.0,
        })
        assert resp.status_code == 200
        db.session.refresh(p)
        assert float(p.stock_actual) == pytest.approx(90.0, abs=0.01)

    def test_salida_stock_insuficiente(self, client, inv_admin, db):
        _login(client, inv_admin.id, 'admin')
        p = self._crear_producto(db, stock=10)
        resp = client.post('/api/v1/movimientos/', json={
            'tipo': 'SALIDA', 'producto_id': p.id, 'cantidad': 9999.0,
        })
        assert resp.status_code == 400
        assert 'insuficiente' in resp.get_json()['detail'].lower()

    def test_ajuste_positivo(self, client, inv_admin, db):
        _login(client, inv_admin.id, 'admin')
        p = self._crear_producto(db, stock=100)
        resp = client.post('/api/v1/movimientos/', json={
            'tipo': 'AJUSTE', 'producto_id': p.id, 'cantidad': 5.0,
        })
        assert resp.status_code == 200
        db.session.refresh(p)
        assert float(p.stock_actual) == pytest.approx(105.0, abs=0.01)

    def test_ajuste_negativo_valido(self, client, inv_admin, db):
        _login(client, inv_admin.id, 'admin')
        p = self._crear_producto(db, stock=100)
        resp = client.post('/api/v1/movimientos/', json={
            'tipo': 'AJUSTE', 'producto_id': p.id, 'cantidad': -5.0,
        })
        assert resp.status_code == 200
        db.session.refresh(p)
        assert float(p.stock_actual) == pytest.approx(95.0, abs=0.01)

    def test_ajuste_provoca_stock_negativo(self, client, inv_admin, db):
        _login(client, inv_admin.id, 'admin')
        p = self._crear_producto(db, stock=10)
        resp = client.post('/api/v1/movimientos/', json={
            'tipo': 'AJUSTE', 'producto_id': p.id, 'cantidad': -50.0,
        })
        assert resp.status_code == 400
        assert 'negativo' in resp.get_json()['detail'].lower()

    def test_traspaso_reduce_stock(self, client, inv_admin, db):
        _login(client, inv_admin.id, 'admin')
        p = self._crear_producto(db, stock=100)
        resp = client.post('/api/v1/movimientos/', json={
            'tipo': 'TRASPASO', 'producto_id': p.id, 'cantidad': 5.0,
            'almacen_origen_id': self._bodega_id,
            'almacen_destino_id': self._bodega_dest_id,
        })
        assert resp.status_code == 200, resp.get_json()

    def test_traspaso_stock_insuficiente(self, client, inv_admin, db):
        _login(client, inv_admin.id, 'admin')
        p = self._crear_producto(db, stock=5)
        resp = client.post('/api/v1/movimientos/', json={
            'tipo': 'TRASPASO', 'producto_id': p.id, 'cantidad': 9999.0,
            'almacen_origen_id': self._bodega_id,
            'almacen_destino_id': self._bodega_dest_id,
        })
        assert resp.status_code == 400

    def test_movimiento_producto_inexistente(self, client, inv_admin):
        _login(client, inv_admin.id, 'admin')
        resp = client.post('/api/v1/movimientos/', json={
            'tipo': 'ENTRADA', 'producto_id': 999999, 'cantidad': 10.0,
        })
        assert resp.status_code == 404

    def test_tipo_movimiento_invalido(self, client, inv_admin, db):
        _login(client, inv_admin.id, 'admin')
        p = self._crear_producto(db)
        resp = client.post('/api/v1/movimientos/', json={
            'tipo': 'TYPO_INEXISTENTE', 'producto_id': p.id, 'cantidad': 10.0,
        })
        assert resp.status_code == 422

    def test_cantidad_excede_limite_maximo(self, client, inv_admin, db):
        _login(client, inv_admin.id, 'admin')
        p = self._crear_producto(db)
        resp = client.post('/api/v1/movimientos/', json={
            'tipo': 'ENTRADA', 'producto_id': p.id, 'cantidad': 999_999.0,
        })
        assert resp.status_code == 422


# ═══════════════════════════════════════════════════════════════════════════════
# 7. SOLICITUDES DE MATERIAL
# ═══════════════════════════════════════════════════════════════════════════════

class TestSolicitudes:

    def _crear_producto(self, db):
        from decimal import Decimal
        p = Producto(
            codigo=f'SOL-{uuid.uuid4().hex[:6]}', descripcion='X', categoria='M',
            unidad='pza', stock_actual=Decimal('200'), stock_minimo=5,
        )
        db.session.add(p); db.session.commit()
        return p

    def test_solicitante_crea_solicitud(self, client, inv_solicitante, db):
        _login(client, inv_solicitante.id, 'solicitante_material')
        p = self._crear_producto(db)
        resp = client.post('/api/v1/solicitudes/', json={
            'proyecto': 'Alpha',
            'detalles': [{'producto_id': p.id, 'cantidad_solicitada': 10.0}],
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['estatus'] == 'PENDIENTE'
        assert len(data['detalles']) == 1

    def test_solicitud_sin_detalles(self, client, inv_solicitante):
        _login(client, inv_solicitante.id, 'solicitante_material')
        resp = client.post('/api/v1/solicitudes/', json={'proyecto': 'X', 'detalles': []})
        assert resp.status_code == 422

    def test_solicitud_sin_proyecto_rechaza(self, client, inv_solicitante, db):
        """Proyecto es obligatorio: sin él (o vacío) debe responder 422."""
        _login(client, inv_solicitante.id, 'solicitante_material')
        p = self._crear_producto(db)
        det = [{'producto_id': p.id, 'cantidad_solicitada': 5.0}]
        # Falta la clave proyecto.
        resp = client.post('/api/v1/solicitudes/', json={'detalles': det})
        assert resp.status_code == 422
        # Proyecto en blanco tampoco se acepta.
        resp2 = client.post('/api/v1/solicitudes/', json={'proyecto': '   ', 'detalles': det})
        assert resp2.status_code == 422

    def test_solicitud_cantidad_cero(self, client, inv_solicitante, db):
        _login(client, inv_solicitante.id, 'solicitante_material')
        p = self._crear_producto(db)
        resp = client.post('/api/v1/solicitudes/', json={
            'proyecto': 'Proyecto Test',
            'detalles': [{'producto_id': p.id, 'cantidad_solicitada': 0.0}],
        })
        assert resp.status_code == 422

    def test_solicitud_producto_inexistente_retorna_400(self, client, inv_solicitante):
        _login(client, inv_solicitante.id, 'solicitante_material')
        resp = client.post('/api/v1/solicitudes/', json={
            'proyecto': 'Proyecto Test',
            'detalles': [{'producto_id': 999999, 'cantidad_solicitada': 5.0}],
        })
        assert resp.status_code == 400
        # `detail` puede ser str o lista de strings (errores multilínea).
        detail = resp.get_json()['detail']
        text = ' '.join(detail) if isinstance(detail, list) else detail
        assert '999999' in text

    def test_rol_no_autorizado_no_crea_solicitud(self, client, inv_outsider, db):
        _login(client, inv_outsider.id, 'coordinador')
        p = self._crear_producto(db)
        resp = client.post('/api/v1/solicitudes/', json={
            'proyecto': 'Proyecto Test',
            'detalles': [{'producto_id': p.id, 'cantidad_solicitada': 5.0}],
        })
        # El decorador de login del blueprint inventario_api bloquea coordinador con 403.
        assert resp.status_code == 403

    def test_solicitante_solo_ve_suyas(self, client, inv_solicitante, inv_admin, db):
        # Solicitud de otro usuario
        otra = SolicitudMaterial(solicitante_id=inv_admin.id, proyecto='Otra', estatus='PENDIENTE')
        db.session.add(otra); db.session.commit()

        _login(client, inv_solicitante.id, 'solicitante_material')
        p = self._crear_producto(db)
        client.post('/api/v1/solicitudes/', json={
            'proyecto': 'Proyecto Test',
            'detalles': [{'producto_id': p.id, 'cantidad_solicitada': 1.0}],
        })
        resp = client.get('/api/v1/solicitudes/')
        assert resp.status_code == 200
        for s in resp.get_json():
            assert s['solicitante_id'] == inv_solicitante.id

    def test_admin_ve_todas(self, client, inv_admin):
        _login(client, inv_admin.id, 'admin')
        resp = client.get('/api/v1/solicitudes/')
        assert resp.status_code == 200
        assert isinstance(resp.get_json(), list)

    def test_actualizar_estado_solicitud(self, client, inv_solicitante, inv_admin, db):
        p = self._crear_producto(db)
        _login(client, inv_solicitante.id, 'solicitante_material')
        r = client.post('/api/v1/solicitudes/', json={
            'proyecto': 'Proyecto Test',
            'detalles': [{'producto_id': p.id, 'cantidad_solicitada': 3.0}],
        })
        sol_id = r.get_json()['id']
        _logout(client)
        _login(client, inv_admin.id, 'admin')
        resp = client.patch(f'/api/v1/solicitudes/{sol_id}/estado', json={'estatus': 'APROBADA'})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['estatus'] == 'APROBADA'
        assert data['fecha_cierre'] is not None

    def test_actualizar_estado_inexistente(self, client, inv_admin):
        _login(client, inv_admin.id, 'admin')
        resp = client.patch('/api/v1/solicitudes/999999/estado', json={'estatus': 'APROBADA'})
        assert resp.status_code == 404

    def test_actualizar_estado_typo(self, client, inv_solicitante, inv_admin, db):
        p = self._crear_producto(db)
        _login(client, inv_solicitante.id, 'solicitante_material')
        r = client.post('/api/v1/solicitudes/', json={
            'proyecto': 'Proyecto Test',
            'detalles': [{'producto_id': p.id, 'cantidad_solicitada': 1.0}],
        })
        sol_id = r.get_json()['id']
        _logout(client)
        _login(client, inv_admin.id, 'admin')
        resp = client.patch(f'/api/v1/solicitudes/{sol_id}/estado', json={'estatus': 'APROBANDO'})
        assert resp.status_code == 422

    def test_solicitante_no_aprueba_su_solicitud(self, client, inv_solicitante, db):
        p = self._crear_producto(db)
        _login(client, inv_solicitante.id, 'solicitante_material')
        r = client.post('/api/v1/solicitudes/', json={
            'proyecto': 'Proyecto Test',
            'detalles': [{'producto_id': p.id, 'cantidad_solicitada': 1.0}],
        })
        sol_id = r.get_json()['id']
        resp = client.patch(f'/api/v1/solicitudes/{sol_id}/estado', json={'estatus': 'APROBADA'})
        assert resp.status_code == 403

    def test_volver_a_pendiente_limpia_fecha_cierre(self, client, inv_solicitante, inv_admin, db):
        p = self._crear_producto(db)
        _login(client, inv_solicitante.id, 'solicitante_material')
        r = client.post('/api/v1/solicitudes/', json={
            'proyecto': 'Proyecto Test',
            'detalles': [{'producto_id': p.id, 'cantidad_solicitada': 1.0}],
        })
        sol_id = r.get_json()['id']
        _logout(client)
        _login(client, inv_admin.id, 'admin')

        ap = client.patch(f'/api/v1/solicitudes/{sol_id}/estado', json={'estatus': 'APROBADA'})
        assert ap.get_json()['fecha_cierre'] is not None

        pend = client.patch(f'/api/v1/solicitudes/{sol_id}/estado', json={'estatus': 'PENDIENTE'})
        assert pend.status_code == 200
        assert pend.get_json()['fecha_cierre'] is None


# ═══════════════════════════════════════════════════════════════════════════════
# 8. PROYECTOS
# ═══════════════════════════════════════════════════════════════════════════════

class TestProyectos:

    def test_listar_proyectos(self, client, inv_admin):
        _login(client, inv_admin.id, 'admin')
        resp = client.get('/api/v1/proyectos/')
        assert resp.status_code == 200
        assert isinstance(resp.get_json(), list)

    def test_listar_proyectos_sin_autenticar(self, client):
        resp = client.get('/api/v1/proyectos/')
        assert resp.status_code == 401


# ═══════════════════════════════════════════════════════════════════════════════
# 9. AUDITORÍA
# ═══════════════════════════════════════════════════════════════════════════════

class TestAuditoria:

    def test_crear_producto_genera_audit(self, client, inv_admin, db):
        _login(client, inv_admin.id, 'admin')
        antes = db.session.query(AuditLog).count()
        client.post('/api/v1/productos/', json={
            'codigo': f'AUD-{uuid.uuid4().hex[:6]}', 'descripcion': 'X', 'categoria': 'T',
            'unidad': 'pza', 'stock_actual': 0, 'stock_minimo': 0,
        })
        despues = db.session.query(AuditLog).count()
        assert despues > antes

    def test_crear_almacen_genera_audit(self, client, inv_admin, db):
        _login(client, inv_admin.id, 'admin')
        antes = db.session.query(AuditLog).count()
        client.post('/api/v1/almacenes/', json={
            'nombre': f'Alm Audit {uuid.uuid4().hex[:4]}', 'activo': True,
        })
        despues = db.session.query(AuditLog).count()
        assert despues > antes


# ═══════════════════════════════════════════════════════════════════════════════
# 10. EDGE CASES
# ═══════════════════════════════════════════════════════════════════════════════

class TestEdgeCases:

    def test_skip_negativo(self, client, inv_admin):
        _login(client, inv_admin.id, 'admin')
        resp = client.get('/api/v1/productos/?skip=-1&limit=10')
        # Validación clamp: skip<0 → 422
        assert resp.status_code in (200, 422)

    def test_limit_cero(self, client, inv_admin):
        _login(client, inv_admin.id, 'admin')
        resp = client.get('/api/v1/productos/?skip=0&limit=0')
        assert resp.status_code == 200
        assert resp.get_json() == []

    def test_campos_extra_son_ignorados(self, client, inv_admin):
        _login(client, inv_admin.id, 'admin')
        resp = client.post('/api/v1/almacenes/', json={
            'nombre': f'Alm Extra {uuid.uuid4().hex[:4]}', 'activo': True,
            'campo_inexistente': 'X',
        })
        assert resp.status_code == 200

    def test_id_no_numerico_rechazado(self, client, inv_admin):
        _login(client, inv_admin.id, 'admin')
        # Flask's <int:id> converter devuelve 404 cuando el path no parsea como int
        resp = client.put('/api/v1/productos/abc', json={})
        assert resp.status_code == 404

    def test_payload_vacio(self, client, inv_admin):
        _login(client, inv_admin.id, 'admin')
        resp = client.post('/api/v1/productos/', data=b'',
                           content_type='application/json')
        assert resp.status_code == 422

    def test_content_type_incorrecto(self, client, inv_admin):
        _login(client, inv_admin.id, 'admin')
        resp = client.post('/api/v1/productos/',
                           data={'codigo': 'FORM-001', 'descripcion': 'Test'})
        # request.get_json(silent=True) devuelve None → schema rechaza
        assert resp.status_code == 422
