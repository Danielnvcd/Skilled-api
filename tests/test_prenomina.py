"""Tests de integración para prenómina: descuentos, depósitos, y cierre."""
import json
import pytest
from datetime import date, timedelta
from app.models import (
    Prenomina, Prestamo, AbonoPrestamo,
    ReporteSemanal, DescuentoPrenomina, DepositoExtra
)


class TestApiDescuento:
    """Tests para POST /prenomina/api/descuento."""

    def _crear_prenomina(self, db, trabajador):
        p = Prenomina(
            trabajador_id=trabajador.id,
            fecha_inicio=date.today(),
            fecha_fin=date.today() + timedelta(days=6),
            estado='ABIERTA',
            salario_base=5000,
            total_percepciones=5000,
            total_deducciones=0,
            total_a_pagar=5000,
        )
        db.session.add(p)
        db.session.commit()
        return p

    def test_descuento_exitoso(self, logged_in_admin, db, trabajador):
        pren = self._crear_prenomina(db, trabajador)
        resp = logged_in_admin.post('/prenomina/api/descuento',
            data=json.dumps({
                'prenomina_id': pren.id,
                'tipo': 'MANUAL',
                'concepto': 'Descuento test',
                'monto': 200
            }),
            content_type='application/json'
        )
        assert resp.get_json()['success'] is True

    def test_descuento_monto_negativo(self, logged_in_admin, db, trabajador):
        pren = self._crear_prenomina(db, trabajador)
        resp = logged_in_admin.post('/prenomina/api/descuento',
            data=json.dumps({
                'prenomina_id': pren.id,
                'tipo': 'MANUAL',
                'concepto': 'Negativo',
                'monto': -50
            }),
            content_type='application/json'
        )
        assert resp.status_code == 400

    def test_descuento_sin_json(self, logged_in_admin):
        resp = logged_in_admin.post('/prenomina/api/descuento',
            data='no es json',
            content_type='text/plain'
        )
        assert resp.status_code == 400

    def test_descuento_campo_faltante(self, logged_in_admin, db, trabajador):
        pren = self._crear_prenomina(db, trabajador)
        resp = logged_in_admin.post('/prenomina/api/descuento',
            data=json.dumps({
                'prenomina_id': pren.id,
                'tipo': 'MANUAL'
                # Falta concepto y monto
            }),
            content_type='application/json'
        )
        assert resp.status_code == 400
        assert 'obligatorios' in resp.get_json()['message']


class TestApiDeposito:
    """Tests para POST /prenomina/api/deposito."""

    def _crear_prenomina(self, db, trabajador):
        p = Prenomina(
            trabajador_id=trabajador.id,
            fecha_inicio=date.today(),
            fecha_fin=date.today() + timedelta(days=6),
            estado='ABIERTA',
            salario_base=5000,
            total_percepciones=5000,
            total_deducciones=0,
            total_a_pagar=5000,
        )
        db.session.add(p)
        db.session.commit()
        return p

    def test_deposito_exitoso(self, logged_in_admin, db, trabajador):
        pren = self._crear_prenomina(db, trabajador)
        resp = logged_in_admin.post('/prenomina/api/deposito',
            data=json.dumps({
                'prenomina_id': pren.id,
                'monto': 500,
                'concepto': 'Bono test'
            }),
            content_type='application/json'
        )
        assert resp.get_json()['success'] is True

    def test_deposito_monto_cero(self, logged_in_admin, db, trabajador):
        pren = self._crear_prenomina(db, trabajador)
        resp = logged_in_admin.post('/prenomina/api/deposito',
            data=json.dumps({
                'prenomina_id': pren.id,
                'monto': 0,
                'concepto': 'Zero'
            }),
            content_type='application/json'
        )
        assert resp.status_code == 400


class TestCerrarPrenomina:
    """Tests para el cierre de prenómina y el abono correcto de préstamos."""

    def _setup_prenomina_con_prestamo(self, db, trabajador, monto_restante=1000, descuento_semanal=250):
        """Crea una prenomina ABIERTA + un préstamo ACTIVO para el trabajador."""
        pren = Prenomina(
            trabajador_id=trabajador.id,
            fecha_inicio=date(2025, 3, 1),
            fecha_fin=date(2025, 3, 7),
            estado='ABIERTA',
            salario_base=5000,
            descuento_prestamos=descuento_semanal,
            total_percepciones=5000,
            total_deducciones=descuento_semanal,
            total_a_pagar=5000 - descuento_semanal,
        )
        db.session.add(pren)

        prestamo = Prestamo(
            trabajador_id=trabajador.id,
            monto_total=1000,
            plazo_semanas=4,
            descuento_semanal=descuento_semanal,
            monto_restante=monto_restante,
            estado='ACTIVO',
            activo=True
        )
        db.session.add(prestamo)
        db.session.commit()
        return pren, prestamo

    def test_cierre_abono_normal(self, logged_in_admin, db, trabajador):
        """Cierre con saldo > descuento: abona exactamente el descuento."""
        pren, prestamo = self._setup_prenomina_con_prestamo(db, trabajador,
            monto_restante=1000, descuento_semanal=250)

        resp = logged_in_admin.post('/prenomina/cerrar/2025-03-01',
            content_type='application/json'
        )
        data = resp.get_json()
        assert data['success'] is True

        # Verificar que el abono fue de 250 (no más)
        db.session.refresh(prestamo)
        abonos = AbonoPrestamo.query.filter_by(prestamo_id=prestamo.id).all()
        assert len(abonos) == 1
        assert float(abonos[0].monto) == 250.0
        assert float(prestamo.monto_restante) == 750.0

    def test_cierre_abono_parcial_no_sobrepaga(self, logged_in_admin, db, trabajador):
        """Cierre con saldo < descuento: abona solo lo que resta (no sobrepaga)."""
        pren, prestamo = self._setup_prenomina_con_prestamo(db, trabajador,
            monto_restante=100, descuento_semanal=500)

        resp = logged_in_admin.post('/prenomina/cerrar/2025-03-01',
            content_type='application/json'
        )
        assert resp.get_json()['success'] is True

        db.session.refresh(prestamo)
        abonos = AbonoPrestamo.query.filter_by(prestamo_id=prestamo.id).all()
        assert len(abonos) == 1
        assert float(abonos[0].monto) == 100.0  # Solo lo que restaba
        assert float(prestamo.monto_restante) == 0.0
        assert prestamo.estado == 'LIQUIDADO'

    def test_cierre_prestamo_saldo_cero_se_liquida(self, logged_in_admin, db, trabajador):
        """Préstamo con saldo 0 pero estado ACTIVO: debe marcarse como LIQUIDADO sin crear abono."""
        pren, prestamo = self._setup_prenomina_con_prestamo(db, trabajador,
            monto_restante=0, descuento_semanal=250)

        resp = logged_in_admin.post('/prenomina/cerrar/2025-03-01',
            content_type='application/json'
        )
        assert resp.get_json()['success'] is True

        db.session.refresh(prestamo)
        assert prestamo.estado == 'LIQUIDADO'
        assert prestamo.activo is False
        # No debe haber abonos (saldo ya era 0)
        abonos = AbonoPrestamo.query.filter_by(prestamo_id=prestamo.id).all()
        assert len(abonos) == 0
