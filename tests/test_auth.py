"""Tests de integración para autenticación y seguridad."""
import json
import pytest
from sqlalchemy import text as sql_text
from app.models import User
from werkzeug.security import generate_password_hash


class TestTotpCifrado:
    """A2: totp_secret debe almacenarse cifrado (Fernet) en la BD, nunca en texto plano."""

    def test_valor_en_db_no_es_texto_plano(self, db):
        """El valor crudo en BD debe ser ciphertext Fernet, no el secreto en claro."""
        import pyotp
        user = User(
            username='user_totp_cifrado',
            password_hash='hash',
            role='admin'
        )
        plaintext_secret = pyotp.random_base32()
        user.totp_secret = plaintext_secret
        db.session.add(user)
        db.session.commit()

        row = db.session.execute(
            sql_text("SELECT totp_secret FROM users WHERE id = :id"),
            {'id': user.id}
        ).fetchone()
        raw_value = row[0]

        assert raw_value != plaintext_secret, "El secreto TOTP NO debe guardarse en texto plano"
        assert raw_value.startswith('gAAA'), "Se espera ciphertext Fernet (empieza con 'gAAA')"

    def test_round_trip_descifrado_correcto(self, db):
        """Leer totp_secret a través del modelo deve devolver el valor original."""
        import pyotp
        user = User(
            username='user_totp_roundtrip',
            password_hash='hash',
            role='admin'
        )
        plaintext_secret = pyotp.random_base32()
        user.totp_secret = plaintext_secret
        db.session.add(user)
        db.session.commit()

        db.session.expire(user)
        reloaded = User.query.get(user.id)

        assert reloaded.totp_secret == plaintext_secret, "El secreto descifrado debe coincidir con el original"


class TestLogin:
    """Tests para /login."""

    def test_login_exitoso_admin(self, client, admin_user):
        resp = client.post('/login', data={
            'username': 'admin_test',
            'password': 'password123'
        }, follow_redirects=False)
        assert resp.status_code in [302, 303]
        # Verificar que se redirigió a home (no a login ni 2FA)
        assert '/login' not in resp.headers.get('Location', '')

    def test_login_fallido(self, client, admin_user):
        resp = client.post('/login', data={
            'username': 'admin_test',
            'password': 'wrongpassword'
        }, follow_redirects=True)
        assert b'incorrectas' in resp.data

    def test_login_usuario_inexistente(self, client):
        resp = client.post('/login', data={
            'username': 'noexiste',
            'password': 'password123'
        }, follow_redirects=True)
        assert b'incorrectas' in resp.data


class TestAccessControl:
    """Tests de control de acceso por rol."""

    def test_ruta_sin_login_redirige(self, client):
        resp = client.get('/', follow_redirects=False)
        assert resp.status_code in [302, 303]
        assert 'login' in resp.headers.get('Location', '')

    def test_coordinador_accede_trabajadores(self, logged_in_coordinador):
        resp = logged_in_coordinador.get('/trabajadores/', follow_redirects=True)
        assert resp.status_code == 200

    def test_coordinador_accede_horas(self, logged_in_coordinador):
        resp = logged_in_coordinador.get('/horas/', follow_redirects=False)
        # No debe devolver access denied
        assert resp.status_code == 200

    def test_admin_accede_todo(self, logged_in_admin):
        resp = logged_in_admin.get('/trabajadores/', follow_redirects=False)
        assert resp.status_code == 200

    def test_password_version_expirada_redirige_a_login(self, client, db):
        """A4: admin_required invalida sesiones con password_version desactualizada."""
        user = User(
            username='admin_version_test',
            password_hash='hash',
            role='admin',
            password_version=2  # versión actual en BD
        )
        db.session.add(user)
        db.session.commit()

        with client.session_transaction() as sess:
            sess['user_id'] = user.id
            sess['user'] = user.username
            sess['role'] = 'admin'
            sess['password_version'] = 1  # versión antigua → mismatch

        resp = client.get('/users/', follow_redirects=True)
        # Debe redirigir a login con mensaje de contraseña cambiada
        html = resp.data.decode('utf-8')
        assert 'login' in html.lower() or 'contraseña' in html.lower() or 'inicia' in html.lower()


class TestSelfDeletion:
    """Tests para bloqueo de auto-eliminación."""

    def test_admin_no_puede_eliminar_su_cuenta(self, logged_in_admin, admin_user):
        resp = logged_in_admin.post(f'/users/delete/{admin_user.id}', follow_redirects=True)
        assert b'propia cuenta' in resp.data.lower() or b'no puedes' in resp.data.lower()

    def test_admin_puede_eliminar_otro(self, logged_in_admin, db):
        other_user = User(
            username='other_user',
            password_hash=generate_password_hash('pass'),
            role='admin'
        )
        db.session.add(other_user)
        db.session.commit()
        other_id = other_user.id

        resp = logged_in_admin.post(f'/users/delete/{other_id}', follow_redirects=True)
        assert b'eliminado' in resp.data.lower()


class TestProfileOrphanSession:
    """Sesión huérfana en /profile."""

    def test_profile_usuario_eliminado(self, client):
        """Si user_id en sesión no existe, debe redirigir a login."""
        with client.session_transaction() as sess:
            sess['user_id'] = 99999
            sess['user'] = 'fantasma'
            sess['role'] = 'admin'

        resp = client.get('/profile', follow_redirects=True)
        assert b'login' in resp.data.lower() or b'inicia' in resp.data.lower()


class TestVerify2FA:
    """Tests para /verify-2fa."""

    def test_verify_2fa_sin_presession(self, client):
        """Sin pre_2fa_user_id en sesión, redirige a login."""
        resp = client.get('/verify-2fa', follow_redirects=False)
        assert resp.status_code in [302, 303]
        assert 'login' in resp.headers.get('Location', '')

    def test_verify_2fa_usuario_eliminado(self, client, db):
        """Si el usuario fue eliminado entre login y 2FA, redirige limpiamente."""
        with client.session_transaction() as sess:
            sess['pre_2fa_user_id'] = 99999

        resp = client.post('/verify-2fa', data={'code': '123456'}, follow_redirects=True)
        assert b'login' in resp.data.lower() or b'inv' in resp.data.lower()

    def test_2fa_exitoso_limpia_pre_session_y_establece_user_id(self, client, db):
        """B2: Tras 2FA exitoso, pre_2fa_user_id se elimina y user_id queda en sesión."""
        import pyotp
        secret = pyotp.random_base32()
        user = User(
            username='user_2fa_session_clean',
            password_hash=generate_password_hash('pass'),
            role='admin',
            totp_secret=secret
        )
        db.session.add(user)
        db.session.commit()

        with client.session_transaction() as sess:
            sess['pre_2fa_user_id'] = user.id

        valid_code = pyotp.TOTP(secret).now()
        resp = client.post('/verify-2fa', data={'code': valid_code}, follow_redirects=False)

        assert resp.status_code in [302, 303], "Debe redirigir tras 2FA exitoso"

        with client.session_transaction() as sess:
            assert 'pre_2fa_user_id' not in sess, "pre_2fa_user_id debe haberse limpiado"
            assert 'user_id' in sess, "user_id debe estar en sesión tras 2FA"
            assert sess['user_id'] == user.id
