"""Tests del API JWT `/api/proyectos/*` — CRUD del catálogo de Proyectos.

Cobertura:
  - GET  /mios           lista enriquecida para "Mis Proyectos" (coord/admin)
  - GET  /               listado con filtros `q` y `estado`
  - GET  /meta           opciones para el modal (coordinadores + trabajadores)
  - GET  /<id>           detalle (coord solo el propio)
  - POST /               crear (admin)
  - PUT  /<id>           actualizar (admin)

Reglas no obvias:
  - Solo admin/super_admin/coordinador pueden listar; coord ve solo los suyos.
  - El campo `coordinador_id` solo acepta usuarios con rol válido
    (`coordinador|admin|super_admin`) — un `solicitante_material` no puede
    ser coordinador.
  - Detalle: coord viendo proyecto ajeno → 403 (no 404; no leak existencia).
  - Crear/Actualizar: campos obligatorios `numero_proyecto`+`nombre`;
    `numero_proyecto` único (carrera cubierta con IntegrityError → 409).
  - Derivación: `no_proyecto`/`ubicacion_actual`/`coord_a_cargo` del
    Trabajador se RECALCULAN desde sus proyectos ACTIVOS en cada mutación
    (M:N — varios proyectos se unen con ', '). Salir del proyecto o que el
    proyecto se desactive elimina la relación de esos campos.
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
def superadmin(db):
    u = User(username='proy_super', password_hash=generate_password_hash('Pass123!'),
              role='super_admin')
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

    def test_paginacion(self, client, admin, db):
        for i in range(3):
            db.session.add(Proyecto(numero_proyecto=f'PG-{i}', nombre=f'P{i}', activo=True))
        db.session.commit()
        r = client.get('/api/proyectos?page=1&per_page=2', headers=_hdr(admin))
        body = r.get_json()
        assert len(body['items']) == 2
        assert body['total'] == 3
        assert body['pages'] == 2
        assert body['has_next'] is True

    def test_sort_participantes_desc(self, client, admin, proyecto_coord, db):
        vacio = Proyecto(numero_proyecto='PRY-VAC', nombre='Sin gente', activo=True)
        db.session.add(vacio); db.session.commit()
        r = client.get('/api/proyectos?sort=participantes&dir=desc', headers=_hdr(admin))
        items = r.get_json()['items']
        assert items[0]['numero_proyecto'] == 'PRY-100'
        assert items[0]['participantes_count'] == 1
        assert items[-1]['participantes_count'] == 0

    def test_sort_invalido_cae_a_default(self, client, admin, proyecto_coord):
        r = client.get('/api/proyectos?sort=drop_table&dir=desc', headers=_hdr(admin))
        assert r.status_code == 200
        assert r.get_json()['total'] == 1

    def test_row_incluye_full_name_del_coordinador(
        self, client, admin, coord, proyecto_coord, db,
    ):
        coord.full_name = 'Carlos Coordinador'
        db.session.commit()
        r = client.get('/api/proyectos', headers=_hdr(admin))
        row = next(p for p in r.get_json()['items'] if p['numero_proyecto'] == 'PRY-100')
        assert row['coordinador']['full_name'] == 'Carlos Coordinador'
        # El SPA arma el avatar con id + profile_pic (UserAvatar)
        assert 'profile_pic' in row['coordinador']


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

    def test_incluye_super_admin_como_coordinador(self, client, admin, superadmin):
        # _VALID_COORD_ROLES acepta super_admin; el selector debe listarlo.
        r = client.get('/api/proyectos/meta', headers=_hdr(admin))
        usernames = {c['username'] for c in r.get_json()['coordinadores']}
        assert superadmin.username in usernames

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


# ═══════════════════════════════════════════════════════════════════════════════
# 8. DERIVACIÓN M:N (expediente/credenciales reflejan SOLO proyectos activos)
# ═══════════════════════════════════════════════════════════════════════════════

class TestDerivacion:

    def _put(self, client, admin, p, **overrides):
        payload = {
            'numero_proyecto': p.numero_proyecto,
            'nombre': p.nombre,
            'activo': p.activo,
            'coordinador_id': p.coordinador_id,
            'participantes_ids': [t.id for t in p.participantes],
        }
        payload.update(overrides)
        return client.put(f'/api/proyectos/{p.id}', headers=_hdr(admin), json=payload)

    def test_trabajador_en_dos_proyectos_une_con_coma(
        self, client, admin, proyecto_coord, trab_ok, db,
    ):
        r = client.post('/api/proyectos', headers=_hdr(admin), json={
            'numero_proyecto': 'AAA-001', 'nombre': 'Obra segunda',
            'participantes_ids': [trab_ok.id],
        })
        assert r.status_code == 201
        db.session.refresh(trab_ok)
        # Orden alfabético por numero_proyecto
        assert trab_ok.no_proyecto == 'AAA-001, PRY-100'
        assert 'Obra segunda' in trab_ok.ubicacion_actual
        assert 'Obra coord' in trab_ok.ubicacion_actual

    def test_salir_de_un_proyecto_conserva_el_otro(
        self, client, admin, proyecto_coord, trab_ok, db,
    ):
        client.post('/api/proyectos', headers=_hdr(admin), json={
            'numero_proyecto': 'AAA-001', 'nombre': 'Obra segunda',
            'participantes_ids': [trab_ok.id],
        })
        # Lo sacan de PRY-100; debe conservar SOLO AAA-001
        r = self._put(client, admin, proyecto_coord, participantes_ids=[])
        assert r.status_code == 200
        db.session.refresh(trab_ok)
        assert trab_ok.no_proyecto == 'AAA-001'
        assert trab_ok.ubicacion_actual == 'Obra segunda'

    def test_desactivar_proyecto_suelta_la_relacion(
        self, client, admin, proyecto_coord, trab_ok, db,
    ):
        r = self._put(client, admin, proyecto_coord, activo=False)
        assert r.status_code == 200
        db.session.refresh(trab_ok)
        assert trab_ok.no_proyecto is None
        assert trab_ok.ubicacion_actual is None
        assert trab_ok.coord_a_cargo is None

    def test_reactivar_proyecto_restaura_la_relacion(
        self, client, admin, proyecto_coord, trab_ok, db,
    ):
        self._put(client, admin, proyecto_coord, activo=False)
        r = self._put(client, admin, proyecto_coord, activo=True)
        assert r.status_code == 200
        db.session.refresh(trab_ok)
        assert trab_ok.no_proyecto == 'PRY-100'

    def test_credenciales_no_muestran_proyecto_inactivo(
        self, client, admin, proyecto_coord, trab_ok, db,
    ):
        self._put(client, admin, proyecto_coord, activo=False)
        r = client.get('/api/trabajadores/credenciales-lista', headers=_hdr(admin))
        fila = next(x for x in r.get_json()['items'] if x['no_empleado'] == trab_ok.no_empleado)
        assert fila['proyectos_activos'] == ''
        assert fila['coord_a_cargo'] == ''  # sin fallback al string legacy

    def test_coord_a_cargo_se_llena_y_se_limpia(
        self, client, admin, coord, proyecto_coord, trab_ok, db,
    ):
        # Tocar el proyecto rellena coord_a_cargo desde el coordinador actual
        r = self._put(client, admin, proyecto_coord)
        assert r.status_code == 200
        db.session.refresh(trab_ok)
        assert trab_ok.coord_a_cargo == coord.username

        # Quitar el coordinador del proyecto lo limpia (antes quedaba pegado)
        r = self._put(client, admin, proyecto_coord, coordinador_id=None)
        assert r.status_code == 200
        db.session.refresh(trab_ok)
        assert trab_ok.coord_a_cargo is None

    def test_participante_inexistente_devuelve_warning(
        self, client, admin, trab_ok,
    ):
        r = client.post('/api/proyectos', headers=_hdr(admin), json={
            'numero_proyecto': 'WRN-001', 'nombre': 'Con warning',
            'participantes_ids': [trab_ok.id, 999999],
        })
        assert r.status_code == 201
        body = r.get_json()
        assert body['participantes_ids'] == [trab_ok.id]
        assert len(body['warnings']) == 1
        assert '999999' in body['warnings'][0]

    def test_participantes_ids_malformado_400(self, client, admin):
        r = client.post('/api/proyectos', headers=_hdr(admin), json={
            'numero_proyecto': 'BAD-001', 'nombre': 'X',
            'participantes_ids': ['abc'],
        })
        assert r.status_code == 400
