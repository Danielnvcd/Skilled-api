"""Tests de integración para el módulo de horas (RegistroDiarioHoras, ReporteSemanal)."""
import json
import pytest
from datetime import date, timedelta
from app.models import ReporteSemanal, RegistroDiarioHoras, Proyecto, Trabajador


class TestHorasEdgeCases:
    """Pruebas para errores humanos en registro de horas."""

    @pytest.fixture
    def proyecto_test(self, db, admin_user, trabajador):
        p = Proyecto(
            numero_proyecto='HORAS-01',
            nombre='Proyecto Horas Test',
            activo=True,
            coordinador_id=admin_user.id
        )
        p.participantes.append(trabajador)
        db.session.add(p)
        db.session.commit()
        return p

    @pytest.fixture
    def reporte_test(self, db, proyecto_test):
        # Crear reporte semanal para la semana actual
        hoy = date.today()
        inicio_semana = hoy - timedelta(days=hoy.weekday())
        fin_semana = inicio_semana + timedelta(days=6)
        
        reporte = ReporteSemanal(
            proyecto_id=proyecto_test.id,
            fecha_inicio_semana=inicio_semana,
            fecha_fin_semana=fin_semana,
            estado='BORRADOR'
        )
        db.session.add(reporte)
        db.session.commit()
        return reporte

    def test_guardar_horas_negativas(self, logged_in_admin, db, reporte_test, trabajador):
        """No debe registrar si las horas están invertidas sin lógica adecuada (e.g. salida antes que entrada)."""
        resp = logged_in_admin.post(f'/horas/guardar_registro/{reporte_test.id}',
            data={
                'trabajador_id': trabajador.id,
                'fecha': '2025-01-01',
                'hora_entrada': '18:00',
                'hora_salida': '09:00'
            },
            follow_redirects=True
        )
        # Debería lanzar error por horas inconsistentes
        html_response = resp.data.decode('utf-8')
        assert 'antes que la hora de entrada' in html_response.lower() or 'no puede ser anterior' in html_response.lower() or 'traslapan' in html_response.lower() or 'negativas' in html_response.lower()

    def test_guardar_horas_excesivas(self, logged_in_admin, db, reporte_test, trabajador):
        """No debe permitir jornadas imposibles (ej. más de 24h)"""
        # (Esto probará el parser de tiempo nativo o reglas de cruce)
        resp = logged_in_admin.post(f'/horas/guardar_registro/{reporte_test.id}',
            data={
                'trabajador_id': trabajador.id,
                'fecha': '2025-01-01',
                'hora_entrada': '25:00', # Invalida datetime format
                'hora_salida': '28:00'
            },
            follow_redirects=True
        )
        # Deberia dar 400 bad request o flash error
        html_response = resp.data.decode('utf-8')
        assert 'formato' in html_response.lower() or 'inválida' in html_response.lower() or 'error' in html_response.lower()

    def test_guardar_texto_en_horas(self, logged_in_admin, db, reporte_test, trabajador):
        """Si envía texto (form editado), no debe explotar con 500, debe manejarlo."""
        resp = logged_in_admin.post(f'/horas/guardar_registro/{reporte_test.id}',
            data={
                'trabajador_id': trabajador.id,
                'fecha': '2025-01-01',
                'hora_entrada': 'texto',
                'hora_salida': 'invalido'
            },
            follow_redirects=True
        )
        html_response = resp.data.decode('utf-8')
        assert 'formato' in html_response.lower() or 'inválida' in html_response.lower() or 'error' in html_response.lower()
        
    def test_editar_reporte_cerrado(self, logged_in_admin, db, reporte_test, trabajador):
        """No debe permitir agregar/editar registros si el reporte está CERRADO."""
        # Cerrar el reporte
        reporte_test.estado = 'CERRADO'
        db.session.commit()

        resp = logged_in_admin.post(f'/horas/guardar_registro/{reporte_test.id}',
            data={
                'trabajador_id': trabajador.id,
                'fecha': '2025-01-01',
                'hora_entrada': '09:00',
                'hora_salida': '18:00'
            },
            follow_redirects=True
        )
        
        html_response = resp.data.decode('utf-8')
        assert 'cerrado' in html_response.lower()

        # Verificar que no se creó el registro
        registro = RegistroDiarioHoras.query.filter_by(
            reporte_id=reporte_test.id, 
            trabajador_id=trabajador.id
        ).first()
        assert registro is None
