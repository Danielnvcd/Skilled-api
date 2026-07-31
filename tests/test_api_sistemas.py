"""Tests del rol `sistemas` y su panel (`/api/sistemas/*`).

El foco de este archivo es la SEGURIDAD del rol, no su funcionalidad: es el rol
más privilegiado de uso diario (crea cuentas, revoca sesiones), así que lo que
hay que blindar es que no pueda escalar y que nadie más pueda entrar.

Invariantes cubiertos:
  - Solo `sistemas`/`super_admin` entran al panel; admin/RRHH NO.
  - El panel exige 2FA activo, aunque el rol sea correcto.
  - `sistemas` no puede fabricarse un `super_admin` ni tocar esa cuenta.
  - `sistemas` SÍ puede contener a un admin comprometido (revocar/resetear).
  - Admin/RRHH perdió la gestión de usuarios.
  - La observabilidad no filtra IDs crudos ni tumba requests si Redis cae.
"""
import pyotp
import pytest
from werkzeug.security import generate_password_hash

from app.models import AuditLog, RefreshToken, User
from app.routes.api_auth import _encode_access_token


def _hdr(user):
    return {'Authorization': f'Bearer {_encode_access_token(user)}'}


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def sistemas(db):
    """Cuenta de TI con 2FA activo — el estado normal del rol."""
    secret = pyotp.random_base32()
    u = User(username='sis_ti', password_hash=generate_password_hash('SuperPass123!'),
             role='sistemas', password_version=1, totp_secret=secret)
    db.session.add(u); db.session.commit()
    u._test_totp_secret = secret
    return u


@pytest.fixture
def sistemas_sin_2fa(db):
    u = User(username='sis_sin2fa', password_hash=generate_password_hash('SuperPass123!'),
             role='sistemas', password_version=1)
    db.session.add(u); db.session.commit()
    return u


@pytest.fixture
def rrhh(db):
    """Admin de RRHH: tras la separación de ejes, NO administra cuentas."""
    u = User(username='sis_rrhh', password_hash=generate_password_hash('SuperPass123!'),
             role='admin', password_version=1)
    db.session.add(u); db.session.commit()
    return u


@pytest.fixture
def super_admin(db):
    u = User(username='sis_super', password_hash=generate_password_hash('SuperPass123!'),
             role='super_admin', password_version=1)
    db.session.add(u); db.session.commit()
    return u


@pytest.fixture
def coord(db):
    u = User(username='sis_coord', password_hash=generate_password_hash('SuperPass123!'),
             role='coordinador', password_version=1)
    db.session.add(u); db.session.commit()
    return u


_RUTAS_PANEL = [
    ('get', '/api/sistemas/estado'),
    ('get', '/api/sistemas/peticiones'),
    ('get', '/api/sistemas/sesiones'),
    ('get', '/api/sistemas/eventos-seguridad'),
]


# ─── Acceso al panel ──────────────────────────────────────────────────────────

class TestAccesoAlPanel:

    @pytest.mark.parametrize('metodo,ruta', _RUTAS_PANEL)
    def test_sin_token_401(self, client, metodo, ruta):
        assert getattr(client, metodo)(ruta).status_code == 401

    @pytest.mark.parametrize('metodo,ruta', _RUTAS_PANEL)
    def test_sistemas_con_2fa_entra(self, client, sistemas, metodo, ruta):
        r = getattr(client, metodo)(ruta, headers=_hdr(sistemas))
        assert r.status_code == 200, r.get_json()

    @pytest.mark.parametrize('metodo,ruta', _RUTAS_PANEL)
    def test_admin_rrhh_no_entra(self, client, rrhh, metodo, ruta):
        """El eje de permisos de RRHH es independiente del de sistemas."""
        r = getattr(client, metodo)(ruta, headers=_hdr(rrhh))
        assert r.status_code == 403

    @pytest.mark.parametrize('metodo,ruta', _RUTAS_PANEL)
    def test_coordinador_no_entra(self, client, coord, metodo, ruta):
        assert getattr(client, metodo)(ruta, headers=_hdr(coord)).status_code == 403

    @pytest.mark.parametrize('metodo,ruta', _RUTAS_PANEL)
    def test_sistemas_sin_2fa_no_entra(self, client, sistemas_sin_2fa, metodo, ruta):
        """Rol correcto pero sin segundo factor: el panel no abre.

        Es el rol que puede crear cuentas y revocar sesiones; una contraseña
        filtrada no puede alcanzar para entrar.
        """
        r = getattr(client, metodo)(ruta, headers=_hdr(sistemas_sin_2fa))
        assert r.status_code == 403
        assert r.get_json().get('requiere_2fa') is True

    def test_sin_2fa_el_login_normal_sigue_funcionando(self, client, sistemas_sin_2fa):
        """El 2FA se exige en el PANEL, no en el login: nadie queda fuera de la
        aplicación por no haber inscrito su TOTP todavía."""
        r = client.post('/api/auth/login', json={
            'username': 'sis_sin2fa', 'password': 'SuperPass123!',
        })
        assert r.status_code == 200
        assert 'token' in r.get_json()

    def test_super_admin_entra_como_recuperacion(self, client, super_admin, db):
        """super_admin es la salida de emergencia si la cuenta de sistemas se
        bloquea. Se le exige 2FA igual que a sistemas."""
        r = client.get('/api/sistemas/estado', headers=_hdr(super_admin))
        assert r.status_code == 403
        assert r.get_json().get('requiere_2fa') is True

        super_admin.totp_secret = pyotp.random_base32()
        db.session.commit()
        assert client.get('/api/sistemas/estado', headers=_hdr(super_admin)).status_code == 200


# ─── Anti-escalación ──────────────────────────────────────────────────────────

class TestAntiEscalacion:

    def test_sistemas_no_puede_crear_super_admin(self, client, sistemas):
        r = client.post('/api/users', headers=_hdr(sistemas), json={
            'username': 'nuevo_super', 'password': 'SuperPass1234!', 'role': 'super_admin',
        })
        assert r.status_code == 403
        assert User.query.filter_by(username='nuevo_super').first() is None

    def test_sistemas_no_puede_revocar_sesiones_de_super_admin(
        self, client, sistemas, super_admin, db,
    ):
        r = client.delete(f'/api/users/{super_admin.id}/sessions', headers=_hdr(sistemas))
        assert r.status_code == 403

    def test_sistemas_no_puede_resetear_password_de_super_admin(
        self, client, sistemas, super_admin,
    ):
        r = client.post(f'/api/users/{super_admin.id}/password', headers=_hdr(sistemas),
                        json={'new_password': 'OtraPass1234!'})
        assert r.status_code == 403

    def test_sistemas_no_puede_desactivar_super_admin(self, client, sistemas, super_admin):
        r = client.delete(f'/api/users/{super_admin.id}', headers=_hdr(sistemas))
        assert r.status_code == 403

    def test_sistemas_si_puede_contener_a_un_admin_comprometido(
        self, client, sistemas, rrhh, db,
    ):
        """Contrapartida de lo anterior: cerrarle la sesión a una cuenta
        comprometida es justamente para lo que existe el rol. Si esto se
        bloqueara, un incidente se quedaría sin quién lo contenga."""
        pv_antes = rrhh.password_version
        r = client.delete(f'/api/users/{rrhh.id}/sessions', headers=_hdr(sistemas))
        assert r.status_code == 200, r.get_json()
        db.session.refresh(rrhh)
        assert rrhh.password_version == pv_antes + 1

    def test_resetear_password_ajena_no_borra_su_2fa(self, client, sistemas, coord, db):
        """Aunque sistemas resetee una contraseña, el segundo factor del usuario
        sobrevive — el reseteo por sí solo no da acceso a la cuenta."""
        coord.totp_secret = pyotp.random_base32()
        db.session.commit()
        secreto_antes = coord.totp_secret

        r = client.post(f'/api/users/{coord.id}/password', headers=_hdr(sistemas),
                        json={'new_password': 'NuevaPass1234!'})
        assert r.status_code == 200
        db.session.refresh(coord)
        assert coord.totp_secret == secreto_antes


# ─── La gestión de usuarios salió de admin ────────────────────────────────────

class TestGestionUsuariosMovida:

    def test_admin_rrhh_ya_no_lista_usuarios(self, client, rrhh):
        assert client.get('/api/users', headers=_hdr(rrhh)).status_code == 403

    def test_admin_rrhh_ya_no_crea_usuarios(self, client, rrhh):
        r = client.post('/api/users', headers=_hdr(rrhh), json={
            'username': 'creado_por_rrhh', 'password': 'SuperPass1234!', 'role': 'coordinador',
        })
        assert r.status_code == 403
        assert User.query.filter_by(username='creado_por_rrhh').first() is None

    def test_admin_rrhh_ya_no_revoca_sesiones(self, client, rrhh, coord):
        assert client.delete(
            f'/api/users/{coord.id}/sessions', headers=_hdr(rrhh),
        ).status_code == 403

    def test_sistemas_si_crea_usuarios(self, client, sistemas):
        r = client.post('/api/users', headers=_hdr(sistemas), json={
            'username': 'creado_por_ti', 'password': 'SuperPass1234!', 'role': 'coordinador',
        })
        assert r.status_code == 201, r.get_json()
        assert r.get_json()['role'] == 'coordinador'

    def test_sistemas_puede_crear_otro_sistemas(self, client, sistemas):
        """TI necesita poder dar de alta a sus pares sin depender de super_admin."""
        r = client.post('/api/users', headers=_hdr(sistemas), json={
            'username': 'otro_ti', 'password': 'SuperPass1234!', 'role': 'sistemas',
        })
        assert r.status_code == 201, r.get_json()

    def test_la_gestion_de_usuarios_no_exige_2fa(self, client, sistemas_sin_2fa):
        """El 2FA se exige en el PANEL. `/api/users` conserva su propio gate para
        no dejar a la organización sin poder crear cuentas durante la migración
        al rol nuevo."""
        r = client.get('/api/users', headers=_hdr(sistemas_sin_2fa))
        assert r.status_code == 200


# ─── Sesiones ─────────────────────────────────────────────────────────────────

class TestSesionesGlobales:

    def _crear_rt(self, db, user):
        import secrets
        from datetime import datetime, timedelta, timezone
        from app.routes.api_auth._core import _hash_token
        raw = secrets.token_urlsafe(32)
        t = RefreshToken(token_hash=_hash_token(raw), user_id=user.id,
                         expires_at=datetime.now(timezone.utc) + timedelta(days=7))
        db.session.add(t); db.session.commit()
        return t

    def test_lista_sesiones_de_todos_los_usuarios(self, client, sistemas, coord, db):
        self._crear_rt(db, coord)
        r = client.get('/api/sistemas/sesiones', headers=_hdr(sistemas))
        assert r.status_code == 200
        usuarios = {s['username'] for s in r.get_json()}
        assert 'sis_coord' in usuarios

    def test_revoca_una_sesion_ajena(self, client, sistemas, coord, db):
        t = self._crear_rt(db, coord)
        r = client.delete(f'/api/sistemas/sesiones/{t.id}', headers=_hdr(sistemas))
        assert r.status_code == 200
        db.session.refresh(t)
        assert t.revoked is True

    def test_revocar_una_sesion_no_expulsa_de_las_demas(self, client, sistemas, coord, db):
        """Cerrar UNA sesión sospechosa no debe tirar al usuario de todos sus
        dispositivos: para eso está `/api/users/<id>/sessions`."""
        pv_antes = coord.password_version
        t = self._crear_rt(db, coord)
        client.delete(f'/api/sistemas/sesiones/{t.id}', headers=_hdr(sistemas))
        db.session.refresh(coord)
        assert coord.password_version == pv_antes

    def test_no_revoca_sesiones_de_super_admin(self, client, sistemas, super_admin, db):
        t = self._crear_rt(db, super_admin)
        r = client.delete(f'/api/sistemas/sesiones/{t.id}', headers=_hdr(sistemas))
        assert r.status_code == 403
        db.session.refresh(t)
        assert t.revoked is False

    def test_sesion_inexistente_404(self, client, sistemas):
        assert client.delete('/api/sistemas/sesiones/999999',
                             headers=_hdr(sistemas)).status_code == 404


# ─── Observabilidad ───────────────────────────────────────────────────────────

class TestObservabilidad:
    """El registro de peticiones no debe filtrar datos ni afectar la app."""

    def test_no_guarda_urls_crudas_sino_la_regla_de_ruta(self, app):
        """Clave de privacidad: el panel lo ve `sistemas`, que a propósito NO
        tiene acceso a RRHH. Guardar `/api/auth/users/47` filtraría por la
        puerta de atrás qué empleado se consultó."""
        from app.observabilidad import _ruta_normalizada
        with app.test_request_context('/api/auth/users/47'):
            ruta = _ruta_normalizada()
        assert '47' not in ruta
        assert '<int:user_id>' in ruta

    def test_ruta_desconocida_no_se_almacena(self, app):
        """Un escáner probando rutas no debe poder llenar el buffer con sus
        propias URLs. Cubre también los 405: si el método no corresponde a
        ninguna regla, tampoco se guarda el path crudo."""
        from app.observabilidad import _ruta_normalizada
        with app.test_request_context('/ruta/que/no/existe/secreta'):
            assert _ruta_normalizada() == '<desconocida>'
        # 405: la ruta existe pero solo para PUT/DELETE.
        with app.test_request_context('/api/users/47', method='GET'):
            ruta = _ruta_normalizada()
        assert '47' not in ruta

    def test_los_errores_siempre_se_registran(self):
        from app.observabilidad import _debe_registrar
        assert _debe_registrar(500, 1.0) is True
        assert _debe_registrar(403, 1.0) is True

    def test_las_lentas_siempre_se_registran(self):
        from app.observabilidad import _debe_registrar
        assert _debe_registrar(200, 5000.0) is True

    def test_sin_redis_las_requests_siguen_funcionando(self, client, sistemas):
        """En tests no hay Redis: el registro degrada y la app responde igual."""
        assert client.get('/api/sistemas/estado', headers=_hdr(sistemas)).status_code == 200

    def test_redis_caido_no_tumba_las_requests(self, client, sistemas, monkeypatch):
        class _RedisMuerto:
            def __bool__(self):
                return True

            def _explotar(self, *a, **k):
                import redis
                raise redis.exceptions.ConnectionError('caído')

            pipeline = get = set = setex = ttl = incr = expire = delete = ping = _explotar
            lrange = _explotar

        monkeypatch.setattr('app.extensions.get_redis', lambda: _RedisMuerto())
        r = client.get('/api/sistemas/estado', headers=_hdr(sistemas))
        assert r.status_code == 200
        assert r.get_json()['redis']['ok'] is False
        assert len(r.get_json()['defensas_degradadas']) > 0

    def test_el_resumen_no_revienta_sin_eventos(self):
        from app.observabilidad import resumen
        assert resumen([])['total'] == 0

    def test_el_resumen_agrega_por_ruta(self):
        from app.observabilidad import resumen
        eventos = [
            {'metodo': 'GET', 'ruta': '/api/a', 'status': 200, 'ms': 10},
            {'metodo': 'GET', 'ruta': '/api/a', 'status': 500, 'ms': 30},
            {'metodo': 'GET', 'ruta': '/api/b', 'status': 200, 'ms': 900},
        ]
        res = resumen(eventos)
        assert res['total'] == 3
        assert res['errores'] == 1
        assert res['lentas'] == 1
        # Las rutas más lentas primero: es lo que se busca al abrir el panel.
        assert res['por_ruta'][0]['ruta'] == '/api/b'
        fila_a = next(f for f in res['por_ruta'] if f['ruta'] == '/api/a')
        assert fila_a['conteo'] == 2 and fila_a['errores'] == 1


# ─── Eventos de seguridad ─────────────────────────────────────────────────────

class TestEventosSeguridad:

    def test_filtra_solo_lo_relevante(self, client, sistemas, db):
        db.session.add(AuditLog(user='alguien', action='API login fallido para x', ip='1.1.1.1'))
        db.session.add(AuditLog(user='alguien', action='Exportó reporte de nómina', ip='1.1.1.1'))
        db.session.commit()

        r = client.get('/api/sistemas/eventos-seguridad', headers=_hdr(sistemas))
        assert r.status_code == 200
        acciones = [e['accion'] for e in r.get_json()]
        assert any('login fallido' in a for a in acciones)
        assert not any('nómina' in a for a in acciones)

    def test_respeta_el_tope_de_limite(self, client, sistemas, db):
        for i in range(10):
            db.session.add(AuditLog(user='u', action=f'API login fallido {i}', ip='1.1.1.1'))
        db.session.commit()
        r = client.get('/api/sistemas/eventos-seguridad?limite=3', headers=_hdr(sistemas))
        assert len(r.get_json()) == 3


# ─── Acceso a empleados: picker sí, padrón no ─────────────────────────────────

class TestSistemasYDatosDeRRHH:
    """`sistemas` administra cuentas, así que necesita ELEGIR a un empleado para
    ligarlo (`trabajador_id`), pero no debe poder consultar el padrón.

    La pantalla de Usuarios pedía el listado completo y recibía 403 al abrir el
    modal de edición. El arreglo no fue abrir el padrón, sino usar el picker
    ligero que ya existía para el rol `inventario` por la misma razón.
    """

    @pytest.fixture
    def trabajador(self, db):
        from app.models import Trabajador
        t = Trabajador(no_empleado='SIS-001', nombre='Ana', nombre_apellidos='López',
                       activo=True, tipo_nomina='Semanal',
                       salario_real_pactado_x_sem=7000)
        db.session.add(t); db.session.commit()
        return t

    def test_puede_usar_el_picker_ligero(self, client, sistemas, trabajador):
        r = client.get('/api/trabajadores/para-asignar', headers=_hdr(sistemas))
        assert r.status_code == 200, r.get_json()
        items = r.get_json()['items']
        assert any(i['no_empleado'] == 'SIS-001' for i in items)

    def test_el_picker_no_expone_datos_sensibles(self, client, sistemas, trabajador):
        """Lo que hace aceptable abrir este endpoint es justamente que NO trae
        sueldo ni documentos. Si alguien agrega esos campos, este test cae."""
        r = client.get('/api/trabajadores/para-asignar', headers=_hdr(sistemas))
        campos = set(r.get_json()['items'][0].keys())
        prohibidos = {'salario_real_pactado_x_sem', 'curp', 'rfc', 'nss',
                      'area', 'puesto', 'tipo_pago', 'tipo_nomina'}
        assert not (campos & prohibidos), f'el picker filtró PII: {campos & prohibidos}'

    def test_sigue_sin_poder_ver_el_padron_completo(self, client, sistemas, trabajador):
        """El listado normal expone sueldo, RFC y área: sigue siendo de RRHH."""
        r = client.get('/api/trabajadores', headers=_hdr(sistemas))
        assert r.status_code == 403

    def test_sigue_sin_poder_ver_la_nomina(self, client, sistemas):
        assert client.get('/api/dashboard', headers=_hdr(sistemas)).status_code == 403
