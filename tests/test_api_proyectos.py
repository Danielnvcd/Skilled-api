"""Tests del API JWT `/api/proyectos/*` — CRUD del catálogo de Proyectos.

Cobertura:
  - GET  /mios           lista enriquecida para "Mis Proyectos" (coord/admin)
  - GET  /               listado con filtros `q` y `estado`
  - GET  /meta           opciones para el modal (coordinadores + trabajadores)
  - GET  /<id>           detalle (coord solo el propio)
  - POST /               crear (admin)
  - PUT  /<id>           actualizar (admin) — incluye sincronización del
                         campo `no_proyecto`/`ubicacion_actual` en el Trabajador
                         cuando entra o sale de la lista de participantes

Reglas no obvias:
  - Solo admin/super_admin/coordinador pueden listar; coord ve solo los suyos.
  - El campo `coordinador_id` solo acepta usuarios con rol válido
    (`coordinador|admin|super_admin`) — un `solicitante_material` no puede
    ser coordinador.
  - Detalle: coord viendo proyecto ajeno → 403 (no 404; no leak existencia).
  - Crear/Actualizar: campos obligatorios `numero_proyecto`+`nombre`;
    `numero_proyecto` único.
  - Update: si un trabajador deja la lista de participantes, sus campos
    `no_proyecto`/`ubicacion_actual` se limpian.
"""
import pytest
from werkzeug.security import generate_password_hash

from app.models import Proyecto, Trabajador, User
from app.routes.api_auth import _encode_access_token


def _hdr(user):
    return {'Authorization': f'Bearer {_encode_access_token(user)}'}


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def admin(db):
    u = User(username='proy_admin', password_hash=generate_password_hash('Pass123!'), role='admin')
    db.session.add(u); db.session.commit()
    return u


@pytest.fixture
def coord(db):
    u = User(username='proy_coord', password_hash=generate_password_hash('Pass123!'),
              role='coordinador')
    db.session.add(u); db.session.commit()
    return u


@pytest.fixture
def coord_b(db):
    u = User(username='proy_coord_b', password_hash=generate_password_hash('Pass123!'),
              role='coordinador')
    db.session.add(u); db.session.commit()
    return u


@pytest.fixture
def outsider(db):
    u = User(username='proy_out', password_hash=generate_password_hash('Pass123!'),
              role='solicitante_material')
    db.session.add(u); db.session.commit()
    return u


@pytest.fixture
def trab_ok(db):
    """Trabajador 'pickable': con salario y tipo_nomina."""
    t = Trabajador(no_empleado='P-OK1', nombre='Andrea', nombre_apellidos='Soto',
                    activo=True, tipo_nomina='Semanal',
                    salario_real_pactado_x_sem=5000)
    db.session.add(t); db.session.commit()
    return t


@pytest.fixture
def trab_otro(db):
    t = Trabajador(no_empleado='P-OK2', nombre='Bruno', nombre_apellidos='Lara',
                    activo=True, tipo_nomina='Por hora',
                    salario_real_pactado_x_sem=150)
    db.session.add(t); db.session.commit()
    return t


@pytest.fixture
def trab_sin_salario(db):
    """No 'pickable': sin salario → motivos=['Sin salario']."""
    t = Trabajador(no_empleado='P-SAL', nombre='Carla', nombre_apellidos='Mejía',
                    activo=True, tipo_nomina='Semanal',
                    salario_real_pactado_x_sem=0)
    db.session.add(t); db.session.commit()
    return t


@pytest.fixture
def proyecto_coord(db, coord, trab_ok):
    p = Proyecto(numero_proyecto='PRY-100', nombre='Obra coord',
                  activo=True, coordinador_id=coord.id)
    p.participantes.append(trab_ok)
    # Mantener sincronía como lo hace el endpoint /crear
    trab_ok.no_proyecto = p.numero_proyecto
    trab_ok.ubicacion_actual = p.nombre
    db.session.add(p); db.session.commit()
    return p


@pytest.fixture
def proyecto_ajeno(db, coord_b, trab_otro):
    p = Proyecto(numero_proyecto='PRY-200', nombre='Obra de otro coord',
                  activo=True, coordinador_id=coord_b.id)
    p.participantes.append(trab_otro)
    db.session.add(p); db.session.commit()
    return p


# ═══════════════════════════════════════════════════════════════════════════════
# 1. AUTH / AUTHZ
# ═══════════════════════════════════════════════════════════════════════════════

class TestAuth:

    def test_sin_token_401(self, client):
        r = client.get('/api/proyectos')
        assert r.status_code == 401

    def test_outsider_403(self, client, outsider):
        r = client.get('/api/proyectos', headers=_hdr(outsider))
        assert r.status_code == 403

    def test_admin_200(self, client, admin):
        r = client.get('/api/proyectos', headers=_hdr(admin))
        assert r.status_code == 200

    def test_coord_200(self, client, coord):
        r = client.get('/api/proyectos', headers=_hdr(coord))
        assert r.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════════
# 2. /mios
# ═══════════════════════════════════════════════════════════════════════════════

class TestMios:

    def test_coord_solo_los_suyos(
        self, client, coord, proyecto_coord, proyecto_ajeno,
    ):
        r = client.get('/api/proyectos/mios', headers=_hdr(coord))
        assert r.status_code == 200
        codigos = {p['numero_proyecto'] for p in r.get_json()}
        assert codigos == {'PRY-100'}

    def test_admin_ve_todos_activos(
        self, client, admin, proyecto_coord, proyecto_ajeno,
    ):
        r = client.get('/api/proyectos/mios', headers=_hdr(admin))
        codigos = {p['numero_proyecto'] for p in r.get_json()}
        assert codigos == {'PRY-100', 'PRY-200'}

    def test_outsider_403(self, client, outsider):
        r = client.get('/api/proyectos/mios', headers=_hdr(outsider))
        assert r.status_code == 403

    def test_payload_incluye_participantes(
        self, client, coord, proyecto_coord, trab_ok,
    ):
        r = client.get('/api/proyectos/mios', headers=_hdr(coord))
        body = r.get_json()
        p = next(x for x in body if x['numero_proyecto'] == 'PRY-100')
        assert p['participantes_count'] == 1
        assert p['participantes'][0]['no_empleado'] == trab_ok.no_empleado


# ═══════════════════════════════════════════════════════════════════════════════
# 3. LISTAR (filtros)
# ═══════════════════════════════════════════════════════════════════════════════

class TestListar:

    def test_admin_ve_activos_e_inactivos(
        self, client, admin, proyecto_coord, db,
    ):
        # Crear un proyecto inactivo
        inactivo = Proyecto(numero_proyecto='PRY-INA', nombre='Cerrado',
                             activo=False, coordinador_id=admin.id)
        db.session.add(inactivo); db.session.commit()

        r = client.get('/api/proyectos', headers=_hdr(admin))
        codigos = {p['numero_proyecto'] for p in r.get_json()['items']}
        assert {'PRY-100', 'PRY-INA'} <= codigos

    def test_filtro_estado_activos(
        self, client, admin, proyecto_coord, db,
    ):
        inactivo = Proyecto(numero_proyecto='PRY-INA', nombre='Cerrado',
                             activo=False, coordinador_id=admin.id)
        db.session.add(inactivo); db.session.commit()

        r = client.get('/api/proyectos?estado=activos', headers=_hdr(admin))
        codigos = {p['numero_proyecto'] for p in r.get_json()['items']}
        assert 'PRY-100' in codigos and 'PRY-INA' not in codigos

    def test_filtro_estado_inactivos(self, client, admin, db):
        inactivo = Proyecto(numero_proyecto='PRY-INA', nombre='Cerrado',
                             activo=False, coordinador_id=admin.id)
        db.session.add(inactivo); db.session.commit()

        r = client.get('/api/proyectos?estado=inactivos', headers=_hdr(admin))
        codigos = {p['numero_proyecto'] for p in r.get_json()['items']}
        assert 'PRY-INA' in codigos

    def test_filtro_q_por_numero(self, client, admin, proyecto_coord, proyecto_ajeno):
        r = client.get('/api/proyectos?q=200', headers=_hdr(admin))
        codigos = {p['numero_proyecto'] for p in r.get_json()['items']}
        assert codigos == {'PRY-200'}

    def test_filtro_q_por_nombre(self, client, admin, proyecto_coord, proyecto_ajeno):
        r = client.get('/api/proyectos?q=otro+coord', headers=_hdr(admin))
        codigos = {p['numero_proyecto'] for p in r.get_json()['items']}
        assert codigos == {'PRY-200'}

    def test_coord_solo_ve_los_suyos(
        self, client, coord, proyecto_coord, proyecto_ajeno,
    ):
        r = client.get('/api/proyectos', headers=_hdr(coord))
        codigos = {p['numero_proyecto'] for p in r.get_json()['items']}
        assert codigos == {'PRY-100'}


# ═══════════════════════════════════════════════════════════════════════════════
# 4. META
# ═══════════════════════════════════════════════════════════════════════════════

class TestMeta:

    def test_admin_recibe_meta(
        self, client, admin, coord, trab_ok, trab_sin_salario,
    ):
        r = client.get('/api/proyectos/meta', headers=_hdr(admin))
        assert r.status_code == 200
        body = r.get_json()
        coord_usernames = {c['username'] for c in body['coordinadores']}
        assert coord.username in coord_usernames
        codigos = {t['no_empleado'] for t in body['trabajadores']}
        assert {'P-OK1', 'P-SAL'} <= codigos

    def test_coordinador_403(self, client, coord):
        r = client.get('/api/proyectos/meta', headers=_hdr(coord))
        assert r.status_code == 403

    def test_trabajador_no_disponible_si_sin_salario(
        self, client, admin, trab_sin_salario,
    ):
        r = client.get('/api/proyectos/meta', headers=_hdr(admin))
        ts = {t['no_empleado']: t for t in r.get_json()['trabajadores']}
        sin_sal = ts['P-SAL']
        assert sin_sal['disponible'] is False
        assert 'Sin salario' in sin_sal['motivos_no_disponible']


# ═══════════════════════════════════════════════════════════════════════════════
# 5. OBTENER
# ═══════════════════════════════════════════════════════════════════════════════

class TestObtener:

    def test_admin_ve_cualquiera(self, client, admin, proyecto_ajeno):
        r = client.get(f'/api/proyectos/{proyecto_ajeno.id}', headers=_hdr(admin))
        assert r.status_code == 200
        assert r.get_json()['numero_proyecto'] == 'PRY-200'

    def test_coord_ve_el_suyo(self, client, coord, proyecto_coord):
        r = client.get(f'/api/proyectos/{proyecto_coord.id}', headers=_hdr(coord))
        assert r.status_code == 200

    def test_coord_no_ve_ajeno_403(self, client, coord, proyecto_ajeno):
        r = client.get(f'/api/proyectos/{proyecto_ajeno.id}', headers=_hdr(coord))
        assert r.status_code == 403

    def test_outsider_403(self, client, outsider, proyecto_coord):
        r = client.get(f'/api/proyectos/{proyecto_coord.id}', headers=_hdr(outsider))
        assert r.status_code == 403

    def test_inexistente_404(self, client, admin):
        r = client.get('/api/proyectos/99999', headers=_hdr(admin))
        assert r.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# 6. CREAR
# ═══════════════════════════════════════════════════════════════════════════════

class TestCrear:

    def test_admin_crea_proyecto(self, client, admin, coord, trab_ok, db):
        r = client.post('/api/proyectos', headers=_hdr(admin), json={
            'numero_proyecto': 'NEW-001',
            'nombre': 'Construcción Norte',
            'activo': True,
            'coordinador_id': coord.id,
            'participantes_ids': [trab_ok.id],
        })
        assert r.status_code == 201, r.get_json()
        body = r.get_json()
        assert body['numero_proyecto'] == 'NEW-001'
        assert body['coordinador_id'] == coord.id
        assert body['participantes_ids'] == [trab_ok.id]
        # El trabajador queda sincronizado con el nuevo proyecto
        db.session.refresh(trab_ok)
        assert trab_ok.no_proyecto == 'NEW-001'
        assert trab_ok.ubicacion_actual == 'Construcción Norte'

    def test_coord_no_puede_crear_403(self, client, coord):
        r = client.post('/api/proyectos', headers=_hdr(coord), json={
            'numero_proyecto': 'X', 'nombre': 'X',
        })
        assert r.status_code == 403

    def test_falta_numero_400(self, client, admin):
        r = client.post('/api/proyectos', headers=_hdr(admin), json={
            'nombre': 'Sin número',
        })
        assert r.status_code == 400

    def test_falta_nombre_400(self, client, admin):
        r = client.post('/api/proyectos', headers=_hdr(admin), json={
            'numero_proyecto': 'X',
        })
        assert r.status_code == 400

    def test_numero_duplicado_409(self, client, admin, proyecto_coord):
        r = client.post('/api/proyectos', headers=_hdr(admin), json={
            'numero_proyecto': proyecto_coord.numero_proyecto,
            'nombre': 'Duplicado',
        })
        assert r.status_code == 409

    def test_coordinador_inexistente_400(self, client, admin):
        r = client.post('/api/proyectos', headers=_hdr(admin), json={
            'numero_proyecto': 'NEW-001', 'nombre': 'X',
            'coordinador_id': 99999,
        })
        assert r.status_code == 400

    def test_coordinador_rol_invalido_400(self, client, admin, outsider):
        # outsider tiene rol solicitante_material → no puede coordinar
        r = client.post('/api/proyectos', headers=_hdr(admin), json={
            'numero_proyecto': 'NEW-002', 'nombre': 'X',
            'coordinador_id': outsider.id,
        })
        assert r.status_code == 400

    def test_coordinador_id_no_numerico_400(self, client, admin):
        r = client.post('/api/proyectos', headers=_hdr(admin), json={
            'numero_proyecto': 'NEW-003', 'nombre': 'X',
            'coordinador_id': 'abc',
        })
        assert r.status_code == 400


# ═══════════════════════════════════════════════════════════════════════════════
# 7. ACTUALIZAR
# ═══════════════════════════════════════════════════════════════════════════════

class TestActualizar:

    def test_admin_actualiza(self, client, admin, proyecto_coord, db):
        r = client.put(f'/api/proyectos/{proyecto_coord.id}', headers=_hdr(admin), json={
            'numero_proyecto': 'PRY-100',
            'nombre': 'Obra coord (renombrada)',
            'activo': True,
            'coordinador_id': proyecto_coord.coordinador_id,
            'participantes_ids': [t.id for t in proyecto_coord.participantes],
        })
        assert r.status_code == 200, r.get_json()
        db.session.refresh(proyecto_coord)
        assert proyecto_coord.nombre == 'Obra coord (renombrada)'

    def test_coord_no_puede_actualizar_403(self, client, coord, proyecto_coord):
        r = client.put(f'/api/proyectos/{proyecto_coord.id}', headers=_hdr(coord), json={
            'numero_proyecto': 'X', 'nombre': 'Y',
        })
        assert r.status_code == 403

    def test_actualizar_inexistente_404(self, client, admin):
        r = client.put('/api/proyectos/99999', headers=_hdr(admin), json={
            'numero_proyecto': 'X', 'nombre': 'Y',
        })
        assert r.status_code == 404

    def test_cambiar_numero_a_uno_duplicado_409(
        self, client, admin, proyecto_coord, proyecto_ajeno,
    ):
        r = client.put(f'/api/proyectos/{proyecto_coord.id}', headers=_hdr(admin), json={
            'numero_proyecto': proyecto_ajeno.numero_proyecto,
            'nombre': proyecto_coord.nombre,
        })
        assert r.status_code == 409

    def test_coordinador_rol_invalido_400(
        self, client, admin, proyecto_coord, outsider,
    ):
        r = client.put(f'/api/proyectos/{proyecto_coord.id}', headers=_hdr(admin), json={
            'numero_proyecto': proyecto_coord.numero_proyecto,
            'nombre': proyecto_coord.nombre,
            'coordinador_id': outsider.id,
        })
        assert r.status_code == 400

    def test_quitar_participante_limpia_sus_campos(
        self, client, admin, proyecto_coord, trab_ok, db,
    ):
        # Antes del PUT, trab_ok está en el proyecto y tiene no_proyecto sincronizado
        assert trab_ok.no_proyecto == 'PRY-100'

        r = client.put(f'/api/proyectos/{proyecto_coord.id}', headers=_hdr(admin), json={
            'numero_proyecto': proyecto_coord.numero_proyecto,
            'nombre': proyecto_coord.nombre,
            'coordinador_id': proyecto_coord.coordinador_id,
            'participantes_ids': [],  # ← lo sacamos
        })
        assert r.status_code == 200
        db.session.refresh(trab_ok)
        # Al salir del proyecto, sus campos se limpian
        assert trab_ok.no_proyecto is None
        assert trab_ok.ubicacion_actual is None

    def test_agregar_participante_sincroniza_campos(
        self, client, admin, proyecto_coord, trab_otro, db,
    ):
        # trab_otro empieza sin proyecto asignado
        assert trab_otro.no_proyecto in (None, '')

        r = client.put(f'/api/proyectos/{proyecto_coord.id}', headers=_hdr(admin), json={
            'numero_proyecto': proyecto_coord.numero_proyecto,
            'nombre': proyecto_coord.nombre,
            'coordinador_id': proyecto_coord.coordinador_id,
            'participantes_ids': [t.id for t in proyecto_coord.participantes] + [trab_otro.id],
        })
        assert r.status_code == 200
        db.session.refresh(trab_otro)
        assert trab_otro.no_proyecto == 'PRY-100'
        assert trab_otro.ubicacion_actual == proyecto_coord.nombre
