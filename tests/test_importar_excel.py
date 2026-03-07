import pytest
import pandas as pd
import io
from app.models import Trabajador

def _create_excel_stream(df):
    """Crea un archivo Excel en memoria a partir de un DataFrame."""
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Empleados')
    output.seek(0)
    return output

class TestImportarExcel:
    """Pruebas de la funcionalidad de Importar Trabajadores vía Excel."""

    def test_importar_acceso_denegado_si_no_logueado(self, client):
        """Verificar que un usuario no logueado redirige a login."""
        response = client.get('/trabajadores/importar')
        assert response.status_code == 302
        assert '/login' in response.headers.get('Location', '')

    def test_importar_get_view(self, logged_in_admin):
        """Verificar que carga la vista del formulario."""
        response = logged_in_admin.get('/trabajadores/importar')
        assert response.status_code == 200
        assert b'Importar Empleados' in response.data

    def test_procesar_importacion_sin_archivo(self, logged_in_admin):
        """Si no se sube un archivo, debe reportarse el error y no crashear."""
        response = logged_in_admin.post('/trabajadores/procesar_importacion', data={}, follow_redirects=True)
        assert response.status_code == 200
        assert b'No se seleccion\xc3\xb3 ning\xc3\xban archivo' in response.data

    def test_importacion_exitosa(self, logged_in_admin, db):
        """Prueba de camino feliz subiendo un excel válido con 2 trabajadores."""
        data = {
            'No. Empleado': ['IMP-001', 'IMP-002'],
            'Nombre(s)': ['Juan Prueba', 'Diana Muestra'],
            'Apellidos': ['Lopez', 'Garcia'],
            'Area': ['Admin', 'Proyectos'],
            'Fecha Ingreso (YYYY-MM-DD)': ['2026-01-01', '2026-02-15'],
            'Salario Real Pactado por Semana': [2500.50, 3100.00]
        }
        df = pd.DataFrame(data)
        excel_file = _create_excel_stream(df)
        
        response = logged_in_admin.post(
            '/trabajadores/procesar_importacion',
            data={'archivo_excel': (excel_file, 'test.xlsx')},
            content_type='multipart/form-data',
            follow_redirects=True
        )
        
        assert response.status_code == 200
        assert b'Se importaron 2 empleados correctamente' in response.data
        
        # Verificar BD
        t1 = Trabajador.query.filter_by(no_empleado='IMP-001').first()
        t2 = Trabajador.query.filter_by(no_empleado='IMP-002').first()
        
        assert t1 is not None
        assert t1.nombre == 'Juan Prueba'
        assert float(t1.salario_real_pactado_x_sem) == 2500.50
        
        assert t2 is not None
        assert t2.area == 'Proyectos'
        assert str(t2.fecha_ingreso) == '2026-02-15'

    def test_importacion_fechas_nombres_invalidos(self, logged_in_admin, db):
        """Verificar el comportamiento cuando la fecha es inválida o el formato es malo."""
        data = {
            'No. Empleado': ['IMP-ERR-01'],
            'Nombre(s)': ['Error Prueba'],
            'Fecha Ingreso (YYYY-MM-DD)': ['Fecha-Mala'],
            'Salario Base Pactado': ['Sueldo en letras']
        }
        df = pd.DataFrame(data)
        excel_file = _create_excel_stream(df)
        
        response = logged_in_admin.post(
            '/trabajadores/procesar_importacion',
            data={'archivo_excel': (excel_file, 'test.xlsx')},
            content_type='multipart/form-data',
            follow_redirects=True
        )
        
        assert response.status_code == 200
        # No se insertó porque la fecha detuvo el procesamiento de la fila.
        assert b'Fecha-Mala' in response.data
        
        # Verificamos que NO se agregó
        t1 = Trabajador.query.filter_by(no_empleado='IMP-ERR-01').first()
        assert t1 is None

    def test_importacion_registro_duplicado(self, logged_in_admin, db, trabajador):
        """Verificar que no se duplique un número de empleado (fixture ya tiene 'T001')."""
        data = {
            'No. Empleado': ['T001', 'IMP-OK'],
            'Nombre(s)': ['Intento Duplicado', 'Si Pasa'],
            'Apellidos': ['...', '...']
        }
        df = pd.DataFrame(data)
        excel_file = _create_excel_stream(df)
        
        response = logged_in_admin.post(
            '/trabajadores/procesar_importacion',
            data={'archivo_excel': (excel_file, 'test.xlsx')},
            content_type='multipart/form-data',
            follow_redirects=True
        )
        
        assert response.status_code == 200
        # Menciona el éxito del usuario que sí pasó
        assert b'Se importaron 1 empleados correctamente' in response.data
        # Menciona el error del usuario T001
        assert b'El empleado T001 ya existe' in response.data
        
        t_ok = Trabajador.query.filter_by(no_empleado='IMP-OK').first()
        assert t_ok is not None
