"""Tests de integración para autenticación y seguridad."""
import json
import pytest
from app.models import User
from werkzeug.security import generate_password_hash


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

    def test_coordinador_no_accede_trabajadores(self, logged_in_coordinador):
        resp = logged_in_coordinador.get('/trabajadores/', follow_redirects=True)
        assert b'denegado' in resp.data.lower()

    def test_coordinador_accede_horas(self, logged_in_coordinador):
        resp = logged_in_coordinador.get('/horas/', follow_redirects=False)
        # No debe devolver access denied
        assert resp.status_code == 200

    def test_admin_accede_todo(self, logged_in_admin):
        resp = logged_in_admin.get('/trabajadores/', follow_redirects=False)
        assert resp.status_code == 200


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
