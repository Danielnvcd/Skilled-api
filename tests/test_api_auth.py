"""Tests del API JWT `/api/auth/*` — login, refresh, logout, perfil, sesiones.

Cobertura:
  - POST   /login                       login básico (con/sin 2FA)
  - POST   /verify-2fa                  segundo factor TOTP
  - POST   /refresh                     rotación de RT (con CSRF gate)
  - POST   /logout                      cierre + cookie cleanup
  - GET    /me                          perfil propio
  - GET    /me/activity                 audit log propio
  - GET    /users                       directorio (HIGH-01: PII por rol)
  - GET    /users/<id>                  detalle (self/admin ve todo)
  - GET    /users/<id>/foto             servir foto (path-traversal guard)
  - POST   /profile                     actualizar perfil + foto
  - DELETE /profile/foto                limpiar foto
  - POST   /change-password/<id>        cambio propio (con TOTP si aplica)
  - GET    /sessions                    listar RTs activos propios
  - DELETE /sessions/<id>               revocar uno
  - DELETE /sessions/all                pánico: revoca todo + ++password_version

NO se prueban aquí (requieren Redis o flujo Fernet completo):
  - lockout escalado por username/IP
  - /setup-2fa, /confirm-2fa, /disable-2fa
  - /backup-codes
  - anti-replay TOTP, race-vs-replay del RT

Reglas no obvias:
  - /refresh y /logout exigen header `X-Requested-With: XMLHttpRequest` —
    bloquea CSRF cuando RT_COOKIE_SAMESITE=None.
  - /change-password requiere TOTP si el usuario tiene `totp_secret`.
  - /users devuelve vista PÚBLICA (sin role/totp/last_seen) si el solicitante
    no es admin — HIGH-01 fix contra enumeración de admins sin 2FA.
"""
from datetime import datetime, timedelta, timezone

import pyotp
import pytest
from werkzeug.security import generate_password_hash

from app.extensions import db as flask_db
from app.models import AuditLog, RefreshToken, User
from app.routes.api_auth import _encode_access_token


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _hdr(user):
    return {'Authorization': f'Bearer {_encode_access_token(user)}'}


def _csrf_hdr(user=None):
    """Headers para endpoints protegidos por cookie (refresh/logout)."""
    h = {'X-Requested-With': 'XMLHttpRequest'}
    if user is not None:
        h['Authorization'] = f'Bearer {_encode_access_token(user)}'
    return h


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def au_admin(db):
    u = User(username='au_admin',
              password_hash=generate_password_hash('SuperPass1!'),
              role='admin', password_version=1)
    db.session.add(u); db.session.commit()
    return u


@pytest.fixture
def au_coord(db):
    u = User(username='au_coord',
              password_hash=generate_password_hash('SuperPass1!'),
              role='coordinador', password_version=1)
    db.session.add(u); db.session.commit()
    return u


@pytest.fixture
def au_otro(db):
    """Usuario "objetivo" — para probar PII por rol y permisos cruzados."""
    u = User(username='au_otro',
              password_hash=generate_password_hash('SuperPass1!'),
              role='inventario', password_version=1)
    db.session.add(u); db.session.commit()
    return u


@pytest.fixture
def au_totp_user(db):
    """Usuario con TOTP activo (secret base32 cifrado por EncryptedString)."""
    secret = pyotp.random_base32()
    u = User(username='au_totp',
              password_hash=generate_password_hash('SuperPass1!'),
              role='admin', password_version=1)
    u.totp_secret = secret
    db.session.add(u); db.session.commit()
    # Stash del secret en el objeto para que los tests lo lean
    u._test_totp_secret = secret
    return u


def _set_rt_cookie(client, raw_value):
    """Coloca la cookie de refresh token en el client. Usa el nombre `rt_api`.

    Werkzeug 2.4+ cambió la firma de `set_cookie`: ya no acepta el dominio
    como primer argumento posicional. Pasamos solo (key, value).
    """
    client.set_cookie('rt_api', raw_value)


def _get_rt_cookie(client):
    """Devuelve el valor de la cookie `rt_api` del client, o None."""
    return client.get_cookie('rt_api')


def _crear_rt(db, user, *, raw=None, revoked=False, expires_in_days=7):
    """Crea un RefreshToken válido y devuelve (instancia, raw_value)."""
    from app.routes.api_auth._core import _hash_token
    import secrets
    if raw is None:
        raw = secrets.token_urlsafe(32)
    t = RefreshToken(
        token_hash=_hash_token(raw),
        user_id=user.id,
        expires_at=datetime.now(timezone.utc) + timedelta(days=expires_in_days),
        revoked=revoked,
    )
    db.session.add(t); db.session.commit()
    return t, raw


# ═══════════════════════════════════════════════════════════════════════════════
# 1. LOGIN
# ═══════════════════════════════════════════════════════════════════════════════

class TestLogin:

    def test_login_exitoso(self, client, au_admin):
        r = client.post('/api/auth/login', json={
            'username': 'au_admin', 'password': 'SuperPass1!',
        })
        assert r.status_code == 200, r.get_json()
        body = r.get_json()
        assert 'token' in body
        assert body['user']['username'] == 'au_admin'
        # Cookie de refresh emitida
        assert _get_rt_cookie(client) is not None

    def test_falta_username_400(self, client, au_admin):
        r = client.post('/api/auth/login', json={'password': 'SuperPass1!'})
        assert r.status_code == 400

    def test_falta_password_400(self, client, au_admin):
        r = client.post('/api/auth/login', json={'username': 'au_admin'})
        assert r.status_code == 400

    def test_password_incorrecta_401(self, client, au_admin):
        r = client.post('/api/auth/login', json={
            'username': 'au_admin', 'password': 'mala',
        })
        assert r.status_code == 401

    def test_usuario_inexistente_401(self, client):
        r = client.post('/api/auth/login', json={
            'username': 'no-existe', 'password': 'SuperPass1!',
        })
        assert r.status_code == 401

    def test_username_demasiado_largo_401(self, client):
        r = client.post('/api/auth/login', json={
            'username': 'x' * 200, 'password': 'SuperPass1!',
        })
        assert r.status_code == 401

    def test_password_demasiado_largo_401(self, client):
        r = client.post('/api/auth/login', json={
            'username': 'au_admin', 'password': 'x' * 1000,
        })
        assert r.status_code == 401

    def test_usuario_con_2fa_pide_stepToken(self, client, au_totp_user):
        r = client.post('/api/auth/login', json={
            'username': 'au_totp', 'password': 'SuperPass1!',
        })
        assert r.status_code == 200
        body = r.get_json()
        assert body.get('requires2fa') is True
        assert 'stepToken' in body
        # NO devuelve token de acceso todavía
        assert 'token' not in body


# ═══════════════════════════════════════════════════════════════════════════════
# 2. VERIFY-2FA
# ═══════════════════════════════════════════════════════════════════════════════

class TestVerify2fa:

    def _step_token(self, client, user):
        r = client.post('/api/auth/login', json={
            'username': user.username, 'password': 'SuperPass1!',
        })
        return r.get_json()['stepToken']

    def test_codigo_correcto_devuelve_jwt(self, client, au_totp_user):
        step = self._step_token(client, au_totp_user)
        code = pyotp.TOTP(au_totp_user._test_totp_secret).now()
        r = client.post('/api/auth/verify-2fa', json={
            'stepToken': step, 'code': code,
        })
        assert r.status_code == 200, r.get_json()
        body = r.get_json()
        assert 'token' in body
        assert body['user']['username'] == 'au_totp'

    def test_codigo_incorrecto_401(self, client, au_totp_user):
        step = self._step_token(client, au_totp_user)
        r = client.post('/api/auth/verify-2fa', json={
            'stepToken': step, 'code': '000000',
        })
        assert r.status_code == 401

    def test_stepToken_invalido_401(self, client, au_totp_user):
        r = client.post('/api/auth/verify-2fa', json={
            'stepToken': 'no-jwt', 'code': '123456',
        })
        assert r.status_code == 401

    def test_codigo_demasiado_largo_401(self, client, au_totp_user):
        step = self._step_token(client, au_totp_user)
        r = client.post('/api/auth/verify-2fa', json={
            'stepToken': step, 'code': 'x' * 100,
        })
        assert r.status_code == 401


# ═══════════════════════════════════════════════════════════════════════════════
# 3. REFRESH
# ═══════════════════════════════════════════════════════════════════════════════

class TestRefresh:

    def test_sin_x_requested_with_403(self, client, au_admin):
        r = client.post('/api/auth/refresh')
        assert r.status_code == 403

    def test_sin_cookie_401(self, client):
        r = client.post('/api/auth/refresh', headers=_csrf_hdr())
        assert r.status_code == 401

    def test_cookie_invalida_401(self, client):
        _set_rt_cookie(client, 'cookie-inventada-que-no-existe')
        r = client.post('/api/auth/refresh', headers=_csrf_hdr())
        assert r.status_code == 401

    def test_cookie_revocada_401(self, client, db, au_admin):
        tok, raw = _crear_rt(db, au_admin, revoked=True)
        _set_rt_cookie(client, raw)
        r = client.post('/api/auth/refresh', headers=_csrf_hdr())
        # Fuera de la ventana de gracia: 401 (replay) — sin Redis, el endpoint
        # asume "race" y también devuelve 401 sin revocar familia. Ambos OK.
        assert r.status_code == 401

    def test_cookie_expirada_401(self, client, db, au_admin):
        tok, raw = _crear_rt(db, au_admin, expires_in_days=-1)
        _set_rt_cookie(client, raw)
        r = client.post('/api/auth/refresh', headers=_csrf_hdr())
        assert r.status_code == 401

    def test_refresh_exitoso_rota_token(self, client, db, au_admin):
        tok, raw = _crear_rt(db, au_admin)
        viejo_id = tok.id
        _set_rt_cookie(client, raw)
        r = client.post('/api/auth/refresh', headers=_csrf_hdr())
        assert r.status_code == 200, r.get_json()
        body = r.get_json()
        assert 'token' in body
        assert body['user']['id'] == au_admin.id
        # El RT viejo desaparece: el endpoint hace housekeeping que borra
        # los `revoked=True` viejos en el mismo commit del rollover.
        # (Si no se borrase, quedaría con `revoked=True` — ambas formas son
        # equivalentes para invalidar la sesión vieja.)
        viejo = RefreshToken.query.get(viejo_id)
        assert viejo is None or viejo.revoked is True
        # Hay un RT nuevo activo del mismo usuario
        nuevos = RefreshToken.query.filter_by(user_id=au_admin.id, revoked=False).all()
        assert len(nuevos) == 1
        assert nuevos[0].id != viejo_id


# ═══════════════════════════════════════════════════════════════════════════════
# 4. LOGOUT
# ═══════════════════════════════════════════════════════════════════════════════

class TestLogout:

    def test_sin_x_requested_with_403(self, client):
        r = client.post('/api/auth/logout')
        assert r.status_code == 403

    def test_logout_revoca_rt_cookie(self, client, db, au_admin):
        tok, raw = _crear_rt(db, au_admin)
        _set_rt_cookie(client, raw)
        r = client.post('/api/auth/logout', headers=_csrf_hdr(au_admin))
        assert r.status_code == 200
        flask_db.session.refresh(tok)
        assert tok.revoked is True

    def test_logout_sin_cookie_es_ok(self, client, au_admin):
        # Logout sin sesión: no falla — solo limpia y responde ok
        r = client.post('/api/auth/logout', headers=_csrf_hdr(au_admin))
        assert r.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════════
# 5. /me y /me/activity
# ═══════════════════════════════════════════════════════════════════════════════

class TestMe:

    def test_sin_token_401(self, client):
        r = client.get('/api/auth/me')
        assert r.status_code == 401

    def test_me_devuelve_datos_propios(self, client, au_admin):
        r = client.get('/api/auth/me', headers=_hdr(au_admin))
        assert r.status_code == 200
        body = r.get_json()
        assert body['id'] == au_admin.id
        assert body['username'] == 'au_admin'
        assert body['role'] == 'admin'

    def test_me_activity_devuelve_audit_log_propio(
        self, client, au_admin, db,
    ):
        # Insertamos algunas filas de audit log a nombre del usuario
        for i in range(3):
            db.session.add(AuditLog(user=au_admin.username, action=f'acc {i}',
                                     ip='127.0.0.1'))
        # Ruido de OTRO usuario, no debe aparecer
        db.session.add(AuditLog(user='alguien_mas', action='OTRA',
                                 ip='127.0.0.1'))
        db.session.commit()

        r = client.get('/api/auth/me/activity', headers=_hdr(au_admin))
        assert r.status_code == 200
        actions = [row['action'] for row in r.get_json()]
        assert all(a.startswith('acc ') for a in actions)
        assert 'OTRA' not in actions

    def test_me_activity_respeta_limit(self, client, au_admin, db):
        for i in range(30):
            db.session.add(AuditLog(user=au_admin.username, action=f'a{i}',
                                     ip='127.0.0.1'))
        db.session.commit()
        r = client.get('/api/auth/me/activity?limit=10', headers=_hdr(au_admin))
        assert len(r.get_json()) == 10


# ═══════════════════════════════════════════════════════════════════════════════
# 6. DIRECTORIO /users
# ═══════════════════════════════════════════════════════════════════════════════

class TestDirectorio:

    def test_listar_admin_ve_role(self, client, au_admin, au_otro):
        r = client.get('/api/auth/users', headers=_hdr(au_admin))
        assert r.status_code == 200
        users = r.get_json()
        # Admin recibe `role`, `totp_enabled`, `last_seen` en el payload
        sample = next(u for u in users if u['id'] == au_admin.id)
        assert 'role' in sample
        assert 'totp_enabled' in sample

    def test_listar_coord_recibe_vista_publica(
        self, client, au_coord, au_admin,
    ):
        r = client.get('/api/auth/users', headers=_hdr(au_coord))
        assert r.status_code == 200
        admin_obj = next(u for u in r.get_json() if u['id'] == au_admin.id)
        # HIGH-01: coord NO debe ver `role`, `totp_enabled`, `last_seen` de admin
        assert 'role' not in admin_obj
        assert 'totp_enabled' not in admin_obj
        assert 'last_seen' not in admin_obj

    def test_get_admin_ve_pii_completa(
        self, client, au_admin, au_otro,
    ):
        r = client.get(f'/api/auth/users/{au_otro.id}', headers=_hdr(au_admin))
        body = r.get_json()
        assert 'role' in body

    def test_get_otro_user_recibe_vista_publica(
        self, client, au_coord, au_otro,
    ):
        # coord viendo el perfil de inv → vista pública
        r = client.get(f'/api/auth/users/{au_otro.id}', headers=_hdr(au_coord))
        body = r.get_json()
        assert 'role' not in body

    def test_get_propio_devuelve_completo(self, client, au_coord):
        # Coord viendo SU PROPIO perfil → ve `role` aunque no sea admin
        r = client.get(f'/api/auth/users/{au_coord.id}', headers=_hdr(au_coord))
        body = r.get_json()
        assert body['role'] == 'coordinador'

    def test_get_inexistente_404(self, client, au_admin):
        r = client.get('/api/auth/users/99999', headers=_hdr(au_admin))
        assert r.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# 7. /users/<id>/foto
# ═══════════════════════════════════════════════════════════════════════════════

class TestUserFoto:

    def test_sin_foto_404(self, client, au_admin, au_otro):
        r = client.get(f'/api/auth/users/{au_otro.id}/foto', headers=_hdr(au_admin))
        assert r.status_code == 404

    def test_foto_default_404(self, client, au_admin, db):
        au_admin.profile_pic = 'default.png'
        db.session.commit()
        r = client.get(f'/api/auth/users/{au_admin.id}/foto', headers=_hdr(au_admin))
        assert r.status_code == 404

    def test_path_traversal_bloqueado_400(self, client, au_admin, db):
        au_admin.profile_pic = '../../etc/passwd'
        db.session.commit()
        r = client.get(f'/api/auth/users/{au_admin.id}/foto', headers=_hdr(au_admin))
        assert r.status_code == 400

    def test_archivo_no_existe_en_disco_404(self, client, au_admin, db):
        au_admin.profile_pic = 'noexiste.webp'
        db.session.commit()
        r = client.get(f'/api/auth/users/{au_admin.id}/foto', headers=_hdr(au_admin))
        assert r.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# 8. /profile (POST y DELETE foto)
# ═══════════════════════════════════════════════════════════════════════════════

class TestPerfilPropio:

    def test_actualiza_campos_snake(self, client, au_admin, db):
        r = client.post('/api/auth/profile', headers=_hdr(au_admin), data={
            'full_name': 'Aurora Admin',
            'area': 'TI',
            'position': 'Gerente',
        })
        assert r.status_code == 200
        db.session.refresh(au_admin)
        assert au_admin.full_name == 'Aurora Admin'
        assert au_admin.area == 'TI'

    def test_actualiza_campos_camel(self, client, au_admin, db):
        r = client.post('/api/auth/profile', headers=_hdr(au_admin), data={
            'fullName': 'Aurora Camel',
            'contactInfo': 'ext 1000',
        })
        assert r.status_code == 200
        db.session.refresh(au_admin)
        assert au_admin.full_name == 'Aurora Camel'
        assert au_admin.contact_info == 'ext 1000'

    def test_delete_foto_pone_null(self, client, au_admin, db):
        au_admin.profile_pic = 'algo.webp'
        db.session.commit()
        r = client.delete('/api/auth/profile/foto', headers=_hdr(au_admin))
        assert r.status_code == 200
        db.session.refresh(au_admin)
        assert au_admin.profile_pic is None


# ═══════════════════════════════════════════════════════════════════════════════
# 9. CHANGE PASSWORD (propio)
# ═══════════════════════════════════════════════════════════════════════════════

class TestChangePassword:

    def test_solo_propio_403(self, client, au_admin, au_otro):
        r = client.post(
            f'/api/auth/change-password/{au_otro.id}',
            headers=_hdr(au_admin),
            json={'current_password': 'X', 'new_password': 'StrongPass1!'},
        )
        assert r.status_code == 403

    def test_falta_campos_400(self, client, au_admin):
        r = client.post(
            f'/api/auth/change-password/{au_admin.id}',
            headers=_hdr(au_admin),
            json={'current_password': 'SuperPass1!'},
        )
        assert r.status_code == 400

    def test_current_password_incorrecta_401(self, client, au_admin):
        r = client.post(
            f'/api/auth/change-password/{au_admin.id}',
            headers=_hdr(au_admin),
            json={
                'current_password': 'mala',
                'new_password': 'OtraStrong1!',
            },
        )
        assert r.status_code == 401

    def test_password_debil_400(self, client, au_admin):
        r = client.post(
            f'/api/auth/change-password/{au_admin.id}',
            headers=_hdr(au_admin),
            json={
                'current_password': 'SuperPass1!',
                'new_password': '12345',
            },
        )
        assert r.status_code == 400

    def test_password_igual_a_actual_400(self, client, au_admin):
        r = client.post(
            f'/api/auth/change-password/{au_admin.id}',
            headers=_hdr(au_admin),
            json={
                'current_password': 'SuperPass1!',
                'new_password': 'SuperPass1!',
            },
        )
        assert r.status_code == 400

    def test_cambio_exitoso(self, client, au_admin, db):
        pv_antes = au_admin.password_version
        r = client.post(
            f'/api/auth/change-password/{au_admin.id}',
            headers=_hdr(au_admin),
            json={
                'current_password': 'SuperPass1!',
                'new_password': 'OtraStrong1!',
            },
        )
        assert r.status_code == 200
        db.session.refresh(au_admin)
        # password_version se incrementa → invalida JWT vivos
        assert au_admin.password_version == pv_antes + 1

    def test_con_2fa_requiere_totp(self, client, au_totp_user):
        r = client.post(
            f'/api/auth/change-password/{au_totp_user.id}',
            headers=_hdr(au_totp_user),
            json={
                'current_password': 'SuperPass1!',
                'new_password': 'OtraStrong1!',
            },
        )
        assert r.status_code == 401
        assert r.get_json().get('requires_totp') is True

    def test_con_2fa_totp_correcto(self, client, au_totp_user, db):
        code = pyotp.TOTP(au_totp_user._test_totp_secret).now()
        r = client.post(
            f'/api/auth/change-password/{au_totp_user.id}',
            headers=_hdr(au_totp_user),
            json={
                'current_password': 'SuperPass1!',
                'new_password': 'OtraStrong1!',
                'code': code,
            },
        )
        assert r.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════════
# 10. SESSIONS
# ═══════════════════════════════════════════════════════════════════════════════

class TestSessions:

    def test_lista_solo_propias_y_activas(
        self, client, au_admin, au_otro, db,
    ):
        # Tres RT del admin: 2 activos, 1 revocado
        _crear_rt(db, au_admin)
        _crear_rt(db, au_admin)
        _crear_rt(db, au_admin, revoked=True)
        # Y uno ajeno (no debe aparecer)
        _crear_rt(db, au_otro)

        r = client.get('/api/auth/sessions', headers=_hdr(au_admin))
        assert r.status_code == 200
        items = r.get_json()
        assert len(items) == 2

    def test_revoke_session_propia(self, client, au_admin, db):
        tok, _ = _crear_rt(db, au_admin)
        r = client.delete(
            f'/api/auth/sessions/{tok.id}',
            headers=_hdr(au_admin),
        )
        assert r.status_code == 200
        db.session.refresh(tok)
        assert tok.revoked is True

    def test_revoke_session_ajena_404(
        self, client, au_admin, au_otro, db,
    ):
        ajena, _ = _crear_rt(db, au_otro)
        r = client.delete(
            f'/api/auth/sessions/{ajena.id}',
            headers=_hdr(au_admin),
        )
        # No es del au_admin → 404 (no 403, para no confirmar existencia)
        assert r.status_code == 404
        db.session.refresh(ajena)
        assert ajena.revoked is False

    def test_revoke_session_inexistente_404(self, client, au_admin):
        r = client.delete('/api/auth/sessions/99999', headers=_hdr(au_admin))
        assert r.status_code == 404

    def test_revoke_all_panic(self, client, au_admin, au_otro, db):
        _crear_rt(db, au_admin); _crear_rt(db, au_admin)
        ajena, _ = _crear_rt(db, au_otro)
        pv_antes = au_admin.password_version

        r = client.delete('/api/auth/sessions/all', headers=_hdr(au_admin))
        assert r.status_code == 200

        # Todos los RT del au_admin quedan revocados
        activos = RefreshToken.query.filter_by(
            user_id=au_admin.id, revoked=False,
        ).count()
        assert activos == 0
        # password_version incrementa → JWT vivos invalidados
        db.session.refresh(au_admin)
        assert au_admin.password_version == pv_antes + 1
        # La sesión ajena NO se toca
        db.session.refresh(ajena)
        assert ajena.revoked is False
