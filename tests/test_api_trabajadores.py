"""Tests del API JWT `/api/trabajadores/*` — CRUD del padrón de empleados.

Cobertura:
  - GET    /                         listar (filtros estado / q / paginación)
  - GET    /ficha-tecnica            ficha médica (admin ve todos, coord solo
                                     los de sus proyectos)
  - GET    /<id>                     detalle (PII enmascarada si no es admin)
  - POST   /                         crear (admin)
  - PUT    /<id>                     actualizar (whitelist por rol)
  - DELETE /<id>                     dar de baja
  - POST   /<id>/reactivar           reactivar
  - POST   /bulk                     baja/reactivar en lote
  - 401 sin token, 403 por rol, 404 inexistente

Auth: JWT real (`_encode_access_token`). POST/PUT envían `request.form` →
usamos `data=` (form-urlencoded), no `json=`.
"""
from datetime import date

import pytest
from werkzeug.security import generate_password_hash

from app.models import Proyecto, Trabajador, User
from app.routes.api_auth import _encode_access_token


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _hdr(user):
    return {'Authorization': f'Bearer {_encode_access_token(user)}'}


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def admin(db):
    u = User(username='emp_admin', password_hash=generate_password_hash('Pass123!'), role='admin')
    db.session.add(u); db.session.commit()
    return u


@pytest.fixture
def coord(db):
    u = User(username='emp_coord', password_hash=generate_password_hash('Pass123!'),
              role='coordinador')
    db.session.add(u); db.session.commit()
    return u


@pytest.fixture
def outsider(db):
    """Rol sin permisos sobre /api/trabajadores."""
    u = User(username='emp_out', password_hash=generate_password_hash('Pass123!'),
              role='solicitante_material')
    db.session.add(u); db.session.commit()
    return u


@pytest.fixture
def trab_a(db):
    t = Trabajador(
        no_empleado='EMP-A', nombre='Ana', nombre_apellidos='García',
        activo=True, tipo_nomina='Semanal',
        salario_real_pactado_x_sem=5000,
        curp='GARA800101HDFRRN09', rfc='GARA800101AAA', nss='12345678901',
        tipo_sangre='O+', alergias='Polen',
    )
    db.session.add(t); db.session.commit()
    return t


@pytest.fixture
def trab_b(db):
    t = Trabajador(
        no_empleado='EMP-B', nombre='Bruno', nombre_apellidos='Hernández',
        activo=True, tipo_nomina='Por hora',
        salario_real_pactado_x_sem=150,
    )
    db.session.add(t); db.session.commit()
    return t


@pytest.fixture
def trab_baja(db):
    """Trabajador dado de baja — no entra en el filtro `activos`."""
    t = Trabajador(
        no_empleado='EMP-X', nombre='Xavier', nombre_apellidos='Pérez',
        activo=False, fecha_baja=date(2026, 1, 15), tipo_nomina='Semanal',
        salario_real_pactado_x_sem=4000,
    )
    db.session.add(t); db.session.commit()
    return t


@pytest.fixture
def proyecto_coord(db, coord, trab_a):
    """Proyecto donde `coord` es coordinador y `trab_a` es participante.
    Necesario para que el coord pueda ver/editar a `trab_a` (regla
    `_authorized`)."""
    p = Proyecto(
        numero_proyecto='COORD-P1', nombre='Obra del coord',
        activo=True, coordinador_id=coord.id,
    )
    p.participantes.append(trab_a)
    db.session.add(p); db.session.commit()
    return p


# ═══════════════════════════════════════════════════════════════════════════════
# 1. AUTH / AUTHZ
# ═══════════════════════════════════════════════════════════════════════════════

class TestAuth:

    def test_sin_token_retorna_401(self, client):
        r = client.get('/api/trabajadores')
        assert r.status_code == 401

    def test_solicitante_material_403(self, client, outsider):
        r = client.get('/api/trabajadores', headers=_hdr(outsider))
        assert r.status_code == 403

    def test_admin_lista(self, client, admin):
        r = client.get('/api/trabajadores', headers=_hdr(admin))
        assert r.status_code == 200

    def test_coordinador_lista(self, client, coord):
        r = client.get('/api/trabajadores', headers=_hdr(coord))
        assert r.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════════
# 2. LISTAR (filtros + paginación)
# ═══════════════════════════════════════════════════════════════════════════════

class TestListar:

    def test_default_solo_activos(self, client, admin, trab_a, trab_b, trab_baja):
        r = client.get('/api/trabajadores', headers=_hdr(admin))
        assert r.status_code == 200
        codigos = {t['no_empleado'] for t in r.get_json()['items']}
        assert codigos == {'EMP-A', 'EMP-B'}
        # trab_baja no debe aparecer en activos
        assert 'EMP-X' not in codigos

    def test_estado_bajas_solo_admin(self, client, admin, trab_baja):
        r = client.get('/api/trabajadores?estado=bajas', headers=_hdr(admin))
        assert r.status_code == 200
        codigos = {t['no_empleado'] for t in r.get_json()['items']}
        assert 'EMP-X' in codigos

    def test_estado_bajas_coordinador_403(self, client, coord, trab_baja):
        r = client.get('/api/trabajadores?estado=bajas', headers=_hdr(coord))
        assert r.status_code == 403

    def test_estado_todos_admin(self, client, admin, trab_a, trab_baja):
        r = client.get('/api/trabajadores?estado=todos', headers=_hdr(admin))
        assert r.status_code == 200
        codigos = {t['no_empleado'] for t in r.get_json()['items']}
        assert 'EMP-A' in codigos and 'EMP-X' in codigos

    def test_busqueda_q_por_nombre(self, client, admin, trab_a, trab_b):
        r = client.get('/api/trabajadores?q=Bruno', headers=_hdr(admin))
        assert r.status_code == 200
        items = r.get_json()['items']
        assert len(items) == 1 and items[0]['no_empleado'] == 'EMP-B'

    def test_busqueda_q_por_no_empleado(self, client, admin, trab_a, trab_b):
        r = client.get('/api/trabajadores?q=EMP-A', headers=_hdr(admin))
        items = r.get_json()['items']
        assert len(items) == 1 and items[0]['no_empleado'] == 'EMP-A'

    def test_paginacion(self, client, admin, trab_a, trab_b):
        r = client.get('/api/trabajadores?page=1&per_page=1', headers=_hdr(admin))
        body = r.get_json()
        assert len(body['items']) == 1
        assert body['total'] == 2
        assert body['pages'] == 2
        assert body['has_next'] is True

    def test_coordinador_solo_ve_los_de_su_proyecto(
        self, client, coord, trab_a, trab_b, proyecto_coord,
    ):
        # `proyecto_coord` agrega solo a trab_a → trab_b queda fuera
        r = client.get('/api/trabajadores', headers=_hdr(coord))
        assert r.status_code == 200
        codigos = {t['no_empleado'] for t in r.get_json()['items']}
        assert codigos == {'EMP-A'}


# ═══════════════════════════════════════════════════════════════════════════════
# 3. FICHA TÉCNICA
# ═══════════════════════════════════════════════════════════════════════════════

class TestFichaTecnica:

    def test_admin_ve_todos_activos(self, client, admin, trab_a, trab_b):
        r = client.get('/api/trabajadores/ficha-tecnica', headers=_hdr(admin))
        assert r.status_code == 200
        items = r.get_json()['items']
        assert len(items) == 2

    def test_coordinador_solo_de_sus_proyectos(
        self, client, coord, trab_a, trab_b, proyecto_coord,
    ):
        r = client.get('/api/trabajadores/ficha-tecnica', headers=_hdr(coord))
        items = r.get_json()['items']
        assert len(items) == 1
        assert items[0]['no_empleado'] == 'EMP-A'

    def test_coordinador_sin_proyectos_vacio(self, client, coord, trab_a):
        r = client.get('/api/trabajadores/ficha-tecnica', headers=_hdr(coord))
        assert r.get_json()['items'] == []

    def test_outsider_403(self, client, outsider):
        r = client.get('/api/trabajadores/ficha-tecnica', headers=_hdr(outsider))
        assert r.status_code == 403


# ═══════════════════════════════════════════════════════════════════════════════
# 4. OBTENER UNO
# ═══════════════════════════════════════════════════════════════════════════════

class TestObtener:

    def test_admin_ve_pii_sin_mascarar(self, client, admin, trab_a):
        r = client.get(f'/api/trabajadores/{trab_a.id}', headers=_hdr(admin))
        assert r.status_code == 200
        body = r.get_json()
        # Admin ve el CURP/RFC/NSS completos
        assert body['curp'] == 'GARA800101HDFRRN09'
        assert body['rfc'] == 'GARA800101AAA'
        assert body['nss'] == '12345678901'

    def test_coordinador_ve_pii_enmascarada(
        self, client, coord, trab_a, proyecto_coord,
    ):
        r = client.get(f'/api/trabajadores/{trab_a.id}', headers=_hdr(coord))
        assert r.status_code == 200
        body = r.get_json()
        # PII enmascarada: deja inicio + fin, asteriscos en medio
        assert '*' in body['curp']
        assert '*' in body['rfc']
        assert '*' in body['nss']

    def test_coordinador_sin_proyecto_403(self, client, coord, trab_a):
        # `coord` no tiene proyecto que incluya a trab_a → 403
        r = client.get(f'/api/trabajadores/{trab_a.id}', headers=_hdr(coord))
        assert r.status_code == 403

    def test_inexistente_404(self, client, admin):
        r = client.get('/api/trabajadores/99999', headers=_hdr(admin))
        assert r.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# 5. CREAR
# ═══════════════════════════════════════════════════════════════════════════════

class TestCrear:

    def test_admin_crea_basico(self, client, admin):
        r = client.post('/api/trabajadores', headers=_hdr(admin), data={
            'no_empleado': 'NEW-001',
            'nombre': 'Carlos',
            'nombre_apellidos': 'López',
            'tipo_nomina': 'Semanal',
            'salario_real_pactado_x_sem': '5000',
        })
        assert r.status_code == 201, r.get_json()
        body = r.get_json()
        assert 'id' in body
        # Persistido en BD
        t = Trabajador.query.get(body['id'])
        assert t.no_empleado == 'NEW-001'
        assert t.nombre == 'Carlos'
        assert float(t.salario_real_pactado_x_sem) == 5000.0

    def test_coordinador_no_puede_crear_403(self, client, coord):
        r = client.post('/api/trabajadores', headers=_hdr(coord), data={
            'no_empleado': 'X', 'nombre': 'X',
        })
        assert r.status_code == 403

    def test_falta_no_empleado_400(self, client, admin):
        r = client.post('/api/trabajadores', headers=_hdr(admin), data={
            'nombre': 'SinNumero', 'nombre_apellidos': 'X',
        })
        assert r.status_code == 400

    def test_no_empleado_duplicado_409(self, client, admin, trab_a):
        r = client.post('/api/trabajadores', headers=_hdr(admin), data={
            'no_empleado': trab_a.no_empleado,  # 'EMP-A'
            'nombre': 'Duplicado',
            'nombre_apellidos': 'X',
        })
        assert r.status_code == 409

    def test_salario_negativo_400(self, client, admin):
        r = client.post('/api/trabajadores', headers=_hdr(admin), data={
            'no_empleado': 'NEG-001',
            'nombre': 'X', 'nombre_apellidos': 'X',
            'salario_real_pactado_x_sem': '-100',
        })
        assert r.status_code == 400


# ═══════════════════════════════════════════════════════════════════════════════
# 6. ACTUALIZAR (whitelist por rol)
# ═══════════════════════════════════════════════════════════════════════════════

class TestActualizar:

    def test_admin_actualiza_campos_financieros(self, client, admin, trab_a, db):
        r = client.put(f'/api/trabajadores/{trab_a.id}', headers=_hdr(admin), data={
            'nombre': trab_a.nombre,
            'salario_real_pactado_x_sem': '6500',
            'tipo_nomina': 'Por hora',
        })
        assert r.status_code == 200, r.get_json()
        db.session.refresh(trab_a)
        assert float(trab_a.salario_real_pactado_x_sem) == 6500.0
        assert trab_a.tipo_nomina == 'Por hora'

    def test_coordinador_actualiza_campos_medicos(
        self, client, coord, trab_a, proyecto_coord, db,
    ):
        r = client.put(f'/api/trabajadores/{trab_a.id}', headers=_hdr(coord), data={
            'tipo_sangre': 'A+',
            'alergias': 'Penicilina',
            'celular': '5544332211',
        })
        assert r.status_code == 200, r.get_json()
        db.session.refresh(trab_a)
        assert trab_a.tipo_sangre == 'A+'
        assert trab_a.alergias == 'Penicilina'
        assert trab_a.celular == '5544332211'

    def test_coordinador_no_puede_cambiar_salario(
        self, client, coord, trab_a, proyecto_coord, db,
    ):
        salario_original = float(trab_a.salario_real_pactado_x_sem)
        r = client.put(f'/api/trabajadores/{trab_a.id}', headers=_hdr(coord), data={
            'salario_real_pactado_x_sem': '9999',
            'tipo_sangre': 'B-',  # un campo permitido también
        })
        assert r.status_code == 200
        # Hay warning explicando que el campo se ignoró
        body = r.get_json()
        assert any('admin' in w.lower() for w in body.get('warnings', []))
        # Y el salario NO cambió
        db.session.refresh(trab_a)
        assert float(trab_a.salario_real_pactado_x_sem) == salario_original
        # El campo permitido sí se aplicó
        assert trab_a.tipo_sangre == 'B-'

    def test_coordinador_no_puede_cambiar_no_empleado_403(
        self, client, coord, trab_a, proyecto_coord,
    ):
        r = client.put(f'/api/trabajadores/{trab_a.id}', headers=_hdr(coord), data={
            'no_empleado': 'HACKEADO',
        })
        assert r.status_code == 403

    def test_coordinador_sin_proyecto_403(self, client, coord, trab_a):
        # coord SIN proyecto_coord → no autorizado
        r = client.put(f'/api/trabajadores/{trab_a.id}', headers=_hdr(coord), data={
            'tipo_sangre': 'A+',
        })
        assert r.status_code == 403

    def test_inexistente_404(self, client, admin):
        r = client.put('/api/trabajadores/99999', headers=_hdr(admin), data={
            'nombre': 'X',
        })
        assert r.status_code == 404

    def test_cambiar_no_empleado_a_uno_duplicado_409(
        self, client, admin, trab_a, trab_b,
    ):
        r = client.put(f'/api/trabajadores/{trab_a.id}', headers=_hdr(admin), data={
            'no_empleado': trab_b.no_empleado,
        })
        assert r.status_code == 409


# ═══════════════════════════════════════════════════════════════════════════════
# 7. DAR BAJA / REACTIVAR
# ═══════════════════════════════════════════════════════════════════════════════

class TestBajaYReactivar:

    def test_admin_da_baja(self, client, admin, trab_a, db):
        r = client.delete(f'/api/trabajadores/{trab_a.id}', headers=_hdr(admin))
        assert r.status_code == 200
        db.session.refresh(trab_a)
        assert trab_a.activo is False
        assert trab_a.fecha_baja == date.today()

    def test_coordinador_no_puede_dar_baja_403(
        self, client, coord, trab_a, proyecto_coord,
    ):
        r = client.delete(f'/api/trabajadores/{trab_a.id}', headers=_hdr(coord))
        assert r.status_code == 403

    def test_dar_baja_inexistente_404(self, client, admin):
        r = client.delete('/api/trabajadores/99999', headers=_hdr(admin))
        assert r.status_code == 404

    def test_admin_reactiva(self, client, admin, trab_baja, db):
        r = client.post(
            f'/api/trabajadores/{trab_baja.id}/reactivar',
            headers=_hdr(admin),
        )
        assert r.status_code == 200
        db.session.refresh(trab_baja)
        assert trab_baja.activo is True
        assert trab_baja.fecha_baja is None

    def test_coordinador_no_puede_reactivar_403(self, client, coord, trab_baja):
        r = client.post(
            f'/api/trabajadores/{trab_baja.id}/reactivar',
            headers=_hdr(coord),
        )
        assert r.status_code == 403


# ═══════════════════════════════════════════════════════════════════════════════
# 8. BULK
# ═══════════════════════════════════════════════════════════════════════════════

class TestBulk:

    def test_bulk_baja_admin(self, client, admin, trab_a, trab_b, db):
        r = client.post('/api/trabajadores/bulk', headers=_hdr(admin), json={
            'action': 'baja',
            'ids': [trab_a.id, trab_b.id],
        })
        assert r.status_code == 200, r.get_json()
        body = r.get_json()
        assert body['affected'] == 2
        assert set(body['ids']) == {trab_a.id, trab_b.id}
        db.session.refresh(trab_a); db.session.refresh(trab_b)
        assert trab_a.activo is False and trab_b.activo is False

    def test_bulk_reactivar_admin(self, client, admin, trab_baja, db):
        r = client.post('/api/trabajadores/bulk', headers=_hdr(admin), json={
            'action': 'reactivar',
            'ids': [trab_baja.id],
        })
        assert r.status_code == 200
        db.session.refresh(trab_baja)
        assert trab_baja.activo is True

    def test_bulk_marca_skipped_si_ya_inactivo(
        self, client, admin, trab_a, trab_baja,
    ):
        r = client.post('/api/trabajadores/bulk', headers=_hdr(admin), json={
            'action': 'baja',
            'ids': [trab_a.id, trab_baja.id],   # trab_baja ya está inactivo
        })
        body = r.get_json()
        assert body['affected'] == 1
        assert body['ids'] == [trab_a.id]
        razones = {s['reason'] for s in body['skipped'] if s['id'] == trab_baja.id}
        assert 'ya_inactivo' in razones

    def test_bulk_id_inexistente_marca_skipped(self, client, admin, trab_a):
        r = client.post('/api/trabajadores/bulk', headers=_hdr(admin), json={
            'action': 'baja',
            'ids': [trab_a.id, 99999],
        })
        body = r.get_json()
        assert body['affected'] == 1
        skipped_ids = {s['id'] for s in body['skipped']}
        assert 99999 in skipped_ids

    def test_bulk_accion_invalida_422(self, client, admin, trab_a):
        r = client.post('/api/trabajadores/bulk', headers=_hdr(admin), json={
            'action': 'eliminar',  # no permitido
            'ids': [trab_a.id],
        })
        assert r.status_code == 422

    def test_bulk_ids_vacios_422(self, client, admin):
        r = client.post('/api/trabajadores/bulk', headers=_hdr(admin), json={
            'action': 'baja', 'ids': [],
        })
        assert r.status_code == 422

    def test_bulk_mas_de_100_ids_422(self, client, admin):
        r = client.post('/api/trabajadores/bulk', headers=_hdr(admin), json={
            'action': 'baja', 'ids': list(range(101)),
        })
        assert r.status_code == 422

    def test_bulk_coordinador_403(self, client, coord, trab_a):
        r = client.post('/api/trabajadores/bulk', headers=_hdr(coord), json={
            'action': 'baja', 'ids': [trab_a.id],
        })
        assert r.status_code == 403
