"""Tests del API JWT `/api/users/*` — administración de usuarios.

El actor de estos tests es el fixture `gestor`, con rol `sistemas`. La gestión
de cuentas salió de `admin` (que quedó para RRHH) cuando se separaron los dos
ejes de permiso. Que un admin de RRHH ya NO pueda entrar aquí se prueba en
tests/test_api_sistemas.py::TestGestionUsuariosMovida.

Cobertura:
  - GET    /                       listar (orden: super_admin > sistemas > coord …)
  - POST   /                       crear (gestor)
  - PUT    /<id>                   actualizar perfil + trabajador_id link
  - DELETE /<id>                   eliminar (con candados)
  - DELETE /<id>/sessions          forzar logout (revoca RTs + ++password_version)
  - POST   /<id>/password          reset de contraseña por gestor

Reglas no obvias que cubrimos:
  - Solo sistemas/super_admin entran (`require_gestion_usuarios`).
  - Usuario literal "admin" NO puede ser eliminado ni que un tercero le
    cambie la contraseña.
  - `sistemas` SÍ puede revocar sesiones y resetear la contraseña de un admin
    (contener una cuenta comprometida es para lo que existe el rol), pero NO
    puede tocar una cuenta `super_admin` ni crear una: esa es la última línea
    de recuperación y debe quedar alguien por encima.
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
from app.extensions import db as flask_db


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _hdr(user):
    return {'Authorization': f'Bearer {_encode_access_token(user)}'}


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def gestor(db):
    """El actor que administra cuentas.

    Era `admin` hasta que los dos ejes de permiso se separaron: `admin` quedó
    para RRHH (nómina, empleados) y la gestión de cuentas se movió al rol
    `sistemas`. Que un admin de RRHH YA NO pueda entrar aquí se prueba en
    tests/test_api_sistemas.py::TestGestionUsuariosMovida.
    """
    u = User(username='usr_sistemas', password_hash=generate_password_hash('Pass123!'),
             role='sistemas', password_version=1)
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
    eliminación y reset de contraseña por terceros, sea cual sea su rol."""
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

    def test_admin_200(self, client, gestor):
        r = client.get('/api/users', headers=_hdr(gestor))
        assert r.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════════
# 2. LISTAR
# ═══════════════════════════════════════════════════════════════════════════════

class TestListar:

    def test_listar_orden_por_rol(self, client, gestor, super_admin, coord):
        # Crear también un usuario con rol inventario para validar orden completo
        from werkzeug.security import generate_password_hash as gph
        from app.extensions import db as _db
        inv = User(username='usr_inv', password_hash=gph('Pass123!'),
                   role='inventario', password_version=1)
        _db.session.add(inv); _db.session.commit()

        r = client.get('/api/users', headers=_hdr(gestor))
        assert r.status_code == 200
        roles = [u['role'] for u in r.get_json()]
        # Orden por privilegio: super_admin > sistemas > coordinador > inventario.
        # `sistemas` se insertó justo debajo de super_admin porque administra
        # las cuentas de todos los demás.
        idx_super = roles.index('super_admin')
        idx_sistemas = roles.index('sistemas')
        idx_coord = roles.index('coordinador')
        idx_inv = roles.index('inventario')
        assert idx_super < idx_sistemas < idx_coord < idx_inv


# ═══════════════════════════════════════════════════════════════════════════════
# 3. CREAR
# ═══════════════════════════════════════════════════════════════════════════════

class TestCrear:

    def test_admin_crea_usuario(self, client, gestor, db):
        r = client.post('/api/users', headers=_hdr(gestor), json={
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

    def test_campos_faltantes_400(self, client, gestor):
        r = client.post('/api/users', headers=_hdr(gestor), json={
            'username': 'incompleto',
        })
        assert r.status_code == 400

    def test_rol_no_valido_400(self, client, gestor):
        r = client.post('/api/users', headers=_hdr(gestor), json={
            'username': 'x', 'password': 'StrongPass1!', 'role': 'hacker',
        })
        assert r.status_code == 400

    def test_no_se_puede_crear_super_admin_403(self, client, gestor):
        """super_admin es la cuenta de recuperación: no se crea desde la API.

        Si `sistemas` pudiera fabricarse una, no quedaría ningún control por
        encima suyo. Responde 403 (no 400) porque no es un dato mal formado
        sino una operación prohibida para este rol.
        """
        r = client.post('/api/users', headers=_hdr(gestor), json={
            'username': 'pretender', 'password': 'StrongPass1234!',
            'role': 'super_admin',
        })
        assert r.status_code == 403
        assert User.query.filter_by(username='pretender').first() is None

    def test_username_duplicado_409(self, client, gestor):
        r = client.post('/api/users', headers=_hdr(gestor), json={
            'username': gestor.username,
            'password': 'StrongPass1!', 'role': 'coordinador',
        })
        assert r.status_code == 409

    def test_password_debil_400(self, client, gestor):
        r = client.post('/api/users', headers=_hdr(gestor), json={
            'username': 'tester', 'password': '12345',  # sin mayúsculas, etc.
            'role': 'coordinador',
        })
        assert r.status_code == 400


# ═══════════════════════════════════════════════════════════════════════════════
# 4. ACTUALIZAR
# ═══════════════════════════════════════════════════════════════════════════════

class TestActualizar:

    def test_actualizar_perfil(self, client, gestor, coord, db):
        r = client.put(f'/api/users/{coord.id}', headers=_hdr(gestor), json={
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

    def test_actualizar_inexistente_404(self, client, gestor):
        r = client.put('/api/users/99999', headers=_hdr(gestor), json={'full_name': 'X'})
        assert r.status_code == 404

    def test_role_no_se_puede_cambiar_via_put(self, client, gestor, coord, db):
        # El PUT acepta el payload pero ignora `role` silenciosamente
        r = client.put(f'/api/users/{coord.id}', headers=_hdr(gestor), json={
            'role': 'gestor',  # ← intento de escalación
            'full_name': 'Sigue coord',
        })
        assert r.status_code == 200
        db.session.refresh(coord)
        assert coord.role == 'coordinador'  # no cambió
        assert coord.full_name == 'Sigue coord'  # los otros campos sí

    def test_link_trabajador_ok(self, client, gestor, coord, trab, db):
        r = client.put(f'/api/users/{coord.id}', headers=_hdr(gestor), json={
            'trabajador_id': trab.id,
        })
        assert r.status_code == 200
        body = r.get_json()
        assert body['trabajador_id'] == trab.id
        assert body['trabajador_no_empleado'] == 'EMP-U1'

    def test_desvincula_trabajador_con_null(self, client, gestor, coord, trab, db):
        coord.trabajador_id = trab.id
        db.session.commit()
        r = client.put(f'/api/users/{coord.id}', headers=_hdr(gestor), json={
            'trabajador_id': None,
        })
        assert r.status_code == 200
        assert r.get_json()['trabajador_id'] is None

    def test_link_trabajador_inexistente_404(self, client, gestor, coord):
        r = client.put(f'/api/users/{coord.id}', headers=_hdr(gestor), json={
            'trabajador_id': 99999,
        })
        assert r.status_code == 404

    def test_link_trabajador_no_numerico_400(self, client, gestor, coord):
        r = client.put(f'/api/users/{coord.id}', headers=_hdr(gestor), json={
            'trabajador_id': 'abc',
        })
        assert r.status_code == 400

    def test_link_trabajador_ya_ligado_a_otro_user_409(
        self, client, gestor, coord, otro_admin, trab, db,
    ):
        otro_admin.trabajador_id = trab.id
        db.session.commit()
        r = client.put(f'/api/users/{coord.id}', headers=_hdr(gestor), json={
            'trabajador_id': trab.id,
        })
        assert r.status_code == 409


# ═══════════════════════════════════════════════════════════════════════════════
# 5. ELIMINAR
# ═══════════════════════════════════════════════════════════════════════════════

class TestEliminar:

    def test_eliminar_usuario_coord(self, client, gestor, coord, db):
        r = client.delete(f'/api/users/{coord.id}', headers=_hdr(gestor))
        assert r.status_code == 200
        assert r.get_json()['ok'] is True
        # Borrado lógico: el usuario NO se borra físicamente (tiene FKs en otras
        # tablas); se desactiva para conservar su historial.
        eliminado = flask_db.session.get(User, coord.id)
        assert eliminado is not None
        assert eliminado.activo is False

    def test_no_eliminar_propia_cuenta_400(self, client, gestor):
        r = client.delete(f'/api/users/{gestor.id}', headers=_hdr(gestor))
        assert r.status_code == 400
        assert flask_db.session.get(User, gestor.id) is not None

    def test_no_eliminar_usuario_admin_literal_400(
        self, client, gestor, admin_literal,
    ):
        r = client.delete(f'/api/users/{admin_literal.id}', headers=_hdr(gestor))
        assert r.status_code == 400

    def test_admin_no_puede_eliminar_super_admin_403(
        self, client, gestor, super_admin,
    ):
        r = client.delete(f'/api/users/{super_admin.id}', headers=_hdr(gestor))
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
        desactivado = flask_db.session.get(User, otro.id)
        assert desactivado is not None
        assert desactivado.activo is False

    def test_eliminar_inexistente_404(self, client, gestor):
        r = client.delete('/api/users/99999', headers=_hdr(gestor))
        assert r.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# 5-bis. REACTIVAR (borrado lógico reversible)
# ═══════════════════════════════════════════════════════════════════════════════

class TestReactivar:

    def test_reactivar_usuario_desactivado(self, client, gestor, coord, db):
        # Primero desactivar
        r = client.delete(f'/api/users/{coord.id}', headers=_hdr(gestor))
        assert r.status_code == 200
        assert flask_db.session.get(User, coord.id).activo is False
        # Luego reactivar
        r = client.post(f'/api/users/{coord.id}/reactivar', headers=_hdr(gestor))
        assert r.status_code == 200
        assert r.get_json()['activo'] is True
        assert flask_db.session.get(User, coord.id).activo is True

    def test_reactivar_cuenta_ya_activa_400(self, client, gestor, coord):
        r = client.post(f'/api/users/{coord.id}/reactivar', headers=_hdr(gestor))
        assert r.status_code == 400

    def test_reactivar_requiere_admin_403(self, client, coord, db):
        # Un coordinador (no gestor) no puede reactivar cuentas: require_admin → 403.
        inactivo = User(username='coord2', password_hash=generate_password_hash('Pass123!'),
                        role='coordinador', password_version=1, activo=False)
        db.session.add(inactivo); db.session.commit()
        r = client.post(f'/api/users/{inactivo.id}/reactivar', headers=_hdr(coord))
        assert r.status_code == 403


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

    def test_revocar_sesiones_de_coord(self, client, gestor, coord, db):
        self._rt(db, coord); self._rt(db, coord)
        pv_antes = coord.password_version
        r = client.delete(f'/api/users/{coord.id}/sessions', headers=_hdr(gestor))
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

    def test_sistemas_si_puede_revocar_sesiones_de_un_admin(
        self, client, gestor, otro_admin,
    ):
        """Cambio de contrato respecto al modelo anterior.

        Antes un admin no podía tocar a otro admin (anti-escalación lateral
        entre pares). Ahora quien administra cuentas es `sistemas`, un rol
        distinto, y poder desconectar a un admin comprometido es justamente
        para lo que existe: si esto siguiera bloqueado, un incidente con la
        cuenta de RRHH se quedaría sin quién lo contenga.

        Lo que SÍ sigue protegido es `super_admin` — ver el test de abajo.
        """
        r = client.delete(
            f'/api/users/{otro_admin.id}/sessions',
            headers=_hdr(gestor),
        )
        assert r.status_code == 200, r.get_json()

    def test_sistemas_no_puede_revocar_sesiones_de_super_admin_403(
        self, client, gestor, super_admin,
    ):
        r = client.delete(
            f'/api/users/{super_admin.id}/sessions',
            headers=_hdr(gestor),
        )
        assert r.status_code == 403

    def test_super_admin_si_puede_revocar_sesiones_de_admin(
        self, client, super_admin, gestor, db,
    ):
        self._rt(db, gestor)
        r = client.delete(
            f'/api/users/{gestor.id}/sessions',
            headers=_hdr(super_admin),
        )
        assert r.status_code == 200

    def test_admin_puede_revocar_sus_propias_sesiones(
        self, client, gestor, db,
    ):
        self._rt(db, gestor)
        r = client.delete(
            f'/api/users/{gestor.id}/sessions',
            headers=_hdr(gestor),
        )
        assert r.status_code == 200

    def test_revocar_inexistente_404(self, client, gestor):
        r = client.delete('/api/users/99999/sessions', headers=_hdr(gestor))
        assert r.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# 7. CAMBIAR PASSWORD
# ═══════════════════════════════════════════════════════════════════════════════

class TestCambiarPassword:

    def test_admin_resetea_password_de_coord(self, client, gestor, coord, db):
        pv_antes = coord.password_version
        r = client.post(f'/api/users/{coord.id}/password', headers=_hdr(gestor), json={
            'new_password': 'NuevaPass123!',
        })
        assert r.status_code == 200
        db.session.refresh(coord)
        assert check_password_hash(coord.password_hash, 'NuevaPass123!')
        # password_version incrementa → invalida JWT vivos
        assert coord.password_version == pv_antes + 1

    def test_acepta_alias_newPassword_camelCase(self, client, gestor, coord, db):
        r = client.post(f'/api/users/{coord.id}/password', headers=_hdr(gestor), json={
            'newPassword': 'OtraPass1234!',
        })
        assert r.status_code == 200
        db.session.refresh(coord)
        assert check_password_hash(coord.password_hash, 'OtraPass1234!')

    def test_password_vacia_400(self, client, gestor, coord):
        r = client.post(f'/api/users/{coord.id}/password', headers=_hdr(gestor), json={
            'new_password': '',
        })
        assert r.status_code == 400

    def test_password_debil_400(self, client, gestor, coord):
        r = client.post(f'/api/users/{coord.id}/password', headers=_hdr(gestor), json={
            'new_password': 'abc',
        })
        assert r.status_code == 400

    def test_admin_no_puede_resetear_password_de_admin_literal_403(
        self, client, gestor, admin_literal,
    ):
        r = client.post(
            f'/api/users/{admin_literal.id}/password',
            headers=_hdr(gestor),
            json={'new_password': 'StrongPass1!'},
        )
        assert r.status_code == 403

    def test_sistemas_si_puede_resetear_password_de_un_admin(
        self, client, gestor, otro_admin,
    ):
        """Igual que con las sesiones: resetear la contraseña de un usuario es
        la operación de soporte más común y ahora le corresponde a `sistemas`.

        No es una vía de escalación: el reseteo NO borra el `totp_secret` del
        usuario (ver el comentario en api_users/seguridad.py), así que no
        alcanza para entrar a una cuenta ajena que tenga 2FA.
        """
        r = client.post(
            f'/api/users/{otro_admin.id}/password',
            headers=_hdr(gestor),
            json={'new_password': 'StrongPass1234!'},
        )
        assert r.status_code == 200, r.get_json()

    def test_sistemas_no_puede_resetear_password_de_super_admin_403(
        self, client, gestor, super_admin,
    ):
        r = client.post(
            f'/api/users/{super_admin.id}/password',
            headers=_hdr(gestor),
            json={'new_password': 'StrongPass1234!'},
        )
        assert r.status_code == 403

    def test_super_admin_si_puede_resetear_password_de_admin(
        self, client, super_admin, gestor,
    ):
        r = client.post(
            f'/api/users/{gestor.id}/password',
            headers=_hdr(super_admin),
            json={'new_password': 'StrongPass1!'},
        )
        assert r.status_code == 200

    def test_admin_puede_cambiar_su_propia_password(self, client, gestor):
        r = client.post(
            f'/api/users/{gestor.id}/password',
            headers=_hdr(gestor),
            json={'new_password': 'MiNuevaPass1!'},
        )
        assert r.status_code == 200

    def test_password_de_inexistente_404(self, client, gestor):
        r = client.post('/api/users/99999/password', headers=_hdr(gestor), json={
            'new_password': 'StrongPass1!',
        })
        assert r.status_code == 404
