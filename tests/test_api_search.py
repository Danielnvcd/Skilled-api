"""Tests del API JWT `/api/v1/buscar` — buscador global multi-recurso.

Cobertura:
  - GET /api/v1/buscar?q=...

Reglas no obvias:
  - `q < 2` caracteres → respuesta vacía (todas las listas vacías), no 400.
  - `q > 80` caracteres → 422.
  - `limit` por defecto 6, clamp `[1, 10]`; valores no numéricos caen a 6.
  - Buckets visibles por rol:
      productos    → inventario/admin/super_admin/solicitante_material
      solicitudes  → solicitante_material (solo propias), inventario, admin/super_admin
      categorías   → cualquier autenticado
      herramientas → inventario/admin/super_admin/solicitante_material
      trabajadores → admin/super_admin/inventario   (NO coord, NO solicitante)
      proyectos    → cualquier autenticado
  - Búsqueda por folio de solicitud: 'SOL-000123' / '123' resuelve al id 123.
  - Cada item del payload trae `{tipo, id, label, subtitle, url}`.
"""
from decimal import Decimal

import pytest
from werkzeug.security import generate_password_hash

from app.extensions import db as flask_db
from app.models import (
    CategoriaConfig, Herramienta, Producto, Proyecto,
    SolicitudMaterial, SolicitudMaterialDetalle, Trabajador, User,
)
from app.routes.api_auth import _encode_access_token


def _hdr(user):
    return {'Authorization': f'Bearer {_encode_access_token(user)}'}


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def s_admin(db):
    u = User(username='s_admin', password_hash=generate_password_hash('Pass123!'), role='admin')
    db.session.add(u); db.session.commit()
    return u


@pytest.fixture
def s_inventario(db):
    u = User(username='s_inv', password_hash=generate_password_hash('Pass123!'),
              role='inventario')
    db.session.add(u); db.session.commit()
    return u


@pytest.fixture
def s_solicitante(db):
    u = User(username='s_sol', password_hash=generate_password_hash('Pass123!'),
              role='solicitante_material')
    db.session.add(u); db.session.commit()
    return u


@pytest.fixture
def s_solicitante_otro(db):
    u = User(username='s_sol_b', password_hash=generate_password_hash('Pass123!'),
              role='solicitante_material')
    db.session.add(u); db.session.commit()
    return u


@pytest.fixture
def s_coord(db):
    u = User(username='s_coord', password_hash=generate_password_hash('Pass123!'),
              role='coordinador')
    db.session.add(u); db.session.commit()
    return u


@pytest.fixture
def producto_taladro(db):
    p = Producto(codigo='TLD-100', descripcion='Taladro inalámbrico',
                  categoria='Herramientas', unidad='pza',
                  stock_actual=Decimal('5'), stock_minimo=Decimal('1'), activo=True)
    db.session.add(p); db.session.commit()
    return p


@pytest.fixture
def producto_inactivo(db):
    p = Producto(codigo='TLD-INA', descripcion='Taladro viejo',
                  categoria='Herramientas', unidad='pza',
                  stock_actual=Decimal('0'), stock_minimo=Decimal('0'), activo=False)
    db.session.add(p); db.session.commit()
    return p


@pytest.fixture
def categoria_cfg(db):
    c = CategoriaConfig(nombre='Eléctricos')
    db.session.add(c); db.session.commit()
    return c


@pytest.fixture
def herramienta(db):
    h = Herramienta(sku='HRR-ROUTER-01', descripcion='Router inalámbrico',
                     clasificacion='Eléctricas', unidad='pza',
                     piezas=1, serializada=True, activo=True)
    db.session.add(h); db.session.commit()
    return h


@pytest.fixture
def trabajador_diana(db):
    t = Trabajador(no_empleado='EMP-DI', nombre='Diana', nombre_apellidos='Routero',
                    activo=True, tipo_nomina='Semanal',
                    salario_real_pactado_x_sem=Decimal('5000'))
    db.session.add(t); db.session.commit()
    return t


@pytest.fixture
def proyecto_router(db, s_admin):
    p = Proyecto(numero_proyecto='ROUTER-2026', nombre='Obra Router Norte',
                  activo=True, coordinador_id=s_admin.id)
    db.session.add(p); db.session.commit()
    return p


@pytest.fixture
def solicitud_propia(db, s_solicitante, producto_taladro):
    s = SolicitudMaterial(solicitante_id=s_solicitante.id,
                            proyecto='Obra Router Norte', estatus='PENDIENTE')
    db.session.add(s); db.session.flush()
    db.session.add(SolicitudMaterialDetalle(
        solicitud_id=s.id, tipo_item='MATERIAL',
        producto_id=producto_taladro.id, cantidad_solicitada=1,
    ))
    db.session.commit()
    return s


@pytest.fixture
def solicitud_ajena(db, s_solicitante_otro, producto_taladro):
    s = SolicitudMaterial(solicitante_id=s_solicitante_otro.id,
                            proyecto='Obra Router Norte', estatus='PENDIENTE')
    db.session.add(s); db.session.commit()
    return s


# ═══════════════════════════════════════════════════════════════════════════════
# 1. AUTH
# ═══════════════════════════════════════════════════════════════════════════════

class TestAuth:

    def test_sin_token_401(self, client):
        r = client.get('/api/v1/buscar?q=router')
        assert r.status_code == 401

    def test_admin_200(self, client, s_admin):
        r = client.get('/api/v1/buscar?q=router', headers=_hdr(s_admin))
        assert r.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════════
# 2. VALIDACIONES DE QUERY
# ═══════════════════════════════════════════════════════════════════════════════

class TestQueryValidation:

    def test_q_menor_a_2_chars_devuelve_vacio(
        self, client, s_admin, producto_taladro,
    ):
        r = client.get('/api/v1/buscar?q=t', headers=_hdr(s_admin))
        assert r.status_code == 200
        body = r.get_json()
        # Todas las listas vacías
        for bucket in ('productos', 'solicitudes', 'categorias',
                        'trabajadores', 'herramientas', 'proyectos'):
            assert body[bucket] == []

    def test_q_vacio_devuelve_vacio(self, client, s_admin):
        r = client.get('/api/v1/buscar?q=', headers=_hdr(s_admin))
        assert r.status_code == 200
        assert r.get_json()['productos'] == []

    def test_q_excede_80_chars_422(self, client, s_admin):
        q = 'x' * 81
        r = client.get(f'/api/v1/buscar?q={q}', headers=_hdr(s_admin))
        assert r.status_code == 422

    def test_q_de_80_chars_es_permitido(self, client, s_admin):
        q = 'x' * 80
        r = client.get(f'/api/v1/buscar?q={q}', headers=_hdr(s_admin))
        assert r.status_code == 200

    def test_limit_default_es_6(
        self, client, s_admin, db,
    ):
        # Crear 7 productos que matchean → con limit default 6, devuelve 6
        for i in range(7):
            db.session.add(Producto(
                codigo=f'LIM-{i:03d}', descripcion=f'Limit prod {i}',
                categoria='Test', unidad='pza',
                stock_actual=Decimal('1'), stock_minimo=Decimal('0'), activo=True,
            ))
        db.session.commit()
        r = client.get('/api/v1/buscar?q=LIM', headers=_hdr(s_admin))
        assert len(r.get_json()['productos']) == 6

    def test_limit_clamp_superior_a_10(self, client, s_admin, db):
        for i in range(12):
            db.session.add(Producto(
                codigo=f'CLP-{i:03d}', descripcion='X', categoria='T',
                unidad='pza', stock_actual=0, stock_minimo=0, activo=True,
            ))
        db.session.commit()
        r = client.get('/api/v1/buscar?q=CLP&limit=99', headers=_hdr(s_admin))
        # Aunque pidan 99, máximo 10
        assert len(r.get_json()['productos']) == 10

    def test_limit_clamp_inferior_a_1(self, client, s_admin, producto_taladro):
        r = client.get('/api/v1/buscar?q=Taladro&limit=0', headers=_hdr(s_admin))
        # limit=0 se sube a 1 → todavía devuelve hasta 1 resultado
        assert r.status_code == 200

    def test_limit_no_numerico_cae_a_default(
        self, client, s_admin, producto_taladro,
    ):
        r = client.get('/api/v1/buscar?q=Taladro&limit=abc', headers=_hdr(s_admin))
        assert r.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════════
# 3. PRODUCTOS
# ═══════════════════════════════════════════════════════════════════════════════

class TestProductos:

    def test_admin_encuentra_producto(self, client, s_admin, producto_taladro):
        r = client.get('/api/v1/buscar?q=Taladro', headers=_hdr(s_admin))
        items = r.get_json()['productos']
        assert len(items) == 1
        it = items[0]
        assert it['tipo'] == 'producto'
        assert it['id'] == producto_taladro.id
        assert 'TLD-100' in it['label']

    def test_no_devuelve_productos_inactivos(
        self, client, s_admin, producto_inactivo,
    ):
        r = client.get('/api/v1/buscar?q=Taladro', headers=_hdr(s_admin))
        ids = [p['id'] for p in r.get_json()['productos']]
        assert producto_inactivo.id not in ids

    def test_solicitante_puede_buscar_productos(
        self, client, s_solicitante, producto_taladro,
    ):
        r = client.get('/api/v1/buscar?q=Taladro', headers=_hdr(s_solicitante))
        assert len(r.get_json()['productos']) == 1

    def test_coord_no_ve_productos(
        self, client, s_coord, producto_taladro,
    ):
        # coord no está en `_ROLES_INVENTARIO | {'solicitante_material'}`
        r = client.get('/api/v1/buscar?q=Taladro', headers=_hdr(s_coord))
        assert r.get_json()['productos'] == []

    def test_busqueda_por_codigo(self, client, s_admin, producto_taladro):
        r = client.get('/api/v1/buscar?q=TLD-100', headers=_hdr(s_admin))
        assert len(r.get_json()['productos']) == 1

    def test_busqueda_por_categoria(self, client, s_admin, producto_taladro):
        r = client.get('/api/v1/buscar?q=Herramien', headers=_hdr(s_admin))
        ids = [p['id'] for p in r.get_json()['productos']]
        assert producto_taladro.id in ids


# ═══════════════════════════════════════════════════════════════════════════════
# 4. SOLICITUDES
# ═══════════════════════════════════════════════════════════════════════════════

class TestSolicitudes:

    def test_solicitante_solo_ve_las_suyas(
        self, client, s_solicitante, solicitud_propia, solicitud_ajena,
    ):
        r = client.get('/api/v1/buscar?q=Router', headers=_hdr(s_solicitante))
        items = r.get_json()['solicitudes']
        ids = {it['id'] for it in items}
        assert solicitud_propia.id in ids
        assert solicitud_ajena.id not in ids

    def test_admin_ve_todas(
        self, client, s_admin, solicitud_propia, solicitud_ajena,
    ):
        r = client.get('/api/v1/buscar?q=Router', headers=_hdr(s_admin))
        ids = {it['id'] for it in r.get_json()['solicitudes']}
        assert {solicitud_propia.id, solicitud_ajena.id} <= ids

    def test_coord_no_ve_solicitudes(
        self, client, s_coord, solicitud_propia,
    ):
        # coord no está en `_ROLES_SOLICITANTE`
        r = client.get('/api/v1/buscar?q=Router', headers=_hdr(s_coord))
        assert r.get_json()['solicitudes'] == []

    def test_busqueda_por_folio_numerico(
        self, client, s_admin, solicitud_propia,
    ):
        sol_id = solicitud_propia.id
        # 'SOL-000123' → 123
        folio = f'SOL-{sol_id:06d}'
        r = client.get(f'/api/v1/buscar?q={folio}', headers=_hdr(s_admin))
        ids = {it['id'] for it in r.get_json()['solicitudes']}
        assert sol_id in ids


# ═══════════════════════════════════════════════════════════════════════════════
# 5. CATEGORÍAS
# ═══════════════════════════════════════════════════════════════════════════════

class TestCategorias:

    def test_cualquier_rol_ve_categorias(
        self, client, s_coord, categoria_cfg,
    ):
        r = client.get('/api/v1/buscar?q=Eléctr', headers=_hdr(s_coord))
        items = r.get_json()['categorias']
        assert len(items) == 1
        assert items[0]['tipo'] == 'categoria'
        assert items[0]['label'] == 'Eléctricos'


# ═══════════════════════════════════════════════════════════════════════════════
# 6. HERRAMIENTAS
# ═══════════════════════════════════════════════════════════════════════════════

class TestHerramientas:

    def test_admin_ve_herramientas(self, client, s_admin, herramienta):
        r = client.get('/api/v1/buscar?q=Router', headers=_hdr(s_admin))
        items = r.get_json()['herramientas']
        skus = {h['id'] for h in items}
        assert herramienta.id in skus

    def test_inventario_ve_herramientas(
        self, client, s_inventario, herramienta,
    ):
        r = client.get('/api/v1/buscar?q=Router', headers=_hdr(s_inventario))
        assert any(h['id'] == herramienta.id for h in r.get_json()['herramientas'])

    def test_solicitante_ve_herramientas(
        self, client, s_solicitante, herramienta,
    ):
        # solicitante_material también ve herramientas (las puede solicitar)
        r = client.get('/api/v1/buscar?q=Router', headers=_hdr(s_solicitante))
        assert any(h['id'] == herramienta.id for h in r.get_json()['herramientas'])

    def test_coord_no_ve_herramientas(
        self, client, s_coord, herramienta,
    ):
        r = client.get('/api/v1/buscar?q=Router', headers=_hdr(s_coord))
        assert r.get_json()['herramientas'] == []


# ═══════════════════════════════════════════════════════════════════════════════
# 7. TRABAJADORES
# ═══════════════════════════════════════════════════════════════════════════════

class TestTrabajadores:

    def test_admin_encuentra_trabajador(
        self, client, s_admin, trabajador_diana,
    ):
        r = client.get('/api/v1/buscar?q=Diana', headers=_hdr(s_admin))
        items = r.get_json()['trabajadores']
        assert any(t['id'] == trabajador_diana.id for t in items)

    def test_inventario_ve_trabajadores(
        self, client, s_inventario, trabajador_diana,
    ):
        r = client.get('/api/v1/buscar?q=Diana', headers=_hdr(s_inventario))
        assert any(t['id'] == trabajador_diana.id for t in r.get_json()['trabajadores'])

    def test_coord_no_ve_trabajadores_via_search(
        self, client, s_coord, trabajador_diana,
    ):
        # coord no está en `_ROLES_INVENTARIO | _ROLES_ADMIN`
        r = client.get('/api/v1/buscar?q=Diana', headers=_hdr(s_coord))
        assert r.get_json()['trabajadores'] == []

    def test_solicitante_no_ve_trabajadores(
        self, client, s_solicitante, trabajador_diana,
    ):
        r = client.get('/api/v1/buscar?q=Diana', headers=_hdr(s_solicitante))
        assert r.get_json()['trabajadores'] == []


# ═══════════════════════════════════════════════════════════════════════════════
# 8. PROYECTOS
# ═══════════════════════════════════════════════════════════════════════════════

class TestProyectos:

    def test_cualquier_rol_ve_proyectos(
        self, client, s_coord, proyecto_router,
    ):
        r = client.get('/api/v1/buscar?q=ROUTER', headers=_hdr(s_coord))
        items = r.get_json()['proyectos']
        assert any(p['id'] == proyecto_router.id for p in items)

    def test_solicitante_tambien_ve_proyectos(
        self, client, s_solicitante, proyecto_router,
    ):
        r = client.get('/api/v1/buscar?q=Router', headers=_hdr(s_solicitante))
        assert any(p['id'] == proyecto_router.id for p in r.get_json()['proyectos'])

    def test_no_lista_proyectos_inactivos(
        self, client, s_admin, db,
    ):
        inactivo = Proyecto(numero_proyecto='ROUTER-OFF', nombre='Apagado',
                             activo=False, coordinador_id=s_admin.id)
        db.session.add(inactivo); db.session.commit()
        r = client.get('/api/v1/buscar?q=ROUTER-OFF', headers=_hdr(s_admin))
        assert r.get_json()['proyectos'] == []


# ═══════════════════════════════════════════════════════════════════════════════
# 9. FORMATO Y TOTAL
# ═══════════════════════════════════════════════════════════════════════════════

class TestPayload:

    def test_items_tienen_campos_esperados(
        self, client, s_admin, producto_taladro,
    ):
        r = client.get('/api/v1/buscar?q=Taladro', headers=_hdr(s_admin))
        item = r.get_json()['productos'][0]
        for k in ('tipo', 'id', 'label', 'subtitle', 'url'):
            assert k in item

    def test_total_suma_todas_las_listas(
        self, client, s_admin, producto_taladro, proyecto_router,
    ):
        r = client.get('/api/v1/buscar?q=Router', headers=_hdr(s_admin))
        body = r.get_json()
        suma = sum(
            len(body[b])
            for b in ('productos', 'solicitudes', 'categorias',
                      'trabajadores', 'herramientas', 'proyectos')
        )
        assert body['total'] == suma
