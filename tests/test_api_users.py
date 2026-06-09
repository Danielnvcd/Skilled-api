"""Tests del API JWT `/api/users/*` — administración de usuarios.

Cobertura:
  - GET    /                       listar (orden: super_admin > admin > coord …)
  - POST   /                       crear (admin)
  - PUT    /<id>                   actualizar perfil + trabajador_id link
  - DELETE /<id>                   eliminar (con candados)
  - DELETE /<id>/sessions          forzar logout (revoca RTs + ++password_version)
  - POST   /<id>/password          reset de contraseña por admin

Reglas no obvias que cubrimos:
  - Solo admin/super_admin entran (`require_admin`).
  - Usuario literal "admin" NO puede ser eliminado ni que un tercero le
    cambie la contraseña.
  - Un admin NO puede eliminar/revocar-sesiones/resetear-password de OTRO
    admin: eso es privilegio de super_admin (anti-escalación lateral).
  - Auto-DELETE bloqueado (no puedes eliminar tu propia cuenta).
  - Liga `trabajador_id` es 1:1: si otro user ya está ligado al mismo
    Trabajador → 409.
  - Cambio de contraseña / revocar sesiones: ambos incrementan
    `password_version`, lo que invalida JWT vivos al instante.
  - El campo `role` se ignora silenciosamente en PUT (no se debe cambiar
    en caliente).

NO se prueban aquí: `/foto` (requiere FileStorage real).
"""
import pytest
from werkzeug.security import check_password_hash, generate_password_hash

from app.models import RefreshToken, Trabajador, User
from app.routes.api_auth import _encode_access_token


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _hdr(user):
    return {'Authorization': f'Bearer {_encode_access_token(user)}'}


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def admin(db):
    u = User(username='usr_admin', password_hash=generate_password_hash('Pass123!'),
              role='admin', password_version=1)
    db.session.add(u); db.session.commit()
    return u


@pytest.fixture
def super_admin(db):
    u = User(username='usr_super', password_hash=generate_password_hash('Pass123!'),
              role='super_admin', password_version=1)
    db.session.add(u); db.session.commit()
    return u


@pytest.fixture
def admin_literal(db):
    """Usuario cuyo username es exactamente "admin" — protegido contra
    eliminación y reset de contraseña por terceros."""
    u = User(username='admin', password_hash=generate_password_hash('Pass123!'),
              role='admin', password_version=1)
    db.session.add(u); db.session.commit()
    return u


@pytest.fixture
def coord(db):
    u = User(username='usr_coord', password_hash=generate_password_hash('Pass123!'),
              role='coordinador', password_version=1)
    db.session.add(u); db.session.commit()
    return u


@pytest.fixture
def otro_admin(db):
    u = User(username='otro_admin', password_hash=generate_password_hash('Pass123!'),
              role='admin', password_version=1)
    db.session.add(u); db.session.commit()
    return u


@pytest.fixture
def trab(db):
    t = Trabajador(no_empleado='EMP-U1', nombre='Diana', nombre_apellidos='Salinas',
                    activo=True, tipo_nomina='Semanal',
                    salario_real_pactado_x_sem=5000)
    db.session.add(t); db.session.commit()
    return t


# ═══════════════════════════════════════════════════════════════════════════════
# 1. AUTH / AUTHZ
# ═══════════════════════════════════════════════════════════════════════════════

class TestAuth:

    def test_sin_token_401(self, client):
        r = client.get('/api/users')
        assert r.status_code == 401

    def test_coordinador_403(self, client, coord):
        r = client.get('/api/users', headers=_hdr(coord))
        assert r.status_code == 403

    def test_admin_200(self, client, admin):
        r = client.get('/api/users', headers=_hdr(admin))
        assert r.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════════
# 2. LISTAR
# ═══════════════════════════════════════════════════════════════════════════════

class TestListar:

    def test_listar_orden_por_rol(self, client, admin, super_admin, coord):
        # Crear también un usuario con rol inventario para validar orden completo
        from werkzeug.security import generate_password_hash as gph
        from app.extensions import db as _db
        inv = User(username='usr_inv', password_hash=gph('Pass123!'),
                   role='inventario', password_version=1)
        _db.session.add(inv); _db.session.commit()

        r = client.get('/api/users', headers=_hdr(admin))
        assert r.status_code == 200
        roles = [u['role'] for u in r.get_json()]
        # super_admin antes que admin, admin antes que coordinador,
        # coordinador antes que inventario
        idx_super = roles.index('super_admin')
        idx_admin = roles.index('admin')
        idx_coord = roles.index('coordinador')
        idx_inv = roles.index('inventario')
        assert idx_super < idx_admin < idx_coord < idx_inv


# ═══════════════════════════════════════════════════════════════════════════════
# 3. CREAR
# ═══════════════════════════════════════════════════════════════════════════════

class TestCrear:

    def test_admin_crea_usuario(self, client, admin, db):
        r = client.post('/api/users', headers=_hdr(admin), json={
            'username': 'nuevo_coord',
            'password': 'StrongPass1!',
            'role': 'coordinador',
        })
        assert r.status_code == 201, r.get_json()
        body = r.get_json()
        assert body['username'] == 'nuevo_coord'
        assert body['role'] == 'coordinador'
        u = User.query.filter_by(username='nuevo_coord').first()
        assert u is not None
        assert check_password_hash(u.password_hash, 'StrongPass1!')

    def test_coordinador_403(self, client, coord):
        r = client.post('/api/users', headers=_hdr(coord), json={
            'username': 'x', 'password': 'StrongPass1!', 'role': 'coordinador',
        })
        assert r.status_code == 403

    def test_campos_faltantes_400(self, client, admin):
        r = client.post('/api/users', headers=_hdr(admin), json={
            'username': 'incompleto',
        })
        assert r.status_code == 400

    def test_rol_no_valido_400(self, client, admin):
        r = client.post('/api/users', headers=_hdr(admin), json={
            'username': 'x', 'password': 'StrongPass1!', 'role': 'hacker',
        })
        assert r.status_code == 400

    def test_no_se_puede_crear_super_admin_400(self, client, admin):
        # super_admin no está en _VALID_NEW_ROLES — solo se crea por seeding
        r = client.post('/api/users', headers=_hdr(admin), json={
            'username': 'pretender', 'password': 'StrongPass1!',
            'role': 'super_admin',
        })
        assert r.status_code == 400

    def test_username_duplicado_409(self, client, admin):
        r = client.post('/api/users', headers=_hdr(admin), json={
            'username': admin.username,
            'password': 'StrongPass1!', 'role': 'coordinador',
        })
        assert r.status_code == 409

    def test_password_debil_400(self, client, admin):
        r = client.post('/api/users', headers=_hdr(admin), json={
            'username': 'tester', 'password': '12345',  # sin mayúsculas, etc.
            'role': 'coordinador',
        })
        assert r.status_code == 400


# ═══════════════════════════════════════════════════════════════════════════════
# 4. ACTUALIZAR
# ═══════════════════════════════════════════════════════════════════════════════

class TestActualizar:

    def test_actualizar_perfil(self, client, admin, coord, db):
        r = client.put(f'/api/users/{coord.id}', headers=_hdr(admin), json={
            'full_name': 'Carmen Coordinadora',
            'area': 'Construcción',
            'position': 'Coordinadora',
            'factory': 'Planta 2',
            'contact_info': 'ext 123',
        })
        assert r.status_code == 200, r.get_json()
        db.session.refresh(coord)
        assert coord.full_name == 'Carmen Coordinadora'
        assert coord.area == 'Construcción'

    def test_actualizar_inexistente_404(self, client, admin):
        r = client.put('/api/users/99999', headers=_hdr(admin), json={'full_name': 'X'})
        assert r.status_code == 404

    def test_role_no_se_puede_cambiar_via_put(self, client, admin, coord, db):
        # El PUT acepta el payload pero ignora `role` silenciosamente
        r = client.put(f'/api/users/{coord.id}', headers=_hdr(admin), json={
            'role': 'admin',  # ← intento de escalación
            'full_name': 'Sigue coord',
        })
        assert r.status_code == 200
        db.session.refresh(coord)
        assert coord.role == 'coordinador'  # no cambió
        assert coord.full_name == 'Sigue coord'  # los otros campos sí

    def test_link_trabajador_ok(self, client, admin, coord, trab, db):
        r = client.put(f'/api/users/{coord.id}', headers=_hdr(admin), json={
            'trabajador_id': trab.id,
        })
        assert r.status_code == 200
        body = r.get_json()
        assert body['trabajador_id'] == trab.id
        assert body['trabajador_no_empleado'] == 'EMP-U1'

    def test_desvincula_trabajador_con_null(self, client, admin, coord, trab, db):
        coord.trabajador_id = trab.id
        db.session.commit()
        r = client.put(f'/api/users/{coord.id}', headers=_hdr(admin), json={
            'trabajador_id': None,
        })
        assert r.status_code == 200
        assert r.get_json()['trabajador_id'] is None

    def test_link_trabajador_inexistente_404(self, client, admin, coord):
        r = client.put(f'/api/users/{coord.id}', headers=_hdr(admin), json={
            'trabajador_id': 99999,
        })
        assert r.status_code == 404

    def test_link_trabajador_no_numerico_400(self, client, admin, coord):
        r = client.put(f'/api/users/{coord.id}', headers=_hdr(admin), json={
            'trabajador_id': 'abc',
        })
        assert r.status_code == 400

    def test_link_trabajador_ya_ligado_a_otro_user_409(
        self, client, admin, coord, otro_admin, trab, db,
    ):
        otro_admin.trabajador_id = trab.id
        db.session.commit()
        r = client.put(f'/api/users/{coord.id}', headers=_hdr(admin), json={
            'trabajador_id': trab.id,
        })
        assert r.status_code == 409


# ═══════════════════════════════════════════════════════════════════════════════
# 5. ELIMINAR
# ═══════════════════════════════════════════════════════════════════════════════

class TestEliminar:

    def test_eliminar_usuario_coord(self, client, admin, coord, db):
        r = client.delete(f'/api/users/{coord.id}', headers=_hdr(admin))
        assert r.status_code == 200
        assert r.get_json()['ok'] is True
        assert User.query.get(coord.id) is None

    def test_no_eliminar_propia_cuenta_400(self, client, admin):
        r = client.delete(f'/api/users/{admin.id}', headers=_hdr(admin))
        assert r.status_code == 400
        assert User.query.get(admin.id) is not None

    def test_no_eliminar_usuario_admin_literal_400(
        self, client, admin, admin_literal,
    ):
        r = client.delete(f'/api/users/{admin_literal.id}', headers=_hdr(admin))
        assert r.status_code == 400

    def test_admin_no_puede_eliminar_super_admin_403(
        self, client, admin, super_admin,
    ):
        r = client.delete(f'/api/users/{super_admin.id}', headers=_hdr(admin))
        assert r.status_code == 403

    def test_super_admin_si_puede_eliminar_super_admin(
        self, client, super_admin, db,
    ):
        # Creamos un segundo super_admin para que pueda borrar al primero
        otro = User(username='otro_super', password_hash=generate_password_hash('Pass123!'),
                     role='super_admin', password_version=1)
        db.session.add(otro); db.session.commit()
        r = client.delete(f'/api/users/{otro.id}', headers=_hdr(super_admin))
        assert r.status_code == 200
        assert User.query.get(otro.id) is None

    def test_eliminar_inexistente_404(self, client, admin):
        r = client.delete('/api/users/99999', headers=_hdr(admin))
        assert r.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# 6. REVOCAR SESIONES
# ═══════════════════════════════════════════════════════════════════════════════

class TestRevocarSesiones:

    def _rt(self, db, user):
        # token_hash tiene UNIQUE — usamos uuid para no colisionar entre
        # dos llamadas consecutivas dentro del mismo test (el timestamp
        # antes generaba colisiones en ejecuciones <1ms).
        import uuid as _uuid
        from datetime import datetime, timedelta, timezone
        t = RefreshToken(
            token_hash=_uuid.uuid4().hex,
            user_id=user.id,
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
            revoked=False,
        )
        db.session.add(t); db.session.commit()
        return t

    def test_revocar_sesiones_de_coord(self, client, admin, coord, db):
        self._rt(db, coord); self._rt(db, coord)
        pv_antes = coord.password_version
        r = client.delete(f'/api/users/{coord.id}/sessions', headers=_hdr(admin))
        assert r.status_code == 200
        body = r.get_json()
        assert body['ok'] is True
        assert body['revocadas'] == 2
        # password_version se incrementa → invalida JWT vivos
        db.session.refresh(coord)
        assert coord.password_version == pv_antes + 1
        # Todos los RT del user quedan revoked=True
        activos = RefreshToken.query.filter_by(user_id=coord.id, revoked=False).count()
        assert activos == 0

    def test_admin_no_puede_revocar_sesiones_de_otro_admin_403(
        self, client, admin, otro_admin,
    ):
        r = client.delete(
            f'/api/users/{otro_admin.id}/sessions',
            headers=_hdr(admin),
        )
        assert r.status_code == 403

    def test_super_admin_si_puede_revocar_sesiones_de_admin(
        self, client, super_admin, admin, db,
    ):
        self._rt(db, admin)
        r = client.delete(
            f'/api/users/{admin.id}/sessions',
            headers=_hdr(super_admin),
        )
        assert r.status_code == 200

    def test_admin_puede_revocar_sus_propias_sesiones(
        self, client, admin, db,
    ):
        self._rt(db, admin)
        r = client.delete(
            f'/api/users/{admin.id}/sessions',
            headers=_hdr(admin),
        )
        assert r.status_code == 200

    def test_revocar_inexistente_404(self, client, admin):
        r = client.delete('/api/users/99999/sessions', headers=_hdr(admin))
        assert r.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# 7. CAMBIAR PASSWORD
# ═══════════════════════════════════════════════════════════════════════════════

class TestCambiarPassword:

    def test_admin_resetea_password_de_coord(self, client, admin, coord, db):
        pv_antes = coord.password_version
        r = client.post(f'/api/users/{coord.id}/password', headers=_hdr(admin), json={
            'new_password': 'NuevaPass1!',
        })
        assert r.status_code == 200
        db.session.refresh(coord)
        assert check_password_hash(coord.password_hash, 'NuevaPass1!')
        # password_version incrementa → invalida JWT vivos
        assert coord.password_version == pv_antes + 1

    def test_acepta_alias_newPassword_camelCase(self, client, admin, coord, db):
        r = client.post(f'/api/users/{coord.id}/password', headers=_hdr(admin), json={
            'newPassword': 'OtraPass1!',
        })
        assert r.status_code == 200
        db.session.refresh(coord)
        assert check_password_hash(coord.password_hash, 'OtraPass1!')

    def test_password_vacia_400(self, client, admin, coord):
        r = client.post(f'/api/users/{coord.id}/password', headers=_hdr(admin), json={
            'new_password': '',
        })
        assert r.status_code == 400

    def test_password_debil_400(self, client, admin, coord):
        r = client.post(f'/api/users/{coord.id}/password', headers=_hdr(admin), json={
            'new_password': 'abc',
        })
        assert r.status_code == 400

    def test_admin_no_puede_resetear_password_de_admin_literal_403(
        self, client, admin, admin_literal,
    ):
        r = client.post(
            f'/api/users/{admin_literal.id}/password',
            headers=_hdr(admin),
            json={'new_password': 'StrongPass1!'},
        )
        assert r.status_code == 403

    def test_admin_no_puede_resetear_password_de_otro_admin_403(
        self, client, admin, otro_admin,
    ):
        r = client.post(
            f'/api/users/{otro_admin.id}/password',
            headers=_hdr(admin),
            json={'new_password': 'StrongPass1!'},
        )
        assert r.status_code == 403

    def test_super_admin_si_puede_resetear_password_de_admin(
        self, client, super_admin, admin,
    ):
        r = client.post(
            f'/api/users/{admin.id}/password',
            headers=_hdr(super_admin),
            json={'new_password': 'StrongPass1!'},
        )
        assert r.status_code == 200

    def test_admin_puede_cambiar_su_propia_password(self, client, admin):
        r = client.post(
            f'/api/users/{admin.id}/password',
            headers=_hdr(admin),
            json={'new_password': 'MiNueva1!'},
        )
        assert r.status_code == 200

    def test_password_de_inexistente_404(self, client, admin):
        r = client.post('/api/users/99999/password', headers=_hdr(admin), json={
            'new_password': 'StrongPass1!',
        })
        assert r.status_code == 404
