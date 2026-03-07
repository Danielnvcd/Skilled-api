"""Tests unitarios para la función _parse_date de trabajadores."""
from datetime import date


class TestParseDate:
    """Tests para el helper _parse_date."""

    def test_fecha_valida(self):
        from app.routes.trabajadores import _parse_date
        assert _parse_date('2025-03-15') == date(2025, 3, 15)

    def test_fecha_vacia(self):
        from app.routes.trabajadores import _parse_date
        assert _parse_date('') is None

    def test_fecha_none(self):
        from app.routes.trabajadores import _parse_date
        assert _parse_date(None) is None

    def test_formato_invalido(self):
        from app.routes.trabajadores import _parse_date
        assert _parse_date('15/03/2025') is None

    def test_texto_aleatorio(self):
        from app.routes.trabajadores import _parse_date
        assert _parse_date('no-es-fecha') is None

    def test_fecha_dia_31(self):
        from app.routes.trabajadores import _parse_date
        assert _parse_date('2025-01-31') == date(2025, 1, 31)

    def test_fecha_bisiesto(self):
        from app.routes.trabajadores import _parse_date
        assert _parse_date('2024-02-29') == date(2024, 2, 29)

    def test_fecha_no_bisiesto(self):
        """29 de feb en año no bisiesto → None."""
        from app.routes.trabajadores import _parse_date
        assert _parse_date('2025-02-29') is None

import pandas as pd
import io

class TestImportacionExcel:
    """Tests para simular errores humanos y validación de importación de Excel."""
    
    def test_importacion_exitosa(self, logged_in_admin, db):
        df = pd.DataFrame([{
            'No. Empleado': 'IMP001',
            'Nombre(s)': 'Juan',
            'Apellidos': 'Perez',
            'Salario Real Pactado por Semana': 1500.50,
            'Fecha Ingreso (YYYY-MM-DD)': '2025-01-01',
            'Tipo de Nomina (Semanal/Por hora/Cuadrado)': 'Semanal'
        }])
        output = io.BytesIO()
        df.to_excel(output, index=False)
        output.seek(0)
        
        response = logged_in_admin.post('/trabajadores/procesar_importacion', data={
            'archivo_excel': (output, 'test.xlsx')
        }, follow_redirects=True)
        
        assert response.status_code == 200
        assert b'Se importaron 1 empleados correctamente' in response.data
        
        from app.models import Trabajador
        t = Trabajador.query.filter_by(no_empleado='IMP001').first()
        assert t is not None
        assert t.nombre == 'Juan'
        assert t.salario_real_pactado_x_sem == 1500.50

    def test_importacion_falta_no_empleado(self, logged_in_admin, db):
        df = pd.DataFrame([{
            'No. Empleado': '',
            'Nombre(s)': 'Sin',
            'Apellidos': 'Numero'
        }])
        output = io.BytesIO()
        df.to_excel(output, index=False)
        output.seek(0)
        
        response = logged_in_admin.post('/trabajadores/procesar_importacion', data={
            'archivo_excel': (output, 'test.xlsx')
        }, follow_redirects=True)
        
        assert b'Errores encontrados' in response.data
        assert b'Falta No. Empleado' in response.data

    def test_importacion_tipos_invalidos(self, logged_in_admin, db):
        # Simulamos que se captura texto donde va un número y fecha inválida
        df = pd.DataFrame([{
            'No. Empleado': 'IMP002',
            'Nombre(s)': 'Error',
            'Apellidos': 'Tipo',
            'Salario Real Pactado por Semana': 'Mil quinientos',
            'Fecha Ingreso (YYYY-MM-DD)': 'ayer'
        }])
        output = io.BytesIO()
        df.to_excel(output, index=False)
        output.seek(0)
        
        response = logged_in_admin.post('/trabajadores/procesar_importacion', data={
            'archivo_excel': (output, 'test.xlsx')
        }, follow_redirects=True)
        
        # Debe fallar fila completa y no insertarla
        assert b'Errores encontrados' in response.data
        assert b'no es un' in response.data # no es un número válido
        assert b'no tiene el formato correcto' in response.data
        
        from app.models import Trabajador
        # Nos aseguramos de que no guardó al empleado IMP002 con tipos corruptos
        t = Trabajador.query.filter_by(no_empleado='IMP002').first()
        assert t is None

    def test_importacion_empleado_duplicado(self, logged_in_admin, db):
        from app.models import Trabajador
        t_existente = Trabajador(no_empleado='IMP003', nombre='Existente', nombre_apellidos='Falso')
        db.session.add(t_existente)
        db.session.commit()

        df = pd.DataFrame([{
            'No. Empleado': 'IMP003',
            'Nombre(s)': 'Copia',
            'Apellidos': 'Fake'
        }])
        output = io.BytesIO()
        df.to_excel(output, index=False)
        output.seek(0)
        
        response = logged_in_admin.post('/trabajadores/procesar_importacion', data={
            'archivo_excel': (output, 'test.xlsx')
        }, follow_redirects=True)
        
        assert b'ya existe' in response.data
        
    def test_importacion_archivo_invalido(self, logged_in_admin):
        # ¿Qué pasa si sube un txt o nada?
        response = logged_in_admin.post('/trabajadores/procesar_importacion', data={
            'archivo_excel': (io.BytesIO(b'dummy content'), 'test.txt')
        }, follow_redirects=True)
        
        assert b'Formato no v' in response.data # Formato no válido

    def test_importacion_mixta(self, logged_in_admin, db):
        # 3 empleados: 1 válido, 1 inválido (sin ID), 1 válido
        df = pd.DataFrame([
            {
                'No. Empleado': 'MIX001',
                'Nombre(s)': 'Valido',
                'Apellidos': 'Uno',
                'Salario Real Pactado por Semana': 1000.0,
            },
            {
                'No. Empleado': '', # Invalido
                'Nombre(s)': 'Invalido',
                'Apellidos': 'Falla',
            },
            {
                'No. Empleado': 'MIX003',
                'Nombre(s)': 'Valido',
                'Apellidos': 'Tres',
                'Salario Real Pactado por Semana': 2000.0,
            }
        ])
        output = io.BytesIO()
        df.to_excel(output, index=False)
        output.seek(0)
        
        response = logged_in_admin.post('/trabajadores/procesar_importacion', data={
            'archivo_excel': (output, 'test_mixto.xlsx')
        }, follow_redirects=True)
        
        assert response.status_code == 200
        
        # Deberíamos tener un mensaje de éxito (2 importados) y uno de error (1 falló)
        content = response.data.decode('utf-8')
        assert 'Se importaron 2 empleados correctamente' in content
        assert 'Errores encontrados' in content
        assert 'Falta No. Empleado' in content
        
        # Verificar exactitud en base de datos
        from app.models import Trabajador
        t1 = Trabajador.query.filter_by(no_empleado='MIX001').first()
        assert t1 is not None
        assert t1.nombre == 'Valido'
        
        t3 = Trabajador.query.filter_by(no_empleado='MIX003').first()
        assert t3 is not None
        assert t3.nombre == 'Valido'

class TestCrearTrabajadorEdgeCases:
    """Pruebas para capturar errores humanos en la creación de trabajadores mediante el formulario web."""

    def test_crear_trabajador_salario_negativo(self, logged_in_admin, db):
        resp = logged_in_admin.post('/trabajadores/agregar',
            data={
                'no_empleado': 'ERR-001',
                'nombre_apellidos': 'Falso',
                'nombre': 'Falso',
                'salario_real_pactado_x_sem': -1000,
                'tipo_nomina': 'Semanal'
            },
            follow_redirects=True
        )
        html_response = resp.data.decode('utf-8')
        assert 'negativo' in html_response.lower() or 'inválido' in html_response.lower()
        
        from app.models import Trabajador
        t = Trabajador.query.filter_by(no_empleado='ERR-001').first()
        # Podría validar que no lo guardó o lo guardó con validación extra. Ideal no guardarlo.
        # Si se guardó porque el controlador no bloquea, este assert fallaría y nos avisaría del bug
        if t:
            assert t.salario_real_pactado_x_sem >= 0

    def test_crear_trabajador_sin_no_empleado(self, logged_in_admin, db):
        resp = logged_in_admin.post('/trabajadores/agregar',
            data={
                'no_empleado': '',
                'nombre_apellidos': 'Falso',
                'nombre': 'Falso',
                'salario_real_pactado_x_sem': 1000,
                'tipo_nomina': 'Semanal'
            },
            follow_redirects=True
        )
        html_response = resp.data.decode('utf-8')
        assert 'obligatorio' in html_response.lower() or 'requerido' in html_response.lower()

        from app.models import Trabajador
        t = Trabajador.query.filter_by(nombre='Falso').first()
        assert t is None

    def test_crear_trabajador_duplicado(self, logged_in_admin, db):
        # Crear 1 manual
        from app.models import Trabajador
        t1 = Trabajador(
            no_empleado='DUP-002',
            nombre_apellidos='Original',
            nombre='Original',
            salario_real_pactado_x_sem=1000,
            tipo_nomina='Semanal'
        )
        db.session.add(t1)
        db.session.commit()

        # Intentar crear 2 desde forms
        resp = logged_in_admin.post('/trabajadores/agregar',
            data={
                'no_empleado': 'DUP-002',
                'nombre_apellidos': 'Copia',
                'nombre': 'Copia',
                'salario_real_pactado_x_sem': 1000,
                'tipo_nomina': 'Semanal'
            },
            follow_redirects=True
        )
        html_response = resp.data.decode('utf-8')
        assert 'ya existe' in html_response.lower() or 'duplicado' in html_response.lower()

        t_dup = Trabajador.query.filter_by(nombre='Copia').first()
        assert t_dup is None


