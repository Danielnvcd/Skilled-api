"""Apartado de archivos privados del panel de sistemas.

  GET  /api/sistemas/archivos              inventario: en R2 / por subir / perdidos
  POST /api/sistemas/archivos/sincronizar  sube a R2 lo que sigue en disco

R2 se simula con un cliente en memoria: estos tests no tocan la red.
"""
import io
import os

import pyotp
import pytest
from werkzeug.security import generate_password_hash

from app.models import DocumentoTrabajador, User
from app.routes.api_auth import _encode_access_token
from app.utils import archivos


def _hdr(user):
    return {'Authorization': f'Bearer {_encode_access_token(user)}'}


class _FakeR2:
    def __init__(self):
        self.objetos = {}

    def put_object(self, Bucket, Key, Body, ContentType=None, **kw):
        self.objetos[(Bucket, Key)] = Body
        return {}

    def get_object(self, Bucket, Key):
        if (Bucket, Key) not in self.objetos:
            raise KeyError('NoSuchKey')
        return {'Body': io.BytesIO(self.objetos[(Bucket, Key)])}

    def head_object(self, Bucket, Key):
        if (Bucket, Key) not in self.objetos:
            raise KeyError('404')
        return {}

    def delete_object(self, Bucket, Key):
        self.objetos.pop((Bucket, Key), None)
        return {}

    def list_objects_v2(self, Bucket, MaxKeys=1000, Prefix=None, ContinuationToken=None):
        claves = [k for (b, k) in self.objetos if b == Bucket
                  and (not Prefix or k.startswith(Prefix))]
        return {'Contents': [{'Key': k} for k in sorted(claves)], 'IsTruncated': False}


@pytest.fixture
def sistemas(db):
    u = User(username='ops_archivos', password_hash=generate_password_hash('SuperPass123!'),
             role='sistemas', password_version=1, totp_secret=pyotp.random_base32())
    db.session.add(u); db.session.commit()
    return u


@pytest.fixture
def correr_inline(monkeypatch):
    """Ejecuta el background task en el momento, dentro del request.

    `_run_sync` termina con `db.session.remove()`, que aquí destruiría la sesión
    con savepoint del fixture `db` y se llevaría los datos del test por delante.
    El worker real sí necesita soltarla; el test no, así que se neutraliza.
    """
    from app.routes.api_sistemas import archivos as mod
    monkeypatch.setattr(mod.db.session, 'remove', lambda: None)
    monkeypatch.setattr(mod.socketio, 'start_background_task',
                        lambda fn, *a, **kw: fn(*a, **kw))


@pytest.fixture
def r2_falso(monkeypatch):
    fake = _FakeR2()
    monkeypatch.setenv('R2_PRIVADO_BUCKET', 'bucket-de-prueba')
    monkeypatch.setenv('R2_PRIVADO_ACCOUNT_ID', 'cuenta')
    monkeypatch.setenv('R2_PRIVADO_ACCESS_KEY_ID', 'llave')
    monkeypatch.setenv('R2_PRIVADO_SECRET_ACCESS_KEY', 'secreto')
    monkeypatch.setattr(archivos, '_get_client', lambda: fake)
    return fake


def _documento_en_disco(app, db, trabajador, nombre='contrato.pdf'):
    """Crea un DocumentoTrabajador cuyo archivo existe solo en disco."""
    key = f'trabajadores/{trabajador.id}/{nombre}'
    with app.app_context():
        destino = archivos.ruta_local(key)
    os.makedirs(os.path.dirname(destino), exist_ok=True)
    with open(destino, 'wb') as f:
        f.write(b'%PDF-1.4 contenido')
    doc = DocumentoTrabajador(trabajador_id=trabajador.id, nombre_archivo=nombre,
                              ruta_archivo=key)
    db.session.add(doc); db.session.commit()
    return key


# ── Permisos ──────────────────────────────────────────────────────────────────

def test_requiere_rol_sistemas(client, admin_user):
    assert client.get('/api/sistemas/archivos', headers=_hdr(admin_user)).status_code == 403
    assert client.post('/api/sistemas/archivos/sincronizar',
                       headers=_hdr(admin_user)).status_code == 403


def test_sin_token_401(client):
    assert client.get('/api/sistemas/archivos').status_code == 401


# ── Estado ────────────────────────────────────────────────────────────────────

def test_estado_reporta_deshabilitado_sin_bucket(client, sistemas):
    """Sin R2 configurado la app funciona igual; la UI lo explica con `enabled`."""
    r = client.get('/api/sistemas/archivos', headers=_hdr(sistemas))
    assert r.status_code == 200
    assert r.get_json()['enabled'] is False


def test_estado_no_revienta_con_credenciales_malas(client, sistemas, monkeypatch):
    """Un Account ID mal puesto daba 500. Debe ser un aviso accionable.

    Se simula el fallo real: boto3 arma el endpoint con el valor de la variable y
    lanza ValueError antes de cualquier llamada de red.
    """
    monkeypatch.setenv('R2_PRIVADO_BUCKET', 'bucket-de-prueba')
    monkeypatch.setenv('R2_PRIVADO_ACCOUNT_ID', 'cfat_token_pegado_donde_no_va')
    monkeypatch.setenv('R2_PRIVADO_ACCESS_KEY_ID', 'llave')
    monkeypatch.setenv('R2_PRIVADO_SECRET_ACCESS_KEY', 'secreto')

    def cliente_imposible():
        raise ValueError('Invalid endpoint: https://cfat_token_pegado_donde_no_va'
                         '.r2.cloudflarestorage.com')
    monkeypatch.setattr(archivos, '_get_client', cliente_imposible)

    r = client.get('/api/sistemas/archivos', headers=_hdr(sistemas))
    assert r.status_code == 200
    datos = r.get_json()
    assert datos['enabled'] is True
    assert 'R2_PRIVADO_ACCOUNT_ID' in datos['error']
    assert datos['pendientes'] == 0     # no reportar todo como pendiente


def test_el_error_no_filtra_el_valor_de_la_credencial(client, sistemas, monkeypatch):
    """El mensaje de botocore lleva el endpoint, y el endpoint lleva el secreto."""
    secreto = 'cfat_ESTO_ES_UN_TOKEN_QUE_NO_DEBE_SALIR'
    monkeypatch.setenv('R2_PRIVADO_BUCKET', 'bucket-de-prueba')
    monkeypatch.setenv('R2_PRIVADO_ACCOUNT_ID', secreto)
    monkeypatch.setenv('R2_PRIVADO_ACCESS_KEY_ID', 'llave')
    monkeypatch.setenv('R2_PRIVADO_SECRET_ACCESS_KEY', 'secreto')

    def cliente_imposible():
        raise ValueError(f'Invalid endpoint: https://{secreto}.r2.cloudflarestorage.com')
    monkeypatch.setattr(archivos, '_get_client', cliente_imposible)

    cuerpo = client.get('/api/sistemas/archivos', headers=_hdr(sistemas)).get_data(as_text=True)
    assert secreto not in cuerpo


def test_una_ruta_invalida_en_bd_no_tumba_el_panel(client, db, sistemas, trabajador, r2_falso):
    """El panel reporta sobre datos sucios: no puede reventar por uno de ellos."""
    db.session.add(DocumentoTrabajador(trabajador_id=trabajador.id,
                                       nombre_archivo='raro.pdf',
                                       ruta_archivo='../../fuera/del/area.pdf'))
    db.session.commit()

    r = client.get('/api/sistemas/archivos', headers=_hdr(sistemas))
    assert r.status_code == 200
    datos = r.get_json()
    # Inservible de todas formas: se cataloga como faltante y se muestra cuál es.
    assert datos['faltantes'] == 1
    assert any('area.pdf' in f['key'] for f in datos['detalle_faltantes'])


def test_una_ruta_invalida_no_se_encola_para_subir(client, db, sistemas, trabajador, r2_falso):
    db.session.add(DocumentoTrabajador(trabajador_id=trabajador.id,
                                       nombre_archivo='raro.pdf',
                                       ruta_archivo='../../fuera/del/area.pdf'))
    db.session.commit()

    r = client.post('/api/sistemas/archivos/sincronizar', headers=_hdr(sistemas))
    assert r.status_code == 200
    assert r.get_json()['encolados'] == 0


def test_sincronizar_rechaza_si_el_bucket_no_responde(client, sistemas, monkeypatch):
    monkeypatch.setenv('R2_PRIVADO_BUCKET', 'bucket-de-prueba')
    monkeypatch.setenv('R2_PRIVADO_ACCOUNT_ID', 'cuenta')
    monkeypatch.setenv('R2_PRIVADO_ACCESS_KEY_ID', 'llave')
    monkeypatch.setenv('R2_PRIVADO_SECRET_ACCESS_KEY', 'secreto')

    def cliente_imposible():
        raise RuntimeError('sin red')
    monkeypatch.setattr(archivos, '_get_client', cliente_imposible)

    r = client.post('/api/sistemas/archivos/sincronizar', headers=_hdr(sistemas))
    assert r.status_code == 400
    assert 'No se pudo contactar' in r.get_json()['error']


def test_estado_clasifica_pendiente_en_r2_y_faltante(
    app, client, db, sistemas, trabajador, r2_falso,
):
    key_disco = _documento_en_disco(app, db, trabajador, 'en-disco.pdf')

    # Un segundo documento ya subido a R2.
    key_r2 = f'trabajadores/{trabajador.id}/en-nube.pdf'
    with app.app_context():
        archivos.guardar(key_r2, b'%PDF ya migrado', 'application/pdf')
    db.session.add(DocumentoTrabajador(trabajador_id=trabajador.id,
                                       nombre_archivo='en-nube.pdf', ruta_archivo=key_r2))

    # Un tercero que la BD referencia pero no existe en ningún lado.
    db.session.add(DocumentoTrabajador(trabajador_id=trabajador.id,
                                       nombre_archivo='perdido.pdf',
                                       ruta_archivo=f'trabajadores/{trabajador.id}/perdido.pdf'))
    db.session.commit()

    datos = client.get('/api/sistemas/archivos', headers=_hdr(sistemas)).get_json()
    assert datos['enabled'] is True
    assert datos['en_r2'] == 1
    assert datos['pendientes'] == 1
    assert datos['faltantes'] == 1
    assert datos['total'] == 3

    docs = next(f for f in datos['familias'] if f['clave'] == 'documento')
    assert (docs['en_r2'], docs['pendientes'], docs['faltantes']) == (1, 1, 1)
    assert any(f['key'].endswith('perdido.pdf') for f in datos['detalle_faltantes'])
    assert key_disco  # el pendiente sigue en disco, no se tocó


# ── Sincronizar ───────────────────────────────────────────────────────────────

def test_sincronizar_sin_bucket_devuelve_400(client, sistemas):
    r = client.post('/api/sistemas/archivos/sincronizar', headers=_hdr(sistemas))
    assert r.status_code == 400
    assert 'no está configurado' in r.get_json()['error']


def test_sincronizar_encola_solo_lo_pendiente(app, client, db, sistemas, trabajador, r2_falso):
    """El endpoint encola; comprobamos QUÉ selecciona, sin correr la subida."""
    from app.routes.api_sistemas import archivos as mod

    key = _documento_en_disco(app, db, trabajador, 'por-subir.pdf')
    capturado = {}

    def capturar(fn, _app, user_id, items, job_id):
        capturado['items'] = items
        capturado['user_id'] = user_id

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(mod.socketio, 'start_background_task', capturar)
        r = client.post('/api/sistemas/archivos/sincronizar', headers=_hdr(sistemas))

    assert r.status_code == 200
    cuerpo = r.get_json()
    assert cuerpo['encolados'] == 1
    assert cuerpo['job_id']
    assert capturado['items'] == [('documento', key)]
    assert capturado['user_id'] == sistemas.id


def test_sincronizar_sube_los_pendientes(app, client, db, sistemas, trabajador,
                                         r2_falso, correr_inline):
    key = _documento_en_disco(app, db, trabajador, 'por-subir.pdf')

    r = client.post('/api/sistemas/archivos/sincronizar', headers=_hdr(sistemas))
    assert r.status_code == 200
    assert r.get_json()['encolados'] == 1
    assert r2_falso.objetos[('bucket-de-prueba', key)] == b'%PDF-1.4 contenido'
    # No se borra la copia local: eso queda para el script con --borrar-local.
    with app.app_context():
        assert os.path.exists(archivos.ruta_local(key))


def test_sincronizar_es_idempotente(app, client, db, sistemas, trabajador,
                                    r2_falso, correr_inline):
    _documento_en_disco(app, db, trabajador, 'una-vez.pdf')
    client.post('/api/sistemas/archivos/sincronizar', headers=_hdr(sistemas))

    # Segunda corrida: ya está en R2, no queda nada pendiente.
    cuerpo = client.post('/api/sistemas/archivos/sincronizar',
                         headers=_hdr(sistemas)).get_json()
    assert cuerpo['encolados'] == 0
    assert cuerpo['job_id'] is None


def test_estado_refleja_la_sincronizacion(app, client, db, sistemas, trabajador,
                                          r2_falso, correr_inline):
    """Tras sincronizar, el inventario debe mover el archivo de "por subir" a "en R2"."""
    _documento_en_disco(app, db, trabajador, 'movido.pdf')

    antes = client.get('/api/sistemas/archivos', headers=_hdr(sistemas)).get_json()
    assert (antes['pendientes'], antes['en_r2']) == (1, 0)

    client.post('/api/sistemas/archivos/sincronizar', headers=_hdr(sistemas))

    despues = client.get('/api/sistemas/archivos', headers=_hdr(sistemas)).get_json()
    assert (despues['pendientes'], despues['en_r2']) == (0, 1)


def test_sincronizar_ignora_los_faltantes(client, db, sistemas, trabajador, r2_falso):
    """Una fila sin archivo no debe encolarse ni contar como error de subida."""
    db.session.add(DocumentoTrabajador(trabajador_id=trabajador.id,
                                       nombre_archivo='fantasma.pdf',
                                       ruta_archivo=f'trabajadores/{trabajador.id}/fantasma.pdf'))
    db.session.commit()

    cuerpo = client.post('/api/sistemas/archivos/sincronizar',
                         headers=_hdr(sistemas)).get_json()
    assert cuerpo['encolados'] == 0


def test_sincronizar_queda_en_bitacora(app, client, db, sistemas, trabajador, r2_falso):
    from app.models import AuditLog
    _documento_en_disco(app, db, trabajador, 'auditado.pdf')
    client.post('/api/sistemas/archivos/sincronizar', headers=_hdr(sistemas))

    entradas = AuditLog.query.filter(AuditLog.user == 'ops_archivos').all()
    assert any('sincronizó' in (e.action or '') for e in entradas)


# ── Candado anti-doble-corrida ────────────────────────────────────────────────

def test_dos_sincronizaciones_a_la_vez_dan_409(app, client, db, sistemas, trabajador,
                                               r2_falso, monkeypatch):
    """Con Redis, el segundo admin que pulse Sincronizar recibe 409, no un duplicado."""
    from app.routes.api_sistemas import archivos as mod

    _documento_en_disco(app, db, trabajador, 'concurrente.pdf')
    tomados = []
    # El primer intento toma el candado; el segundo lo encuentra ocupado.
    monkeypatch.setattr(mod, '_tomar_candado',
                        lambda job_id: not tomados and (tomados.append(job_id) or True))
    # Que no corra la tanda: solo interesa quién obtiene el turno.
    monkeypatch.setattr(mod.socketio, 'start_background_task', lambda *a, **kw: None)

    primera = client.post('/api/sistemas/archivos/sincronizar', headers=_hdr(sistemas))
    segunda = client.post('/api/sistemas/archivos/sincronizar', headers=_hdr(sistemas))

    assert primera.status_code == 200
    assert segunda.status_code == 409
    assert 'en curso' in segunda.get_json()['error']


def test_sin_redis_no_se_bloquea(app, client, db, sistemas, trabajador, r2_falso,
                                 monkeypatch, correr_inline):
    """Subir es idempotente: sin Redis se prefiere trabajo duplicado a no poder."""
    from app.routes.api_sistemas import archivos as mod
    monkeypatch.setattr('app.extensions.get_redis', lambda: None)

    _documento_en_disco(app, db, trabajador, 'sin-redis.pdf')
    assert mod._tomar_candado('job-x') is True
    mod._soltar_candado('job-x')     # no debe lanzar sin Redis

    r = client.post('/api/sistemas/archivos/sincronizar', headers=_hdr(sistemas))
    assert r.status_code == 200


def test_el_candado_se_libera_aunque_la_tanda_reviente(app, db, sistemas, trabajador,
                                                       r2_falso, monkeypatch):
    """Si `_subir_lote` explota, el candado no puede quedarse tomado."""
    from app.routes.api_sistemas import archivos as mod

    soltados = []
    monkeypatch.setattr(mod, '_soltar_candado', lambda job_id: soltados.append(job_id))
    monkeypatch.setattr(mod, '_subir_lote', lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('boom')))
    monkeypatch.setattr(mod.db.session, 'remove', lambda: None)

    with pytest.raises(RuntimeError):
        mod._run_sync(app, sistemas.id, [('documento', 'x/y.pdf')], 'job-boom')

    assert soltados == ['job-boom']


# ── Consistencia con el script de línea de comandos ───────────────────────────

def test_el_script_usa_la_misma_fuente_de_verdad():
    """El backfill CLI importa `keys_referenciadas` del panel: una sola verdad."""
    ruta = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        'scripts', 'migrar_archivos_a_r2.py')
    with open(ruta, encoding='utf-8') as f:
        fuente = f.read()
    assert 'from app.routes.api_sistemas.archivos import keys_referenciadas' in fuente
