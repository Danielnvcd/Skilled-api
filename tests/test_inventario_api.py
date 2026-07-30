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
    User, Almacen, Estante, Producto, StockPorAlmacen, StockAlmacenProyecto,
    MovimientoInventario, Proyecto, Trabajador,
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

    def test_listar_paginado_por_paginas(self, client, inv_admin, db):
        _login(client, inv_admin.id, 'admin')
        # Categoría propia para aislar el conteo de otros productos del fixture.
        for i in range(3):
            db.session.add(Producto(codigo=f'PG-{i}', descripcion=f'P{i}', categoria='PAGINADO',
                                    unidad='pza', stock_actual=0, stock_minimo=0))
        db.session.commit()

        r1 = client.get('/api/v1/productos/paginado?categoria=PAGINADO&per_page=2&page=1')
        assert r1.status_code == 200
        d1 = r1.get_json()
        assert d1['total'] == 3
        assert d1['pages'] == 2
        assert d1['page'] == 1
        assert d1['per_page'] == 2
        assert len(d1['items']) == 2

        r2 = client.get('/api/v1/productos/paginado?categoria=PAGINADO&per_page=2&page=2')
        d2 = r2.get_json()
        assert len(d2['items']) == 1
        # Sin traslape entre páginas (orden determinista por id).
        ids1 = {p['id'] for p in d1['items']}
        ids2 = {p['id'] for p in d2['items']}
        assert ids1.isdisjoint(ids2)

    # ── Filtros avanzados del catálogo ──────────────────────────────────────────

    def test_filtro_stock_bajo(self, client, inv_admin, db):
        _login(client, inv_admin.id, 'admin')
        db.session.add_all([
            Producto(codigo='SB-LOW', descripcion='Bajo', categoria='F', unidad='pza', stock_actual=2, stock_minimo=5),
            Producto(codigo='SB-OK', descripcion='Ok', categoria='F', unidad='pza', stock_actual=50, stock_minimo=5),
        ])
        db.session.commit()
        resp = client.get('/api/v1/productos/?stock=bajo')
        assert resp.status_code == 200
        codigos = {p['codigo'] for p in resp.get_json()}
        assert 'SB-LOW' in codigos
        assert 'SB-OK' not in codigos

    def test_filtro_imagen_sin(self, client, inv_admin, db):
        _login(client, inv_admin.id, 'admin')
        db.session.add_all([
            Producto(codigo='IMG-CON', descripcion='Con', categoria='F', unidad='pza',
                     stock_actual=1, stock_minimo=0, imagen_url='http://x/y.jpg'),
            Producto(codigo='IMG-SIN', descripcion='Sin', categoria='F', unidad='pza',
                     stock_actual=1, stock_minimo=0),
        ])
        db.session.commit()
        resp = client.get('/api/v1/productos/?imagen=sin')
        assert resp.status_code == 200
        codigos = {p['codigo'] for p in resp.get_json()}
        assert 'IMG-SIN' in codigos
        assert 'IMG-CON' not in codigos

    def test_filtro_unidad_y_endpoint_unidades(self, client, inv_admin, db):
        _login(client, inv_admin.id, 'admin')
        db.session.add_all([
            Producto(codigo='U-KG', descripcion='Kilo', categoria='F', unidad='kg', stock_actual=1, stock_minimo=0),
            Producto(codigo='U-PZA', descripcion='Pieza', categoria='F', unidad='pza', stock_actual=1, stock_minimo=0),
        ])
        db.session.commit()
        ru = client.get('/api/v1/productos/unidades/')
        assert ru.status_code == 200
        assert 'kg' in ru.get_json()
        resp = client.get('/api/v1/productos/?unidad=kg')
        assert resp.status_code == 200
        codigos = {p['codigo'] for p in resp.get_json()}
        assert codigos == {'U-KG'}

    def test_filtro_compra_activa(self, client, inv_admin, db):
        from app.models import SolicitudCompra, SolicitudCompraDetalle
        _login(client, inv_admin.id, 'admin')
        p_en = Producto(codigo='C-EN', descripcion='EnCompra', categoria='F', unidad='pza', stock_actual=1, stock_minimo=0)
        p_no = Producto(codigo='C-NO', descripcion='SinCompra', categoria='F', unidad='pza', stock_actual=1, stock_minimo=0)
        db.session.add_all([p_en, p_no]); db.session.flush()
        sc = SolicitudCompra(solicitado_por_id=inv_admin.id, estatus='PENDIENTE')
        db.session.add(sc); db.session.flush()
        db.session.add(SolicitudCompraDetalle(
            solicitud_compra_id=sc.id, producto_id=p_en.id, cantidad_solicitada=10,
        ))
        db.session.commit()
        resp = client.get('/api/v1/productos/?compra=activa')
        assert resp.status_code == 200
        codigos = {p['codigo'] for p in resp.get_json()}
        assert 'C-EN' in codigos
        assert 'C-NO' not in codigos

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
# 3-bis. PRODUCTOS DE CABLE — Tipo + Tamaño obligatorios y unidad forzada a M
# ═══════════════════════════════════════════════════════════════════════════════

class TestProductosCable:

    def test_crear_cable_sin_tipo_tamano_falla(self, client, inv_admin):
        _login(client, inv_admin.id, 'admin')
        resp = client.post('/api/v1/productos/', json={
            'codigo': 'CAB-001', 'descripcion': 'Cable THHN', 'categoria': 'Cable',
            'unidad': 'M', 'stock_actual': 100, 'stock_minimo': 10,
        })
        assert resp.status_code == 422
        assert 'cable' in resp.get_json()['detail'].lower()

    def test_crear_cable_completo_fuerza_unidad_m(self, client, inv_admin):
        _login(client, inv_admin.id, 'admin')
        # Aunque mandemos 'rollo', el backend debe forzar la unidad a 'M'.
        resp = client.post('/api/v1/productos/', json={
            'codigo': 'CAB-002', 'descripcion': 'Cable THHN cal 12', 'categoria': 'Cables',
            'unidad': 'rollo', 'stock_actual': 250.5, 'stock_minimo': 20,
            'cable_tipo': 'THHN', 'cable_calibre': '12',
        })
        assert resp.status_code == 200, resp.get_json()
        data = resp.get_json()
        assert data['unidad'] == 'M'
        assert data['cable_tipo'] == 'THHN'
        assert data['cable_calibre'] == '12'
        # unidad M admite decimales → el stock 250.5 se conserva.
        assert data['stock_actual'] == 250.5

    def test_crear_cable_detecta_categoria_con_acentos_y_espacios(self, client, inv_admin):
        _login(client, inv_admin.id, 'admin')
        resp = client.post('/api/v1/productos/', json={
            'codigo': 'CAB-003', 'descripcion': 'Cable desnudo', 'categoria': 'Cablería Eléctrica',
            'unidad': 'pza', 'stock_actual': 5, 'stock_minimo': 0,
            'cable_tipo': 'Desnudo', 'cable_calibre': '2/0',
        })
        assert resp.status_code == 200, resp.get_json()
        assert resp.get_json()['unidad'] == 'M'

    def test_producto_normal_no_exige_cable(self, client, inv_admin):
        _login(client, inv_admin.id, 'admin')
        resp = client.post('/api/v1/productos/', json={
            'codigo': 'NOCAB-001', 'descripcion': 'Tornillo', 'categoria': 'Tornillería',
            'unidad': 'pza', 'stock_actual': 10, 'stock_minimo': 0,
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['cable_tipo'] is None
        assert data['cable_calibre'] is None

    def test_producto_normal_ignora_campos_cable(self, client, inv_admin):
        """Un producto no-cable no debe arrastrar cable_tipo/calibre aunque el
        cliente los mande (evita datos huérfanos)."""
        _login(client, inv_admin.id, 'admin')
        resp = client.post('/api/v1/productos/', json={
            'codigo': 'NOCAB-002', 'descripcion': 'Tuerca', 'categoria': 'Tuercas',
            'unidad': 'pza', 'stock_actual': 10, 'stock_minimo': 0,
            'cable_tipo': 'THHN', 'cable_calibre': '12',
        })
        assert resp.status_code == 200
        assert resp.get_json()['cable_tipo'] is None

    def test_update_a_cable_exige_tipo_tamano(self, client, inv_admin, db):
        _login(client, inv_admin.id, 'admin')
        p = Producto(codigo='UPCAB-001', descripcion='X', categoria='General',
                     unidad='pza', stock_actual=0, stock_minimo=0)
        db.session.add(p); db.session.commit()
        # Cambiar la categoría a cable sin dar Tipo/Tamaño → 422.
        resp = client.put(f'/api/v1/productos/{p.id}', json={'categoria': 'Cable'})
        assert resp.status_code == 422
        # Con los datos completos → OK y unidad forzada a M.
        resp2 = client.put(f'/api/v1/productos/{p.id}', json={
            'categoria': 'Cable', 'cable_tipo': 'THW', 'cable_calibre': '10',
        })
        assert resp2.status_code == 200, resp2.get_json()
        assert resp2.get_json()['unidad'] == 'M'
        assert resp2.get_json()['cable_calibre'] == '10'

    def test_update_parcial_de_cable_conserva_datos(self, client, inv_admin, db):
        """Editar solo el precio de un cable NO debe borrar Tipo/Tamaño ni fallar
        por 'faltan datos de cable'."""
        _login(client, inv_admin.id, 'admin')
        p = Producto(codigo='UPCAB-002', descripcion='Cable', categoria='Cable',
                     unidad='M', cable_tipo='THHN', cable_calibre='14',
                     stock_actual=0, stock_minimo=0)
        db.session.add(p); db.session.commit()
        resp = client.put(f'/api/v1/productos/{p.id}', json={'precio_unitario': 99.5})
        assert resp.status_code == 200, resp.get_json()
        data = resp.get_json()
        assert data['cable_tipo'] == 'THHN'
        assert data['cable_calibre'] == '14'

    def test_solicitud_detalle_incluye_datos_cable(self, client, inv_solicitante, db):
        """El detalle de la solicitud expone cable_tipo/calibre del producto para
        que el carrito/solicitudes/PDF puedan mostrarlos."""
        from decimal import Decimal
        p = Producto(codigo='CAB-SOL', descripcion='Cable THHN', categoria='Cable',
                     unidad='M', cable_tipo='THHN', cable_calibre='12',
                     stock_actual=Decimal('500'), stock_minimo=0)
        db.session.add(p); db.session.commit()
        _login(client, inv_solicitante.id, 'solicitante_material')
        resp = client.post('/api/v1/solicitudes/', json={
            'proyecto': 'Cableado nave 1',
            'detalles': [{'producto_id': p.id, 'cantidad_solicitada': 30.5}],
        })
        assert resp.status_code == 200, resp.get_json()
        det = resp.get_json()['detalles'][0]
        assert det['cable_tipo'] == 'THHN'
        assert det['cable_calibre'] == '12'


# ═══════════════════════════════════════════════════════════════════════════════
# 3-ter. IMPORTACIÓN EXCEL — columnas de cable a prueba de tontos
# ═══════════════════════════════════════════════════════════════════════════════

def _build_import_xlsx(rows):
    """Construye un .xlsx en memoria con la fila de encabezados oficiales + las
    filas de datos dadas (dicts con llave = nombre de columna)."""
    import io as _io
    from openpyxl import Workbook
    HEADERS = [
        'Código (SKU)', 'Descripción', 'Categoría', 'Unidad',
        'Stock Inicial', 'Stock Mínimo', 'Precio Unitario', 'URL Imagen (opcional)',
        'Tipo (cable)', 'Tamaño mm²/AWG (cable)',
        # Feature stock por proyecto: destino del stock inicial por fila.
        'Almacén', 'Proyecto',
    ]
    wb = Workbook()
    ws = wb.active
    ws.append(HEADERS)
    for r in rows:
        ws.append([r.get(h, '') for h in HEADERS])
    buf = _io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.getvalue()


class TestImportarCable:

    def _post(self, client, rows):
        import io as _io
        data = {'archivo': (_io.BytesIO(_build_import_xlsx(rows)), 'materiales.xlsx')}
        return client.post('/api/v1/productos/importar', data=data,
                           content_type='multipart/form-data')

    def test_importar_cable_nuevo_fuerza_m(self, client, inv_admin, db):
        _login(client, inv_admin.id, 'admin')
        resp = self._post(client, [{
            'Código (SKU)': 'IMP-CAB-1', 'Descripción': 'Cable THHN', 'Categoría': 'Cable',
            'Unidad': 'rollo', 'Stock Inicial': 100, 'Stock Mínimo': 10,
            'Tipo (cable)': 'THHN', 'Tamaño mm²/AWG (cable)': '12',
        }])
        assert resp.status_code == 200, resp.get_json()
        body = resp.get_json()
        assert body['exitosos'] == 1 and body['errores'] == []
        p = Producto.query.filter_by(codigo='IMP-CAB-1').first()
        assert p is not None
        assert p.unidad == 'M'  # forzado aunque el Excel dijera "rollo"
        assert p.cable_tipo == 'THHN' and p.cable_calibre == '12'

    def test_importar_cable_sin_datos_es_error(self, client, inv_admin, db):
        _login(client, inv_admin.id, 'admin')
        resp = self._post(client, [{
            'Código (SKU)': 'IMP-CAB-2', 'Descripción': 'Cable X', 'Categoría': 'Cables',
            'Unidad': 'M', 'Stock Inicial': 5, 'Stock Mínimo': 0,
            # Falta Tipo y Tamaño → debe reportar error y NO crear el producto.
        }])
        assert resp.status_code == 200, resp.get_json()
        body = resp.get_json()
        assert body['exitosos'] == 0
        assert len(body['errores']) == 1 and 'cable' in body['errores'][0].lower()
        assert Producto.query.filter_by(codigo='IMP-CAB-2').first() is None

    def test_importar_rellena_cable_existente(self, client, inv_admin, db):
        """Un cable ya existente sin Tipo/Tamaño se completa al reimportar."""
        _login(client, inv_admin.id, 'admin')
        p = Producto(codigo='IMP-CAB-3', descripcion='Cable viejo', categoria='Cable',
                     unidad='M', cable_tipo=None, cable_calibre=None,
                     stock_actual=0, stock_minimo=0)
        db.session.add(p); db.session.commit()
        resp = self._post(client, [{
            'Código (SKU)': 'IMP-CAB-3', 'Descripción': 'Cable viejo', 'Categoría': 'Cable',
            'Unidad': 'M', 'Stock Inicial': 0, 'Stock Mínimo': 0,
            'Tipo (cable)': 'THW', 'Tamaño mm²/AWG (cable)': '10',
        }])
        assert resp.status_code == 200, resp.get_json()
        assert resp.get_json()['actualizados'] == 1
        db.session.refresh(p)
        assert p.cable_tipo == 'THW' and p.cable_calibre == '10'

    def test_importar_no_cable_ignora_columnas_cable(self, client, inv_admin, db):
        """Llenar Tipo/Tamaño en una categoría que no es cable NO debe guardarlos."""
        _login(client, inv_admin.id, 'admin')
        resp = self._post(client, [{
            'Código (SKU)': 'IMP-NOCAB-1', 'Descripción': 'Tornillo', 'Categoría': 'Tornillería',
            'Unidad': 'pza', 'Stock Inicial': 10, 'Stock Mínimo': 0,
            'Tipo (cable)': 'THHN', 'Tamaño mm²/AWG (cable)': '12',
        }])
        assert resp.status_code == 200, resp.get_json()
        assert resp.get_json()['exitosos'] == 1
        p = Producto.query.filter_by(codigo='IMP-NOCAB-1').first()
        assert p.cable_tipo is None and p.cable_calibre is None
        assert p.unidad == 'pza'  # no se fuerza a M en no-cable

    def test_exportar_catalogo_trae_productos(self, client, inv_admin, db):
        """El export debe traer los productos existentes ya llenos, con los
        encabezados oficiales (incl. cable)."""
        import io as _io
        from openpyxl import load_workbook
        from decimal import Decimal
        db.session.add_all([
            Producto(codigo='EXP-1', descripcion='Tornillo', categoria='Tornillería',
                     unidad='pza', stock_actual=Decimal('10'), stock_minimo=2, precio_unitario=Decimal('3.5')),
            Producto(codigo='EXP-CAB', descripcion='Cable', categoria='Cable', unidad='M',
                     cable_tipo='THHN', cable_calibre='12', stock_actual=Decimal('100'), stock_minimo=0),
        ])
        db.session.commit()
        _login(client, inv_admin.id, 'admin')
        resp = client.get('/api/v1/productos/exportar')
        assert resp.status_code == 200
        ws = load_workbook(_io.BytesIO(resp.data)).active
        # Encabezados en fila 4; datos desde fila 5.
        headers = [ws.cell(row=4, column=c).value for c in range(1, ws.max_column + 1)]
        assert 'Tamaño mm²/AWG (cable)' in headers
        codigos = {ws.cell(row=r, column=1).value for r in range(5, ws.max_row + 1)}
        assert {'EXP-1', 'EXP-CAB'}.issubset(codigos)

    def test_roundtrip_export_reimport_sin_cambios_falsos(self, client, inv_admin, db):
        """Exportar el catálogo y reimportar ese MISMO archivo sin editar no debe
        reportar ningún cambio (sin falsos positivos por formato de números)."""
        import io as _io
        from decimal import Decimal
        db.session.add_all([
            Producto(codigo='RT-1', descripcion='Tornillo', categoria='Tornillería',
                     unidad='pza', stock_actual=Decimal('10'), stock_minimo=2, precio_unitario=Decimal('3.5')),
            Producto(codigo='RT-2', descripcion='Cable THHN', categoria='Cable', unidad='M',
                     cable_tipo='THHN', cable_calibre='12', stock_actual=Decimal('250.5'),
                     stock_minimo=0, precio_unitario=Decimal('18.75')),
            Producto(codigo='RT-3', descripcion='Bote pintura', categoria='Pinturas',
                     unidad='Lts', stock_actual=Decimal('4'), stock_minimo=1, precio_unitario=Decimal('0')),
        ])
        db.session.commit()
        _login(client, inv_admin.id, 'admin')

        exp = client.get('/api/v1/productos/exportar')
        assert exp.status_code == 200
        data = {'archivo': (_io.BytesIO(exp.data), 'catalogo_materiales.xlsx')}
        imp = client.post('/api/v1/productos/importar', data=data,
                          content_type='multipart/form-data')
        assert imp.status_code == 200, imp.get_json()
        body = imp.get_json()
        assert body['exitosos'] == 0
        assert body['actualizados'] == 0, body['cambios_detalle']
        assert body['sin_cambios'] == 3

    def test_reimport_sin_cambios(self, client, inv_admin, db):
        """Reimportar una fila idéntica no debe actualizar: cuenta 'sin cambios'."""
        from decimal import Decimal
        p = Producto(codigo='DIF-0', descripcion='Tuerca', categoria='Tuercas',
                     unidad='pza', stock_actual=Decimal('5'), stock_minimo=1, precio_unitario=Decimal('2'))
        db.session.add(p); db.session.commit()
        _login(client, inv_admin.id, 'admin')
        resp = self._post(client, [{
            'Código (SKU)': 'DIF-0', 'Descripción': 'Tuerca', 'Categoría': 'Tuercas',
            'Unidad': 'pza', 'Stock Inicial': 5, 'Stock Mínimo': 1, 'Precio Unitario': 2,
        }])
        assert resp.status_code == 200, resp.get_json()
        body = resp.get_json()
        assert body['actualizados'] == 0
        assert body['sin_cambios'] == 1
        assert body['cambios_detalle'] == []

    def test_reimport_detecta_solo_el_cambio(self, client, inv_admin, db):
        """Cambiar un solo campo → actualizados=1 y el detalle lista ese campo.
        El stock actual NO se modifica aunque venga otro Stock Inicial."""
        from decimal import Decimal
        p = Producto(codigo='DIF-1', descripcion='Tuerca', categoria='Tuercas',
                     unidad='pza', stock_actual=Decimal('5'), stock_minimo=1, precio_unitario=Decimal('2'))
        db.session.add(p); db.session.commit()
        pid = p.id
        _login(client, inv_admin.id, 'admin')
        resp = self._post(client, [{
            'Código (SKU)': 'DIF-1', 'Descripción': 'Tuerca', 'Categoría': 'Tuercas',
            'Unidad': 'pza', 'Stock Inicial': 999, 'Stock Mínimo': 1, 'Precio Unitario': 7.5,
        }])
        assert resp.status_code == 200, resp.get_json()
        body = resp.get_json()
        assert body['actualizados'] == 1 and body['sin_cambios'] == 0
        assert body['cambios_detalle'][0]['codigo'] == 'DIF-1'
        assert any('precio' in c for c in body['cambios_detalle'][0]['cambios'])
        db.session.refresh(p)
        assert float(p.precio_unitario) == 7.5
        assert float(p.stock_actual) == 5.0  # el stock NO cambió

    def test_plantilla_incluye_columnas_cable(self, client, inv_admin):
        """La plantilla descargable debe traer las 2 columnas de cable."""
        import io as _io
        from openpyxl import load_workbook
        _login(client, inv_admin.id, 'admin')
        resp = client.get('/api/v1/productos/plantilla-importar')
        assert resp.status_code == 200
        ws = load_workbook(_io.BytesIO(resp.data)).active
        headers = [ws.cell(row=4, column=c).value for c in range(1, ws.max_column + 1)]
        assert 'Tipo (cable)' in headers
        assert 'Tamaño mm²/AWG (cable)' in headers

    def test_categoria_ambigua_pide_confirmacion(self, client, inv_admin, db):
        """Importar 'Cable azul' cuando existe 'Cable' → pide confirmación (no crea nada)."""
        db.session.add(Producto(codigo='EXIST-CAB', descripcion='Cable base', categoria='Cable',
                                unidad='M', cable_tipo='THHN', cable_calibre='12',
                                stock_actual=0, stock_minimo=0))
        db.session.commit()
        _login(client, inv_admin.id, 'admin')
        resp = self._post(client, [{
            'Código (SKU)': 'CAB-AZ-1', 'Descripción': 'Cable azul THHN', 'Categoría': 'Cable azul',
            'Unidad': 'M', 'Stock Inicial': 10, 'Stock Mínimo': 0,
            'Tipo (cable)': 'THHN', 'Tamaño mm²/AWG (cable)': '12',
        }])
        assert resp.status_code == 200, resp.get_json()
        body = resp.get_json()
        assert body.get('necesita_confirmacion') is True
        amb = body['categorias_ambiguas']
        assert any(a['nombre'] == 'Cable azul' and a['sugerencia'] == 'Cable' for a in amb)
        assert 'Cable' in body['categorias_existentes']
        # No se creó nada todavía.
        assert Producto.query.filter_by(codigo='CAB-AZ-1').first() is None

    def test_confirmar_mapea_a_existente(self, client, inv_admin, db):
        """Con el mapeo 'Cable azul'→'Cable', el producto entra en 'Cable'."""
        import io as _io
        db.session.add(Producto(codigo='EXIST-CAB2', descripcion='Cable base', categoria='Cable',
                                unidad='M', cable_tipo='THHN', cable_calibre='12',
                                stock_actual=0, stock_minimo=0))
        db.session.commit()
        _login(client, inv_admin.id, 'admin')
        import json as _json
        data = {
            'archivo': (_io.BytesIO(_build_import_xlsx([{
                'Código (SKU)': 'CAB-AZ-2', 'Descripción': 'Cable azul THHN', 'Categoría': 'Cable azul',
                'Unidad': 'M', 'Stock Inicial': 10, 'Stock Mínimo': 0,
                'Tipo (cable)': 'THHN', 'Tamaño mm²/AWG (cable)': '12',
            }])), 'materiales.xlsx'),
            'categoria_mapeo': _json.dumps({'Cable azul': 'Cable'}),
        }
        resp = client.post('/api/v1/productos/importar', data=data,
                           content_type='multipart/form-data')
        assert resp.status_code == 200, resp.get_json()
        body = resp.get_json()
        assert body.get('necesita_confirmacion') is not True
        assert body['exitosos'] == 1
        p = Producto.query.filter_by(codigo='CAB-AZ-2').first()
        assert p is not None and p.categoria == 'Cable'  # se agregó a la existente

    def test_confirmar_crear_nueva(self, client, inv_admin, db):
        """Con el mapeo 'Cable azul'→'' (crear nueva), se crea como categoría propia."""
        import io as _io, json as _json
        db.session.add(Producto(codigo='EXIST-CAB3', descripcion='Cable base', categoria='Cable',
                                unidad='M', cable_tipo='THHN', cable_calibre='12',
                                stock_actual=0, stock_minimo=0))
        db.session.commit()
        _login(client, inv_admin.id, 'admin')
        data = {
            'archivo': (_io.BytesIO(_build_import_xlsx([{
                'Código (SKU)': 'CAB-AZ-3', 'Descripción': 'Cable azul THHN', 'Categoría': 'Cable azul',
                'Unidad': 'M', 'Stock Inicial': 10, 'Stock Mínimo': 0,
                'Tipo (cable)': 'THHN', 'Tamaño mm²/AWG (cable)': '12',
            }])), 'materiales.xlsx'),
            'categoria_mapeo': _json.dumps({'Cable azul': ''}),
        }
        resp = client.post('/api/v1/productos/importar', data=data,
                           content_type='multipart/form-data')
        assert resp.status_code == 200, resp.get_json()
        assert resp.get_json()['exitosos'] == 1
        p = Producto.query.filter_by(codigo='CAB-AZ-3').first()
        assert p is not None and p.categoria == 'Cable azul'  # categoría nueva propia

    def test_columnas_cable_junto_a_categoria(self, client, inv_admin):
        """Tipo y Tamaño deben ir INMEDIATAMENTE después de Categoría."""
        import io as _io
        from openpyxl import load_workbook
        _login(client, inv_admin.id, 'admin')
        resp = client.get('/api/v1/productos/plantilla-importar')
        ws = load_workbook(_io.BytesIO(resp.data)).active
        headers = [ws.cell(row=4, column=c).value for c in range(1, ws.max_column + 1)]
        i_cat = headers.index('Categoría')
        assert headers[i_cat + 1] == 'Tipo (cable)'
        assert headers[i_cat + 2] == 'Tamaño mm²/AWG (cable)'

    def test_export_seccionado_por_categoria(self, client, inv_admin, db):
        """El export agrupa por categoría con filas de sección (marcador '#')."""
        import io as _io
        from openpyxl import load_workbook
        from decimal import Decimal
        db.session.add_all([
            Producto(codigo='SEC-A1', descripcion='A1', categoria='Alfa', unidad='pza',
                     stock_actual=Decimal('1'), stock_minimo=0),
            Producto(codigo='SEC-A2', descripcion='A2', categoria='Alfa', unidad='pza',
                     stock_actual=Decimal('1'), stock_minimo=0),
            Producto(codigo='SEC-B1', descripcion='B1', categoria='Beta', unidad='pza',
                     stock_actual=Decimal('1'), stock_minimo=0),
        ])
        db.session.commit()
        _login(client, inv_admin.id, 'admin')
        resp = client.get('/api/v1/productos/exportar')
        assert resp.status_code == 200
        ws = load_workbook(_io.BytesIO(resp.data)).active
        col_a = [ws.cell(row=r, column=1).value for r in range(5, ws.max_row + 1)]
        secciones = [v for v in col_a if isinstance(v, str) and v.startswith('#')]
        # Al menos una sección por categoría (Alfa, Beta).
        assert len(secciones) >= 2
        assert any('Alfa' in s for s in secciones)
        assert any('Beta' in s for s in secciones)

    def test_import_ignora_filas_de_seccion(self, client, inv_admin, db):
        """Una fila cuyo código empieza con '#' (sección) se salta sin error."""
        _login(client, inv_admin.id, 'admin')
        resp = self._post(client, [
            {'Código (SKU)': '#  ▸  Tornillería  (1)'},  # fila de sección
            {'Código (SKU)': 'SEC-OK', 'Descripción': 'Tornillo', 'Categoría': 'Tornillería',
             'Unidad': 'pza', 'Stock Inicial': 3, 'Stock Mínimo': 0},
        ])
        assert resp.status_code == 200, resp.get_json()
        body = resp.get_json()
        assert body['exitosos'] == 1
        assert body['errores'] == []  # la fila de sección NO es un error
        assert Producto.query.filter_by(codigo='SEC-OK').first() is not None

    def test_importar_cable_existente_sin_columnas_no_rompe(self, client, inv_admin, db):
        """Reimportar un cable con plantilla vieja (sin columnas de cable) NO debe
        fallar ni borrar el Tipo/Tamaño ya guardado."""
        _login(client, inv_admin.id, 'admin')
        p = Producto(codigo='IMP-CAB-4', descripcion='Cable', categoria='Cable',
                     unidad='M', cable_tipo='THHN', cable_calibre='8',
                     stock_actual=0, stock_minimo=0)
        db.session.add(p); db.session.commit()
        # Fila sin las columnas de cable (se envían vacías, como plantilla vieja).
        resp = self._post(client, [{
            'Código (SKU)': 'IMP-CAB-4', 'Descripción': 'Cable', 'Categoría': 'Cable',
            'Unidad': 'M', 'Stock Inicial': 0, 'Stock Mínimo': 0, 'Precio Unitario': 55,
        }])
        assert resp.status_code == 200, resp.get_json()
        assert resp.get_json()['actualizados'] == 1
        db.session.refresh(p)
        assert p.cable_tipo == 'THHN' and p.cable_calibre == '8'


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
# 5-bis. REJILLA DE ESTANTE + STOCK POR CELDA (Pausa 11)
# ═══════════════════════════════════════════════════════════════════════════════

class TestEstanteLayout:

    def _setup(self, db, *, stock=100, filas=3, columnas=4):
        a = Almacen(nombre='Alm Grid', qr_code=str(uuid.uuid4()), activo=True)
        db.session.add(a); db.session.commit()
        e = Estante(nombre='Rack G', almacen_id=a.id, qr_code=str(uuid.uuid4()),
                    activo=True, filas=filas, columnas=columnas)
        p = Producto(codigo='GR-001', descripcion='Tubo', categoria='F', unidad='pza',
                     stock_actual=stock, stock_minimo=0)
        db.session.add_all([e, p]); db.session.commit()
        db.session.add(StockPorAlmacen(producto_id=p.id, almacen_id=a.id, cantidad=stock))
        db.session.commit()
        return a, e, p

    def test_crear_estante_con_rejilla(self, client, inv_admin, db):
        _login(client, inv_admin.id)
        a = Almacen(nombre='A', qr_code=str(uuid.uuid4()), activo=True)
        db.session.add(a); db.session.commit()
        resp = client.post('/api/v1/estantes/', json={
            'nombre': 'R1', 'almacen_id': a.id, 'filas': 2, 'columnas': 5,
        })
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['filas'] == 2 and body['columnas'] == 5

    def test_guardar_y_leer_layout(self, client, inv_admin, db):
        _login(client, inv_admin.id)
        a, e, p = self._setup(db)
        resp = client.put(f'/api/v1/estantes/{e.id}/layout', json={
            'posiciones': [{'producto_id': p.id, 'fila': 3, 'columna': 2, 'cantidad': 40}],
        })
        assert resp.status_code == 200, resp.get_json()
        lay = client.get(f'/api/v1/estantes/{e.id}/layout').get_json()
        assert len(lay['celdas']) == 1
        celda = lay['celdas'][0]
        assert celda['fila'] == 3 and celda['columna'] == 2 and celda['cantidad'] == 40
        assert lay['stock_almacen'][str(p.id)] == 100

    def test_layout_rechaza_posicion_fuera_de_rejilla(self, client, inv_admin, db):
        _login(client, inv_admin.id)
        a, e, p = self._setup(db, filas=2, columnas=2)
        resp = client.put(f'/api/v1/estantes/{e.id}/layout', json={
            'posiciones': [{'producto_id': p.id, 'fila': 9, 'columna': 1, 'cantidad': 1}],
        })
        assert resp.status_code == 422

    def test_layout_rechaza_cantidad_mayor_que_stock(self, client, inv_admin, db):
        _login(client, inv_admin.id)
        a, e, p = self._setup(db, stock=10)
        resp = client.put(f'/api/v1/estantes/{e.id}/layout', json={
            'posiciones': [{'producto_id': p.id, 'fila': 1, 'columna': 1, 'cantidad': 25}],
        })
        assert resp.status_code == 422
        assert 'errores' in resp.get_json()

    def test_layout_fila_sin_columna_es_invalido(self, client, inv_admin, db):
        _login(client, inv_admin.id)
        a, e, p = self._setup(db)
        resp = client.put(f'/api/v1/estantes/{e.id}/layout', json={
            'posiciones': [{'producto_id': p.id, 'fila': 1, 'columna': None, 'cantidad': 1}],
        })
        assert resp.status_code == 422

    def test_legacy_set_productos_sigue_funcionando(self, client, inv_admin, db):
        _login(client, inv_admin.id)
        a, e, p = self._setup(db)
        resp = client.put(f'/api/v1/estantes/{e.id}/productos', json={'producto_ids': [p.id]})
        assert resp.status_code == 200
        lay = client.get(f'/api/v1/estantes/{e.id}/layout').get_json()
        # Asignado pero sin ubicar (fila/columna NULL, cantidad 0).
        assert len(lay['celdas']) == 1
        assert lay['celdas'][0]['fila'] is None and lay['celdas'][0]['cantidad'] == 0

    def test_scan_qr_devuelve_productos_del_rack_con_ubicacion(self, client, inv_admin, db):
        _login(client, inv_admin.id)
        a, e, p = self._setup(db)
        client.put(f'/api/v1/estantes/{e.id}/layout', json={
            'posiciones': [{'producto_id': p.id, 'fila': 2, 'columna': 3, 'cantidad': 12}],
        })
        resp = client.get(f'/api/v1/estantes/{e.qr_code}/inventario')
        assert resp.status_code == 200
        body = resp.get_json()
        assert len(body['productos']) == 1
        prod = body['productos'][0]
        assert prod['id'] == p.id
        assert prod['ubicacion'] == {'fila': 2, 'columna': 3, 'cantidad': 12}

    def test_reducir_rejilla_deja_sin_ubicar(self, client, inv_admin, db):
        _login(client, inv_admin.id)
        a, e, p = self._setup(db, filas=4, columnas=4)
        client.put(f'/api/v1/estantes/{e.id}/layout', json={
            'posiciones': [{'producto_id': p.id, 'fila': 4, 'columna': 4, 'cantidad': 5}],
        })
        # Reducir a 2x2 → la posición (4,4) queda fuera y pasa a "sin ubicar".
        resp = client.put(f'/api/v1/estantes/{e.id}', json={'filas': 2, 'columnas': 2})
        assert resp.status_code == 200
        lay = client.get(f'/api/v1/estantes/{e.id}/layout').get_json()
        celda = lay['celdas'][0]
        assert celda['fila'] is None and celda['columna'] is None
        # No pierde la cantidad capturada.
        assert celda['cantidad'] == 5

    def test_mover_estante_de_almacen_resetea_celdas(self, client, inv_admin, db):
        """Mover un estante a otro almacén deja sus celdas 'sin ubicar' y en 0:
        el sub-libro de celdas es POR almacén y las unidades físicas no viajan
        con el registro del estante (evita romper Σceldas ≤ stock en el destino)."""
        _login(client, inv_admin.id)
        a, e, p = self._setup(db)
        client.put(f'/api/v1/estantes/{e.id}/layout', json={
            'posiciones': [{'producto_id': p.id, 'fila': 1, 'columna': 1, 'cantidad': 30}],
        })
        destino = Almacen(nombre='Alm Destino', qr_code=str(uuid.uuid4()), activo=True)
        db.session.add(destino); db.session.commit()

        resp = client.put(f'/api/v1/estantes/{e.id}', json={'almacen_id': destino.id})
        assert resp.status_code == 200

        lay = client.get(f'/api/v1/estantes/{e.id}/layout').get_json()
        assert lay['estante']['almacen_id'] == destino.id
        # La colocación sigue (el producto continúa asignado al estante) pero
        # queda sin ubicar y en 0 — sin 'sobrante' contra el almacén destino.
        celda = lay['celdas'][0]
        assert celda['fila'] is None and celda['columna'] is None
        assert celda['cantidad'] == 0
        assert lay['sobrante_por_producto'] == {}

    def test_editar_estante_sin_cambiar_almacen_conserva_celdas(self, client, inv_admin, db):
        """Re-enviar el mismo almacen_id (o editar solo el nombre) NO debe
        resetear las celdas: el reseteo es exclusivo del cambio real de bodega."""
        _login(client, inv_admin.id)
        a, e, p = self._setup(db)
        client.put(f'/api/v1/estantes/{e.id}/layout', json={
            'posiciones': [{'producto_id': p.id, 'fila': 2, 'columna': 2, 'cantidad': 15}],
        })
        resp = client.put(f'/api/v1/estantes/{e.id}', json={
            'nombre': 'Rack G renombrado', 'almacen_id': a.id,
        })
        assert resp.status_code == 200
        lay = client.get(f'/api/v1/estantes/{e.id}/layout').get_json()
        celda = lay['celdas'][0]
        assert celda['fila'] == 2 and celda['columna'] == 2 and celda['cantidad'] == 15


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
        # Feature 'stock por proyecto': la fuente de verdad es
        # stock_almacen_proyecto (bucket general = proyecto NULL); stock_por_almacen
        # y stock_actual son caches que se recalculan desde los buckets. Sembramos
        # ambos para que los movimientos partan del stock esperado.
        db.session.add(StockAlmacenProyecto(
            producto_id=p.id, almacen_id=self._bodega_id, proyecto_id=None,
            cantidad=Decimal(str(stock)),
        ))
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


# ═══════════════════════════════════════════════════════════════════════════════
# 12. MOVIMIENTOS — PARTES (quién entrega / quién recibe) + VALE PDF
# ═══════════════════════════════════════════════════════════════════════════════

class TestMovimientoPartes:

    @pytest.fixture(autouse=True)
    def _seed(self, db):
        self._bodega = Almacen(nombre='Bodega Partes', qr_code=str(uuid.uuid4()), activo=True)
        db.session.add(self._bodega); db.session.commit()

    def _producto(self, db, stock=100):
        from decimal import Decimal
        p = Producto(codigo=f'PT-{uuid.uuid4().hex[:6]}', descripcion='X', categoria='T',
                     unidad='pza', stock_actual=Decimal(str(stock)), stock_minimo=0)
        db.session.add(p); db.session.flush()
        db.session.add(StockAlmacenProyecto(producto_id=p.id, almacen_id=self._bodega.id,
                                            proyecto_id=None, cantidad=Decimal(str(stock))))
        db.session.add(StockPorAlmacen(producto_id=p.id, almacen_id=self._bodega.id,
                                       cantidad=Decimal(str(stock))))
        db.session.commit()
        return p

    def _trabajador(self, db, activo=True):
        t = Trabajador(no_empleado=f'TP-{uuid.uuid4().hex[:5]}', nombre='Juan',
                       nombre_apellidos='Pérez López', activo=activo)
        db.session.add(t); db.session.commit()
        return t

    def test_salida_con_partes_persiste_y_serializa(self, client, inv_admin, db):
        _login(client, inv_admin.id, 'admin')
        p = self._producto(db, stock=100)
        t = self._trabajador(db)
        resp = client.post('/api/v1/movimientos/', json={
            'tipo': 'SALIDA', 'producto_id': p.id, 'cantidad': 10.0,
            'almacen_origen_id': self._bodega.id,
            'entrega_nombre': 'Almacén Central',
            'recibe_trabajador_id': t.id,
        })
        assert resp.status_code == 200, resp.get_json()
        body = resp.get_json()
        assert body['entrega_nombre'] == 'Almacén Central'
        assert body['recibe_nombre'] == t.nombre_completo
        assert body['recibe_trabajador_id'] == t.id
        mov = MovimientoInventario.query.get(body['id'])
        assert mov.recibe_trabajador_id == t.id
        assert mov.entrega_nombre == 'Almacén Central'

    def test_vale_pdf_responde_ok(self, client, inv_admin, db):
        _login(client, inv_admin.id, 'admin')
        p = self._producto(db, stock=50)
        resp = client.post('/api/v1/movimientos/', json={
            'tipo': 'ENTRADA', 'producto_id': p.id, 'cantidad': 5.0,
            'almacen_destino_id': self._bodega.id,
            'entrega_nombre': 'Proveedor X', 'recibe_nombre': 'Bodeguero',
        })
        assert resp.status_code == 200, resp.get_json()
        mov_id = resp.get_json()['id']
        pdf = client.get(f'/api/v1/movimientos/{mov_id}/pdf')
        assert pdf.status_code == 200
        assert pdf.mimetype == 'application/pdf'
        assert pdf.data[:4] == b'%PDF'

    def test_vale_pdf_404_si_no_existe(self, client, inv_admin):
        _login(client, inv_admin.id, 'admin')
        resp = client.get('/api/v1/movimientos/999999/pdf')
        assert resp.status_code == 404

    def test_trabajador_inexistente_422(self, client, inv_admin, db):
        _login(client, inv_admin.id, 'admin')
        p = self._producto(db)
        resp = client.post('/api/v1/movimientos/', json={
            'tipo': 'ENTRADA', 'producto_id': p.id, 'cantidad': 5.0,
            'almacen_destino_id': self._bodega.id,
            'recibe_trabajador_id': 999999,
        })
        assert resp.status_code == 422

    def test_trabajador_inactivo_422(self, client, inv_admin, db):
        _login(client, inv_admin.id, 'admin')
        p = self._producto(db)
        t = self._trabajador(db, activo=False)
        resp = client.post('/api/v1/movimientos/', json={
            'tipo': 'ENTRADA', 'producto_id': p.id, 'cantidad': 5.0,
            'almacen_destino_id': self._bodega.id,
            'entrega_trabajador_id': t.id,
        })
        assert resp.status_code == 422


# ═══════════════════════════════════════════════════════════════════════════════
# 13. EDITOR DE STOCK POR BUCKET (ajustar-buckets → AJUSTES)
# ═══════════════════════════════════════════════════════════════════════════════

class TestAjustarBuckets:

    @pytest.fixture(autouse=True)
    def _seed(self, db):
        self._bodega = Almacen(nombre='Bodega Buckets', qr_code=str(uuid.uuid4()), activo=True)
        db.session.add(self._bodega); db.session.commit()

    def _producto(self, db, general=50):
        from decimal import Decimal
        p = Producto(codigo=f'BK-{uuid.uuid4().hex[:6]}', descripcion='X', categoria='T',
                     unidad='pza', stock_actual=Decimal(str(general)), stock_minimo=0)
        db.session.add(p); db.session.flush()
        db.session.add(StockAlmacenProyecto(producto_id=p.id, almacen_id=self._bodega.id,
                                            proyecto_id=None, cantidad=Decimal(str(general))))
        db.session.add(StockPorAlmacen(producto_id=p.id, almacen_id=self._bodega.id,
                                       cantidad=Decimal(str(general))))
        db.session.commit()
        return p

    def test_subir_bucket_genera_ajuste(self, client, inv_admin, db):
        _login(client, inv_admin.id, 'admin')
        p = self._producto(db, general=50)
        resp = client.post(f'/api/v1/productos/{p.id}/ajustar-buckets', json={
            'buckets': [{'almacen_id': self._bodega.id, 'proyecto_id': None, 'cantidad_objetivo': 60}],
        })
        assert resp.status_code == 200, resp.get_json()
        assert resp.get_json()['buckets_ajustados'] == 1
        db.session.refresh(p)
        assert float(p.stock_actual) == pytest.approx(60.0, abs=0.01)
        ajustes = MovimientoInventario.query.filter_by(producto_id=p.id, tipo='AJUSTE').all()
        assert len(ajustes) == 1 and float(ajustes[0].cantidad) == pytest.approx(10.0, abs=0.01)

    def test_agregar_bucket_proyecto(self, client, inv_admin, db):
        _login(client, inv_admin.id, 'admin')
        p = self._producto(db, general=50)
        proy = Proyecto(numero_proyecto=f'PB-{uuid.uuid4().hex[:5]}', nombre='Obra', activo=True)
        db.session.add(proy); db.session.commit()
        resp = client.post(f'/api/v1/productos/{p.id}/ajustar-buckets', json={
            'buckets': [
                {'almacen_id': self._bodega.id, 'proyecto_id': None, 'cantidad_objetivo': 50},
                {'almacen_id': self._bodega.id, 'proyecto_id': proy.id, 'cantidad_objetivo': 20},
            ],
        })
        assert resp.status_code == 200, resp.get_json()
        db.session.refresh(p)
        assert float(p.stock_actual) == pytest.approx(70.0, abs=0.01)
        bucket = StockAlmacenProyecto.query.filter_by(
            producto_id=p.id, almacen_id=self._bodega.id, proyecto_id=proy.id).first()
        assert bucket is not None and float(bucket.cantidad) == pytest.approx(20.0, abs=0.01)

    def test_ajuste_respeta_reservas(self, client, inv_admin, db):
        from decimal import Decimal
        _login(client, inv_admin.id, 'admin')
        p = self._producto(db, general=50)
        p.stock_reservado = Decimal('40')
        db.session.commit()
        resp = client.post(f'/api/v1/productos/{p.id}/ajustar-buckets', json={
            'buckets': [{'almacen_id': self._bodega.id, 'proyecto_id': None, 'cantidad_objetivo': 5}],
        })
        assert resp.status_code == 409
        db.session.refresh(p)
        assert float(p.stock_actual) == pytest.approx(50.0, abs=0.01)  # rollback

    def test_bucket_duplicado_422(self, client, inv_admin, db):
        _login(client, inv_admin.id, 'admin')
        p = self._producto(db)
        resp = client.post(f'/api/v1/productos/{p.id}/ajustar-buckets', json={
            'buckets': [
                {'almacen_id': self._bodega.id, 'proyecto_id': None, 'cantidad_objetivo': 5},
                {'almacen_id': self._bodega.id, 'proyecto_id': None, 'cantidad_objetivo': 7},
            ],
        })
        assert resp.status_code == 422

    def test_almacen_inexistente_422(self, client, inv_admin, db):
        _login(client, inv_admin.id, 'admin')
        p = self._producto(db)
        resp = client.post(f'/api/v1/productos/{p.id}/ajustar-buckets', json={
            'buckets': [{'almacen_id': 999999, 'proyecto_id': None, 'cantidad_objetivo': 5}],
        })
        assert resp.status_code == 422


# ═══════════════════════════════════════════════════════════════════════════════
# 14. IMPORTACIÓN — destino por Almacén + Proyecto
# ═══════════════════════════════════════════════════════════════════════════════

class TestImportarBuckets:

    def _post(self, client, rows):
        import io as _io
        data = {'archivo': (_io.BytesIO(_build_import_xlsx(rows)), 'materiales.xlsx')}
        return client.post('/api/v1/productos/importar', data=data,
                           content_type='multipart/form-data')

    def test_importar_deposita_en_bucket(self, client, inv_admin, db):
        _login(client, inv_admin.id, 'admin')
        cdmx = Almacen(nombre='CDMX', qr_code=str(uuid.uuid4()), activo=True)
        db.session.add(cdmx)
        proy = Proyecto(numero_proyecto='PROY-A', nombre='Nave', activo=True)
        db.session.add(proy); db.session.commit()
        resp = self._post(client, [{
            'Código (SKU)': 'IMP-BK-1', 'Descripción': 'Tornillo', 'Categoría': 'Tornillería',
            'Unidad': 'pza', 'Stock Inicial': 30, 'Stock Mínimo': 0,
            'Almacén': 'CDMX', 'Proyecto': 'PROY-A',
        }])
        assert resp.status_code == 200, resp.get_json()
        assert resp.get_json()['exitosos'] == 1
        p = Producto.query.filter_by(codigo='IMP-BK-1').first()
        assert p is not None
        bucket = StockAlmacenProyecto.query.filter_by(
            producto_id=p.id, almacen_id=cdmx.id, proyecto_id=proy.id).first()
        assert bucket is not None and float(bucket.cantidad) == pytest.approx(30.0, abs=0.01)
        cache = StockPorAlmacen.query.filter_by(producto_id=p.id, almacen_id=cdmx.id).first()
        assert cache is not None and float(cache.cantidad) == pytest.approx(30.0, abs=0.01)

    def test_importar_almacen_inexistente_error(self, client, inv_admin, db):
        _login(client, inv_admin.id, 'admin')
        # Debe existir al menos una bodega activa (default) para el resto del flujo.
        db.session.add(Almacen(nombre='Central', qr_code=str(uuid.uuid4()), activo=True))
        db.session.commit()
        resp = self._post(client, [{
            'Código (SKU)': 'IMP-BK-2', 'Descripción': 'Tuerca', 'Categoría': 'Tornillería',
            'Unidad': 'pza', 'Stock Inicial': 10, 'Stock Mínimo': 0,
            'Almacén': 'NoExiste',
        }])
        assert resp.status_code == 200, resp.get_json()
        body = resp.get_json()
        assert body['exitosos'] == 0
        assert len(body['errores']) == 1 and 'almac' in body['errores'][0].lower()
        assert Producto.query.filter_by(codigo='IMP-BK-2').first() is None


# ═══════════════════════════════════════════════════════════════════════════════
# 15. RESUMEN POR PROYECTO Y ALMACÉN (portada)
# ═══════════════════════════════════════════════════════════════════════════════

class TestResumenProyectos:

    def _seed(self, db):
        from decimal import Decimal
        a = Almacen(nombre='Alm A', qr_code=str(uuid.uuid4()), activo=True)
        b = Almacen(nombre='Alm B', qr_code=str(uuid.uuid4()), activo=True)
        db.session.add_all([a, b])
        proy = Proyecto(numero_proyecto=f'RP-{uuid.uuid4().hex[:5]}', nombre='Obra', activo=True)
        p = Producto(codigo=f'RP-{uuid.uuid4().hex[:6]}', descripcion='X', categoria='T',
                     unidad='pza', stock_actual=Decimal('150'), stock_minimo=0)
        db.session.add_all([proy, p]); db.session.flush()
        # General en A(100) y B(10); proyecto en A(40).
        db.session.add_all([
            StockAlmacenProyecto(producto_id=p.id, almacen_id=a.id, proyecto_id=None, cantidad=Decimal('100')),
            StockAlmacenProyecto(producto_id=p.id, almacen_id=b.id, proyecto_id=None, cantidad=Decimal('10')),
            StockAlmacenProyecto(producto_id=p.id, almacen_id=a.id, proyecto_id=proy.id, cantidad=Decimal('40')),
        ])
        db.session.commit()
        return a, b, proy

    def test_resumen_matriz(self, client, inv_admin, db):
        _login(client, inv_admin.id, 'admin')
        a, b, proy = self._seed(db)
        resp = client.get('/api/v1/almacenes/resumen-proyectos')
        assert resp.status_code == 200, resp.get_json()
        data = resp.get_json()
        # Columnas: ambos almacenes activos.
        nombres_alm = {c['nombre'] for c in data['almacenes']}
        assert {'Alm A', 'Alm B'}.issubset(nombres_alm)
        assert data['total_unidades'] == pytest.approx(150.0, abs=0.01)
        # Productos distintos con existencia en todo el inventario (1 SKU).
        assert data['total_productos'] == 1
        # General primero.
        assert data['filas'][0]['es_general'] is True
        general = data['filas'][0]
        assert general['total_unidades'] == pytest.approx(110.0, abs=0.01)
        assert general['total_productos'] == 1
        assert general['celdas'][str(a.id)]['unidades'] == pytest.approx(100.0, abs=0.01)
        assert general['celdas'][str(b.id)]['unidades'] == pytest.approx(10.0, abs=0.01)
        # Fila del proyecto.
        fila_proy = next(f for f in data['filas'] if f['proyecto_id'] == proy.id)
        assert fila_proy['total_unidades'] == pytest.approx(40.0, abs=0.01)
        assert fila_proy['celdas'][str(a.id)]['unidades'] == pytest.approx(40.0, abs=0.01)
        assert str(b.id) not in fila_proy['celdas']

    def test_resumen_requiere_login(self, client):
        resp = client.get('/api/v1/almacenes/resumen-proyectos')
        assert resp.status_code == 401
