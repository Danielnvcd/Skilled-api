"""Tests del API `/api/dashboard` — agregado del Inicio de admin.

Regla no obvia: "dado de baja" significa `activo=False` **o** `fecha_baja`
capturada — existen registros legacy/importados con fecha_baja y la bandera
aún encendida. Cumpleañeros y docs/credenciales por vencer filtran ambas
(mismo patrón que /credenciales-lista).
"""
from datetime import date

import pytest
from werkzeug.security import generate_password_hash

from app.models import Trabajador, User
from app.routes.api_auth import _encode_access_token


def _hdr(user):
    return {'Authorization': f'Bearer {_encode_access_token(user)}'}


@pytest.fixture
def admin(db):
    u = User(username='dash_admin', password_hash=generate_password_hash('Pass123!'),
              role='admin')
    db.session.add(u); db.session.commit()
    return u


@pytest.fixture
def coord(db):
    u = User(username='dash_coord', password_hash=generate_password_hash('Pass123!'),
              role='coordinador')
    db.session.add(u); db.session.commit()
    return u


def _trab(db, no_emp, nombre, **kw):
    t = Trabajador(no_empleado=no_emp, nombre=nombre, nombre_apellidos='Prueba', **kw)
    db.session.add(t); db.session.commit()
    return t


class TestDashboard:

    def test_no_admin_403(self, client, coord):
        r = client.get('/api/dashboard', headers=_hdr(coord))
        assert r.status_code == 403

    def test_admin_200(self, client, admin):
        r = client.get('/api/dashboard', headers=_hdr(admin))
        assert r.status_code == 200
        body = r.get_json()
        assert 'stats' in body and 'cumpleañeros' in body

    def test_cumpleaneros_excluye_bajas(self, client, admin, db):
        hoy = date.today()
        nacimiento = date(1990, hoy.month, 15)
        _trab(db, 'CUM-A', 'CumpleActiva', activo=True, fecha_nacimiento=nacimiento)
        # Baja lógica normal (DELETE /trabajadores/<id>)
        _trab(db, 'CUM-B', 'CumpleBaja', activo=False, fecha_baja=hoy,
              fecha_nacimiento=nacimiento)
        # Legacy: fecha_baja capturada pero la bandera quedó encendida
        _trab(db, 'CUM-C', 'CumpleLegacy', activo=True, fecha_baja=hoy,
              fecha_nacimiento=nacimiento)

        r = client.get('/api/dashboard', headers=_hdr(admin))
        nombres = {c['nombre'] for c in r.get_json()['cumpleañeros']}
        assert 'CumpleActiva' in nombres
        assert 'CumpleBaja' not in nombres
        assert 'CumpleLegacy' not in nombres
