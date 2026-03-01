"""Tests de integración para el módulo de préstamos."""
import json
import pytest
from app.models import Prestamo, AbonoPrestamo


class TestCrearPrestamo:
    """Tests para POST /prestamos/crear."""

    def test_crear_exitoso(self, logged_in_admin, trabajador):
        resp = logged_in_admin.post('/prestamos/crear',
            data=json.dumps({
                'trabajador_id': trabajador.id,
                'monto_total': 5000,
                'plazo_semanas': 10,
                'descuento_semanal': 500,
                'motivo': 'Test'
            }),
            content_type='application/json'
        )
        data = resp.get_json()
        assert data['success'] is True
        assert 'id' in data

    def test_crear_monto_negativo(self, logged_in_admin, trabajador):
        resp = logged_in_admin.post('/prestamos/crear',
            data=json.dumps({
                'trabajador_id': trabajador.id,
                'monto_total': -100,
                'plazo_semanas': 10,
                'descuento_semanal': 500
            }),
            content_type='application/json'
        )
        assert resp.status_code == 400
        assert resp.get_json()['success'] is False

    def test_crear_monto_cero(self, logged_in_admin, trabajador):
        resp = logged_in_admin.post('/prestamos/crear',
            data=json.dumps({
                'trabajador_id': trabajador.id,
                'monto_total': 0,
                'plazo_semanas': 10,
                'descuento_semanal': 500
            }),
            content_type='application/json'
        )
        assert resp.status_code == 400

    def test_crear_sin_json(self, logged_in_admin):
        resp = logged_in_admin.post('/prestamos/crear',
            data='esto no es json',
            content_type='text/plain'
        )
        assert resp.status_code == 400
        assert 'inválidos' in resp.get_json()['message']

    def test_crear_campos_faltantes(self, logged_in_admin):
        resp = logged_in_admin.post('/prestamos/crear',
            data=json.dumps({'trabajador_id': 1}),
            content_type='application/json'
        )
        assert resp.status_code == 400
        assert 'obligatorios' in resp.get_json()['message']


class TestAbonarPrestamo:
    """Tests para POST /prestamos/abonar/<id>."""

    def _crear_prestamo(self, db, trabajador_id):
        p = Prestamo(
            trabajador_id=trabajador_id,
            monto_total=1000,
            plazo_semanas=4,
            descuento_semanal=250,
            monto_restante=1000,
            estado='ACTIVO',
            activo=True
        )
        db.session.add(p)
        db.session.commit()
        return p

    def test_abono_exitoso(self, logged_in_admin, db, trabajador):
        p = self._crear_prestamo(db, trabajador.id)
        resp = logged_in_admin.post(f'/prestamos/abonar/{p.id}',
            data=json.dumps({'monto': 300}),
            content_type='application/json'
        )
        data = resp.get_json()
        assert data['success'] is True
        assert data['monto_restante'] == 700.0

    def test_abono_liquida_prestamo(self, logged_in_admin, db, trabajador):
        p = self._crear_prestamo(db, trabajador.id)
        resp = logged_in_admin.post(f'/prestamos/abonar/{p.id}',
            data=json.dumps({'monto': 1000}),
            content_type='application/json'
        )
        data = resp.get_json()
        assert data['success'] is True
        assert data['monto_restante'] == 0.0
        assert data['estado'] == 'LIQUIDADO'

    def test_abono_monto_negativo(self, logged_in_admin, db, trabajador):
        p = self._crear_prestamo(db, trabajador.id)
        resp = logged_in_admin.post(f'/prestamos/abonar/{p.id}',
            data=json.dumps({'monto': -100}),
            content_type='application/json'
        )
        assert resp.status_code == 400

    def test_abono_monto_cero(self, logged_in_admin, db, trabajador):
        p = self._crear_prestamo(db, trabajador.id)
        resp = logged_in_admin.post(f'/prestamos/abonar/{p.id}',
            data=json.dumps({'monto': 0}),
            content_type='application/json'
        )
        assert resp.status_code == 400

    def test_abono_sin_monto(self, logged_in_admin, db, trabajador):
        p = self._crear_prestamo(db, trabajador.id)
        resp = logged_in_admin.post(f'/prestamos/abonar/{p.id}',
            data=json.dumps({}),
            content_type='application/json'
        )
        assert resp.status_code == 400

    def test_abono_prestamo_liquidado(self, logged_in_admin, db, trabajador):
        p = self._crear_prestamo(db, trabajador.id)
        p.estado = 'LIQUIDADO'
        db.session.commit()

        resp = logged_in_admin.post(f'/prestamos/abonar/{p.id}',
            data=json.dumps({'monto': 100}),
            content_type='application/json'
        )
        assert resp.status_code == 400
        assert 'liquidado' in resp.get_json()['message'].lower()


class TestEditarPrestamo:
    """Tests para POST /prestamos/editar/<id>."""

    def _crear_prestamo_con_abono(self, db, trabajador_id):
        p = Prestamo(
            trabajador_id=trabajador_id,
            monto_total=1000,
            plazo_semanas=4,
            descuento_semanal=250,
            monto_restante=700,
            estado='ACTIVO',
            activo=True
        )
        db.session.add(p)
        db.session.commit()

        abono = AbonoPrestamo(
            prestamo_id=p.id,
            monto=300,
            fecha_abono=__import__('datetime').date.today(),
            tipo='MANUAL'
        )
        db.session.add(abono)
        db.session.commit()
        return p

    def test_editar_monto_ok(self, logged_in_admin, db, trabajador):
        p = self._crear_prestamo_con_abono(db, trabajador.id)
        resp = logged_in_admin.post(f'/prestamos/editar/{p.id}',
            data=json.dumps({'monto_total': 2000}),
            content_type='application/json'
        )
        data = resp.get_json()
        assert data['success'] is True

    def test_editar_monto_menor_a_abonado(self, logged_in_admin, db, trabajador):
        """No debe permitir reducir monto_total bajo lo ya abonado (300)."""
        p = self._crear_prestamo_con_abono(db, trabajador.id)
        resp = logged_in_admin.post(f'/prestamos/editar/{p.id}',
            data=json.dumps({'monto_total': 200}),
            content_type='application/json'
        )
        assert resp.status_code == 400
        assert 'abonado' in resp.get_json()['message'].lower()

    def test_editar_monto_negativo(self, logged_in_admin, db, trabajador):
        p = self._crear_prestamo_con_abono(db, trabajador.id)
        resp = logged_in_admin.post(f'/prestamos/editar/{p.id}',
            data=json.dumps({'monto_total': -500}),
            content_type='application/json'
        )
        assert resp.status_code == 400

    def test_editar_prestamo_liquidado(self, logged_in_admin, db, trabajador):
        p = self._crear_prestamo_con_abono(db, trabajador.id)
        p.estado = 'LIQUIDADO'
        db.session.commit()

        resp = logged_in_admin.post(f'/prestamos/editar/{p.id}',
            data=json.dumps({'monto_total': 5000}),
            content_type='application/json'
        )
        assert resp.status_code == 400
