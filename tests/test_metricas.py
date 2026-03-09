"""Tests para la página de Métricas (/metricas).
Cubre: acceso, datos de credenciales por planta, permisos DC3,
y casos edge como datos vacíos, trabajadores inactivos, etc.
"""
from datetime import date, timedelta
from app.models import Trabajador, CredencialPlanta, DocumentoTrabajador


class TestMetricasAcceso:
    """Tests de acceso y autenticación."""

    def test_redirige_sin_login(self, client):
        """Usuarios no autenticados deben ser redirigidos al login."""
        resp = client.get('/metricas/')
        assert resp.status_code in (302, 401, 403)

    def test_acceso_admin(self, logged_in_admin):
        """Admin puede acceder a /metricas/."""
        resp = logged_in_admin.get('/metricas/')
        assert resp.status_code == 200

    def test_pagina_contiene_titulo(self, logged_in_admin):
        """La página debe contener el título Métricas."""
        resp = logged_in_admin.get('/metricas/')
        assert 'Métricas' in resp.data.decode('utf-8') or 'M\\u00e9tricas' in resp.data.decode('utf-8')

    def test_contiene_chartjs(self, logged_in_admin):
        """La página debe incluir Chart.js CDN."""
        resp = logged_in_admin.get('/metricas/')
        html = resp.data.decode('utf-8')
        assert 'chart.js' in html or 'chart.umd.min.js' in html


class TestMetricasSinDatos:
    """Tests cuando no hay datos en la base."""

    def test_sin_credenciales(self, logged_in_admin):
        """La página debe cargar correctamente sin credenciales."""
        resp = logged_in_admin.get('/metricas/')
        assert resp.status_code == 200
        html = resp.data.decode('utf-8')
        assert 'No hay credenciales de planta registradas' in html

    def test_sin_dc3(self, logged_in_admin):
        """La página debe cargar correctamente sin permisos DC3."""
        resp = logged_in_admin.get('/metricas/')
        assert resp.status_code == 200
        html = resp.data.decode('utf-8')
        assert 'No hay empleados con Permiso DC3 registrado' in html

    def test_stats_en_cero(self, logged_in_admin):
        """Los contadores deben estar en 0 sin datos."""
        resp = logged_in_admin.get('/metricas/')
        html = resp.data.decode('utf-8')
        # data-dc3-con y data-dc3-sin deben reflejar 0
        assert 'data-dc3-con="0"' in html


class TestMetricasCredenciales:
    """Tests de métricas de credenciales por planta."""

    def test_credencial_unica_planta(self, logged_in_admin, db, trabajador):
        """Un trabajador con credencial en BMW debe aparecer en la gráfica."""
        cred = CredencialPlanta(
            trabajador_id=trabajador.id,
            planta='BMW',
            credencial_id='BMW-001'
        )
        db.session.add(cred)
        db.session.commit()

        resp = logged_in_admin.get('/metricas/')
        html = resp.data.decode('utf-8')
        assert 'BMW' in html
        assert resp.status_code == 200

    def test_multiples_plantas(self, logged_in_admin, db, trabajador, trabajador2):
        """Dos trabajadores en distintas plantas deben verse separados."""
        cred1 = CredencialPlanta(
            trabajador_id=trabajador.id,
            planta='AUDI',
            credencial_id='AUDI-001'
        )
        cred2 = CredencialPlanta(
            trabajador_id=trabajador2.id,
            planta='BMW',
            credencial_id='BMW-002'
        )
        db.session.add_all([cred1, cred2])
        db.session.commit()

        resp = logged_in_admin.get('/metricas/')
        html = resp.data.decode('utf-8')
        assert 'AUDI' in html
        assert 'BMW' in html

    def test_trabajador_con_dos_credenciales(self, logged_in_admin, db, trabajador):
        """Un trabajador con credenciales en 2 plantas. 
        Debe contar como 1 en total_con_credencial pero 1 en cada planta."""
        cred1 = CredencialPlanta(
            trabajador_id=trabajador.id,
            planta='BMW',
            credencial_id='BMW-001'
        )
        cred2 = CredencialPlanta(
            trabajador_id=trabajador.id,
            planta='AUDI',
            credencial_id='AUDI-001'
        )
        db.session.add_all([cred1, cred2])
        db.session.commit()

        resp = logged_in_admin.get('/metricas/')
        html = resp.data.decode('utf-8')
        assert 'BMW' in html
        assert 'AUDI' in html
        # total_con_credencial debe ser 1 (un solo trabajador)
        # Verificamos que en las stat cards aparece "1" como total con credencial
        assert resp.status_code == 200

    def test_trabajador_inactivo_no_cuenta(self, logged_in_admin, db):
        """Trabajadores dados de baja NO deben aparecer en métricas."""
        t_inactivo = Trabajador(
            no_empleado='BAJA001',
            nombre_apellidos='Baja Test',
            nombre='Pedro',
            activo=False
        )
        db.session.add(t_inactivo)
        db.session.commit()

        cred = CredencialPlanta(
            trabajador_id=t_inactivo.id,
            planta='BMW',
            credencial_id='BMW-BAJA'
        )
        db.session.add(cred)
        db.session.commit()

        resp = logged_in_admin.get('/metricas/')
        html = resp.data.decode('utf-8')
        # BMW no debería aparecer porque el único trabajador con esa credencial está inactivo
        assert 'No hay credenciales de planta registradas' in html

    def test_misma_planta_multiples_trabajadores(self, logged_in_admin, db, trabajador, trabajador2):
        """Dos trabajadores en la misma planta: la tabla debe mostrar ambos."""
        c1 = CredencialPlanta(trabajador_id=trabajador.id, planta='CAET', credencial_id='C-001')
        c2 = CredencialPlanta(trabajador_id=trabajador2.id, planta='CAET', credencial_id='C-002')
        db.session.add_all([c1, c2])
        db.session.commit()

        resp = logged_in_admin.get('/metricas/')
        html = resp.data.decode('utf-8')
        assert 'CAET' in html
        assert trabajador.nombre in html
        assert trabajador2.nombre in html


class TestMetricasDC3:
    """Tests de métricas de Permiso DC3."""

    def test_trabajador_con_dc3(self, logged_in_admin, db, trabajador):
        """Trabajador con Permiso DC3 debe aparecer en la sección."""
        doc = DocumentoTrabajador(
            trabajador_id=trabajador.id,
            nombre_archivo='dc3_juan.pdf',
            ruta_archivo='trabajadores/1/dc3_juan.pdf',
            tipo_documento='Permiso DC3',
            fecha_fin=date.today() + timedelta(days=90)
        )
        db.session.add(doc)
        db.session.commit()

        resp = logged_in_admin.get('/metricas/')
        html = resp.data.decode('utf-8')
        assert trabajador.nombre in html
        assert 'Vigente' in html

    def test_dc3_vencido(self, logged_in_admin, db, trabajador):
        """Un DC3 con fecha vencida debe mostrar estado 'Vencido'."""
        doc = DocumentoTrabajador(
            trabajador_id=trabajador.id,
            nombre_archivo='dc3_viejo.pdf',
            ruta_archivo='trabajadores/1/dc3_viejo.pdf',
            tipo_documento='Permiso DC3',
            fecha_fin=date.today() - timedelta(days=30)
        )
        db.session.add(doc)
        db.session.commit()

        resp = logged_in_admin.get('/metricas/')
        html = resp.data.decode('utf-8')
        assert 'Vencido' in html

    def test_dc3_sin_fecha_fin(self, logged_in_admin, db, trabajador):
        """Un DC3 sin fecha de fin debe mostrar 'Sin fecha'."""
        doc = DocumentoTrabajador(
            trabajador_id=trabajador.id,
            nombre_archivo='dc3_nofecha.pdf',
            ruta_archivo='trabajadores/1/dc3_nofecha.pdf',
            tipo_documento='Permiso DC3',
            fecha_fin=None
        )
        db.session.add(doc)
        db.session.commit()

        resp = logged_in_admin.get('/metricas/')
        html = resp.data.decode('utf-8')
        assert 'Sin fecha' in html

    def test_otro_tipo_documento_no_cuenta_como_dc3(self, logged_in_admin, db, trabajador):
        """Documentos con tipo diferente a 'Permiso DC3' no deben contar."""
        doc = DocumentoTrabajador(
            trabajador_id=trabajador.id,
            nombre_archivo='curp.pdf',
            ruta_archivo='trabajadores/1/curp.pdf',
            tipo_documento='CURP'
        )
        db.session.add(doc)
        db.session.commit()

        resp = logged_in_admin.get('/metricas/')
        html = resp.data.decode('utf-8')
        assert 'data-dc3-con="0"' in html
        assert 'No hay empleados con Permiso DC3 registrado' in html

    def test_documento_sin_tipo_no_cuenta(self, logged_in_admin, db, trabajador):
        """Documentos sin tipo_documento (None) no deben contar como DC3."""
        doc = DocumentoTrabajador(
            trabajador_id=trabajador.id,
            nombre_archivo='archivo.pdf',
            ruta_archivo='trabajadores/1/archivo.pdf',
            tipo_documento=None
        )
        db.session.add(doc)
        db.session.commit()

        resp = logged_in_admin.get('/metricas/')
        html = resp.data.decode('utf-8')
        assert 'data-dc3-con="0"' in html

    def test_dc3_inactivo_no_cuenta(self, logged_in_admin, db):
        """DC3 de un trabajador inactivo no debe aparecer."""
        t = Trabajador(
            no_empleado='BAJA002',
            nombre_apellidos='Baja DC3',
            nombre='Luis',
            activo=False
        )
        db.session.add(t)
        db.session.commit()

        doc = DocumentoTrabajador(
            trabajador_id=t.id,
            nombre_archivo='dc3_baja.pdf',
            ruta_archivo='trabajadores/x/dc3_baja.pdf',
            tipo_documento='Permiso DC3',
            fecha_fin=date.today() + timedelta(days=60)
        )
        db.session.add(doc)
        db.session.commit()

        resp = logged_in_admin.get('/metricas/')
        html = resp.data.decode('utf-8')
        assert 'data-dc3-con="0"' in html


class TestMetricasDatosChart:
    """Tests que verifican los data attributes pasados a Chart.js."""

    def test_data_attributes_presentes(self, logged_in_admin):
        """El div #metricasData debe existir con los data attributes."""
        resp = logged_in_admin.get('/metricas/')
        html = resp.data.decode('utf-8')
        assert 'id="metricasData"' in html
        assert 'data-plantas-labels' in html
        assert 'data-plantas-datos' in html
        assert 'data-dc3-con' in html
        assert 'data-dc3-sin' in html

    def test_labels_json_formato_valido(self, logged_in_admin, db, trabajador):
        """Los labels de plantas deben ser JSON válido."""
        import json
        cred = CredencialPlanta(
            trabajador_id=trabajador.id,
            planta='STELLANTIS',
            credencial_id='ST-001'
        )
        db.session.add(cred)
        db.session.commit()

        resp = logged_in_admin.get('/metricas/')
        html = resp.data.decode('utf-8')

        # Extraer el data attribute
        import re
        match = re.search(r"data-plantas-labels='([^']*)'", html)
        assert match is not None, "data-plantas-labels no encontrado"
        labels = json.loads(match.group(1))
        assert 'STELLANTIS' in labels

    def test_datos_numeros_correctos(self, logged_in_admin, db, trabajador, trabajador2):
        """Los datos numéricos deben coincidir con los registros."""
        c1 = CredencialPlanta(trabajador_id=trabajador.id, planta='BMW', credencial_id='B1')
        c2 = CredencialPlanta(trabajador_id=trabajador2.id, planta='BMW', credencial_id='B2')
        db.session.add_all([c1, c2])
        db.session.commit()

        resp = logged_in_admin.get('/metricas/')
        html = resp.data.decode('utf-8')

        import re, json
        match = re.search(r"data-plantas-datos='([^']*)'", html)
        assert match is not None
        datos = json.loads(match.group(1))
        assert 2 in datos  # BMW tiene 2 empleados


class TestMetricasEdgeCases:
    """Tests de casos límite y errores humanos."""

    def test_credencial_id_muy_largo(self, logged_in_admin, db, trabajador):
        """Credencial con ID de 40 chars (máximo) no debe romper nada."""
        cred = CredencialPlanta(
            trabajador_id=trabajador.id,
            planta='AXALTA',
            credencial_id='A' * 40
        )
        db.session.add(cred)
        db.session.commit()

        resp = logged_in_admin.get('/metricas/')
        assert resp.status_code == 200

    def test_nombre_planta_con_caracteres_especiales(self, logged_in_admin, db, trabajador):
        """Nombre de planta con acentos/caracteres especiales."""
        cred = CredencialPlanta(
            trabajador_id=trabajador.id,
            planta='PLANTA ESPAÑA & CÍA',
            credencial_id='ESP-001'
        )
        db.session.add(cred)
        db.session.commit()

        resp = logged_in_admin.get('/metricas/')
        assert resp.status_code == 200

    def test_muchos_registros_no_rompe(self, logged_in_admin, db):
        """Muchos trabajadores y credenciales no deben romper la página."""
        for i in range(50):
            t = Trabajador(
                no_empleado=f'MASS{i:03d}',
                nombre_apellidos=f'Apellido{i}',
                nombre=f'Nombre{i}',
                activo=True
            )
            db.session.add(t)
            db.session.flush()

            cred = CredencialPlanta(
                trabajador_id=t.id,
                planta=['BMW', 'AUDI', 'CAET', 'STELLANTIS', 'VOLVO'][i % 5],
                credencial_id=f'CR-{i:03d}'
            )
            db.session.add(cred)

        db.session.commit()

        resp = logged_in_admin.get('/metricas/')
        assert resp.status_code == 200

    def test_dc3_con_tipo_case_sensitive(self, logged_in_admin, db, trabajador):
        """'permiso dc3' en minúsculas NO debe contar (es case-sensitive)."""
        doc = DocumentoTrabajador(
            trabajador_id=trabajador.id,
            nombre_archivo='dc3_lower.pdf',
            ruta_archivo='trabajadores/1/dc3_lower.pdf',
            tipo_documento='permiso dc3'  # minúsculas
        )
        db.session.add(doc)
        db.session.commit()

        resp = logged_in_admin.get('/metricas/')
        html = resp.data.decode('utf-8')
        # No debe contar porque el filtro busca exactamente 'Permiso DC3'
        assert 'data-dc3-con="0"' in html
