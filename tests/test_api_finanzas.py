"""Tests del rol `finanzas` y su panel `/api/dashboard/finanzas`.

El rol es de solo lectura financiera: ve agregados de dispersión de nómina,
préstamos por recuperar y ajustes Inbursa pendientes. No tiene acceso al
dashboard de admin (PII) ni a módulos operativos.
"""
from datetime import date, timedelta

import pytest
from werkzeug.security import generate_password_hash

from app.models import (
    AjusteDescuento, AjustePeriodo, Prenomina, Prestamo,
    Proyecto, ReporteSemanal, Trabajador, User,
)
from app.routes.api_auth import _encode_access_token


def _hdr(user):
    return {'Authorization': f'Bearer {_encode_access_token(user)}'}


@pytest.fixture
def admin(db):
    u = User(username='fin_admin', password_hash=generate_password_hash('Pass123!'),
              role='admin')
    db.session.add(u); db.session.commit()
    return u


@pytest.fixture
def finanzas(db):
    u = User(username='fin_user', password_hash=generate_password_hash('Pass123!'),
              role='finanzas')
    db.session.add(u); db.session.commit()
    return u


@pytest.fixture
def coord(db):
    u = User(username='fin_coord', password_hash=generate_password_hash('Pass123!'),
              role='coordinador')
    db.session.add(u); db.session.commit()
    return u


@pytest.fixture
def trab(db):
    t = Trabajador(no_empleado='FIN-1', nombre='Fin', nombre_apellidos='Prueba',
                   activo=True)
    db.session.add(t); db.session.commit()
    return t


class TestRolFinanzas:

    def test_sistemas_puede_crear_usuario_finanzas(self, client, db):
        """`finanzas` sigue siendo un rol asignable al dar de alta una cuenta.

        Lo que cambió es QUIÉN da de alta: la gestión de cuentas se movió de
        admin (RRHH) al rol `sistemas`. Ver tests/test_api_sistemas.py.
        """
        gestor = User(username='fin_sistemas',
                      password_hash=generate_password_hash('Pass123!'),
                      role='sistemas')
        db.session.add(gestor); db.session.commit()

        r = client.post('/api/users', headers=_hdr(gestor), json={
            'username': 'contadora', 'password': 'S3gura!Password', 'role': 'finanzas',
        })
        assert r.status_code == 201, r.get_json()
        assert r.get_json()['role'] == 'finanzas'

    def test_admin_rrhh_ya_no_crea_usuarios(self, client, admin):
        r = client.post('/api/users', headers=_hdr(admin), json={
            'username': 'contadora2', 'password': 'S3gura!Password', 'role': 'finanzas',
        })
        assert r.status_code == 403

    def test_finanzas_no_ve_dashboard_admin(self, client, finanzas):
        # El dashboard de admin agrega PII (cumpleaños, docs con nombres) y
        # bitácora — finanzas solo ve su panel agregado.
        r = client.get('/api/dashboard', headers=_hdr(finanzas))
        assert r.status_code == 403


class TestPanelFinanzas:

    def test_coordinador_403(self, client, coord):
        r = client.get('/api/dashboard/finanzas', headers=_hdr(coord))
        assert r.status_code == 403

    def test_vacio(self, client, finanzas):
        body = client.get('/api/dashboard/finanzas', headers=_hdr(finanzas)).get_json()
        assert body['ultima_semana'] is None
        assert body['semana_en_proceso'] is None
        assert body['dispersado_anual'] == {'total': 0, 'semanas': 0}
        assert body['prestamos'] == {'activos': 0, 'por_recuperar': 0}
        assert body['ajustes_pendientes'] == {'registros': 0, 'monto': 0}

    def test_agregados(self, client, finanzas, trab, db):
        hoy = date.today()
        s1 = date(hoy.year, 1, 6)   # semana aprobada vieja (mismo año)
        s2 = date(hoy.year, 1, 13)  # semana aprobada reciente
        s3 = date(hoy.year, 1, 20)  # semana ABIERTA (en proceso)

        p = Proyecto(numero_proyecto='FIN-P', nombre='Obra', activo=True)
        db.session.add(p); db.session.commit()
        for ini, estado_rep in ((s1, 'PRENOMINA_CERRADA'), (s2, 'PRENOMINA_CERRADA'), (s3, 'PRENOMINA_CERRADA')):
            db.session.add(ReporteSemanal(
                fecha_inicio_semana=ini, fecha_fin_semana=ini + timedelta(days=6),
                proyecto_id=p.id, estado=estado_rep,
            ))

        for ini, estado, monto in ((s1, 'APROBADO', 1000), (s2, 'APROBADO', 2500), (s3, 'ABIERTA', 800)):
            db.session.add(Prenomina(
                trabajador_id=trab.id, fecha_inicio=ini,
                fecha_fin=ini + timedelta(days=6), estado=estado,
                total_a_pagar=monto,
            ))

        db.session.add(Prestamo(
            trabajador_id=trab.id, monto_total=5000, plazo_semanas=10,
            descuento_semanal=500, monto_restante=3000, estado='ACTIVO',
        ))
        db.session.add(Prestamo(
            trabajador_id=trab.id, monto_total=2000, plazo_semanas=4,
            descuento_semanal=500, monto_restante=0, estado='LIQUIDADO',
        ))

        periodo = AjustePeriodo(nombre='Enero', fecha_inicio=s1, fecha_fin=s3)
        db.session.add(periodo); db.session.commit()
        db.session.add(AjusteDescuento(
            periodo_id=periodo.id, trabajador_id=trab.id,
            monto=150, fecha_descuento=s1, cobrado=False,
        ))
        db.session.add(AjusteDescuento(
            periodo_id=periodo.id, trabajador_id=trab.id,
            monto=999, fecha_descuento=s1, cobrado=True,
        ))
        db.session.commit()

        body = client.get('/api/dashboard/finanzas', headers=_hdr(finanzas)).get_json()

        assert body['dispersado_anual'] == {'total': 3500.0, 'semanas': 2}
        assert body['ultima_semana'] == {
            'fecha_str': s2.isoformat(), 'trabajadores': 1, 'total': 2500.0,
        }
        # ultimas_semanas viene en orden descendente
        assert [s['fecha_str'] for s in body['ultimas_semanas']] == [s2.isoformat(), s1.isoformat()]
        assert body['semana_en_proceso'] == {
            'fecha_str': s3.isoformat(), 'estado': 'ABIERTA', 'total_estimado': 800.0,
        }
        assert body['prestamos'] == {'activos': 1, 'por_recuperar': 3000.0}
        assert body['ajustes_pendientes'] == {'registros': 1, 'monto': 150.0}

    def test_admin_tambien_accede(self, client, admin):
        r = client.get('/api/dashboard/finanzas', headers=_hdr(admin))
        assert r.status_code == 200
