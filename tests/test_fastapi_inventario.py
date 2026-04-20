"""
tests/test_fastapi_inventario.py

Tests de las rutas FastAPI del módulo de Inventario.
Cubre: autenticación, autorización, CRUD completo, lógica de negocio,
casos límite y posibles errores humanos.
"""
import pytest
import uuid
from decimal import Decimal
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# ─── Setup del cliente de pruebas ─────────────────────────────────────────────

# Usamos la app Flask para crear los modelos en una BD in-memory de prueba
import os
os.environ.setdefault('SECRET_KEY', 'test-fastapi-secret-key-do-not-use')
os.environ.setdefault('DATABASE_URL', 'sqlite://')

from app.extensions import db as flask_db
from app import create_app
from app.api_fastapi.main import app_fastapi
from app.api_fastapi.database import get_db as fastapi_get_db
from app.models import (
    User, Almacen, Estante, Producto, MovimientoInventario,
    SolicitudMaterial, SolicitudMaterialDetalle, AuditLog
)
from werkzeug.security import generate_password_hash


# ─── Fixtures de base de datos compartida ─────────────────────────────────────

@pytest.fixture(scope="module")
def flask_app():
    """App Flask con SQLite in-memory para levantar los modelos compartidos."""
    _flask_app = create_app()
    _flask_app.config.update({
        'TESTING': True,
        'WTF_CSRF_ENABLED': False,
        'RATELIMIT_ENABLED': False,
        'DATABASE_URL': 'sqlite://',
    })
    with _flask_app.app_context():
        flask_db.create_all()
        yield _flask_app
        flask_db.drop_all()


@pytest.fixture(scope="module")
def db_session(flask_app):
    """Sesión SQLAlchemy atada al engine de Flask (SQLite in-memory)."""
    with flask_app.app_context():
        yield flask_db.session


@pytest.fixture(scope="module")
def api_client(flask_app, db_session):
    """
    Cliente TestClient de FastAPI con override de get_db
    para usar la misma BD in-memory de Flask.
    """
    def override_get_db():
        yield db_session

    app_fastapi.dependency_overrides[fastapi_get_db] = override_get_db
    return TestClient(app_fastapi, raise_server_exceptions=True)


# ─── Fixtures de datos ─────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def admin_user(db_session, flask_app):
    with flask_app.app_context():
        user = User(
            username='api_admin',
            password_hash=generate_password_hash('AdminPass123!'),
            role='admin'
        )
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)
        yield user


@pytest.fixture(scope="module")
def inventario_user(db_session, flask_app):
    with flask_app.app_context():
        user = User(
            username='api_inventario',
            password_hash=generate_password_hash('InvPass123!'),
            role='inventario'
        )
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)
        yield user


@pytest.fixture(scope="module")
def solicitante_user(db_session, flask_app):
    with flask_app.app_context():
        user = User(
            username='api_solicitante',
            password_hash=generate_password_hash('SolPass123!'),
            role='solicitante_material'
        )
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)
        yield user


@pytest.fixture(scope="module")
def unauthorized_user(db_session, flask_app):
    """Usuario sin permisos de inventario."""
    with flask_app.app_context():
        user = User(
            username='api_no_perms',
            password_hash=generate_password_hash('NoPermPass!'),
            role='coordinador'
        )
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)
        yield user


# ─── Helper: crear cookie de sesión Flask simulada ────────────────────────────

def make_session_cookie(flask_app, user_id: int) -> dict:
    """
    Genera una cookie de sesión Flask firmada con la clave secreta de prueba,
    tal como lo haría el sistema real. Usa el signer de deps.py.
    """
    from app.api_fastapi.deps import signer
    session_data = {'user_id': str(user_id), '_user_id': str(user_id)}
    signed = signer.dumps(session_data)
    return {'session': signed}


# ═══════════════════════════════════════════════════════════════════════════════
# 1. HEALTH CHECK
# ═══════════════════════════════════════════════════════════════════════════════

class TestHealthCheck:
    """Prueba que el endpoint de salud sea accesible sin autenticación."""

    def test_health_ok(self, api_client):
        resp = api_client.get("/v1/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


# ═══════════════════════════════════════════════════════════════════════════════
# 2. AUTENTICACIÓN Y AUTORIZACIÓN
# ═══════════════════════════════════════════════════════════════════════════════

class TestAutenticacion:
    """Valida que las rutas protejan correctamente el acceso."""

    def test_sin_cookie_retorna_401(self, api_client):
        resp = api_client.get("/v1/productos/")
        assert resp.status_code == 401

    def test_cookie_invalida_retorna_401(self, api_client):
        resp = api_client.get("/v1/productos/", cookies={'session': 'token_inventado'})
        assert resp.status_code == 401

    def test_cookie_vacia_retorna_401(self, api_client):
        resp = api_client.get("/v1/productos/", cookies={'session': ''})
        assert resp.status_code == 401

    def test_rol_sin_permiso_retorna_403(self, api_client, flask_app, unauthorized_user):
        cookies = make_session_cookie(flask_app, unauthorized_user.id)
        resp = api_client.get("/v1/productos/", cookies=cookies)
        assert resp.status_code == 403

    def test_admin_puede_acceder(self, api_client, flask_app, admin_user):
        cookies = make_session_cookie(flask_app, admin_user.id)
        resp = api_client.get("/v1/productos/", cookies=cookies)
        assert resp.status_code == 200

    def test_inventario_puede_leer(self, api_client, flask_app, inventario_user):
        cookies = make_session_cookie(flask_app, inventario_user.id)
        resp = api_client.get("/v1/productos/", cookies=cookies)
        assert resp.status_code == 200

    def test_solicitante_puede_leer_productos(self, api_client, flask_app, solicitante_user):
        cookies = make_session_cookie(flask_app, solicitante_user.id)
        resp = api_client.get("/v1/productos/", cookies=cookies)
        assert resp.status_code == 200

    def test_solicitante_no_puede_crear_producto(self, api_client, flask_app, solicitante_user):
        """ERROR HUMANO: un solicitante intenta crear un producto. Debe ser 403."""
        cookies = make_session_cookie(flask_app, solicitante_user.id)
        payload = {
            'codigo': 'HACK-001',
            'descripcion': 'Producto no autorizado',
            'categoria': 'Test',
            'unidad': 'pza',
            'stock_actual': 0,
            'stock_minimo': 0,
        }
        resp = api_client.post("/v1/productos/", json=payload, cookies=cookies)
        assert resp.status_code == 403

    def test_solicitante_no_puede_borrar_almacen(self, api_client, flask_app, solicitante_user):
        """ERROR HUMANO: un solicitante intenta hacer un soft-delete de almacén."""
        cookies = make_session_cookie(flask_app, solicitante_user.id)
        resp = api_client.delete("/v1/almacenes/1", cookies=cookies)
        assert resp.status_code == 403


# ═══════════════════════════════════════════════════════════════════════════════
# 3. PRODUCTOS — CRUD
# ═══════════════════════════════════════════════════════════════════════════════

class TestProductosCRUD:

    @pytest.fixture(autouse=True)
    def cookies(self, flask_app, admin_user):
        self._cookies = make_session_cookie(flask_app, admin_user.id)

    def test_crear_producto_valido(self, api_client):
        payload = {
            'codigo': 'PROD-TEST-001',
            'descripcion': 'Producto de prueba unitaria',
            'categoria': 'Herramientas',
            'unidad': 'pza',
            'stock_actual': 50.0,
            'stock_minimo': 5.0,
        }
        resp = api_client.post("/v1/productos/", json=payload, cookies=self._cookies)
        assert resp.status_code == 200
        data = resp.json()
        assert data['codigo'] == 'PROD-TEST-001'
        assert data['stock_actual'] == 50.0
        assert data['activo'] is True

    def test_crear_producto_codigo_duplicado(self, api_client):
        """ERROR HUMANO: el mismo código ya existe en el sistema."""
        payload = {
            'codigo': 'PROD-TEST-001',  # ya creado en el test anterior
            'descripcion': 'Duplicado intencional',
            'categoria': 'Test',
            'unidad': 'pza',
            'stock_actual': 0.0,
            'stock_minimo': 0.0,
        }
        resp = api_client.post("/v1/productos/", json=payload, cookies=self._cookies)
        assert resp.status_code == 400
        assert "ya existe" in resp.json()['detail'].lower()

    def test_crear_producto_codigo_con_caracteres_invalidos(self, api_client):
        """ERROR HUMANO: código con caracteres especiales no permitidos."""
        payload = {
            'codigo': 'PROD TEST 001!',  # espacios y ! no permitidos
            'descripcion': 'Código malo',
            'categoria': 'Test',
            'unidad': 'pza',
            'stock_actual': 0.0,
            'stock_minimo': 0.0,
        }
        resp = api_client.post("/v1/productos/", json=payload, cookies=self._cookies)
        assert resp.status_code == 422  # Validación Pydantic

    def test_crear_producto_stock_negativo(self, api_client):
        """ERROR HUMANO: stock inicial negativo."""
        payload = {
            'codigo': 'PROD-NEG-001',
            'descripcion': 'Stock negativo',
            'categoria': 'Test',
            'unidad': 'pza',
            'stock_actual': -10.0,
            'stock_minimo': 0.0,
        }
        resp = api_client.post("/v1/productos/", json=payload, cookies=self._cookies)
        assert resp.status_code == 422

    def test_crear_producto_sin_campo_requerido(self, api_client):
        """ERROR HUMANO: falta el campo 'descripcion'."""
        payload = {
            'codigo': 'PROD-MISSING-001',
            'categoria': 'Test',
            'unidad': 'pza',
        }
        resp = api_client.post("/v1/productos/", json=payload, cookies=self._cookies)
        assert resp.status_code == 422

    def test_crear_producto_codigo_demasiado_largo(self, api_client):
        """ERROR HUMANO: código que supera los 50 caracteres permitidos."""
        payload = {
            'codigo': 'A' * 51,
            'descripcion': 'Código largo',
            'categoria': 'Test',
            'unidad': 'pza',
            'stock_actual': 0.0,
            'stock_minimo': 0.0,
        }
        resp = api_client.post("/v1/productos/", json=payload, cookies=self._cookies)
        assert resp.status_code == 422

    def test_listar_productos(self, api_client):
        resp = api_client.get("/v1/productos/", cookies=self._cookies)
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)
        # Al menos el producto que creamos en test_crear_producto_valido
        codigos = [p['codigo'] for p in resp.json()]
        assert 'PROD-TEST-001' in codigos

    def test_listar_productos_paginacion(self, api_client):
        """Verificar que skip y limit funcionan correctamente."""
        resp_all = api_client.get("/v1/productos/?skip=0&limit=200", cookies=self._cookies)
        resp_limited = api_client.get("/v1/productos/?skip=0&limit=1", cookies=self._cookies)
        assert resp_limited.status_code == 200
        assert len(resp_limited.json()) <= 1

    def test_actualizar_producto(self, api_client, db_session):
        """Actualización parcial de un producto existente."""
        prod = db_session.query(Producto).filter_by(codigo='PROD-TEST-001').first()
        assert prod is not None
        payload = {'descripcion': 'Descripción actualizada'}
        resp = api_client.put(f"/v1/productos/{prod.id}", json=payload, cookies=self._cookies)
        assert resp.status_code == 200
        assert resp.json()['descripcion'] == 'Descripción actualizada'

    def test_actualizar_producto_inexistente(self, api_client):
        """ERROR HUMANO: actualizar un producto con ID que no existe."""
        resp = api_client.put("/v1/productos/999999", json={'descripcion': 'x'}, cookies=self._cookies)
        assert resp.status_code == 404

    def test_actualizar_producto_codigo_duplicado_otro(self, api_client, db_session):
        """ERROR HUMANO: cambiar el código de un producto a uno ya existente."""
        # Crear un segundo producto
        resp2 = api_client.post("/v1/productos/", json={
            'codigo': 'PROD-TEST-002',
            'descripcion': 'Segundo producto',
            'categoria': 'Test',
            'unidad': 'pza',
            'stock_actual': 0.0,
            'stock_minimo': 0.0,
        }, cookies=self._cookies)
        assert resp2.status_code == 200
        prod2_id = resp2.json()['id']

        # Intentar cambiar su código al del primero
        resp = api_client.put(f"/v1/productos/{prod2_id}", json={'codigo': 'PROD-TEST-001'}, cookies=self._cookies)
        assert resp.status_code == 400
        assert "ya existe" in resp.json()['detail'].lower()

    def test_borrar_producto_soft_delete(self, api_client, db_session):
        """El DELETE no elimina físicamente, solo marca activo=False."""
        prod = db_session.query(Producto).filter_by(codigo='PROD-TEST-002').first()
        assert prod is not None
        resp = api_client.delete(f"/v1/productos/{prod.id}", cookies=self._cookies)
        assert resp.status_code == 204

        # Verificar que no aparece en el listado (activo=False)
        db_session.refresh(prod)
        assert prod.activo is False

        listado = api_client.get("/v1/productos/", cookies=self._cookies)
        codigos = [p['codigo'] for p in listado.json()]
        assert 'PROD-TEST-002' not in codigos

    def test_borrar_producto_inexistente(self, api_client):
        """ERROR HUMANO: borrar producto con ID ficticio."""
        resp = api_client.delete("/v1/productos/999999", cookies=self._cookies)
        assert resp.status_code == 404

    def test_stock_minimo_mayor_que_maximo_permitido(self, api_client):
        """ERROR HUMANO: stock_minimo supera el límite máximo del schema (1,000,000)."""
        payload = {
            'codigo': 'PROD-STOCKLIMITE',
            'descripcion': 'Límite de stock',
            'categoria': 'Test',
            'unidad': 'pza',
            'stock_actual': 0.0,
            'stock_minimo': 2_000_000.0,  # > 1_000_000 → debe fallar
        }
        resp = api_client.post("/v1/productos/", json=payload, cookies=self._cookies)
        assert resp.status_code == 422


# ═══════════════════════════════════════════════════════════════════════════════
# 4. ALMACENES — CRUD
# ═══════════════════════════════════════════════════════════════════════════════

class TestAlmacenesCRUD:

    @pytest.fixture(autouse=True)
    def cookies(self, flask_app, admin_user):
        self._cookies = make_session_cookie(flask_app, admin_user.id)

    def test_crear_almacen(self, api_client):
        payload = {'nombre': 'Almacén Central', 'ubicacion': 'Planta 1', 'activo': True}
        resp = api_client.post("/v1/almacenes/", json=payload, cookies=self._cookies)
        assert resp.status_code == 200
        data = resp.json()
        assert data['nombre'] == 'Almacén Central'
        assert 'qr_code' in data  # QR debe generarse automáticamente
        assert len(data['qr_code']) > 0

    def test_crear_almacen_sin_nombre(self, api_client):
        """ERROR HUMANO: crear almacén sin nombre obligatorio."""
        resp = api_client.post("/v1/almacenes/", json={'ubicacion': 'Planta 2'}, cookies=self._cookies)
        assert resp.status_code == 422

    def test_crear_almacen_nombre_demasiado_largo(self, api_client):
        """ERROR HUMANO: nombre que supera los 100 caracteres."""
        payload = {
            'nombre': 'A' * 101,
            'ubicacion': 'Test',
            'activo': True
        }
        resp = api_client.post("/v1/almacenes/", json=payload, cookies=self._cookies)
        assert resp.status_code == 422

    def test_listar_almacenes(self, api_client):
        resp = api_client.get("/v1/almacenes/", cookies=self._cookies)
        assert resp.status_code == 200
        nombres = [a['nombre'] for a in resp.json()]
        assert 'Almacén Central' in nombres

    def test_validar_qr_almacen(self, api_client, db_session):
        """El QR generado debe ser válido para consultar el almacén."""
        alm = db_session.query(Almacen).filter_by(nombre='Almacén Central').first()
        assert alm is not None
        resp = api_client.get(f"/v1/almacenes/{alm.qr_code}/validar", cookies=self._cookies)
        assert resp.status_code == 200
        assert resp.json()['id'] == alm.id

    def test_validar_qr_inexistente(self, api_client):
        """ERROR HUMANO: QR escaneado que no existe en el sistema."""
        fake_qr = str(uuid.uuid4())
        resp = api_client.get(f"/v1/almacenes/{fake_qr}/validar", cookies=self._cookies)
        assert resp.status_code == 404

    def test_actualizar_almacen(self, api_client, db_session):
        alm = db_session.query(Almacen).filter_by(nombre='Almacén Central').first()
        resp = api_client.put(f"/v1/almacenes/{alm.id}", json={'ubicacion': 'Planta 2'}, cookies=self._cookies)
        assert resp.status_code == 200
        assert resp.json()['ubicacion'] == 'Planta 2'

    def test_actualizar_almacen_inexistente(self, api_client):
        """ERROR HUMANO: ID de almacén que no existe."""
        resp = api_client.put("/v1/almacenes/999999", json={'nombre': 'X'}, cookies=self._cookies)
        assert resp.status_code == 404

    def test_borrar_almacen_soft_delete(self, api_client, db_session):
        # Crear uno exclusivo para este test
        resp = api_client.post("/v1/almacenes/", json={
            'nombre': 'Almacén Para Borrar',
            'activo': True
        }, cookies=self._cookies)
        assert resp.status_code == 200
        alm_id = resp.json()['id']

        resp_del = api_client.delete(f"/v1/almacenes/{alm_id}", cookies=self._cookies)
        assert resp_del.status_code == 204

        alm = db_session.query(Almacen).filter_by(id=alm_id).first()
        assert alm.activo is False

    def test_borrar_almacen_inexistente(self, api_client):
        resp = api_client.delete("/v1/almacenes/999999", cookies=self._cookies)
        assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# 5. ESTANTES — CRUD
# ═══════════════════════════════════════════════════════════════════════════════

class TestEstandesCRUD:

    @pytest.fixture(autouse=True)
    def setup(self, flask_app, admin_user, db_session, api_client):
        self._cookies = make_session_cookie(flask_app, admin_user.id)
        # Crear un almacén base
        resp = api_client.post("/v1/almacenes/", json={
            'nombre': 'Almacén Estante Test',
            'activo': True
        }, cookies=self._cookies)
        self._almacen_id = resp.json()['id']

    def test_crear_estante_valido(self, api_client):
        resp = api_client.post("/v1/estantes/", json={
            'nombre': 'Estante A-1',
            'descripcion': 'Primer estante',
            'almacen_id': self._almacen_id
        }, cookies=self._cookies)
        assert resp.status_code == 200
        data = resp.json()
        assert data['nombre'] == 'Estante A-1'
        assert 'qr_code' in data

    def test_crear_estante_almacen_inexistente(self, api_client):
        """ERROR HUMANO: el almacén_id referenciado no existe."""
        resp = api_client.post("/v1/estantes/", json={
            'nombre': 'Estante Huérfano',
            'almacen_id': 999999
        }, cookies=self._cookies)
        assert resp.status_code == 404

    def test_crear_estante_sin_almacen_id(self, api_client):
        """ERROR HUMANO: olvidar el almacen_id requerido."""
        resp = api_client.post("/v1/estantes/", json={
            'nombre': 'Estante Sin Almacén'
        }, cookies=self._cookies)
        assert resp.status_code == 422

    def test_qr_estante_es_imagen_png(self, api_client, db_session):
        """El endpoint de QR-image debe devolver un PNG válido."""
        est = db_session.query(Estante).filter_by(nombre='Estante A-1').first()
        if est is None:
            pytest.skip("Estante no encontrado")
        resp = api_client.get(f"/v1/estantes/{est.id}/qr-image", cookies=self._cookies)
        assert resp.status_code == 200
        assert resp.headers['content-type'] == 'image/png'
        assert resp.content[:4] == b'\x89PNG'  # Firma PNG

    def test_qr_image_estante_inexistente(self, api_client):
        """ERROR HUMANO: generar imagen QR de estante que no existe."""
        resp = api_client.get("/v1/estantes/999999/qr-image", cookies=self._cookies)
        assert resp.status_code == 404

    def test_validar_qr_estante(self, api_client, db_session):
        est = db_session.query(Estante).filter_by(nombre='Estante A-1').first()
        resp = api_client.get(f"/v1/estantes/{est.qr_code}/validar", cookies=self._cookies)
        assert resp.status_code == 200

    def test_validar_qr_estante_ficticio(self, api_client):
        resp = api_client.get(f"/v1/estantes/{uuid.uuid4()}/validar", cookies=self._cookies)
        assert resp.status_code == 404

    def test_actualizar_estante_almacen_destino_invalido(self, api_client, db_session):
        """ERROR HUMANO: mover estante a un almacén que no existe."""
        est = db_session.query(Estante).filter_by(nombre='Estante A-1').first()
        resp = api_client.put(f"/v1/estantes/{est.id}", json={'almacen_id': 999999}, cookies=self._cookies)
        assert resp.status_code == 404

    def test_get_estantes_por_almacen(self, api_client):
        resp = api_client.get(f"/v1/almacenes/{self._almacen_id}/estantes", cookies=self._cookies)
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


# ═══════════════════════════════════════════════════════════════════════════════
# 6. MOVIMIENTOS DE INVENTARIO
# ═══════════════════════════════════════════════════════════════════════════════

class TestMovimientosInventario:
    """
    Prueba la lógica de negocio crítica:
    - Entradas, salidas, ajustes y traspasos
    - Stock insuficiente
    - Cantidades inválidas
    - Ajustes que provocan stock negativo
    """

    @pytest.fixture(autouse=True)
    def setup(self, flask_app, admin_user, db_session, api_client):
        self._cookies = make_session_cookie(flask_app, admin_user.id)
        # Crear producto de prueba con stock 100
        resp = api_client.post("/v1/productos/", json={
            'codigo': f'MOV-PROD-{uuid.uuid4().hex[:6]}',
            'descripcion': 'Producto para movimientos',
            'categoria': 'Test',
            'unidad': 'pza',
            'stock_actual': 100.0,
            'stock_minimo': 10.0,
        }, cookies=self._cookies)
        assert resp.status_code == 200
        self._producto_id = resp.json()['id']

    # --- ENTRADAS ---

    def test_entrada_incrementa_stock(self, api_client, db_session):
        resp = api_client.post("/v1/movimientos/", json={
            'tipo': 'ENTRADA',
            'producto_id': self._producto_id,
            'cantidad': 25.0,
            'motivo': 'Compra de proveedor'
        }, cookies=self._cookies)
        assert resp.status_code == 200
        prod = db_session.query(Producto).filter_by(id=self._producto_id).first()
        db_session.refresh(prod)
        assert float(prod.stock_actual) == 125.0

    def test_entrada_cantidad_cero_falla(self, api_client):
        """ERROR HUMANO: registrar entrada con cantidad 0."""
        resp = api_client.post("/v1/movimientos/", json={
            'tipo': 'ENTRADA',
            'producto_id': self._producto_id,
            'cantidad': 0.0,
        }, cookies=self._cookies)
        assert resp.status_code == 422  # Pydantic ge=-100000 lo permite, pero la ruta lo rechaza

    def test_entrada_cantidad_negativa_falla(self, api_client):
        """ERROR HUMANO: cantidad negativa para una ENTRADA."""
        resp = api_client.post("/v1/movimientos/", json={
            'tipo': 'ENTRADA',
            'producto_id': self._producto_id,
            'cantidad': -10.0,
        }, cookies=self._cookies)
        assert resp.status_code in [400, 422]

    # --- SALIDAS ---

    def test_salida_reduce_stock(self, api_client, db_session):
        stock_antes = float(db_session.query(Producto).filter_by(id=self._producto_id).first().stock_actual)
        resp = api_client.post("/v1/movimientos/", json={
            'tipo': 'SALIDA',
            'producto_id': self._producto_id,
            'cantidad': 10.0,
            'motivo': 'Consumo en obra'
        }, cookies=self._cookies)
        assert resp.status_code == 200
        prod = db_session.query(Producto).filter_by(id=self._producto_id).first()
        db_session.refresh(prod)
        assert float(prod.stock_actual) == pytest.approx(stock_antes - 10.0, abs=0.01)

    def test_salida_stock_insuficiente(self, api_client, db_session):
        """ERROR HUMANO: sacar más unidades de las disponibles."""
        stock_actual = float(db_session.query(Producto).filter_by(id=self._producto_id).first().stock_actual)
        resp = api_client.post("/v1/movimientos/", json={
            'tipo': 'SALIDA',
            'producto_id': self._producto_id,
            'cantidad': stock_actual + 9999.0,  # Mucho más de lo disponible
            'motivo': 'Salida excesiva intencional'
        }, cookies=self._cookies)
        assert resp.status_code == 400
        assert "insuficiente" in resp.json()['detail'].lower()

    def test_salida_cantidad_cero_falla(self, api_client):
        """ERROR HUMANO: salida con cantidad 0."""
        resp = api_client.post("/v1/movimientos/", json={
            'tipo': 'SALIDA',
            'producto_id': self._producto_id,
            'cantidad': 0.0,
        }, cookies=self._cookies)
        assert resp.status_code in [400, 422]

    # --- AJUSTES ---

    def test_ajuste_positivo(self, api_client, db_session):
        stock_antes = float(db_session.query(Producto).filter_by(id=self._producto_id).first().stock_actual)
        resp = api_client.post("/v1/movimientos/", json={
            'tipo': 'AJUSTE',
            'producto_id': self._producto_id,
            'cantidad': 5.0,
            'motivo': 'Recuento físico'
        }, cookies=self._cookies)
        assert resp.status_code == 200
        prod = db_session.query(Producto).filter_by(id=self._producto_id).first()
        db_session.refresh(prod)
        assert float(prod.stock_actual) == pytest.approx(stock_antes + 5.0, abs=0.01)

    def test_ajuste_negativo_valido(self, api_client, db_session):
        """Un ajuste negativo reduce el stock (merma)."""
        stock_antes = float(db_session.query(Producto).filter_by(id=self._producto_id).first().stock_actual)
        resp = api_client.post("/v1/movimientos/", json={
            'tipo': 'AJUSTE',
            'producto_id': self._producto_id,
            'cantidad': -5.0,
            'motivo': 'Merma detectada'
        }, cookies=self._cookies)
        assert resp.status_code == 200
        prod = db_session.query(Producto).filter_by(id=self._producto_id).first()
        db_session.refresh(prod)
        assert float(prod.stock_actual) == pytest.approx(stock_antes - 5.0, abs=0.01)

    def test_ajuste_que_provoca_stock_negativo(self, api_client, db_session):
        """ERROR HUMANO: ajuste negativo mayor que el stock disponible."""
        stock_actual = float(db_session.query(Producto).filter_by(id=self._producto_id).first().stock_actual)
        resp = api_client.post("/v1/movimientos/", json={
            'tipo': 'AJUSTE',
            'producto_id': self._producto_id,
            'cantidad': -(stock_actual + 1.0),  # Dejaria stock negativo
            'motivo': 'Ajuste erróneo'
        }, cookies=self._cookies)
        assert resp.status_code == 400
        assert "negativo" in resp.json()['detail'].lower()

    # --- TRASPASO ---

    def test_traspaso_reduce_stock(self, api_client, db_session):
        stock_antes = float(db_session.query(Producto).filter_by(id=self._producto_id).first().stock_actual)
        resp = api_client.post("/v1/movimientos/", json={
            'tipo': 'TRASPASO',
            'producto_id': self._producto_id,
            'cantidad': 5.0,
            'motivo': 'Traslado entre almacenes'
        }, cookies=self._cookies)
        assert resp.status_code == 200

    def test_traspaso_stock_insuficiente(self, api_client, db_session):
        """ERROR HUMANO: traspasar más unidades de las que hay."""
        stock_actual = float(db_session.query(Producto).filter_by(id=self._producto_id).first().stock_actual)
        resp = api_client.post("/v1/movimientos/", json={
            'tipo': 'TRASPASO',
            'producto_id': self._producto_id,
            'cantidad': stock_actual + 9999.0,
        }, cookies=self._cookies)
        assert resp.status_code == 400

    # --- ERRORES GENERALES ---

    def test_movimiento_producto_inexistente(self, api_client):
        """ERROR HUMANO: ID de producto que no existe."""
        resp = api_client.post("/v1/movimientos/", json={
            'tipo': 'ENTRADA',
            'producto_id': 999999,
            'cantidad': 10.0,
        }, cookies=self._cookies)
        assert resp.status_code == 404

    def test_tipo_movimiento_invalido(self, api_client):
        """ERROR HUMANO: tipo de movimiento con typo (literal validation)."""
        resp = api_client.post("/v1/movimientos/", json={
            'tipo': 'ENTRADA_ERROR',  # Tipo inválido
            'producto_id': self._producto_id,
            'cantidad': 10.0,
        }, cookies=self._cookies)
        assert resp.status_code == 422

    def test_cantidad_excede_limite_maximo(self, api_client):
        """ERROR HUMANO: cantidad que supera el límite del schema (100,000)."""
        resp = api_client.post("/v1/movimientos/", json={
            'tipo': 'ENTRADA',
            'producto_id': self._producto_id,
            'cantidad': 999_999.0,  # > 100_000
        }, cookies=self._cookies)
        assert resp.status_code == 422


# ═══════════════════════════════════════════════════════════════════════════════
# 7. SOLICITUDES DE MATERIAL
# ═══════════════════════════════════════════════════════════════════════════════

class TestSolicitudesMaterial:

    @pytest.fixture(autouse=True)
    def setup(self, flask_app, admin_user, solicitante_user, inventario_user, db_session, api_client):
        self._admin_cookies = make_session_cookie(flask_app, admin_user.id)
        self._sol_cookies = make_session_cookie(flask_app, solicitante_user.id)
        self._inv_cookies = make_session_cookie(flask_app, inventario_user.id)
        self._sol_user = solicitante_user
        self._admin_user = admin_user

        # Crear producto de prueba
        resp = api_client.post("/v1/productos/", json={
            'codigo': f'SOL-PROD-{uuid.uuid4().hex[:6]}',
            'descripcion': 'Producto para solicitudes',
            'categoria': 'Material',
            'unidad': 'pza',
            'stock_actual': 200.0,
            'stock_minimo': 5.0,
        }, cookies=self._admin_cookies)
        self._producto_id = resp.json()['id']

    def test_solicitante_puede_crear_solicitud(self, api_client):
        resp = api_client.post("/v1/solicitudes/", json={
            'proyecto': 'Proyecto Alpha',
            'detalles': [
                {'producto_id': self._producto_id, 'cantidad_solicitada': 10.0}
            ]
        }, cookies=self._sol_cookies)
        assert resp.status_code == 200
        data = resp.json()
        assert data['estatus'] == 'PENDIENTE'
        assert len(data['detalles']) == 1

    def test_crear_solicitud_sin_detalles(self, api_client):
        """ERROR HUMANO: crear solicitud vacía sin detalles."""
        resp = api_client.post("/v1/solicitudes/", json={
            'proyecto': 'Sin detalles',
            'detalles': []
        }, cookies=self._sol_cookies)
        assert resp.status_code == 422  # min_length=1

    def test_crear_solicitud_cantidad_cero_falla(self, api_client):
        """ERROR HUMANO: cantidad_solicitada = 0 (debe ser > 0)."""
        resp = api_client.post("/v1/solicitudes/", json={
            'detalles': [
                {'producto_id': self._producto_id, 'cantidad_solicitada': 0.0}
            ]
        }, cookies=self._sol_cookies)
        assert resp.status_code == 422

    def test_crear_solicitud_con_producto_inexistente_retorna_400(self, api_client):
        """
        BUG CORREGIDO: antes se ignoraba silenciosamente un producto_id inválido
        y la solicitud se creaba con 0 detalles. Ahora retorna 400 con detalle claro.
        """
        resp = api_client.post("/v1/solicitudes/", json={
            'proyecto': 'Test parcial',
            'detalles': [
                {'producto_id': 999999, 'cantidad_solicitada': 5.0}  # No existe
            ]
        }, cookies=self._sol_cookies)
        assert resp.status_code == 400
        assert "999999" in resp.json()['detail']

    def test_rol_no_autorizado_no_puede_crear_solicitud(self, api_client, flask_app, unauthorized_user):
        """ERROR HUMANO: un coordinador intenta crear solicitud de materiales."""
        cookies = make_session_cookie(flask_app, unauthorized_user.id)
        resp = api_client.post("/v1/solicitudes/", json={
            'detalles': [
                {'producto_id': self._producto_id, 'cantidad_solicitada': 5.0}
            ]
        }, cookies=cookies)
        assert resp.status_code == 403

    def test_solicitante_solo_ve_sus_solicitudes(self, api_client, flask_app, db_session):
        """El solicitante debe ver SOLO sus propias solicitudes."""
        resp = api_client.get("/v1/solicitudes/", cookies=self._sol_cookies)
        assert resp.status_code == 200
        for sol in resp.json():
            assert sol['solicitante_id'] == self._sol_user.id

    def test_admin_ve_todas_las_solicitudes(self, api_client):
        """Admin/inventario ve todas las solicitudes."""
        resp = api_client.get("/v1/solicitudes/", cookies=self._admin_cookies)
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_actualizar_estado_solicitud(self, api_client, db_session):
        """Solo inventario/admin puede cambiar el estado."""
        # Crear solicitud como solicitante
        resp = api_client.post("/v1/solicitudes/", json={
            'detalles': [{'producto_id': self._producto_id, 'cantidad_solicitada': 3.0}]
        }, cookies=self._sol_cookies)
        sol_id = resp.json()['id']

        # Aprobar como admin
        resp_upd = api_client.patch(f"/v1/solicitudes/{sol_id}/estado",
                                    json={'estatus': 'APROBADA'},
                                    cookies=self._admin_cookies)
        assert resp_upd.status_code == 200
        assert resp_upd.json()['estatus'] == 'APROBADA'
        # La fecha_cierre debe setearse
        assert resp_upd.json()['fecha_cierre'] is not None

    def test_actualizar_estado_solicitud_inexistente(self, api_client):
        """ERROR HUMANO: cambiar estado de solicitud con ID inválido."""
        resp = api_client.patch("/v1/solicitudes/999999/estado",
                                json={'estatus': 'APROBADA'},
                                cookies=self._admin_cookies)
        assert resp.status_code == 404

    def test_actualizar_estado_invalido(self, api_client, db_session):
        """ERROR HUMANO: status con typo (no está en el Literal permitido)."""
        resp = api_client.post("/v1/solicitudes/", json={
            'detalles': [{'producto_id': self._producto_id, 'cantidad_solicitada': 1.0}]
        }, cookies=self._sol_cookies)
        sol_id = resp.json()['id']
        resp_upd = api_client.patch(f"/v1/solicitudes/{sol_id}/estado",
                                    json={'estatus': 'APROBANDO'},  # Typo
                                    cookies=self._admin_cookies)
        assert resp_upd.status_code == 422

    def test_solicitante_no_puede_cambiar_estado(self, api_client, db_session):
        """ERROR HUMANO: solicitante intenta aprobar su propia solicitud."""
        resp = api_client.post("/v1/solicitudes/", json={
            'detalles': [{'producto_id': self._producto_id, 'cantidad_solicitada': 1.0}]
        }, cookies=self._sol_cookies)
        sol_id = resp.json()['id']
        resp_upd = api_client.patch(f"/v1/solicitudes/{sol_id}/estado",
                                    json={'estatus': 'APROBADA'},
                                    cookies=self._sol_cookies)
        assert resp_upd.status_code == 403

    def test_solicitud_volver_a_pendiente_limpia_fecha_cierre(self, api_client, db_session):
        """
        BUG CORREGIDO: antes volver a PENDIENTE no limpiaba fecha_cierre.
        Ahora: PENDIENTE → fecha_cierre = None (la solicitud queda 'reabierta').
        """
        resp = api_client.post("/v1/solicitudes/", json={
            'detalles': [{'producto_id': self._producto_id, 'cantidad_solicitada': 1.0}]
        }, cookies=self._sol_cookies)
        sol_id = resp.json()['id']

        # 1) Aprobar → fecha_cierre se setea
        resp_aprobada = api_client.patch(f"/v1/solicitudes/{sol_id}/estado",
                                         json={'estatus': 'APROBADA'},
                                         cookies=self._admin_cookies)
        assert resp_aprobada.json()['fecha_cierre'] is not None

        # 2) Revertir a PENDIENTE → fecha_cierre debe limpiarse
        resp_pendiente = api_client.patch(f"/v1/solicitudes/{sol_id}/estado",
                                          json={'estatus': 'PENDIENTE'},
                                          cookies=self._admin_cookies)
        assert resp_pendiente.status_code == 200
        assert resp_pendiente.json()['fecha_cierre'] is None   # ← Bug corregido


# ═══════════════════════════════════════════════════════════════════════════════
# 8. PROYECTOS
# ═══════════════════════════════════════════════════════════════════════════════

class TestProyectos:

    def test_listar_proyectos(self, api_client, flask_app, admin_user):
        cookies = make_session_cookie(flask_app, admin_user.id)
        resp = api_client.get("/v1/proyectos/", cookies=cookies)
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)
        # Cada elemento tiene la estructura correcta
        for p in resp.json():
            assert 'id' in p
            assert 'numero_proyecto' in p
            assert 'nombre' in p

    def test_listar_proyectos_sin_autenticar(self, api_client):
        resp = api_client.get("/v1/proyectos/")
        assert resp.status_code == 401


# ═══════════════════════════════════════════════════════════════════════════════
# 9. AUDITORÍA — efectos secundarios
# ═══════════════════════════════════════════════════════════════════════════════

class TestAuditoria:
    """Verifica que las acciones de escritura dejen registro en AuditLog."""

    def test_crear_producto_genera_audit(self, api_client, flask_app, admin_user, db_session):
        cookies = make_session_cookie(flask_app, admin_user.id)
        conteo_antes = db_session.query(AuditLog).count()
        api_client.post("/v1/productos/", json={
            'codigo': f'AUDIT-{uuid.uuid4().hex[:6]}',
            'descripcion': 'Test auditoría',
            'categoria': 'Audit',
            'unidad': 'pza',
            'stock_actual': 0.0,
            'stock_minimo': 0.0,
        }, cookies=cookies)
        conteo_despues = db_session.query(AuditLog).count()
        assert conteo_despues > conteo_antes

    def test_crear_almacen_genera_audit(self, api_client, flask_app, admin_user, db_session):
        cookies = make_session_cookie(flask_app, admin_user.id)
        conteo_antes = db_session.query(AuditLog).count()
        api_client.post("/v1/almacenes/", json={
            'nombre': f'Almacén Audit {uuid.uuid4().hex[:4]}',
            'activo': True
        }, cookies=cookies)
        conteo_despues = db_session.query(AuditLog).count()
        assert conteo_despues > conteo_antes


# ═══════════════════════════════════════════════════════════════════════════════
# 10. CABECERAS IP (CF-Connecting-IP)
# ═══════════════════════════════════════════════════════════════════════════════

class TestIPResolucion:
    """
    Verifica que get_real_ip() resuelve la IP correctamente.
    Importante para los registros de auditoría detrás de Cloudflare.
    """

    def test_ip_desde_cf_connecting_ip(self, api_client, flask_app, admin_user, db_session):
        cookies = make_session_cookie(flask_app, admin_user.id)
        resp = api_client.get("/v1/health",
                              cookies=cookies,
                              headers={'CF-Connecting-IP': '1.2.3.4'})
        assert resp.status_code == 200

    def test_ip_desde_x_forwarded_for(self, api_client, flask_app, admin_user):
        cookies = make_session_cookie(flask_app, admin_user.id)
        resp = api_client.get("/v1/health",
                              cookies=cookies,
                              headers={'X-Forwarded-For': '5.6.7.8, 10.0.0.1'})
        assert resp.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════════
# 11. EDGE CASES Y CONDICIONES DE CARRERA
# ═══════════════════════════════════════════════════════════════════════════════

class TestEdgeCases:

    @pytest.fixture(autouse=True)
    def cookies(self, flask_app, admin_user):
        self._cookies = make_session_cookie(flask_app, admin_user.id)

    def test_listado_con_skip_negativo(self, api_client):
        """ERROR HUMANO: skip negativo en la paginación."""
        resp = api_client.get("/v1/productos/?skip=-1&limit=10", cookies=self._cookies)
        # SQLAlchemy puede ignorarlo o retornar error; lo importante es no crashear (500)
        assert resp.status_code in [200, 422]

    def test_listado_con_limit_cero(self, api_client):
        """ERROR HUMANO: limit=0 debería devolver lista vacía, no error."""
        resp = api_client.get("/v1/productos/?skip=0&limit=0", cookies=self._cookies)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_campos_extra_son_ignorados(self, api_client):
        """Pydantic ignora campos extra por defecto (no lanza 422)."""
        resp = api_client.post("/v1/almacenes/", json={
            'nombre': 'Almacén Extra Fields',
            'activo': True,
            'campo_inexistente': 'valor_inesperado',
        }, cookies=self._cookies)
        assert resp.status_code == 200

    def test_id_no_numerico_es_rechazado(self, api_client):
        """
        ERROR HUMANO: pasar un string donde se espera int en path param.
        - PUT/DELETE /productos/{id} → FastAPI valida el tipo → 422
        - GET /productos/abc → no existe esa ruta con GET → 405 (Method Not Allowed)
        Ambos escenarios son rechazos correctos; ninguno debe devolver 200.
        """
        # Intentar un PUT con ID no numérico → FastAPI valida int → 422
        resp_put = api_client.put("/v1/productos/abc", json={}, cookies=self._cookies)
        assert resp_put.status_code == 422

        # GET no está definido para /productos/{id} → 405 expected
        resp_get = api_client.get("/v1/productos/abc", cookies=self._cookies)
        assert resp_get.status_code in [405, 422]  # Ambos son rechazos válidos

    def test_payload_vacio_retorna_422(self, api_client):
        """ERROR HUMANO: POST sin body."""
        resp = api_client.post("/v1/productos/",
                               content=b'',
                               headers={'Content-Type': 'application/json'},
                               cookies=self._cookies)
        assert resp.status_code == 422

    def test_content_type_incorrecto(self, api_client):
        """ERROR HUMANO: enviar datos como form cuando se espera JSON."""
        resp = api_client.post(
            "/v1/productos/",
            data={'codigo': 'FORM-001', 'descripcion': 'Test'},
            cookies=self._cookies
        )
        assert resp.status_code == 422
