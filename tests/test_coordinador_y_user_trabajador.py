"""Tests de:
  - Coordinador puede crear solicitudes de material y ver solo las suyas.
  - PUT /api/users/<id> acepta y valida `trabajador_id`.

Auth: JWT real (Bearer).
"""
from decimal import Decimal

import pytest
from werkzeug.security import generate_password_hash

from app.models import User, Producto, SolicitudMaterial, Trabajador
from app.routes.api_auth import _encode_access_token


def _hdr(user):
    return {'Authorization': f'Bearer {_encode_access_token(user)}'}


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def admin(db):
    u = User(username='ct_admin', password_hash=generate_password_hash('Pass123!'), role='admin')
    db.session.add(u); db.session.commit()
    return u


@pytest.fixture
def coord(db):
    u = User(username='ct_coord', password_hash=generate_password_hash('Pass123!'), role='coordinador')
    db.session.add(u); db.session.commit()
    return u


@pytest.fixture
def coord_b(db):
    u = User(username='ct_coord_b', password_hash=generate_password_hash('Pass123!'), role='coordinador')
    db.session.add(u); db.session.commit()
    return u


@pytest.fixture
def inv_user(db):
    u = User(username='ct_inv', password_hash=generate_password_hash('Pass123!'), role='inventario')
    db.session.add(u); db.session.commit()
    return u


@pytest.fixture
def producto(db):
    p = Producto(codigo='CT-001', descripcion='Producto test',
                  categoria='Suministros', unidad='pza',
                  stock_actual=Decimal('10'), stock_minimo=Decimal('0'), activo=True)
    db.session.add(p); db.session.commit()
    return p


@pytest.fixture
def trabajador_a(db):
    t = Trabajador(no_empleado='EMP-100', nombre_apellidos='Juan Pérez',
                    nombre='Juan', activo=True, tipo_nomina='Semanal',
                    salario_real_pactado_x_sem=5000)
    db.session.add(t); db.session.commit()
    return t


@pytest.fixture
def trabajador_b(db):
    t = Trabajador(no_empleado='EMP-200', nombre_apellidos='María Ruiz',
                    nombre='María', activo=True, tipo_nomina='Semanal',
                    salario_real_pactado_x_sem=5000)
    db.session.add(t); db.session.commit()
    return t


# ═══════════════════════════════════════════════════════════════════════════════
# Coordinador → solicitudes
# ═══════════════════════════════════════════════════════════════════════════════

class TestCoordinadorSolicitudes:

    def test_coordinador_puede_crear_solicitud(self, client, coord, producto):
        r = client.post('/api/v1/solicitudes/', headers=_hdr(coord), json={
            'proyecto': 'Obra coordinada',
            'detalles': [{
                'tipo_item': 'MATERIAL',
                'producto_id': producto.id,
                'cantidad_solicitada': 3,
            }],
        })
        assert r.status_code == 200, r.get_json()
        body = r.get_json()
        assert body['solicitante_id'] == coord.id
        assert body['estatus'] == 'PENDIENTE'

    def test_coordinador_solo_ve_las_suyas(self, client, coord, coord_b, inv_user, producto):
        # coord crea una, coord_b crea otra
        client.post('/api/v1/solicitudes/', headers=_hdr(coord), json={
            'detalles': [{'tipo_item': 'MATERIAL', 'producto_id': producto.id,
                          'cantidad_solicitada': 1}],
        })
        client.post('/api/v1/solicitudes/', headers=_hdr(coord_b), json={
            'detalles': [{'tipo_item': 'MATERIAL', 'producto_id': producto.id,
                          'cantidad_solicitada': 2}],
        })

        r = client.get('/api/v1/solicitudes/', headers=_hdr(coord))
        assert r.status_code == 200
        sols = r.get_json()
        assert len(sols) == 1
        assert sols[0]['solicitante_id'] == coord.id

        # inventario ve todas
        r_inv = client.get('/api/v1/solicitudes/', headers=_hdr(inv_user))
        assert r_inv.status_code == 200
        assert len(r_inv.get_json()) == 2

    def test_coordinador_no_puede_aprobar(self, client, coord, inv_user, producto):
        r = client.post('/api/v1/solicitudes/', headers=_hdr(coord), json={
            'detalles': [{'tipo_item': 'MATERIAL', 'producto_id': producto.id,
                          'cantidad_solicitada': 1}],
        })
        sol_id = r.get_json()['id']
        # Coord NO puede aprobar (requiere inventario_admin)
        rp = client.patch(f'/api/v1/solicitudes/{sol_id}/estado',
                           headers=_hdr(coord), json={'estatus': 'APROBADA'})
        assert rp.status_code == 403

    def test_coordinador_puede_ver_catalogo(self, client, coord, producto):
        r = client.get('/api/v1/productos/', headers=_hdr(coord))
        assert r.status_code == 200
        codigos = [p['codigo'] for p in r.get_json()]
        assert 'CT-001' in codigos

    def test_coordinador_no_puede_entregar(self, client, coord, inv_user, producto):
        """Coordinador no puede usar el endpoint de entrega parcial."""
        r = client.post('/api/v1/solicitudes/', headers=_hdr(coord), json={
            'detalles': [{'tipo_item': 'MATERIAL', 'producto_id': producto.id,
                          'cantidad_solicitada': 2}],
        })
        sol_id = r.get_json()['id']
        # Aprobar como inventario
        client.patch(f'/api/v1/solicitudes/{sol_id}/estado',
                     headers=_hdr(inv_user), json={'estatus': 'APROBADA'})
        det_id = r.get_json()['detalles'][0]['id']
        # Coord intenta entregar — debe fallar
        re = client.post(f'/api/v1/solicitudes/{sol_id}/entregar',
                          headers=_hdr(coord), json={
            'entregas': [{'detalle_id': det_id, 'cantidad_entregada': 1}],
        })
        assert re.status_code == 403

    def test_coordinador_imprime_solo_las_suyas(self, client, coord, coord_b, producto):
        r = client.post('/api/v1/solicitudes/', headers=_hdr(coord_b), json={
            'detalles': [{'tipo_item': 'MATERIAL', 'producto_id': producto.id,
                          'cantidad_solicitada': 1}],
        })
        sol_id = r.get_json()['id']
        # coord intenta imprimir solicitud de coord_b → 403
        rp = client.get(f'/api/v1/solicitudes/{sol_id}/pdf', headers=_hdr(coord))
        assert rp.status_code == 403


# ═══════════════════════════════════════════════════════════════════════════════
# PUT /api/users/<id> — trabajador_id
# ═══════════════════════════════════════════════════════════════════════════════

class TestUserTrabajadorLink:

    def test_admin_liga_usuario_a_trabajador(self, client, admin, coord, trabajador_a):
        r = client.put(f'/api/users/{coord.id}', headers=_hdr(admin), json={
            'trabajador_id': trabajador_a.id,
        })
        assert r.status_code == 200, r.get_json()
        body = r.get_json()
        assert body['trabajador_id'] == trabajador_a.id
        assert body['trabajador_no_empleado'] == 'EMP-100'
        assert body['trabajador_nombre'] == 'Juan Pérez'

    def test_admin_desvincula_con_null(self, client, db, admin, coord, trabajador_a):
        coord.trabajador_id = trabajador_a.id
        db.session.commit()
        r = client.put(f'/api/users/{coord.id}', headers=_hdr(admin), json={
            'trabajador_id': None,
        })
        assert r.status_code == 200
        assert r.get_json()['trabajador_id'] is None

    def test_trabajador_inexistente(self, client, admin, coord):
        r = client.put(f'/api/users/{coord.id}', headers=_hdr(admin), json={
            'trabajador_id': 99999,
        })
        assert r.status_code == 404

    def test_trabajador_no_int(self, client, admin, coord):
        r = client.put(f'/api/users/{coord.id}', headers=_hdr(admin), json={
            'trabajador_id': 'abc',
        })
        assert r.status_code == 400

    def test_un_trabajador_un_usuario(self, client, db, admin, coord, coord_b, trabajador_a):
        """Si trabajador ya está ligado a otro usuario, rechazar con 409."""
        coord.trabajador_id = trabajador_a.id
        db.session.commit()
        r = client.put(f'/api/users/{coord_b.id}', headers=_hdr(admin), json={
            'trabajador_id': trabajador_a.id,
        })
        assert r.status_code == 409
        assert 'ligado' in r.get_json()['error'].lower()

    def test_revincular_mismo_usuario_ok(self, client, db, admin, coord, trabajador_a, trabajador_b):
        """Re-ligar al mismo usuario a otro trabajador no debe fallar por la regla 1:1."""
        coord.trabajador_id = trabajador_a.id
        db.session.commit()
        r = client.put(f'/api/users/{coord.id}', headers=_hdr(admin), json={
            'trabajador_id': trabajador_b.id,
        })
        assert r.status_code == 200
        assert r.get_json()['trabajador_id'] == trabajador_b.id

    def test_payload_sin_trabajador_no_toca_valor(self, client, db, admin, coord, trabajador_a):
        coord.trabajador_id = trabajador_a.id
        coord.area = 'Zona 1'
        db.session.commit()
        r = client.put(f'/api/users/{coord.id}', headers=_hdr(admin), json={
            'area': 'Zona 2',
        })
        assert r.status_code == 200
        body = r.get_json()
        assert body['area'] == 'Zona 2'
        assert body['trabajador_id'] == trabajador_a.id  # no se tocó

    def test_listado_incluye_trabajador(self, client, db, admin, coord, trabajador_a):
        coord.trabajador_id = trabajador_a.id
        db.session.commit()
        r = client.get('/api/users', headers=_hdr(admin))
        assert r.status_code == 200
        users = r.get_json()
        coord_dict = next(u for u in users if u['id'] == coord.id)
        assert coord_dict['trabajador_id'] == trabajador_a.id
        assert coord_dict['trabajador_no_empleado'] == 'EMP-100'

    def test_no_admin_no_puede_ligar(self, client, coord, trabajador_a):
        r = client.put(f'/api/users/{coord.id}', headers=_hdr(coord), json={
            'trabajador_id': trabajador_a.id,
        })
        assert r.status_code == 403
