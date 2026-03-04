"""Tests de precisión decimal para cálculos monetarios.

Verifica que el sistema no tenga errores de punto flotante
en operaciones con dinero (el clásico 0.1 + 0.2 ≠ 0.3).
"""
import json
import pytest
from decimal import Decimal
from datetime import date, timedelta
from app.models import (
    Prenomina, Prestamo, AbonoPrestamo, Trabajador,
    ReporteSemanal, RegistroDiarioHoras, DescuentoPrenomina, DepositoExtra,
    Proyecto, User
)


class TestDecimalPrecisionPrestamos:
    """Verifica que los préstamos no pierdan centavos por float."""

    def _crear_prestamo(self, db, trabajador_id, monto, descuento):
        p = Prestamo(
            trabajador_id=trabajador_id,
            monto_total=monto,
            plazo_semanas=10,
            descuento_semanal=descuento,
            monto_restante=monto,
            estado='ACTIVO',
            activo=True
        )
        db.session.add(p)
        db.session.commit()
        return p

    def test_abonos_multiples_llegan_a_cero_exacto(self, logged_in_admin, db, trabajador):
        """4 abonos de $1250.33 a un préstamo de $5001.32 deben dejar exactamente $0.00."""
        p = self._crear_prestamo(db, trabajador.id, Decimal('5001.32'), Decimal('1250.33'))

        for i in range(4):
            resp = logged_in_admin.post(f'/prestamos/abonar/{p.id}',
                data=json.dumps({'monto': '1250.33'}),
                content_type='application/json'
            )
            data = resp.get_json()
            assert data['success'] is True

        db.session.refresh(p)
        assert Decimal(str(p.monto_restante)) == Decimal('0.00')
        assert p.estado == 'LIQUIDADO'

    def test_crear_prestamo_valores_fraccionarios(self, logged_in_admin, db, trabajador):
        """Crear un préstamo con valores que causan error en float: 0.1 + 0.2."""
        resp = logged_in_admin.post('/prestamos/crear',
            data=json.dumps({
                'trabajador_id': trabajador.id,
                'monto_total': '0.30',
                'plazo_semanas': 3,
                'descuento_semanal': '0.10'
            }),
            content_type='application/json'
        )
        data = resp.get_json()
        assert data['success'] is True

        prestamo = Prestamo.query.get(data['id'])
        assert Decimal(str(prestamo.monto_total)) == Decimal('0.30')
        assert Decimal(str(prestamo.descuento_semanal)) == Decimal('0.10')

    def test_editar_recalcula_restante_exacto(self, logged_in_admin, db, trabajador):
        """Editar monto_total recalcula restante sin drift."""
        p = self._crear_prestamo(db, trabajador.id, Decimal('1000.00'), Decimal('100.00'))

        # Abonar 333.33
        logged_in_admin.post(f'/prestamos/abonar/{p.id}',
            data=json.dumps({'monto': '333.33'}),
            content_type='application/json'
        )

        # Editar monto total a 2000
        resp = logged_in_admin.post(f'/prestamos/editar/{p.id}',
            data=json.dumps({'monto_total': '2000.00'}),
            content_type='application/json'
        )
        assert resp.get_json()['success'] is True

        db.session.refresh(p)
        # Restante = 2000.00 - 333.33 = 1666.67 exacto
        assert Decimal(str(p.monto_restante)) == Decimal('1666.67')


class TestDecimalPrecisionPrenomina:
    """Verifica que los cálculos de prenómina no pierdan centavos."""

    def _crear_prenomina(self, db, trabajador, **overrides):
        defaults = dict(
            trabajador_id=trabajador.id,
            fecha_inicio=date(2025, 6, 1),
            fecha_fin=date(2025, 6, 7),
            estado='ABIERTA',
            salario_base=Decimal('1234.50'),
            pago_horas_extras=Decimal('567.30'),
            pago_viaticos=Decimal('89.20'),
            pago_festivos=Decimal('0'),
            depositos_otros=Decimal('0'),
            descuento_infonavit=Decimal('123.45'),
            ajuste_inbursa=Decimal('67.89'),
            descuento_incidencias=Decimal('0'),
            descuento_prestamos=Decimal('0'),
            descuentos_otros=Decimal('0'),
            total_percepciones=Decimal('1891.00'),
            total_deducciones=Decimal('191.34'),
            total_a_pagar=Decimal('1699.66'),
        )
        defaults.update(overrides)
        p = Prenomina(**defaults)
        db.session.add(p)
        db.session.commit()
        return p

    def test_recalculo_totales_exactos(self, logged_in_admin, db, trabajador):
        """Agregar un descuento debe recalcular totales sin drift."""
        pren = self._crear_prenomina(db, trabajador)

        # Agregar descuento de $33.33
        resp = logged_in_admin.post('/prenomina/api/descuento',
            data=json.dumps({
                'prenomina_id': pren.id,
                'tipo': 'MANUAL',
                'concepto': 'Test precisión',
                'monto': '33.33'
            }),
            content_type='application/json'
        )
        data = resp.get_json()
        assert data['success'] is True

        db.session.refresh(pren)
        # total_deducciones = 123.45 + 67.89 + 0 + 0 + 33.33 = 224.67
        assert Decimal(str(pren.total_deducciones)) == Decimal('224.67')
        # total_a_pagar = 1891.00 - 224.67 = 1666.33
        assert Decimal(str(pren.total_a_pagar)) == Decimal('1666.33')

    def test_deposito_y_descuento_no_dejan_centavo_fantasma(self, logged_in_admin, db, trabajador):
        """Agregar depósito de $0.10 + descuento de $0.10 debe dejar el neto igual al original."""
        pren = self._crear_prenomina(db, trabajador)
        total_original = Decimal(str(pren.total_a_pagar))

        # Agregar depósito
        logged_in_admin.post('/prenomina/api/deposito',
            data=json.dumps({
                'prenomina_id': pren.id,
                'monto': '0.10',
                'concepto': 'Ajuste'
            }),
            content_type='application/json'
        )

        # Agregar descuento del mismo monto
        logged_in_admin.post('/prenomina/api/descuento',
            data=json.dumps({
                'prenomina_id': pren.id,
                'tipo': 'MANUAL',
                'concepto': 'Contrapartida',
                'monto': '0.10'
            }),
            content_type='application/json'
        )

        db.session.refresh(pren)
        # El neto debe ser idéntico al original
        assert Decimal(str(pren.total_a_pagar)) == total_original

    def test_multiples_descuentos_fraccionarios_suman_exacto(self, logged_in_admin, db, trabajador):
        """3 descuentos de $33.33 deben sumar $99.99 (no $99.98999... ni $100.00)."""
        pren = self._crear_prenomina(db, trabajador,
            descuento_infonavit=Decimal('0'),
            ajuste_inbursa=Decimal('0'),
            total_deducciones=Decimal('0'),
            total_a_pagar=Decimal('1891.00'),
        )

        for _ in range(3):
            logged_in_admin.post('/prenomina/api/descuento',
                data=json.dumps({
                    'prenomina_id': pren.id,
                    'tipo': 'MANUAL',
                    'concepto': 'Tercio',
                    'monto': '33.33'
                }),
                content_type='application/json'
            )

        db.session.refresh(pren)
        assert Decimal(str(pren.total_deducciones)) == Decimal('99.99')
        # 1891.00 - 99.99 = 1791.01
        assert Decimal(str(pren.total_a_pagar)) == Decimal('1791.01')


class TestDecimalPrecisionProyectoTotal:
    """Verifica que el acumulado de proyecto no pierda centavos al sumar semanas."""

    def _setup_proyecto_con_semanas(self, db, trabajador, n_semanas=10, salario='1234.57'):
        """Crea un proyecto con N semanas cerradas y prenóminas aprobadas."""
        from werkzeug.security import generate_password_hash

        coord = User(
            username=f'coord_dec_{n_semanas}',
            password_hash=generate_password_hash('pass'),
            role='coordinador'
        )
        db.session.add(coord)
        db.session.flush()

        proy = Proyecto(
            numero_proyecto=f'DEC-{n_semanas}',
            nombre=f'Proyecto Decimal {n_semanas}',
            activo=True,
            coordinador_id=coord.id
        )
        proy.participantes.append(trabajador)
        db.session.add(proy)
        db.session.flush()

        base_date = date(2025, 1, 7)  # Un martes

        for i in range(n_semanas):
            f_ini = base_date + timedelta(weeks=i)
            f_fin = f_ini + timedelta(days=6)

            rep = ReporteSemanal(
                fecha_inicio_semana=f_ini,
                fecha_fin_semana=f_fin,
                proyecto_id=proy.id,
                estado='PRENOMINA_CERRADA'
            )
            db.session.add(rep)
            db.session.flush()

            reg = RegistroDiarioHoras(
                reporte_id=rep.id,
                trabajador_id=trabajador.id,
                fecha=f_ini,
                horas_productivas=8.0,
                tipo_nomina='Semanal'
            )
            db.session.add(reg)

            pren = Prenomina(
                trabajador_id=trabajador.id,
                fecha_inicio=f_ini,
                fecha_fin=f_fin,
                estado='APROBADO',
                salario_base=Decimal(salario),
                total_percepciones=Decimal(salario),
                total_deducciones=Decimal('0'),
                total_a_pagar=Decimal(salario),
                tipo_pago='TRANSFERENCIA'
            )
            db.session.add(pren)

        db.session.commit()
        return proy

    def test_acumulado_10_semanas_sin_drift(self, logged_in_admin, db, trabajador):
        """10 semanas × $1234.57 deben acumular exactamente $12345.70 (no $12345.699...)."""
        self._setup_proyecto_con_semanas(db, trabajador, n_semanas=10, salario='1234.57')

        resp = logged_in_admin.get('/proyecto-total/')
        html = resp.data.decode()

        assert resp.status_code == 200
        # 10 × 1234.57 = 12345.70 exacto
        assert '$12345.70' in html
