"""Tests exhaustivos para el módulo de Ajuste Inbursa.
Cubre: CRUD periodos con trabajadores/meta, CRUD descuentos, validaciones, integración con prenómina.
"""
import pytest
from decimal import Decimal
from datetime import date, time
from app.models import (
    AjustePeriodo, AjusteTrabajadorPeriodo, AjusteDescuento, Trabajador,
    ReporteSemanal, RegistroDiarioHoras
)


class TestAjustePeriodoCRUD:

    def test_crear_periodo_con_trabajadores(self, logged_in_admin, db, trabajador):
        resp = logged_in_admin.post('/ajustes/crear_periodo', data={
            'nombre': 'Marzo 2026',
            'fecha_inicio': '2026-03-01',
            'fecha_fin': '2026-03-31',
            'trabajador_ids': [str(trabajador.id)],
            'montos_meta': ['10000.00'],
        }, follow_redirects=True)
        assert resp.status_code == 200
        periodo = AjustePeriodo.query.first()
        assert periodo is not None
        assert periodo.estado == 'ABIERTO'
        tp = AjusteTrabajadorPeriodo.query.first()
        assert tp is not None
        assert tp.trabajador_id == trabajador.id
        assert float(tp.monto_meta) == 10000.00

    def test_crear_periodo_sin_trabajadores(self, logged_in_admin, db):
        logged_in_admin.post('/ajustes/crear_periodo', data={
            'nombre': 'Vacio',
            'fecha_inicio': '2026-03-01',
            'fecha_fin': '2026-03-31',
        }, follow_redirects=True)
        assert AjustePeriodo.query.count() == 0

    def test_crear_periodo_fecha_invertida(self, logged_in_admin, db, trabajador):
        logged_in_admin.post('/ajustes/crear_periodo', data={
            'nombre': 'Malo',
            'fecha_inicio': '2026-03-31',
            'fecha_fin': '2026-03-01',
            'trabajador_ids': [str(trabajador.id)],
            'montos_meta': ['5000'],
        }, follow_redirects=True)
        assert AjustePeriodo.query.count() == 0

    def test_cerrar_periodo(self, logged_in_admin, db):
        p = AjustePeriodo(nombre='Test', fecha_inicio=date(2026, 3, 1), fecha_fin=date(2026, 3, 31))
        db.session.add(p)
        db.session.commit()
        logged_in_admin.post(f'/ajustes/cerrar_periodo/{p.id}', follow_redirects=True)
        db.session.refresh(p)
        assert p.estado == 'CERRADO'

    def test_cerrar_periodo_ya_cerrado_no_falla(self, logged_in_admin, db):
        p = AjustePeriodo(nombre='Test', fecha_inicio=date(2026, 3, 1), fecha_fin=date(2026, 3, 31), estado='CERRADO')
        db.session.add(p)
        db.session.commit()
        logged_in_admin.post(f'/ajustes/cerrar_periodo/{p.id}', follow_redirects=True)
        db.session.refresh(p)
        assert p.estado == 'CERRADO'

    def test_bloquear_periodo_traslape_con_abierto(self, logged_in_admin, db, trabajador):
        """No se puede crear un periodo si ya hay uno ABIERTO con fechas que se traslapan."""
        existente = AjustePeriodo(nombre='Existente', fecha_inicio=date(2026, 3, 1), fecha_fin=date(2026, 3, 31))
        db.session.add(existente)
        db.session.commit()
        logged_in_admin.post('/ajustes/crear_periodo', data={
            'nombre': 'Nuevo',
            'fecha_inicio': '2026-03-15',
            'fecha_fin': '2026-04-15',
            'trabajador_ids': [str(trabajador.id)],
            'montos_meta': ['5000'],
        }, follow_redirects=True)
        assert AjustePeriodo.query.count() == 1  # Solo el existente

    def test_bloquear_periodo_traslape_con_cerrado(self, logged_in_admin, db, trabajador):
        """No se puede crear un periodo si ya hay uno CERRADO con fechas que se traslapan."""
        existente = AjustePeriodo(nombre='Cerrado', fecha_inicio=date(2026, 3, 1), fecha_fin=date(2026, 3, 31), estado='CERRADO')
        db.session.add(existente)
        db.session.commit()
        logged_in_admin.post('/ajustes/crear_periodo', data={
            'nombre': 'Nuevo',
            'fecha_inicio': '2026-03-20',
            'fecha_fin': '2026-04-20',
            'trabajador_ids': [str(trabajador.id)],
            'montos_meta': ['5000'],
        }, follow_redirects=True)
        assert AjustePeriodo.query.count() == 1

    def test_permitir_periodo_sin_traslape(self, logged_in_admin, db, trabajador):
        """Se puede crear un periodo con fechas que NO se traslapan."""
        existente = AjustePeriodo(nombre='Enero', fecha_inicio=date(2026, 1, 1), fecha_fin=date(2026, 1, 31))
        db.session.add(existente)
        db.session.commit()
        logged_in_admin.post('/ajustes/crear_periodo', data={
            'nombre': 'Marzo',
            'fecha_inicio': '2026-03-01',
            'fecha_fin': '2026-03-31',
            'trabajador_ids': [str(trabajador.id)],
            'montos_meta': ['8000'],
        }, follow_redirects=True)
        assert AjustePeriodo.query.count() == 2


class TestAjusteDescuentoCRUD:

    @pytest.fixture
    def periodo_con_trabajador(self, db, trabajador):
        p = AjustePeriodo(nombre='Marzo', fecha_inicio=date(2026, 3, 1), fecha_fin=date(2026, 3, 31))
        db.session.add(p)
        db.session.flush()
        tp = AjusteTrabajadorPeriodo(periodo_id=p.id, trabajador_id=trabajador.id, monto_meta=Decimal('10000'))
        db.session.add(tp)
        db.session.commit()
        return p

    def test_agregar_descuento(self, logged_in_admin, db, trabajador, periodo_con_trabajador):
        logged_in_admin.post(f'/ajustes/agregar_descuento/{periodo_con_trabajador.id}', data={
            'trabajador_id': trabajador.id,
            'monto': '5000.00',
            'fecha_descuento': '2026-03-10',
            'notas': 'Semana 2',
        }, follow_redirects=True)
        d = AjusteDescuento.query.first()
        assert d is not None
        assert float(d.monto) == 5000.00

    def test_agregar_descuento_monto_cero(self, logged_in_admin, db, trabajador, periodo_con_trabajador):
        logged_in_admin.post(f'/ajustes/agregar_descuento/{periodo_con_trabajador.id}', data={
            'trabajador_id': trabajador.id, 'monto': '0', 'fecha_descuento': '2026-03-10',
        }, follow_redirects=True)
        assert AjusteDescuento.query.count() == 0

    def test_agregar_descuento_monto_negativo(self, logged_in_admin, db, trabajador, periodo_con_trabajador):
        logged_in_admin.post(f'/ajustes/agregar_descuento/{periodo_con_trabajador.id}', data={
            'trabajador_id': trabajador.id, 'monto': '-500', 'fecha_descuento': '2026-03-10',
        }, follow_redirects=True)
        assert AjusteDescuento.query.count() == 0

    def test_agregar_descuento_periodo_cerrado(self, logged_in_admin, db, trabajador, periodo_con_trabajador):
        periodo_con_trabajador.estado = 'CERRADO'
        db.session.commit()
        logged_in_admin.post(f'/ajustes/agregar_descuento/{periodo_con_trabajador.id}', data={
            'trabajador_id': trabajador.id, 'monto': '1000', 'fecha_descuento': '2026-03-10',
        }, follow_redirects=True)
        assert AjusteDescuento.query.count() == 0

    def test_agregar_descuento_trabajador_no_en_periodo(self, logged_in_admin, db, trabajador2, periodo_con_trabajador):
        """No se puede agregar descuento a un trabajador no asignado al periodo."""
        logged_in_admin.post(f'/ajustes/agregar_descuento/{periodo_con_trabajador.id}', data={
            'trabajador_id': trabajador2.id, 'monto': '1000', 'fecha_descuento': '2026-03-10',
        }, follow_redirects=True)
        assert AjusteDescuento.query.count() == 0

    def test_eliminar_descuento(self, logged_in_admin, db, trabajador, periodo_con_trabajador):
        d = AjusteDescuento(periodo_id=periodo_con_trabajador.id, trabajador_id=trabajador.id,
                            monto=Decimal('3000'), fecha_descuento=date(2026, 3, 10))
        db.session.add(d)
        db.session.commit()
        logged_in_admin.post(f'/ajustes/eliminar_descuento/{d.id}', follow_redirects=True)
        assert AjusteDescuento.query.count() == 0

    def test_eliminar_descuento_periodo_cerrado(self, logged_in_admin, db, trabajador, periodo_con_trabajador):
        d = AjusteDescuento(periodo_id=periodo_con_trabajador.id, trabajador_id=trabajador.id,
                            monto=Decimal('3000'), fecha_descuento=date(2026, 3, 10))
        db.session.add(d)
        periodo_con_trabajador.estado = 'CERRADO'
        db.session.commit()
        logged_in_admin.post(f'/ajustes/eliminar_descuento/{d.id}', follow_redirects=True)
        assert AjusteDescuento.query.count() == 1

    def test_eliminar_descuento_cobrado(self, logged_in_admin, db, trabajador, periodo_con_trabajador):
        d = AjusteDescuento(
            periodo_id=periodo_con_trabajador.id, 
            trabajador_id=trabajador.id,
            monto=Decimal('3000'), 
            fecha_descuento=date(2026, 3, 10),
            cobrado=True
        )
        db.session.add(d)
        db.session.commit()
        resp = logged_in_admin.post(f'/ajustes/eliminar_descuento/{d.id}', follow_redirects=True)
        html = resp.data.decode('utf-8').lower()
        assert 'cobrado' in html and 'prenómina' in html
        assert AjusteDescuento.query.count() == 1


class TestAjusteInbursaEnPrenomina:

    def test_prenomina_usa_descuentos_dinamicos(self, db, trabajador, proyecto):
        from app.routes.prenomina import calcular_preview_prenomina
        trabajador.ajuste_inbursa = Decimal('500')
        db.session.commit()

        p = AjustePeriodo(nombre='Mar', fecha_inicio=date(2026, 3, 1), fecha_fin=date(2026, 3, 31))
        db.session.add(p)
        db.session.flush()
        d1 = AjusteDescuento(periodo_id=p.id, trabajador_id=trabajador.id,
                             monto=Decimal('3000'), fecha_descuento=date(2026, 3, 3))
        d2 = AjusteDescuento(periodo_id=p.id, trabajador_id=trabajador.id,
                             monto=Decimal('2000'), fecha_descuento=date(2026, 3, 5))
        db.session.add_all([d1, d2])
        db.session.commit()

        reporte = ReporteSemanal(proyecto_id=proyecto.id, fecha_inicio_semana=date(2026, 3, 2),
                                 fecha_fin_semana=date(2026, 3, 8), estado='TERMINADO')
        db.session.add(reporte)
        db.session.commit()
        reg = RegistroDiarioHoras(reporte_id=reporte.id, trabajador_id=trabajador.id,
            fecha=date(2026, 3, 3), hora_entrada=time(8, 0), hora_salida=time(18, 0),
            tomo_comida=True, horas_productivas=9.5)
        db.session.add(reg)
        db.session.commit()

        preview = calcular_preview_prenomina(date(2026, 3, 2), [reporte])
        assert float(preview[0].ajuste_inbursa) == 5000.0

    def test_prenomina_sin_descuentos_usa_fijo(self, db, trabajador, proyecto):
        from app.routes.prenomina import calcular_preview_prenomina
        trabajador.ajuste_inbursa = Decimal('800')
        db.session.commit()

        reporte = ReporteSemanal(proyecto_id=proyecto.id, fecha_inicio_semana=date(2026, 4, 7),
                                 fecha_fin_semana=date(2026, 4, 13), estado='TERMINADO')
        db.session.add(reporte)
        db.session.commit()
        reg = RegistroDiarioHoras(reporte_id=reporte.id, trabajador_id=trabajador.id,
            fecha=date(2026, 4, 8), hora_entrada=time(8, 0), hora_salida=time(18, 0),
            tomo_comida=True, horas_productivas=9.5)
        db.session.add(reg)
        db.session.commit()

        preview = calcular_preview_prenomina(date(2026, 4, 7), [reporte])
        assert float(preview[0].ajuste_inbursa) == 800.0

    def test_descuento_fuera_de_rango_no_afecta(self, db, trabajador, proyecto):
        from app.routes.prenomina import calcular_preview_prenomina
        trabajador.ajuste_inbursa = Decimal('100')
        db.session.commit()

        p = AjustePeriodo(nombre='Abr', fecha_inicio=date(2026, 4, 1), fecha_fin=date(2026, 4, 30))
        db.session.add(p)
        db.session.flush()
        d = AjusteDescuento(periodo_id=p.id, trabajador_id=trabajador.id,
                            monto=Decimal('9999'), fecha_descuento=date(2026, 4, 20))
        db.session.add(d)
        db.session.commit()

        reporte = ReporteSemanal(proyecto_id=proyecto.id, fecha_inicio_semana=date(2026, 4, 7),
                                 fecha_fin_semana=date(2026, 4, 13), estado='TERMINADO')
        db.session.add(reporte)
        db.session.commit()
        reg = RegistroDiarioHoras(reporte_id=reporte.id, trabajador_id=trabajador.id,
            fecha=date(2026, 4, 8), hora_entrada=time(8, 0), hora_salida=time(18, 0),
            tomo_comida=True, horas_productivas=9.5)
        db.session.add(reg)
        db.session.commit()

        preview = calcular_preview_prenomina(date(2026, 4, 7), [reporte])
        assert float(preview[0].ajuste_inbursa) == 100.0

    def test_ajuste_incluido_en_total_deducciones(self, db, trabajador, proyecto):
        from app.routes.prenomina import calcular_preview_prenomina
        trabajador.ajuste_inbursa = Decimal('0')
        trabajador.infonavit = Decimal('0')
        db.session.commit()

        p = AjustePeriodo(nombre='May', fecha_inicio=date(2026, 5, 1), fecha_fin=date(2026, 5, 31))
        db.session.add(p)
        db.session.flush()
        d = AjusteDescuento(periodo_id=p.id, trabajador_id=trabajador.id,
                            monto=Decimal('1500'), fecha_descuento=date(2026, 5, 5))
        db.session.add(d)
        db.session.commit()

        reporte = ReporteSemanal(proyecto_id=proyecto.id, fecha_inicio_semana=date(2026, 5, 4),
                                 fecha_fin_semana=date(2026, 5, 10), estado='TERMINADO')
        db.session.add(reporte)
        db.session.commit()
        reg = RegistroDiarioHoras(reporte_id=reporte.id, trabajador_id=trabajador.id,
            fecha=date(2026, 5, 5), hora_entrada=time(8, 0), hora_salida=time(18, 0),
            tomo_comida=True, horas_productivas=9.5)
        db.session.add(reg)
        db.session.commit()

        preview = calcular_preview_prenomina(date(2026, 5, 4), [reporte])
        assert float(preview[0].ajuste_inbursa) == 1500.0
        assert float(preview[0].total_deducciones) >= 1500.0

    def test_descuentos_aislados_por_trabajador(self, db, trabajador, proyecto):
        from app.routes.prenomina import calcular_preview_prenomina

        t2 = Trabajador(no_empleado='999', nombre='OTRO', nombre_apellidos='OTRO TEST',
            salario_real_pactado_x_sem=Decimal('5000'), tipo_nomina='Semanal',
            activo=True, hr_extra=0, infonavit=0, ajuste_inbursa=0, viaticos=0, pago_dia_festivo=0)
        db.session.add(t2)
        db.session.commit()
        proyecto.participantes.append(t2)
        db.session.commit()

        p = AjustePeriodo(nombre='Jun', fecha_inicio=date(2026, 6, 1), fecha_fin=date(2026, 6, 30))
        db.session.add(p)
        db.session.flush()
        d1 = AjusteDescuento(periodo_id=p.id, trabajador_id=trabajador.id,
                             monto=Decimal('7000'), fecha_descuento=date(2026, 6, 3))
        d2 = AjusteDescuento(periodo_id=p.id, trabajador_id=t2.id,
                             monto=Decimal('4000'), fecha_descuento=date(2026, 6, 3))
        db.session.add_all([d1, d2])
        db.session.commit()

        reporte = ReporteSemanal(proyecto_id=proyecto.id, fecha_inicio_semana=date(2026, 6, 1),
                                 fecha_fin_semana=date(2026, 6, 7), estado='TERMINADO')
        db.session.add(reporte)
        db.session.commit()
        for t in [trabajador, t2]:
            reg = RegistroDiarioHoras(reporte_id=reporte.id, trabajador_id=t.id,
                fecha=date(2026, 6, 3), hora_entrada=time(8, 0), hora_salida=time(18, 0),
                tomo_comida=True, horas_productivas=9.5)
            db.session.add(reg)
        db.session.commit()

        preview = calcular_preview_prenomina(date(2026, 6, 1), [reporte])
        by_id = {p.trabajador_id: p for p in preview}
        assert float(by_id[trabajador.id].ajuste_inbursa) == 7000.0
        assert float(by_id[t2.id].ajuste_inbursa) == 4000.0
