# -*- coding: utf-8 -*-
"""Tests para el estado de conexion En linea / Desconectado en la lista de usuarios."""
import time
import pytest
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash
from app.models import User


# ── Helpers ───────────────────────────────────────────────────────────────────

def _crear_usuario(db, username, role='coordinador', last_seen=None):
    u = User(
        username=username,
        password_hash=generate_password_hash('Test1234!'),
        role=role,
        last_seen=last_seen,
    )
    db.session.add(u)
    db.session.commit()
    return u


def _login_client(client, user, last_seen_ts=None):
    """Inyecta sesion autenticada en el test client."""
    with client.session_transaction() as sess:
        sess['user_id'] = user.id
        sess['user'] = user.username
        sess['role'] = user.role
        sess['password_version'] = user.password_version or 1
        if last_seen_ts is not None:
            sess['_last_seen_ts'] = last_seen_ts
    return client


# ── Tests de estado en HTML ───────────────────────────────────────────────────

class TestEstadoConexionHTML:
    """La pagina /users/ muestra 'En linea' o 'Desconectado' segun last_seen."""

    def test_sin_last_seen_aparece_desconectado(self, logged_in_admin, db):
        _crear_usuario(db, 'u_nuevo', last_seen=None)

        resp = logged_in_admin.get('/users/', follow_redirects=True)

        assert resp.status_code == 200
        assert 'Desconectado' in resp.get_data(as_text=True)

    def test_last_seen_hace_dos_minutos_aparece_en_linea(self, logged_in_admin, db):
        _crear_usuario(db, 'u_reciente', last_seen=datetime.now() - timedelta(minutes=2))

        resp = logged_in_admin.get('/users/', follow_redirects=True)

        assert resp.status_code == 200
        assert 'En l\xednea' in resp.get_data(as_text=True)

    def test_last_seen_hace_diez_minutos_aparece_desconectado(self, logged_in_admin, db):
        _crear_usuario(db, 'u_inactivo', last_seen=datetime.now() - timedelta(minutes=10))

        resp = logged_in_admin.get('/users/', follow_redirects=True)

        assert resp.status_code == 200
        assert 'Desconectado' in resp.get_data(as_text=True)

    def test_exactamente_300_segundos_es_desconectado(self, logged_in_admin, db):
        # El template usa < 300, por lo tanto 300 exactos NO es "En linea"
        _crear_usuario(db, 'u_limite', last_seen=datetime.now() - timedelta(seconds=300))

        resp = logged_in_admin.get('/users/', follow_redirects=True)

        assert resp.status_code == 200
        assert 'Desconectado' in resp.get_data(as_text=True)

    def test_last_seen_hace_un_segundo_aparece_en_linea(self, logged_in_admin, db):
        _crear_usuario(db, 'u_muy_reciente', last_seen=datetime.now() - timedelta(seconds=1))

        resp = logged_in_admin.get('/users/', follow_redirects=True)

        assert resp.status_code == 200
        assert 'En l\xednea' in resp.get_data(as_text=True)

    def test_ambos_estados_coexisten_en_la_lista(self, logged_in_admin, db):
        _crear_usuario(db, 'u_online',  last_seen=datetime.now() - timedelta(minutes=1))
        _crear_usuario(db, 'u_offline', last_seen=datetime.now() - timedelta(minutes=10))

        resp = logged_in_admin.get('/users/', follow_redirects=True)
        html = resp.get_data(as_text=True)

        assert resp.status_code == 200
        assert 'En l\xednea' in html
        assert 'Desconectado' in html


# ── Tests de update_last_seen ─────────────────────────────────────────────────

class TestUpdateLastSeen:
    """update_last_seen() escribe en BD con throttle de 5 minutos (fallback sin Redis)."""

    def test_primer_request_establece_last_seen(self, client, admin_user, db):
        assert admin_user.last_seen is None

        _login_client(client, admin_user)  # sin _last_seen_ts: throttle en 0
        client.get('/users/', follow_redirects=True)

        db.session.refresh(admin_user)
        assert admin_user.last_seen is not None

    def test_request_anonimo_no_modifica_last_seen(self, client, admin_user, db):
        assert admin_user.last_seen is None

        client.get('/', follow_redirects=True)  # sin sesion

        db.session.refresh(admin_user)
        assert admin_user.last_seen is None

    def test_throttle_activo_no_sobreescribe_last_seen(self, client, admin_user, db):
        hace_un_minuto = datetime.now() - timedelta(minutes=1)
        admin_user.last_seen = hace_un_minuto
        db.session.commit()

        # throttle activo: _last_seen_ts fue hace 60 s < 300 s
        _login_client(client, admin_user, last_seen_ts=time.time() - 60)
        client.get('/users/', follow_redirects=True)

        db.session.refresh(admin_user)
        diferencia = abs((admin_user.last_seen - hace_un_minuto).total_seconds())
        assert diferencia < 2, "last_seen no debe cambiar con throttle activo"

    def test_throttle_expirado_actualiza_last_seen(self, client, admin_user, db):
        hace_diez_minutos = datetime.now() - timedelta(minutes=10)
        admin_user.last_seen = hace_diez_minutos
        db.session.commit()

        # throttle expirado: _last_seen_ts fue hace 400 s > 300 s
        _login_client(client, admin_user, last_seen_ts=time.time() - 400)
        client.get('/users/', follow_redirects=True)

        db.session.refresh(admin_user)
        assert admin_user.last_seen > hace_diez_minutos

    def test_usuario_activo_aparece_en_linea_tras_request(self, client, admin_user, db):
        """Despues de hacer un request, el usuario debe aparecer En linea en la lista."""
        assert admin_user.last_seen is None

        _login_client(client, admin_user)
        client.get('/users/', follow_redirects=True)  # actualiza last_seen

        resp = client.get('/users/', follow_redirects=True)
        assert resp.status_code == 200
        assert 'En l\xednea' in resp.get_data(as_text=True)
