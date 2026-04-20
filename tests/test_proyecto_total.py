"""Tests de integración para la ruta /proyecto-total."""
import pytest
from datetime import date, timedelta
from decimal import Decimal
from app.models import (
    Proyecto, Trabajador, Prenomina, ReporteSemanal,
    RegistroDiarioHoras, User
)


class TestProyectoTotalIndex:
    """Tests para GET /proyecto-total/."""

    def test_index_sin_login_redirige(self, client):
        """Sin sesión debe redirigir al login."""
        resp = client.get('/proyecto-total/')
        assert resp.status_code in (302, 308)

    def test_index_vacio(self, logged_in_admin):
        """Con login pero sin datos, debe mostrar la página sin errores."""
        resp = logged_in_admin.get('/proyecto-total/')
        assert resp.status_code == 200
        assert 'Proyecto Total' in resp.data.decode()
        assert 'Sin datos disponibles' in resp.data.decode()

    def test_index_con_proyecto_sin_cierre(self, logged_in_admin, db, proyecto):
        """Proyecto existente pero sin reportes cerrados: no aparece en la vista."""
        resp = logged_in_admin.get('/proyecto-total/')
        assert resp.status_code == 200
        # El proyecto no debe aparecer porque no tiene prenóminas cerradas
        assert proyecto.nombre not in resp.data.decode()

    def test_index_con_datos_cerrados(self, logged_in_admin, db, trabajador):
        """Proyecto con reporte cerrado y prenómina aprobada: debe mostrar datos."""
        # Crear usuario coordinador
        from werkzeug.security import generate_password_hash
        coord = User(
            username='coord_pt',
            password_hash=generate_password_hash('pass'),
            role='coordinador'
        )
        db.session.add(coord)
        db.session.flush()

        # Crear proyecto
        proy = Proyecto(
            numero_proyecto='PT-001',
            nombre='Proyecto Test Total',
            activo=True,
            coordinador_id=coord.id
        )
        proy.participantes.append(trabajador)
        db.session.add(proy)
        db.session.flush()

        fecha_ini = date(2025, 3, 4)
        fecha_fin = date(2025, 3, 10)

        # Crear reporte semanal cerrado
        reporte = ReporteSemanal(
            fecha_inicio_semana=fecha_ini,
            fecha_fin_semana=fecha_fin,
            proyecto_id=proy.id,
            estado='PRENOMINA_CERRADA'
        )
        db.session.add(reporte)
        db.session.flush()

        # Crear registro de horas (para que el worker aparezca en el proyecto)
        reg = RegistroDiarioHoras(
            reporte_id=reporte.id,
            trabajador_id=trabajador.id,
            fecha=fecha_ini,
            horas_productivas=8.0,
            tipo_nomina='Semanal'
        )
        db.session.add(reg)
        db.session.flush()

        # Crear prenomina aprobada
        pren = Prenomina(
            trabajador_id=trabajador.id,
            fecha_inicio=fecha_ini,
            fecha_fin=fecha_fin,
            estado='APROBADO',
            salario_base=5000,
            pago_viaticos=200,
            depositos_otros=100,
            descuento_infonavit=300,
            total_percepciones=5300,
            total_deducciones=300,
            total_a_pagar=5000,
            tipo_pago='TRANSFERENCIA'
        )
        db.session.add(pren)
        db.session.commit()

        resp = logged_in_admin.get('/proyecto-total/')
        html = resp.data.decode()

        assert resp.status_code == 200
        assert 'Proyecto Test Total' in html
        assert 'PT-001' in html
        # Verify the totals are rendered
        assert '$5000.00' in html
        assert 'Acumulado' in html

    def test_multiple_semanas_acumulan(self, logged_in_admin, db, trabajador):
        """Dos semanas cerradas en el mismo proyecto deben acumular correctamente."""
        from werkzeug.security import generate_password_hash
        coord = User(
            username='coord_multi',
            password_hash=generate_password_hash('pass'),
            role='coordinador'
        )
        db.session.add(coord)
        db.session.flush()

        proy = Proyecto(
            numero_proyecto='PT-MULTI',
            nombre='Proyecto Multiweek',
            activo=True,
            coordinador_id=coord.id
        )
        proy.participantes.append(trabajador)
        db.session.add(proy)
        db.session.flush()

        # Semana 1
        f1_ini = date(2025, 3, 4)
        f1_fin = date(2025, 3, 10)
        rep1 = ReporteSemanal(
            fecha_inicio_semana=f1_ini, fecha_fin_semana=f1_fin,
            proyecto_id=proy.id, estado='PRENOMINA_CERRADA'
        )
        db.session.add(rep1)
        db.session.flush()

        reg1 = RegistroDiarioHoras(
            reporte_id=rep1.id, trabajador_id=trabajador.id,
            fecha=f1_ini, horas_productivas=8.0, tipo_nomina='Semanal'
        )
        db.session.add(reg1)

        pren1 = Prenomina(
            trabajador_id=trabajador.id, fecha_inicio=f1_ini, fecha_fin=f1_fin,
            estado='APROBADO', salario_base=3000,
            total_percepciones=3000, total_deducciones=0, total_a_pagar=3000,
            tipo_pago='TRANSFERENCIA'
        )
        db.session.add(pren1)

        # Semana 2
        f2_ini = date(2025, 3, 11)
        f2_fin = date(2025, 3, 17)
        rep2 = ReporteSemanal(
            fecha_inicio_semana=f2_ini, fecha_fin_semana=f2_fin,
            proyecto_id=proy.id, estado='PRENOMINA_CERRADA'
        )
        db.session.add(rep2)
        db.session.flush()

        reg2 = RegistroDiarioHoras(
            reporte_id=rep2.id, trabajador_id=trabajador.id,
            fecha=f2_ini, horas_productivas=8.0, tipo_nomina='Semanal'
        )
        db.session.add(reg2)

        pren2 = Prenomina(
            trabajador_id=trabajador.id, fecha_inicio=f2_ini, fecha_fin=f2_fin,
            estado='APROBADO', salario_base=4000,
            total_percepciones=4000, total_deducciones=0, total_a_pagar=4000,
            tipo_pago='TRANSFERENCIA'
        )
        db.session.add(pren2)
        db.session.commit()

        resp = logged_in_admin.get('/proyecto-total/')
        html = resp.data.decode()

        assert resp.status_code == 200
        assert 'Proyecto Multiweek' in html
        # Grand total should be 3000 + 4000 = 7000
        assert '$7000.00' in html
        # Both weeks must appear
        assert '2 semanas' in html

    def test_proyecto_sin_horas_no_aparece(self, logged_in_admin, db, trabajador):
        """Proyecto con reporte cerrado pero sin horas registradas: no muestra prenominas."""
        proy = Proyecto(
            numero_proyecto='PT-NOHRS',
            nombre='Sin Horas',
            activo=True
        )
        db.session.add(proy)
        db.session.flush()

        fecha_ini = date(2025, 4, 1)
        fecha_fin = date(2025, 4, 7)

        rep = ReporteSemanal(
            fecha_inicio_semana=fecha_ini, fecha_fin_semana=fecha_fin,
            proyecto_id=proy.id, estado='PRENOMINA_CERRADA'
        )
        db.session.add(rep)
        db.session.flush()

        # Prenomina exists but no RegistroDiarioHoras linking worker to this report
        pren = Prenomina(
            trabajador_id=trabajador.id,
            fecha_inicio=fecha_ini, fecha_fin=fecha_fin,
            estado='APROBADO', salario_base=2000,
            total_percepciones=2000, total_deducciones=0, total_a_pagar=2000,
            tipo_pago='EFECTIVO'
        )
        db.session.add(pren)
        db.session.commit()

        resp = logged_in_admin.get('/proyecto-total/')
        html = resp.data.decode()

        assert resp.status_code == 200
        # Project should not appear since no workers had hours
        assert 'Sin Horas' not in html

    def test_coordinador_no_ve_proyecto_total(self, logged_in_coordinador):
        """Coordinadores son redirigidos al intentar acceder (login_required restringe a admin)."""
        resp = logged_in_coordinador.get('/proyecto-total/')
        # login_required redirects coordinadores away
        assert resp.status_code == 302
