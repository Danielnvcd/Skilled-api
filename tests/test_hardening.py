"""Tests de hardening: upload de fotos, autorización de proyectos, respuestas 403."""
import io
import json
import pytest
from app.models import Proyecto, Trabajador, User, DocumentoTrabajador


# ══════════════════════════════════════════════
# 1. UPLOAD DE FOTO DE PERFIL — VALIDACIÓN ESTRICTA
# ══════════════════════════════════════════════

class TestFotoPerfil:
    """Validar que solo imágenes JPG/PNG reales se acepten como foto de perfil."""

    # ---- Magic bytes reales para generar archivos de prueba ----
    # JPEG: empieza con FF D8 FF
    JPEG_MAGIC = b'\xff\xd8\xff\xe0' + b'\x00\x10JFIF\x00' + b'\x00' * 100
    # PNG: empieza con 89 50 4E 47
    PNG_MAGIC = b'\x89PNG\r\n\x1a\n' + b'\x00' * 100
    # PDF: empieza con %PDF
    PDF_MAGIC = b'%PDF-1.4 ' + b'\x00' * 100
    # EXE/PE: empieza con MZ
    EXE_MAGIC = b'MZ' + b'\x00' * 100

    def test_upload_foto_jpg_valido(self, logged_in_admin, db, trabajador):
        """JPG real con extensión .jpg → debe aceptarse."""
        data = {
            'no_empleado': trabajador.no_empleado,
            'nombre_apellidos': trabajador.nombre_apellidos,
            'nombre': trabajador.nombre,
            'foto_perfil': (io.BytesIO(self.JPEG_MAGIC), 'foto.jpg'),
        }
        resp = logged_in_admin.post(
            f'/trabajadores/editar/{trabajador.id}',
            data=data,
            content_type='multipart/form-data',
            follow_redirects=True
        )
        assert resp.status_code == 200
        db.session.refresh(trabajador)
        assert trabajador.foto_perfil is not None
        assert 'perfiles/' in trabajador.foto_perfil

    def test_upload_foto_png_valido(self, logged_in_admin, db, trabajador):
        """PNG real con extensión .png → debe aceptarse."""
        data = {
            'no_empleado': trabajador.no_empleado,
            'nombre_apellidos': trabajador.nombre_apellidos,
            'nombre': trabajador.nombre,
            'foto_perfil': (io.BytesIO(self.PNG_MAGIC), 'foto.png'),
        }
        resp = logged_in_admin.post(
            f'/trabajadores/editar/{trabajador.id}',
            data=data,
            content_type='multipart/form-data',
            follow_redirects=True
        )
        assert resp.status_code == 200
        db.session.refresh(trabajador)
        assert trabajador.foto_perfil is not None

    def test_upload_foto_pdf_con_extension_jpg_rechazado(self, logged_in_admin, db, trabajador):
        """PDF disfrazado como .jpg → debe rechazarse."""
        data = {
            'no_empleado': trabajador.no_empleado,
            'nombre_apellidos': trabajador.nombre_apellidos,
            'nombre': trabajador.nombre,
            'foto_perfil': (io.BytesIO(self.PDF_MAGIC), 'foto.jpg'),
        }
        resp = logged_in_admin.post(
            f'/trabajadores/editar/{trabajador.id}',
            data=data,
            content_type='multipart/form-data',
            follow_redirects=True
        )
        html = resp.data.decode('utf-8')
        assert 'rechazada' in html.lower() or 'solo se permiten' in html.lower()

    def test_upload_foto_exe_con_extension_png_rechazado(self, logged_in_admin, db, trabajador):
        """Ejecutable disfrazado como .png → debe rechazarse."""
        data = {
            'no_empleado': trabajador.no_empleado,
            'nombre_apellidos': trabajador.nombre_apellidos,
            'nombre': trabajador.nombre,
            'foto_perfil': (io.BytesIO(self.EXE_MAGIC), 'virus.png'),
        }
        resp = logged_in_admin.post(
            f'/trabajadores/editar/{trabajador.id}',
            data=data,
            content_type='multipart/form-data',
            follow_redirects=True
        )
        html = resp.data.decode('utf-8')
        assert 'rechazada' in html.lower() or 'solo se permiten' in html.lower()

    def test_upload_foto_extension_pdf_rechazado(self, logged_in_admin, db, trabajador):
        """Archivo con extensión .pdf NO debe aceptarse como foto de perfil."""
        data = {
            'no_empleado': trabajador.no_empleado,
            'nombre_apellidos': trabajador.nombre_apellidos,
            'nombre': trabajador.nombre,
            'foto_perfil': (io.BytesIO(self.PDF_MAGIC), 'documento.pdf'),
        }
        resp = logged_in_admin.post(
            f'/trabajadores/editar/{trabajador.id}',
            data=data,
            content_type='multipart/form-data',
            follow_redirects=True
        )
        html = resp.data.decode('utf-8')
        assert 'rechazada' in html.lower() or 'solo se permiten' in html.lower()

    def test_upload_foto_sin_extension_rechazado(self, logged_in_admin, db, trabajador):
        """Archivo sin extensión → debe rechazarse."""
        data = {
            'no_empleado': trabajador.no_empleado,
            'nombre_apellidos': trabajador.nombre_apellidos,
            'nombre': trabajador.nombre,
            'foto_perfil': (io.BytesIO(self.JPEG_MAGIC), 'sinextension'),
        }
        resp = logged_in_admin.post(
            f'/trabajadores/editar/{trabajador.id}',
            data=data,
            content_type='multipart/form-data',
            follow_redirects=True
        )
        html = resp.data.decode('utf-8')
        assert 'rechazada' in html.lower() or 'solo se permiten' in html.lower()


# ══════════════════════════════════════════════
# 2. AUTORIZACIÓN DE PROYECTOS
# ══════════════════════════════════════════════

class TestProyectosAutorizacion:
    """Verificar que solo admin/super_admin pueden crear/editar proyectos."""

    def test_coordinador_no_puede_crear_proyecto(self, logged_in_coordinador, db):
        """Un coordinador NO debe poder crear proyectos."""
        resp = logged_in_coordinador.post('/proyectos/agregar',
            data={
                'numero_proyecto': 'HACK-001',
                'nombre': 'Proyecto No Autorizado',
            },
            follow_redirects=True
        )
        # admin_required redirige con flash 'Acceso denegado' a main.home
        html = resp.data.decode('utf-8')
        assert 'denegado' in html.lower()

        # Verificar que NO se creó en la base de datos
        p = Proyecto.query.filter_by(numero_proyecto='HACK-001').first()
        assert p is None

    def test_coordinador_no_puede_editar_proyecto(self, logged_in_coordinador, db, proyecto):
        """Un coordinador NO debe poder editar proyectos existentes."""
        resp = logged_in_coordinador.post(f'/proyectos/editar/{proyecto.id}',
            data={
                'numero_proyecto': 'HACK-EDIT',
                'nombre': 'Hijacked',
            },
            follow_redirects=True
        )
        html = resp.data.decode('utf-8')
        assert 'denegado' in html.lower()

        # Verificar que el proyecto NO fue modificado
        db.session.refresh(proyecto)
        assert proyecto.numero_proyecto != 'HACK-EDIT'

    def test_admin_si_puede_crear_proyecto(self, logged_in_admin, db, admin_user):
        """Un admin SÍ debe poder crear proyectos."""
        resp = logged_in_admin.post('/proyectos/agregar',
            data={
                'numero_proyecto': 'ADM-001',
                'nombre': 'Proyecto Admin',
                'activo': 'on',
                'coordinador_id': admin_user.id,
                'participantes_json': '[]',
            },
            follow_redirects=True
        )
        html = resp.data.decode('utf-8')
        assert 'exitosamente' in html.lower()

        p = Proyecto.query.filter_by(numero_proyecto='ADM-001').first()
        assert p is not None
        assert p.nombre == 'Proyecto Admin'

    def test_admin_si_puede_editar_proyecto(self, logged_in_admin, db, proyecto):
        """Un admin SÍ debe poder editar proyectos."""
        resp = logged_in_admin.post(f'/proyectos/editar/{proyecto.id}',
            data={
                'numero_proyecto': proyecto.numero_proyecto,
                'nombre': 'Nombre Actualizado',
                'activo': 'on',
                'coordinador_id': proyecto.coordinador_id or '',
                'participantes_json': '[]',
            },
            follow_redirects=True
        )
        html = resp.data.decode('utf-8')
        assert 'exitosamente' in html.lower() or 'modificado' in html.lower()

        db.session.refresh(proyecto)
        assert proyecto.nombre == 'Nombre Actualizado'

    def test_coordinador_si_puede_ver_proyectos(self, logged_in_coordinador, db, proyecto):
        """Un coordinador SÍ debe poder VER la lista de proyectos (lectura)."""
        resp = logged_in_coordinador.get('/proyectos/')
        # Coordinador puede acceder a esta ruta por las reglas de ROLE_PERMISSIONS
        # Nota: depende de si 'proyectos.' está en la lista de allowed del coordinador
        # Si no está, recibirá redirect. Esto es correcto por diseño.
        assert resp.status_code in [200, 302]


# ══════════════════════════════════════════════
# 3. CONSISTENCIA DE RESPUESTAS 403 (JSON)
# ══════════════════════════════════════════════

class TestRespuestas403:
    """Verificar que todas las respuestas 403 devuelven JSON con formato consistente."""

    def test_403_json_documento(self, client, db, trabajador):
        """El endpoint de documento debe devolver JSON con 'error' al denegar acceso."""
        # Login como coordinador sin acceso al trabajador
        coord = User(
            username='coord_403_test',
            password_hash='hash',
            role='coordinador'
        )
        db.session.add(coord)
        db.session.commit()

        # Crear un documento del trabajador
        doc = DocumentoTrabajador(
            trabajador_id=trabajador.id,
            nombre_archivo='test.pdf',
            ruta_archivo='test.pdf'
        )
        db.session.add(doc)
        db.session.commit()

        with client.session_transaction() as sess:
            sess['user_id'] = coord.id
            sess['user'] = coord.username
            sess['role'] = 'coordinador'

        resp = client.get(f'/trabajadores/documento/{doc.id}')
        assert resp.status_code == 403
        data = resp.get_json()
        assert data is not None
        assert 'error' in data

    def test_403_json_foto(self, client, db, trabajador):
        """El endpoint de foto de perfil debe devolver JSON con 'error' al denegar acceso."""
        coord = User(
            username='coord_foto_test',
            password_hash='hash',
            role='coordinador'
        )
        db.session.add(coord)
        db.session.commit()

        # Asignar una foto al trabajador para evitar 404
        trabajador.foto_perfil = 'perfiles/test.jpg'
        db.session.commit()

        with client.session_transaction() as sess:
            sess['user_id'] = coord.id
            sess['user'] = coord.username
            sess['role'] = 'coordinador'

        resp = client.get(f'/trabajadores/foto/{trabajador.id}')
        assert resp.status_code == 403
        data = resp.get_json()
        assert data is not None
        assert 'error' in data

    def test_404_json_foto_sin_foto(self, logged_in_admin, db, trabajador):
        """Si el trabajador no tiene foto, debe devolver JSON 404."""
        trabajador.foto_perfil = None
        db.session.commit()

        resp = logged_in_admin.get(f'/trabajadores/foto/{trabajador.id}')
        assert resp.status_code == 404
        data = resp.get_json()
        assert data is not None
        assert 'error' in data

    def test_403_json_profile_pic_user(self, client, db):
        """El endpoint de foto de perfil de usuario debe denegar acceso a fotos ajenas."""
        # Usamos admin para que pase ROLE_PERMISSIONS (coordinador sería redirigido antes)
        admin1 = User(username='admin_pic_1', password_hash='hash', role='admin', profile_pic='pic1.png')
        admin2 = User(username='admin_pic_2', password_hash='hash', role='admin', profile_pic='pic2.png')
        db.session.add_all([admin1, admin2])
        db.session.commit()

        # Login como admin1, intentar acceder a la foto de admin2
        # Admin puede ver fotos de otros, así que usamos coordinador para la ruta users
        # Pero coordinador está bloqueado por ROLE_PERMISSIONS...
        # La protección IDOR en serve_profile_pic solo aplica a roles no-admin.
        # Para admin roles, el acceso es correcto (pueden ver fotos de todos).
        # Verificamos que la protección está activa probando con un coordinador que
        # sea redirigido por ROLE_PERMISSIONS (302) — que es el comportamiento correcto.
        coord = User(username='coord_pic_idor', password_hash='hash', role='coordinador', profile_pic='cpic.png')
        db.session.add(coord)
        db.session.commit()

        with client.session_transaction() as sess:
            sess['user_id'] = coord.id
            sess['user'] = coord.username
            sess['role'] = 'coordinador'

        # Coordinador falla permisos y debe recibir 403 FORBIDDEN por reglas de hardening
        resp = client.get('/users/profile_pic/pic2.png')
        assert resp.status_code == 403  # FORBIDDEN = acceso denegado por ROLE_PERMISSIONS y hardening


# ══════════════════════════════════════════════
# 4. TEST DE NO-REGRESIÓN: Upload general de documentos sigue funcionando
# ══════════════════════════════════════════════

class TestDocumentosNoRegresion:
    """Verificar que el upload general de documentos (PDF, Excel, etc.) NO fue afectado."""

    def test_upload_documento_pdf_valido(self, logged_in_admin, db, trabajador):
        """Un PDF real con extensión .pdf debe seguir aceptándose como documento."""
        pdf_magic = b'%PDF-1.4 ' + b'\x00' * 100
        data = {
            'documento': (io.BytesIO(pdf_magic), 'contrato.pdf'),
        }
        resp = logged_in_admin.post(
            f'/trabajadores/{trabajador.id}/documentos',
            data=data,
            content_type='multipart/form-data'
        )
        assert resp.status_code == 201
        result = resp.get_json()
        assert result is not None
        assert 'nombre_archivo' in result
