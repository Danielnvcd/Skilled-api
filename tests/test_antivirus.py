"""Antivirus (ClamAV) en la subida de documentos de trabajador.

El demonio se simula: estos tests no necesitan clamd instalado ni tocan la red.
Lo que se fija es la política, que es donde un antivirus se arruina:
  - apagado  → todo se comporta como antes
  - limpio   → pasa
  - infectado→ se rechaza y queda en la bitácora como evento de seguridad
  - caído    → NO se confunde con "limpio" (fail-closed por defecto)
"""
import io

import pytest
from werkzeug.security import generate_password_hash

from app.models import AuditLog, DocumentoTrabajador, User
from app.routes.api_auth import _encode_access_token
from app.utils import antivirus


def _hdr(user):
    return {'Authorization': f'Bearer {_encode_access_token(user)}'}


@pytest.fixture
def admin(db):
    u = User(username='av_admin', password_hash=generate_password_hash('Pass123!'),
             role='admin')
    db.session.add(u); db.session.commit()
    return u


@pytest.fixture
def av_encendido(monkeypatch):
    """Enciende el gate del antivirus sin necesitar un clamd real."""
    monkeypatch.setenv('CLAMAV_SOCKET', '/tmp/falso.ctl')
    monkeypatch.setenv('CLAMAV_FAIL_CLOSED', 'true')


class _ClamdFalso:
    def __init__(self, veredicto):
        self.veredicto = veredicto
        self.escaneos = 0

    def instream(self, stream):
        self.escaneos += 1
        stream.read()
        return {'stream': self.veredicto}


def _subir_pdf(client, admin, trabajador, contenido=b'%PDF-1.4 inofensivo'):
    return client.post(
        f'/api/trabajadores/{trabajador.id}/documentos',
        headers=_hdr(admin),
        data={'documento': (io.BytesIO(contenido), 'contrato.pdf')},
        content_type='multipart/form-data',
    )


# ── Gate ──────────────────────────────────────────────────────────────────────

def test_apagado_por_defecto_en_tests(app):
    with app.app_context():
        assert antivirus.habilitado() is False


def test_apagado_no_bloquea_la_subida(client, db, admin, trabajador):
    r = _subir_pdf(client, admin, trabajador)
    assert r.status_code == 201
    assert DocumentoTrabajador.query.count() == 1


def test_escanear_apagado_devuelve_none(app):
    with app.app_context():
        assert antivirus.escanear(b'lo que sea') is None


# ── Veredictos ────────────────────────────────────────────────────────────────

def test_archivo_limpio_pasa(client, db, admin, trabajador, av_encendido, monkeypatch):
    falso = _ClamdFalso(('OK', None))
    monkeypatch.setattr(antivirus, '_cliente', lambda: falso)

    r = _subir_pdf(client, admin, trabajador)
    assert r.status_code == 201
    assert falso.escaneos == 1


def test_archivo_infectado_se_rechaza(client, db, admin, trabajador, av_encendido, monkeypatch):
    monkeypatch.setattr(antivirus, '_cliente',
                        lambda: _ClamdFalso(('FOUND', 'Eicar-Test-Signature')))

    r = _subir_pdf(client, admin, trabajador, b'%PDF-1.4 con sorpresa')
    assert r.status_code == 400
    assert 'Eicar-Test-Signature' in r.get_json()['error']
    # Lo importante: no se guardó nada.
    assert DocumentoTrabajador.query.count() == 0


def test_el_rechazo_queda_como_evento_de_seguridad(client, db, admin, trabajador,
                                                   av_encendido, monkeypatch):
    """Debe salir en Sistemas → Eventos de seguridad, que filtra por 'antivirus'."""
    from app.routes.api_sistemas.endpoints import _PATRONES_SEGURIDAD
    monkeypatch.setattr(antivirus, '_cliente',
                        lambda: _ClamdFalso(('FOUND', 'Pdf.Exploit.CVE_2010_0188')))

    _subir_pdf(client, admin, trabajador)

    entradas = [a.action for a in AuditLog.query.all()]
    assert any('Antivirus rechazó' in a for a in entradas)
    assert 'antivirus' in _PATRONES_SEGURIDAD
    assert any('antivirus' in a.lower() for a in entradas)


# ── El demonio caído NO es "limpio" ───────────────────────────────────────────

def test_antivirus_caido_rechaza_por_defecto(client, db, admin, trabajador,
                                             av_encendido, monkeypatch):
    def explota():
        raise ConnectionError('clamd no responde')
    monkeypatch.setattr(antivirus, '_cliente', explota)

    r = _subir_pdf(client, admin, trabajador)
    assert r.status_code == 503
    assert 'antivirus no está disponible' in r.get_json()['error']
    assert DocumentoTrabajador.query.count() == 0


def test_fail_open_deja_pasar_si_se_configura_asi(client, db, admin, trabajador,
                                                  av_encendido, monkeypatch):
    monkeypatch.setenv('CLAMAV_FAIL_CLOSED', 'false')

    def explota():
        raise ConnectionError('clamd no responde')
    monkeypatch.setattr(antivirus, '_cliente', explota)

    r = _subir_pdf(client, admin, trabajador)
    assert r.status_code == 201


def test_error_del_motor_no_se_toma_por_limpio(app, av_encendido, monkeypatch):
    monkeypatch.setattr(antivirus, '_cliente', lambda: _ClamdFalso(('ERROR', 'algo pasó')))
    with app.app_context():
        with pytest.raises(antivirus.AntivirusNoDisponible):
            antivirus.escanear(b'x')


# ── Salud visible en el panel ─────────────────────────────────────────────────
# Con fail-closed, un clamd muerto rompe la subida de documentos. Si eso no se
# ve en «Estado del servidor», nadie se entera hasta que RRHH se queja.

@pytest.fixture
def sistemas(db):
    import pyotp
    u = User(username='av_ops', password_hash=generate_password_hash('Pass123!'),
             role='sistemas', password_version=1, totp_secret=pyotp.random_base32())
    db.session.add(u); db.session.commit()
    return u


def test_estado_reporta_antivirus_no_configurado(client, sistemas):
    """`ok: None` para que el panel lo pinte en gris, no en rojo de alarma."""
    datos = client.get('/api/sistemas/estado', headers=_hdr(sistemas)).get_json()
    assert datos['antivirus']['ok'] is None
    assert 'sin escanear' in datos['antivirus']['detalle']


def test_estado_reporta_antivirus_vivo(client, sistemas, av_encendido, monkeypatch):
    class _Vivo:
        def ping(self): return 'PONG'
        def version(self): return 'ClamAV 1.0.3/27000'
    monkeypatch.setattr(antivirus, '_cliente', lambda: _Vivo())

    datos = client.get('/api/sistemas/estado', headers=_hdr(sistemas)).get_json()
    assert datos['antivirus']['ok'] is True
    assert datos['antivirus']['fail_closed'] is True
    assert 'ClamAV' in datos['antivirus']['detalle']


def test_version_deshabilitada_no_ensucia_el_panel(client, sistemas, av_encendido, monkeypatch):
    """Muchos clamd traen VERSION deshabilitado: no mostrar su error como si
    fuera la versión, y seguir reportando el servicio como vivo."""
    class _SinVersion:
        def ping(self): return 'PONG'
        def version(self): return 'UNKNOWN COMMAND'
    monkeypatch.setattr(antivirus, '_cliente', lambda: _SinVersion())

    with client.application.app_context():
        assert antivirus.version() is None

    datos = client.get('/api/sistemas/estado', headers=_hdr(sistemas)).get_json()
    assert datos['antivirus']['ok'] is True
    assert datos['antivirus']['detalle'] == 'clamd responde'


def test_estado_avisa_si_el_antivirus_esta_caido(client, sistemas, av_encendido, monkeypatch):
    def explota():
        raise ConnectionError('sin socket')
    monkeypatch.setattr(antivirus, '_cliente', explota)

    datos = client.get('/api/sistemas/estado', headers=_hdr(sistemas)).get_json()
    assert datos['antivirus']['ok'] is False
    # Y sale en la lista de defensas degradadas, que es lo que se ve arriba.
    assert any('antivirus' in d.lower() for d in datos['defensas_degradadas'])


# ── Alcance: las imágenes ya van por Pillow ──────────────────────────────────

def test_las_imagenes_no_pasan_por_clamd(client, db, admin, trabajador,
                                         av_encendido, monkeypatch):
    """El re-encode a WebP ya destruye payloads; escanear sería redundante."""
    from PIL import Image
    falso = _ClamdFalso(('OK', None))
    monkeypatch.setattr(antivirus, '_cliente', lambda: falso)

    buf = io.BytesIO()
    Image.new('RGB', (20, 20), (5, 5, 5)).save(buf, format='PNG')
    buf.seek(0)
    r = client.post(
        f'/api/trabajadores/{trabajador.id}/documentos',
        headers=_hdr(admin),
        data={'documento': (buf, 'ine.png')},
        content_type='multipart/form-data',
    )
    assert r.status_code == 201
    assert falso.escaneos == 0
