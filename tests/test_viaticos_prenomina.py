"""Tests de integración para PATCH /prenomina/api/viaticos."""
import json
import pytest
from datetime import date, timedelta
from decimal import Decimal
from app.models import Prenomina


class TestApiViaticos:
    """Tests para PATCH /prenomina/api/viaticos."""

    def _crear_prenomina(self, db, trabajador, pago_viaticos=0):
        p = Prenomina(
            trabajador_id=trabajador.id,
            fecha_inicio=date.today(),
            fecha_fin=date.today() + timedelta(days=6),
            estado='ABIERTA',
            salario_base=5000,
            pago_viaticos=pago_viaticos,
            total_percepciones=5000 + pago_viaticos,
            total_deducciones=0,
            total_a_pagar=5000 + pago_viaticos,
        )
        db.session.add(p)
        db.session.commit()
        return p

    def test_actualizar_viaticos_exitoso(self, logged_in_admin, db, trabajador):
        pren = self._crear_prenomina(db, trabajador, pago_viaticos=0)
        resp = logged_in_admin.patch(
            '/prenomina/api/viaticos',
            data=json.dumps({'prenomina_id': pren.id, 'monto_viaticos': 350.50}),
            content_type='application/json',
        )
        data = resp.get_json()
        assert resp.status_code == 200
        assert data['success'] is True
        assert abs(data['pago_viaticos'] - 350.50) < 0.01
        # Los totales se recalcularon y contemplan los viáticos
        assert data['total_percepciones'] >= 350.50

    def test_actualizar_viaticos_cero(self, logged_in_admin, db, trabajador):
        pren = self._crear_prenomina(db, trabajador, pago_viaticos=500)
        resp = logged_in_admin.patch(
            '/prenomina/api/viaticos',
            data=json.dumps({'prenomina_id': pren.id, 'monto_viaticos': 0}),
            content_type='application/json',
        )
        data = resp.get_json()
        assert data['success'] is True
        assert data['pago_viaticos'] == 0.0

    def test_actualizar_viaticos_negativo(self, logged_in_admin, db, trabajador):
        pren = self._crear_prenomina(db, trabajador)
        resp = logged_in_admin.patch(
            '/prenomina/api/viaticos',
            data=json.dumps({'prenomina_id': pren.id, 'monto_viaticos': -100}),
            content_type='application/json',
        )
        assert resp.status_code == 400

    def test_actualizar_viaticos_sin_json(self, logged_in_admin):
        resp = logged_in_admin.patch(
            '/prenomina/api/viaticos',
            data='no es json',
            content_type='text/plain',
        )
        assert resp.status_code == 400

    def test_actualizar_viaticos_campo_faltante(self, logged_in_admin, db, trabajador):
        pren = self._crear_prenomina(db, trabajador)
        resp = logged_in_admin.patch(
            '/prenomina/api/viaticos',
            data=json.dumps({'prenomina_id': pren.id}),
            content_type='application/json',
        )
        assert resp.status_code == 400
        assert 'obligatorios' in resp.get_json()['message']

    def test_actualizar_viaticos_prenomina_cerrada(self, logged_in_admin, db, trabajador):
        pren = self._crear_prenomina(db, trabajador)
        pren.estado = 'CERRADA'
        db.session.commit()
        resp = logged_in_admin.patch(
            '/prenomina/api/viaticos',
            data=json.dumps({'prenomina_id': pren.id, 'monto_viaticos': 200}),
            content_type='application/json',
        )
        assert resp.status_code == 400
        assert 'ABIERTAS' in resp.get_json()['message']

    def test_viaticos_se_suman_a_total(self, logged_in_admin, db, trabajador):
        pren = self._crear_prenomina(db, trabajador, pago_viaticos=0)
        salario_base = float(pren.salario_base)
        monto_viaticos = 750.0

        resp = logged_in_admin.patch(
            '/prenomina/api/viaticos',
            data=json.dumps({'prenomina_id': pren.id, 'monto_viaticos': monto_viaticos}),
            content_type='application/json',
        )
        data = resp.get_json()
        assert data['success'] is True
        # total_a_pagar debe incluir el salario base + viáticos
        assert abs(data['total_a_pagar'] - (salario_base + monto_viaticos)) < 0.01
