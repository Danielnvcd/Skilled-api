"""Tests del API `/api/dashboard` — agregado del Inicio de admin.

Regla no obvia: "dado de baja" significa `activo=False` **o** `fecha_baja`
capturada — existen registros legacy/importados con fecha_baja y la bandera
aún encendida. Cumpleañeros y docs/credenciales por vencer filtran ambas
(mismo patrón que /credenciales-lista).
"""
from datetime import date

import pytest
from werkzeug.security import generate_password_hash

from app.models import Proyecto, Trabajador, User
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

    def test_stats_total_excluye_bajas(self, client, admin, db):
        _trab(db, 'ST-A', 'Activa', activo=True)
        _trab(db, 'ST-B', 'Baja', activo=False, fecha_baja=date.today())
        _trab(db, 'ST-C', 'BajaLegacy', activo=True, fecha_baja=date.today())

        r = client.get('/api/dashboard', headers=_hdr(admin))
        assert r.get_json()['stats']['total_trabajadores'] == 1

    def test_puestos_y_proyectos_excluyen_bajas(self, client, admin, db):
        activa = _trab(db, 'GR-A', 'Activa', activo=True, puesto='Soldador')
        baja = _trab(db, 'GR-B', 'Baja', activo=False, fecha_baja=date.today(),
                     puesto='Soldador')
        p = Proyecto(numero_proyecto='DASH-1', nombre='Obra', activo=True)
        p.participantes = [activa, baja]
        db.session.add(p); db.session.commit()

        body = client.get('/api/dashboard', headers=_hdr(admin)).get_json()
        soldador = next(x for x in body['empleados_por_puesto'] if x['label'] == 'Soldador')
        assert soldador['value'] == 1
        obra = next(x for x in body['empleados_por_proyecto'] if x['label'] == 'DASH-1')
        assert obra['value'] == 1

    def test_proyectos_cuenta_por_asignacion(self, client, admin, db):
        # Un trabajador en DOS proyectos activos cuenta en ambas rebanadas
        # (el donut suma asignaciones, no empleados únicos).
        t = _trab(db, 'AS-A', 'MultiProyecto', activo=True)
        for n in ('DASH-2', 'DASH-3'):
            p = Proyecto(numero_proyecto=n, nombre=f'Obra {n}', activo=True)
            p.participantes = [t]
            db.session.add(p)
        db.session.commit()

        body = client.get('/api/dashboard', headers=_hdr(admin)).get_json()
        valores = {x['label']: x['value'] for x in body['empleados_por_proyecto']}
        assert valores['DASH-2'] == 1 and valores['DASH-3'] == 1

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
