"""Capa de almacenamiento de archivos privados (`app/utils/archivos.py`).

Lo que se protege aquí es la promesa de "no romper nada": con el bucket privado
apagado el comportamiento debe ser idéntico al de siempre (disco), y con el
bucket encendido los archivos que aún NO se migraron deben seguir sirviéndose
desde disco (dual-read).

R2 se simula con un cliente falso — estos tests nunca tocan la red.
"""
import io
import os

import pytest
from PIL import Image
from werkzeug.datastructures import FileStorage

from app.utils import archivos


class _FakeR2:
    """Cliente S3 mínimo en memoria: put/get/head/delete sobre un dict."""

    def __init__(self):
        self.objetos = {}
        self.puts = 0

    def put_object(self, Bucket, Key, Body, ContentType=None, **kw):
        self.puts += 1
        self.objetos[(Bucket, Key)] = (Body, ContentType)
        return {}

    def get_object(self, Bucket, Key):
        if (Bucket, Key) not in self.objetos:
            raise KeyError('NoSuchKey')
        body, _ctype = self.objetos[(Bucket, Key)]
        return {'Body': io.BytesIO(body)}

    def head_object(self, Bucket, Key):
        if (Bucket, Key) not in self.objetos:
            raise KeyError('404')
        return {}

    def delete_object(self, Bucket, Key):
        self.objetos.pop((Bucket, Key), None)
        return {}


def _cuerpo(resp):
    """Bytes de una respuesta de `send_file` (viene en direct_passthrough)."""
    resp.direct_passthrough = False
    return resp.get_data()


@pytest.fixture
def r2_falso(monkeypatch):
    """Enciende el bucket privado apuntando a un cliente en memoria."""
    fake = _FakeR2()
    monkeypatch.setenv('R2_PRIVADO_BUCKET', 'bucket-de-prueba')
    monkeypatch.setenv('R2_PRIVADO_ACCOUNT_ID', 'cuenta')
    monkeypatch.setenv('R2_PRIVADO_ACCESS_KEY_ID', 'llave')
    monkeypatch.setenv('R2_PRIVADO_SECRET_ACCESS_KEY', 'secreto')
    monkeypatch.setattr(archivos, '_get_client', lambda: fake)
    return fake


# ── Apagado: todo igual que antes ─────────────────────────────────────────────

def test_apagado_por_defecto_en_tests(app):
    """conftest vacía R2_PRIVADO_* — el módulo debe quedar inerte."""
    with app.app_context():
        assert archivos.habilitado() is False


def test_apagado_guarda_y_lee_de_disco(app):
    with app.app_context():
        assert archivos.guardar('perfiles/x.webp', b'contenido', 'image/webp') is False
        assert os.path.exists(archivos.ruta_local('perfiles/x.webp'))
        assert archivos.existe('perfiles/x.webp') is True
        # `leer` es sólo-R2: con R2 apagado no debe devolver nada.
        assert archivos.leer('perfiles/x.webp') is None


def test_apagado_elimina_de_disco(app):
    with app.app_context():
        archivos.guardar('perfiles/borrame.webp', b'x', 'image/webp')
        archivos.eliminar('perfiles/borrame.webp')
        assert archivos.existe('perfiles/borrame.webp') is False


def test_eliminar_inexistente_no_lanza(app):
    with app.app_context():
        archivos.eliminar('perfiles/no-existe.webp')  # best-effort, sin excepción


# ── Encendido: escribe en R2, no en disco ─────────────────────────────────────

def test_encendido_sube_a_r2_y_no_toca_disco(app, r2_falso):
    with app.app_context():
        assert archivos.habilitado() is True
        assert archivos.guardar('trabajadores/1/acta.pdf', b'%PDF-1.4', 'application/pdf') is True
        assert r2_falso.puts == 1
        assert archivos.leer('trabajadores/1/acta.pdf') == b'%PDF-1.4'
        assert not os.path.exists(archivos.ruta_local('trabajadores/1/acta.pdf'))


def test_encendido_content_type_se_deduce_de_la_extension(app, r2_falso):
    with app.app_context():
        archivos.guardar('perfiles/foto.webp', b'RIFF')
        _body, ctype = r2_falso.objetos[('bucket-de-prueba', 'perfiles/foto.webp')]
        assert ctype == 'image/webp'


def test_fallo_de_r2_cae_a_disco_sin_perder_el_archivo(app, r2_falso, monkeypatch):
    """Si R2 se cae, el upload del usuario NO se pierde: aterriza en disco."""
    def explota(**kw):
        raise RuntimeError('R2 caído')
    monkeypatch.setattr(r2_falso, 'put_object', explota)

    with app.app_context():
        assert archivos.guardar('perfiles/rescatada.webp', b'datos', 'image/webp') is False
        assert os.path.exists(archivos.ruta_local('perfiles/rescatada.webp'))


# ── Dual-read: lo viejo en disco sigue funcionando ────────────────────────────

def test_dual_read_sirve_desde_disco_lo_no_migrado(app, r2_falso):
    """El caso central de la migración: R2 encendido, archivo sólo en disco."""
    with app.app_context():
        destino = archivos.ruta_local('perfiles/legacy.webp')
        os.makedirs(os.path.dirname(destino), exist_ok=True)
        with open(destino, 'wb') as f:
            f.write(b'foto vieja')

        assert archivos.leer('perfiles/legacy.webp') is None  # no está en R2
        assert archivos.existe('perfiles/legacy.webp') is True

        with app.test_request_context():
            resp = archivos.enviar('perfiles/legacy.webp')
            assert resp is not None
            assert resp.mimetype == 'image/webp'
            assert _cuerpo(resp) == b'foto vieja'


def test_enviar_prefiere_r2_sobre_disco(app, r2_falso):
    with app.app_context():
        destino = archivos.ruta_local('perfiles/dup.webp')
        os.makedirs(os.path.dirname(destino), exist_ok=True)
        with open(destino, 'wb') as f:
            f.write(b'version-disco')
        archivos.guardar('perfiles/dup.webp', b'version-r2', 'image/webp')

        with app.test_request_context():
            assert _cuerpo(archivos.enviar('perfiles/dup.webp')) == b'version-r2'


def test_enviar_devuelve_none_si_no_esta_en_ningun_lado(app, r2_falso):
    with app.app_context(), app.test_request_context():
        assert archivos.enviar('perfiles/fantasma.webp') is None


def test_eliminar_borra_en_r2_y_en_disco(app, r2_falso):
    with app.app_context():
        destino = archivos.ruta_local('herramientas/9/foto.jpg')
        os.makedirs(os.path.dirname(destino), exist_ok=True)
        with open(destino, 'wb') as f:
            f.write(b'jpg')
        archivos.guardar('herramientas/9/foto.jpg', b'jpg', 'image/jpeg')

        archivos.eliminar('herramientas/9/foto.jpg')

        assert archivos.existe_en_r2('herramientas/9/foto.jpg') is False
        assert not os.path.exists(destino)


# ── Config ────────────────────────────────────────────────────────────────────

def test_sin_bucket_privado_no_se_usa_el_bucket_publico(app, monkeypatch):
    """Nunca escribir documentos de RRHH en el bucket público del catálogo."""
    monkeypatch.setenv('R2_PRIVADO_BUCKET', '')
    monkeypatch.setenv('R2_BUCKET', 'skilled-productos')
    monkeypatch.setenv('R2_ACCOUNT_ID', 'cuenta')
    monkeypatch.setenv('R2_ACCESS_KEY_ID', 'llave')
    monkeypatch.setenv('R2_SECRET_ACCESS_KEY', 'secreto')
    with app.app_context():
        assert archivos.habilitado() is False


def test_apunta_al_bucket_publico_se_desactiva(app, monkeypatch):
    """Nunca escribir PII en el bucket con dominio conectado, ni por error de tecleo."""
    monkeypatch.setenv('R2_BUCKET', 'skilled-productos')
    monkeypatch.setenv('R2_PRIVADO_BUCKET', 'skilled-productos')
    monkeypatch.setenv('R2_ACCOUNT_ID', 'cuenta')
    monkeypatch.setenv('R2_ACCESS_KEY_ID', 'llave')
    monkeypatch.setenv('R2_SECRET_ACCESS_KEY', 'secreto')
    with app.app_context():
        assert archivos.conflicto_de_bucket() is True
        assert archivos.habilitado() is False


def test_bucket_distinto_no_es_conflicto(app, monkeypatch):
    monkeypatch.setenv('R2_BUCKET', 'skilled-productos')
    monkeypatch.setenv('R2_PRIVADO_BUCKET', 'skilled-privados')
    monkeypatch.setenv('R2_ACCOUNT_ID', 'cuenta')
    monkeypatch.setenv('R2_ACCESS_KEY_ID', 'llave')
    monkeypatch.setenv('R2_SECRET_ACCESS_KEY', 'secreto')
    with app.app_context():
        assert archivos.conflicto_de_bucket() is False
        assert archivos.habilitado() is True


def test_conflicto_desactiva_la_escritura_a_r2(app, monkeypatch, r2_falso):
    """Con conflicto, `guardar` debe aterrizar en disco y no llamar a R2."""
    monkeypatch.setenv('R2_BUCKET', 'bucket-de-prueba')   # == R2_PRIVADO_BUCKET
    with app.app_context():
        assert archivos.guardar('perfiles/segura.webp', b'x', 'image/webp') is False
        assert r2_falso.objetos == {}
        assert os.path.exists(archivos.ruta_local('perfiles/segura.webp'))


def test_llaves_caen_a_las_del_pipeline_publico(app, monkeypatch):
    """Con sólo R2_PRIVADO_BUCKET basta si el token de R2_* ya sirve."""
    monkeypatch.setenv('R2_PRIVADO_BUCKET', 'skilled-privados')
    monkeypatch.setenv('R2_PRIVADO_ACCOUNT_ID', '')
    monkeypatch.setenv('R2_PRIVADO_ACCESS_KEY_ID', '')
    monkeypatch.setenv('R2_PRIVADO_SECRET_ACCESS_KEY', '')
    monkeypatch.setenv('R2_ACCOUNT_ID', 'cuenta')
    monkeypatch.setenv('R2_ACCESS_KEY_ID', 'llave')
    monkeypatch.setenv('R2_SECRET_ACCESS_KEY', 'secreto')
    with app.app_context():
        assert archivos.habilitado() is True
        assert archivos._var('ACCESS_KEY_ID') == 'llave'


def test_keys_se_normalizan(app):
    """Backslashes de Windows y `/` inicial no deben generar keys distintas."""
    assert archivos._norm('\\perfiles\\a.webp') == 'perfiles/a.webp'
    assert archivos._norm('/trabajadores/1/x.pdf') == 'trabajadores/1/x.pdf'


# ── De dónde salieron los bytes ───────────────────────────────────────────────
# Con lectura dual, mirar la foto no dice si vino de R2 o del disco. El header
# lo vuelve comprobable desde la pestaña Red del navegador.

def test_header_dice_r2_cuando_sale_del_bucket(app, r2_falso):
    with app.app_context():
        archivos.guardar('perfiles/desde-r2.webp', b'bytes', 'image/webp')
        with app.test_request_context():
            resp = archivos.enviar('perfiles/desde-r2.webp')
            assert resp.headers['X-Almacenamiento'] == 'r2'


def test_header_dice_disco_cuando_no_esta_migrado(app, r2_falso):
    with app.app_context():
        destino = archivos.ruta_local('perfiles/solo-disco.webp')
        os.makedirs(os.path.dirname(destino), exist_ok=True)
        with open(destino, 'wb') as f:
            f.write(b'bytes')
        with app.test_request_context():
            resp = archivos.enviar('perfiles/solo-disco.webp')
            assert resp.headers['X-Almacenamiento'] == 'disco'


def test_header_dice_disco_con_r2_apagado(app):
    with app.app_context():
        archivos.guardar('perfiles/sin-r2.webp', b'bytes', 'image/webp')
        with app.test_request_context():
            resp = archivos.enviar('perfiles/sin-r2.webp')
            assert resp.headers['X-Almacenamiento'] == 'disco'


# ── Reemplazo de foto: no perder la anterior si falla la nueva ────────────────

def _png_subido(nombre='foto.png'):
    buf = io.BytesIO()
    Image.new('RGB', (10, 10), (1, 2, 3)).save(buf, format='PNG')
    buf.seek(0)
    return FileStorage(stream=buf, filename=nombre, content_type='image/png')


def test_si_falla_la_foto_nueva_la_anterior_sobrevive(app, db, trabajador, monkeypatch):
    """Se guarda la nueva ANTES de borrar la vieja: un fallo no deja al
    trabajador sin ninguna foto."""
    from app.routes.api_trabajadores import _core as core

    with app.app_context():
        archivos.guardar('perfiles/vieja.webp', b'foto vieja', 'image/webp')
        trabajador.foto_perfil = 'perfiles/vieja.webp'

        def explota(*a, **kw):
            raise RuntimeError('almacenamiento caído')
        monkeypatch.setattr(core, '_save_profile_picture', explota)

        with pytest.raises(RuntimeError):
            core._save_foto(trabajador, _png_subido())

        assert trabajador.foto_perfil == 'perfiles/vieja.webp'
        assert os.path.exists(archivos.ruta_local('perfiles/vieja.webp'))


def test_al_reemplazar_bien_si_se_borra_la_anterior(app, db, trabajador):
    from app.routes.api_trabajadores import _core as core

    with app.app_context():
        archivos.guardar('perfiles/vieja.webp', b'foto vieja', 'image/webp')
        trabajador.foto_perfil = 'perfiles/vieja.webp'

        core._save_foto(trabajador, _png_subido())

        assert trabajador.foto_perfil != 'perfiles/vieja.webp'
        assert os.path.exists(archivos.ruta_local(trabajador.foto_perfil))
        assert not os.path.exists(archivos.ruta_local('perfiles/vieja.webp'))


# ── Path traversal ────────────────────────────────────────────────────────────
# `send_from_directory` traía esta protección de fábrica; al servir desde R2 hay
# que sostenerla a mano. Ninguna ruta actual pasa keys del cliente, pero sin esto
# la primera que lo haga se convierte en lectura/escritura arbitraria de archivos.

@pytest.mark.parametrize('key', [
    '../../etc/passwd',
    'perfiles/../../secreto.env',
    '..\\..\\windows\\win.ini',
    '/../fuera.txt',
    'C:/Windows/System32/config/sam',
    './oculto',
    '',
    '   ',
])
def test_keys_con_traversal_se_rechazan(app, key):
    with pytest.raises(archivos.KeyInsegura):
        archivos._norm(key)


def test_guardar_con_traversal_lanza(app):
    """Escribir es un error de programación: debe ser ruidoso, no silencioso."""
    with app.app_context():
        with pytest.raises(archivos.KeyInsegura):
            archivos.guardar('../fuera-del-area.txt', b'x')


def test_enviar_con_traversal_devuelve_none(app):
    """Leer se degrada a 404 en vez de 500: no confirma qué existe fuera."""
    with app.app_context(), app.test_request_context():
        assert archivos.enviar('../../etc/passwd') is None


def test_existe_y_eliminar_con_traversal_no_lanzan(app):
    with app.app_context():
        assert archivos.existe('../../etc/passwd') is False
        archivos.eliminar('../../etc/passwd')   # no-op, sin excepción


def test_ruta_local_exige_contencion(app):
    """Barrera final: el path resuelto debe quedar dentro del UPLOAD_FOLDER."""
    with app.app_context():
        dentro = archivos.ruta_local('perfiles/ok.webp')
        base = os.path.realpath(app.config['UPLOAD_FOLDER'])
        assert os.path.realpath(dentro).startswith(base + os.sep)
