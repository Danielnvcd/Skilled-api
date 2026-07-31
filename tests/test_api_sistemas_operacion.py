"""Tests de las funciones operativas del panel de sistemas.

`test_api_sistemas.py` cubre la SEGURIDAD del rol (quién entra, escalación).
Aquí van las herramientas de operación diaria:

  - Bloqueos: ver quién está bloqueado y liberarlo.
  - Cuentas sin 2FA.
  - Almacenamiento: tamaño de las tablas que crecen sin límite y purga.
  - Imágenes: estado del pipeline hacia R2.
  - Contadores exactos de peticiones.
  - Versión desplegada.
"""
import pyotp
import pytest
from werkzeug.security import generate_password_hash

from app.models import AuditLog, User
from app.routes.api_auth import _encode_access_token


def _hdr(user):
    return {'Authorization': f'Bearer {_encode_access_token(user)}'}


@pytest.fixture
def sistemas(db):
    u = User(username='ops_ti', password_hash=generate_password_hash('SuperPass123!'),
             role='sistemas', password_version=1, totp_secret=pyotp.random_base32())
    db.session.add(u); db.session.commit()
    return u


@pytest.fixture
def sistemas_sin_2fa(db):
    u = User(username='ops_sin2fa', password_hash=generate_password_hash('SuperPass123!'),
             role='sistemas', password_version=1)
    db.session.add(u); db.session.commit()
    return u


@pytest.fixture
def rrhh(db):
    u = User(username='ops_rrhh', password_hash=generate_password_hash('SuperPass123!'),
             role='admin', password_version=1)
    db.session.add(u); db.session.commit()
    return u


@pytest.fixture
def coord(db):
    u = User(username='ops_coord', password_hash=generate_password_hash('SuperPass123!'),
             role='coordinador', password_version=1)
    db.session.add(u); db.session.commit()
    return u


_RUTAS_NUEVAS = [
    '/api/sistemas/bloqueos',
    '/api/sistemas/sin-2fa',
    '/api/sistemas/almacenamiento',
    '/api/sistemas/imagenes',
]


class TestAuthDeLasFuncionesNuevas:
    """Heredan el mismo gate del panel: rol correcto + 2FA activo."""

    @pytest.mark.parametrize('ruta', _RUTAS_NUEVAS)
    def test_sistemas_entra(self, client, sistemas, ruta):
        r = client.get(ruta, headers=_hdr(sistemas))
        assert r.status_code == 200, r.get_json()

    @pytest.mark.parametrize('ruta', _RUTAS_NUEVAS)
    def test_admin_rrhh_no_entra(self, client, rrhh, ruta):
        assert client.get(ruta, headers=_hdr(rrhh)).status_code == 403

    @pytest.mark.parametrize('ruta', _RUTAS_NUEVAS)
    def test_sin_2fa_no_entra(self, client, sistemas_sin_2fa, ruta):
        r = client.get(ruta, headers=_hdr(sistemas_sin_2fa))
        assert r.status_code == 403
        assert r.get_json().get('requiere_2fa') is True

    def test_las_acciones_destructivas_tambien_estan_gateadas(self, client, rrhh):
        assert client.post('/api/sistemas/purgar-bitacora', headers=_hdr(rrhh),
                           json={'meses': 12}).status_code == 403
        assert client.post('/api/sistemas/imagenes/reintentar',
                           headers=_hdr(rrhh)).status_code == 403
        assert client.delete('/api/sistemas/bloqueos/password/x',
                             headers=_hdr(rrhh)).status_code == 403


class _RedisConClaves:
    """Doble de Redis con soporte de scan/ttl/delete sobre un diccionario."""

    def __init__(self, claves):
        self.claves = dict(claves)

    def __bool__(self):
        return True

    def scan(self, cursor=0, match=None, count=100):
        import fnmatch
        return 0, [k for k in self.claves if fnmatch.fnmatch(k, match or '*')]

    def ttl(self, clave):
        return self.claves.get(clave, -2)

    def delete(self, *claves):
        n = 0
        for c in claves:
            if c in self.claves:
                del self.claves[c]
                n += 1
        return n

    def get(self, k):
        return None

    def set(self, *a, **k):
        return True


class TestBloqueos:
    """Liberar un bloqueo: la petición de soporte que el sistema no sabía atender.

    El lockout escalado llega a 24 h; sin esto había que esperar o borrar
    llaves de Redis a mano.
    """

    def test_lista_bloqueos_de_password_y_de_2fa(self, client, sistemas, coord, monkeypatch):
        fake = _RedisConClaves({
            'login_lockout:ops_coord': 600,
            f'twofa_lockout:{coord.id}': 1800,
        })
        monkeypatch.setattr('app.extensions.get_redis', lambda: fake)

        r = client.get('/api/sistemas/bloqueos', headers=_hdr(sistemas))
        assert r.status_code == 200, r.get_json()
        cuerpo = r.get_json()
        assert {b['tipo'] for b in cuerpo} == {'password', '2fa'}
        # Resuelve el username real a partir de la clave, no la muestra cruda.
        assert any(b['username'] == 'ops_coord' for b in cuerpo)


    def test_incluye_el_origen_de_los_intentos(self, client, sistemas, coord, db, monkeypatch):
        """El bloqueo vive en Redis indexado por cuenta, sin IP. El origen sale
        de cruzarlo con la bitácora, y es lo que distingue un despiste del
        usuario de un intento ajeno."""
        from datetime import datetime, timezone
        db.session.add_all([
            AuditLog(user='ops_coord', action="API login fallido para 'ops_coord'",
                     ip='10.0.0.5', created_at=datetime.now(timezone.utc)),
            AuditLog(user='ops_coord', action="API login fallido para 'ops_coord'",
                     ip='10.0.0.5', created_at=datetime.now(timezone.utc)),
            AuditLog(user='ops_coord', action="API login fallido para 'ops_coord'",
                     ip='203.0.113.9', created_at=datetime.now(timezone.utc)),
            # Ruido que NO debe aparecer: no es un intento fallido.
            AuditLog(user='ops_coord', action='Exportó un reporte',
                     ip='10.0.0.5', created_at=datetime.now(timezone.utc)),
        ])
        db.session.commit()

        monkeypatch.setattr('app.extensions.get_redis',
                            lambda: _RedisConClaves({'login_lockout:ops_coord': 600}))
        r = client.get('/api/sistemas/bloqueos', headers=_hdr(sistemas))
        assert r.status_code == 200, r.get_json()
        fila = next(b for b in r.get_json() if b['username'] == 'ops_coord')

        por_ip = {o['ip']: o['intentos'] for o in fila['origenes']}
        assert por_ip == {'10.0.0.5': 2, '203.0.113.9': 1}, por_ip
        # Las IP con más intentos primero: es lo que se mira al triar.
        assert fila['origenes'][0]['ip'] == '10.0.0.5'

    def test_sin_registros_en_bitacora_no_inventa_origen(
        self, client, sistemas, monkeypatch,
    ):
        monkeypatch.setattr('app.extensions.get_redis',
                            lambda: _RedisConClaves({'login_lockout:fantasma': 600}))
        r = client.get('/api/sistemas/bloqueos', headers=_hdr(sistemas))
        assert r.status_code == 200
        assert r.get_json()[0]['origenes'] == []

    def test_liberar_borra_tambien_el_nivel_de_escalacion(self, client, sistemas, monkeypatch):
        """Si solo se quitara el bloqueo, el siguiente fallo dispararía el
        escalón siguiente (30 min, 1 h, 3 h…) y la persona volvería a quedar
        fuera casi de inmediato. Liberar debe dejar la cuenta como nueva."""
        fake = _RedisConClaves({
            'login_lockout:pepe': 600,
            'login_fails:pepe': 5,
            'login_lockout_level:pepe': 3,
        })
        monkeypatch.setattr('app.extensions.get_redis', lambda: fake)

        r = client.delete('/api/sistemas/bloqueos/password/pepe', headers=_hdr(sistemas))
        assert r.status_code == 200, r.get_json()
        assert fake.claves == {}, f'quedaron llaves sin borrar: {fake.claves}'

    def test_liberar_bloqueo_de_2fa(self, client, sistemas, coord, monkeypatch):
        fake = _RedisConClaves({
            f'twofa_lockout:{coord.id}': 600,
            f'twofa_fails:{coord.id}': 5,
            f'twofa_lockout_level:{coord.id}': 2,
        })
        monkeypatch.setattr('app.extensions.get_redis', lambda: fake)

        r = client.delete(f'/api/sistemas/bloqueos/2fa/{coord.id}', headers=_hdr(sistemas))
        assert r.status_code == 200, r.get_json()
        assert fake.claves == {}

    def test_sin_bloqueo_activo_404(self, client, sistemas, monkeypatch):
        monkeypatch.setattr('app.extensions.get_redis', lambda: _RedisConClaves({}))
        r = client.delete('/api/sistemas/bloqueos/password/nadie', headers=_hdr(sistemas))
        assert r.status_code == 404

    def test_tipo_de_bloqueo_invalido_400(self, client, sistemas):
        assert client.delete('/api/sistemas/bloqueos/inventado/x',
                             headers=_hdr(sistemas)).status_code == 400

    def test_nunca_se_usa_KEYS_contra_redis(self):
        """`KEYS` recorre todo el espacio de claves BLOQUEANDO a Redis.

        Con Redis compartido por los 4 workers de gunicorn y consultado en cada
        petición autenticada (blacklist de jti), un KEYS congelaría la
        aplicación entera. Este test fija que solo se use SCAN.
        """
        import pathlib
        import re
        ofensores = []
        for archivo in pathlib.Path('app').rglob('*.py'):
            src = archivo.read_text(encoding='utf-8', errors='replace')
            if re.search(r'\br\.keys\s*\(', src):
                ofensores.append(str(archivo))
        assert not ofensores, f'usan KEYS en vez de SCAN: {ofensores}'


class TestCuentasSin2fa:

    def test_marca_los_roles_sensibles(self, client, sistemas, rrhh, coord):
        r = client.get('/api/sistemas/sin-2fa', headers=_hdr(sistemas))
        assert r.status_code == 200
        cuerpo = r.get_json()
        por_usuario = {u['username']: u for u in cuerpo['usuarios']}
        assert por_usuario['ops_rrhh']['sensible'] is True    # admin
        assert por_usuario['ops_coord']['sensible'] is False  # coordinador
        assert cuerpo['sensibles_sin_2fa'] >= 1

    def test_no_incluye_a_quien_si_tiene_2fa(self, client, sistemas):
        r = client.get('/api/sistemas/sin-2fa', headers=_hdr(sistemas))
        nombres = {u['username'] for u in r.get_json()['usuarios']}
        assert 'ops_ti' not in nombres


class TestAlmacenamientoYPurga:

    def test_degrada_en_sqlite_sin_fallar(self, client, sistemas):
        """El tamaño en disco solo lo da PostgreSQL. En SQLite —lo que usan los
        tests— debe devolver el conteo de filas y bytes=None, no reventar."""
        r = client.get('/api/sistemas/almacenamiento', headers=_hdr(sistemas))
        assert r.status_code == 200, r.get_json()
        cuerpo = r.get_json()
        assert cuerpo['tamano_disponible'] is False
        assert any(t['tabla'] == 'audit_log' for t in cuerpo['tablas'])
        assert all(t['bytes'] is None for t in cuerpo['tablas'])


    def test_las_tablas_vigiladas_existen_de_verdad(self, app):
        """Un nombre de tabla mal escrito no rompe nada: la vista simplemente
        muestra `None` filas y nadie se entera. Pasó con `movimientos`, que en
        realidad se llama `movimientos_inventario`. Este test lo detecta."""
        from sqlalchemy import inspect
        from app.extensions import db
        from app.routes.api_sistemas.mantenimiento import _TABLAS_VIGILADAS
        with app.app_context():
            reales = set(inspect(db.engine).get_table_names())
        declaradas = {t for t, _ in _TABLAS_VIGILADAS}
        faltantes = declaradas - reales
        assert not faltantes, f'tablas vigiladas que no existen: {faltantes}'

    def test_la_vista_reporta_filas_para_todas(self, client, sistemas):
        """Complemento del anterior: ninguna tabla debe salir con filas=None,
        que es el sintoma de un nombre incorrecto."""
        r = client.get('/api/sistemas/almacenamiento', headers=_hdr(sistemas))
        sin_datos = [t['tabla'] for t in r.get_json()['tablas'] if t['filas'] is None]
        assert not sin_datos, f'tablas sin conteo (nombre incorrecto?): {sin_datos}'

    def test_purga_rechaza_menos_de_tres_meses(self, client, sistemas):
        """Vaciar la bitácora reciente es justo lo que no debe poder hacerse:
        es la que sirve para investigar un incidente."""
        r = client.post('/api/sistemas/purgar-bitacora', headers=_hdr(sistemas),
                        json={'meses': 1})
        assert r.status_code == 400

    def test_purga_borra_solo_lo_antiguo(self, client, sistemas, db):
        from datetime import datetime, timedelta, timezone
        db.session.add_all([
            AuditLog(user='x', action='muy antiguo', ip='1.1.1.1',
                     created_at=datetime.now(timezone.utc) - timedelta(days=800)),
            AuditLog(user='x', action='de ayer', ip='1.1.1.1',
                     created_at=datetime.now(timezone.utc) - timedelta(days=1)),
        ])
        db.session.commit()

        r = client.post('/api/sistemas/purgar-bitacora', headers=_hdr(sistemas),
                        json={'meses': 12})
        assert r.status_code == 200, r.get_json()
        acciones = {a.action for a in AuditLog.query.all()}
        assert 'muy antiguo' not in acciones
        assert 'de ayer' in acciones

    def test_la_purga_queda_registrada_en_la_bitacora(self, client, sistemas, db):
        from datetime import datetime, timedelta, timezone
        db.session.add(AuditLog(user='x', action='antiguo', ip='1.1.1.1',
                                created_at=datetime.now(timezone.utc) - timedelta(days=800)))
        db.session.commit()

        client.post('/api/sistemas/purgar-bitacora', headers=_hdr(sistemas),
                    json={'meses': 12})
        acciones = [a.action for a in AuditLog.query.all()]
        assert any('purgó la bitácora' in a for a in acciones), \
            'la purga debe dejar constancia de quién la ejecutó'


class TestImagenesR2:

    def test_reporta_estados_del_pipeline(self, client, sistemas):
        r = client.get('/api/sistemas/imagenes', headers=_hdr(sistemas))
        assert r.status_code == 200
        cuerpo = r.get_json()
        assert 'total_error' in cuerpo
        assert 'fallidos' in cuerpo

    def test_reintentar_sin_errores_no_falla(self, client, sistemas):
        r = client.post('/api/sistemas/imagenes/reintentar', headers=_hdr(sistemas))
        assert r.status_code == 200, r.get_json()
        assert r.get_json()['reencoladas'] == 0


class TestContadoresExactos:
    """Métricas: totales reales, sin muestreo. Complementan el buffer de muestras."""

    def test_tramos_del_histograma(self):
        from app.observabilidad import _tramo_histograma
        assert _tramo_histograma(10) == 'h:<=50'
        assert _tramo_histograma(50) == 'h:<=50'
        assert _tramo_histograma(51) == 'h:<=100'
        assert _tramo_histograma(99999) == 'h:>3000'

    def test_percentiles_desde_histograma(self):
        from app.observabilidad import _percentiles_desde_histograma
        # 950 rápidas y 50 muy lentas: el p50 abajo, el p99 arriba.
        pct = _percentiles_desde_histograma({'<=50': 950, '>3000': 50}, 1000)
        assert pct['p50'] == '<=50'
        assert pct['p99'] == '>3000'

    def test_detalle_del_dia(self):
        from app.observabilidad import _detalle_dia
        d = _detalle_dia({
            'total': '100', 'errores': '5', 'lentas': '2', 'ms_suma': '2500',
            'r:GET /api/x': '50', 't:GET /api/x': '1000',
            's:200': '95', 's:403': '5', 'h:<=50': '100',
        })
        assert d['total'] == 100
        assert d['ms_promedio'] == 25.0
        assert d['por_ruta'][0]['ms_promedio'] == 20.0
        assert {s['status'] for s in d['por_status']} == {'200', '403'}

    def test_dia_vacio_no_revienta(self):
        from app.observabilidad import _detalle_dia
        assert _detalle_dia({})['total'] == 0

    def test_sin_redis_reporta_no_disponible(self, app, monkeypatch):
        from app.observabilidad import leer_contadores
        monkeypatch.setattr('app.extensions.get_redis', lambda: None)
        with app.app_context():
            assert leer_contadores(7)['disponible'] is False

    def test_el_endpoint_los_incluye(self, client, sistemas):
        r = client.get('/api/sistemas/peticiones', headers=_hdr(sistemas))
        assert r.status_code == 200
        assert 'contadores' in r.get_json()

    def test_los_contadores_no_tumban_la_peticion_si_redis_falla(self, client, sistemas):
        """Se incrementan en TODA petición, así que son el punto más sensible:
        si fallaran, caería la aplicación entera. En tests no hay Redis y todo
        debe seguir respondiendo 200."""
        assert client.get('/api/sistemas/estado', headers=_hdr(sistemas)).status_code == 200


class TestVersionDesplegada:

    def test_el_estado_reporta_la_version(self, client, sistemas):
        """Nace de una confusión real: se diagnosticó un fallo que resultó ser
        que producción corría una versión anterior del código."""
        r = client.get('/api/sistemas/estado', headers=_hdr(sistemas))
        assert r.status_code == 200
        version = r.get_json()['version']
        assert 'origen' in version
        assert version['origen'] in ('git', 'variable de entorno', 'desconocido')


    def test_lee_la_version_de_un_archivo_VERSION(self, client, sistemas, app, monkeypatch):
        """El camino recomendado en el servidor: el despliegue escribe el commit
        en un archivo. No necesita que el repo ni el binario de git existan."""
        import os
        import app.routes.api_sistemas.endpoints as ep
        monkeypatch.setattr(ep, '_VERSION_CACHE', None)
        monkeypatch.delenv('APP_COMMIT', raising=False)
        monkeypatch.delenv('APP_VERSION', raising=False)

        ruta = os.path.join(app.config['BASE_DIR'], 'VERSION')
        with open(ruta, 'w', encoding='utf-8') as f:
            f.write('abc1234|2026-07-30T21:00:00-06:00|mensaje del commit')
        try:
            r = client.get('/api/sistemas/estado', headers=_hdr(sistemas))
            v = r.get_json()['version']
            assert v['origen'] == 'archivo VERSION'
            assert v['commit'] == 'abc1234'
            assert v['asunto'] == 'mensaje del commit'
        finally:
            os.remove(ruta)

    def test_la_variable_de_entorno_gana_sobre_todo(self, client, sistemas, monkeypatch):
        import app.routes.api_sistemas.endpoints as ep
        monkeypatch.setattr(ep, '_VERSION_CACHE', None)
        monkeypatch.setenv('APP_COMMIT', 'env12345')
        r = client.get('/api/sistemas/estado', headers=_hdr(sistemas))
        v = r.get_json()['version']
        assert v['origen'] == 'variable de entorno'
        assert v['commit'] == 'env12345'

    def test_si_falla_explica_el_motivo(self, client, sistemas, monkeypatch):
        """Un «desconocido» sin explicación no deja corregir nada. El caso real:
        en producción no salía la versión y no había forma de saber por qué."""
        import subprocess
        import app.routes.api_sistemas.endpoints as ep
        monkeypatch.setattr(ep, '_VERSION_CACHE', None)
        monkeypatch.delenv('APP_COMMIT', raising=False)
        monkeypatch.delenv('APP_VERSION', raising=False)

        def _sin_git(*a, **k):
            raise FileNotFoundError()
        monkeypatch.setattr(subprocess, 'run', _sin_git)

        r = client.get('/api/sistemas/estado', headers=_hdr(sistemas))
        v = r.get_json()['version']
        assert v['origen'] == 'desconocido'
        assert v['detalle'], 'debe decir POR QUÉ no se pudo leer la versión'
        assert 'git' in v['detalle'].lower()

    def test_no_falla_si_git_no_esta_disponible(self, client, sistemas, monkeypatch):
        """En un despliegue que no lleva el repo, no saber la versión no puede
        tirar el panel."""
        import app.routes.api_sistemas.endpoints as ep
        monkeypatch.setattr(ep, '_VERSION_CACHE', None)
        monkeypatch.setenv('APP_COMMIT', '')
        monkeypatch.setattr('subprocess.run', lambda *a, **k: (_ for _ in ()).throw(OSError('sin git')))
        r = client.get('/api/sistemas/estado', headers=_hdr(sistemas))
        assert r.status_code == 200
        assert r.get_json()['version']['origen'] == 'desconocido'
