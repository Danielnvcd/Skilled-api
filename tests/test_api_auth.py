"""Tests del API JWT `/api/auth/*` — login, refresh, logout, perfil, sesiones.

Cobertura:
  - POST   /login                       login básico (con/sin 2FA)
  - POST   /verify-2fa                  segundo factor TOTP
  - POST   /refresh                     rotación de RT (con CSRF gate)
  - POST   /logout                      cierre + cookie cleanup
  - GET    /me                          perfil propio
  - GET    /me/activity                 audit log propio
  - GET    /users                       directorio (HIGH-01: PII por rol)
  - GET    /users/<id>                  detalle (self/admin ve todo)
  - GET    /users/<id>/foto             servir foto (path-traversal guard)
  - POST   /profile                     actualizar perfil + foto
  - DELETE /profile/foto                limpiar foto
  - POST   /change-password/<id>        cambio propio (con TOTP si aplica)
  - GET    /sessions                    listar RTs activos propios
  - DELETE /sessions/<id>               revocar uno
  - DELETE /sessions/all                pánico: revoca todo + ++password_version

NO se prueban aquí (requieren Redis o flujo Fernet completo):
  - lockout escalado por username/IP
  - /setup-2fa, /confirm-2fa, /disable-2fa
  - /backup-codes
  - anti-replay TOTP, race-vs-replay del RT

Reglas no obvias:
  - /refresh y /logout exigen header `X-Requested-With: XMLHttpRequest` —
    bloquea CSRF cuando RT_COOKIE_SAMESITE=None.
  - /change-password requiere TOTP si el usuario tiene `totp_secret`.
  - /users devuelve vista PÚBLICA (sin role/totp/last_seen) si el solicitante
    no es admin — HIGH-01 fix contra enumeración de admins sin 2FA.
"""
from datetime import datetime, timedelta, timezone

import pyotp
import pytest
from werkzeug.security import generate_password_hash

from app.extensions import db as flask_db
from app.models import AuditLog, RefreshToken, User
from app.routes.api_auth import _encode_access_token


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _hdr(user):
    return {'Authorization': f'Bearer {_encode_access_token(user)}'}


def _csrf_hdr(user=None):
    """Headers para endpoints protegidos por cookie (refresh/logout)."""
    h = {'X-Requested-With': 'XMLHttpRequest'}
    if user is not None:
        h['Authorization'] = f'Bearer {_encode_access_token(user)}'
    return h


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def au_admin(db):
    u = User(username='au_admin',
              password_hash=generate_password_hash('SuperPass1!'),
              role='admin', password_version=1)
    db.session.add(u); db.session.commit()
    return u


@pytest.fixture
def au_coord(db):
    u = User(username='au_coord',
              password_hash=generate_password_hash('SuperPass1!'),
              role='coordinador', password_version=1)
    db.session.add(u); db.session.commit()
    return u


@pytest.fixture
def au_otro(db):
    """Usuario "objetivo" — para probar PII por rol y permisos cruzados."""
    u = User(username='au_otro',
              password_hash=generate_password_hash('SuperPass1!'),
              role='inventario', password_version=1)
    db.session.add(u); db.session.commit()
    return u


@pytest.fixture
def au_totp_user(db):
    """Usuario con TOTP activo (secret base32 cifrado por EncryptedString)."""
    secret = pyotp.random_base32()
    u = User(username='au_totp',
              password_hash=generate_password_hash('SuperPass1!'),
              role='admin', password_version=1)
    u.totp_secret = secret
    db.session.add(u); db.session.commit()
    # Stash del secret en el objeto para que los tests lo lean
    u._test_totp_secret = secret
    return u


def _set_rt_cookie(client, raw_value):
    """Coloca la cookie de refresh token en el client. Usa el nombre `rt_api`.

    Werkzeug 2.4+ cambió la firma de `set_cookie`: ya no acepta el dominio
    como primer argumento posicional. Pasamos solo (key, value).
    """
    client.set_cookie('rt_api', raw_value)


def _get_rt_cookie(client):
    """Devuelve la cookie `rt_api` del client, o None.

    La cookie se emite acotada a `/api/auth` (antes iba en `path='/'`), y
    `client.get_cookie()` busca por path exacto — de ahí que haya que pedirla
    explícitamente. Se deja el fallback a `/` para que el helper siga sirviendo
    si algún test simula el estado legacy previo a la migración.
    """
    from app.routes.api_auth._core import _RT_COOKIE_PATH
    return (
        client.get_cookie('rt_api', path=_RT_COOKIE_PATH)
        or client.get_cookie('rt_api')
    )


def _crear_rt(db, user, *, raw=None, revoked=False, expires_in_days=7):
    """Crea un RefreshToken válido y devuelve (instancia, raw_value)."""
    from app.routes.api_auth._core import _hash_token
    import secrets
    if raw is None:
        raw = secrets.token_urlsafe(32)
    t = RefreshToken(
        token_hash=_hash_token(raw),
        user_id=user.id,
        expires_at=datetime.now(timezone.utc) + timedelta(days=expires_in_days),
        revoked=revoked,
    )
    db.session.add(t); db.session.commit()
    return t, raw


# ═══════════════════════════════════════════════════════════════════════════════
# 1. LOGIN
# ═══════════════════════════════════════════════════════════════════════════════

class TestLogin:

    def test_login_exitoso(self, client, au_admin):
        r = client.post('/api/auth/login', json={
            'username': 'au_admin', 'password': 'SuperPass1!',
        })
        assert r.status_code == 200, r.get_json()
        body = r.get_json()
        assert 'token' in body
        assert body['user']['username'] == 'au_admin'
        # Cookie de refresh emitida
        assert _get_rt_cookie(client) is not None

    def test_falta_username_400(self, client, au_admin):
        r = client.post('/api/auth/login', json={'password': 'SuperPass1!'})
        assert r.status_code == 400

    def test_falta_password_400(self, client, au_admin):
        r = client.post('/api/auth/login', json={'username': 'au_admin'})
        assert r.status_code == 400

    def test_password_incorrecta_401(self, client, au_admin):
        r = client.post('/api/auth/login', json={
            'username': 'au_admin', 'password': 'mala',
        })
        assert r.status_code == 401

    def test_usuario_inexistente_401(self, client):
        r = client.post('/api/auth/login', json={
            'username': 'no-existe', 'password': 'SuperPass1!',
        })
        assert r.status_code == 401

    def test_cuenta_desactivada_403(self, client, db):
        # Borrado lógico: con contraseña correcta pero activo=False → 403.
        u = User(username='au_inactivo',
                 password_hash=generate_password_hash('SuperPass1!'),
                 role='inventario', password_version=1, activo=False)
        db.session.add(u); db.session.commit()
        r = client.post('/api/auth/login', json={
            'username': 'au_inactivo', 'password': 'SuperPass1!',
        })
        assert r.status_code == 403

    def test_username_demasiado_largo_401(self, client):
        r = client.post('/api/auth/login', json={
            'username': 'x' * 200, 'password': 'SuperPass1!',
        })
        assert r.status_code == 401

    def test_password_demasiado_largo_401(self, client):
        r = client.post('/api/auth/login', json={
            'username': 'au_admin', 'password': 'x' * 1000,
        })
        assert r.status_code == 401

    def test_usuario_con_2fa_pide_stepToken(self, client, au_totp_user):
        r = client.post('/api/auth/login', json={
            'username': 'au_totp', 'password': 'SuperPass1!',
        })
        assert r.status_code == 200
        body = r.get_json()
        assert body.get('requires2fa') is True
        assert 'stepToken' in body
        # NO devuelve token de acceso todavía
        assert 'token' not in body


# ═══════════════════════════════════════════════════════════════════════════════
# 2. VERIFY-2FA
# ═══════════════════════════════════════════════════════════════════════════════

class TestVerify2fa:

    def _step_token(self, client, user):
        r = client.post('/api/auth/login', json={
            'username': user.username, 'password': 'SuperPass1!',
        })
        return r.get_json()['stepToken']

    def test_codigo_correcto_devuelve_jwt(self, client, au_totp_user):
        step = self._step_token(client, au_totp_user)
        code = pyotp.TOTP(au_totp_user._test_totp_secret).now()
        r = client.post('/api/auth/verify-2fa', json={
            'stepToken': step, 'code': code,
        })
        assert r.status_code == 200, r.get_json()
        body = r.get_json()
        assert 'token' in body
        assert body['user']['username'] == 'au_totp'

    def test_codigo_incorrecto_401(self, client, au_totp_user):
        step = self._step_token(client, au_totp_user)
        r = client.post('/api/auth/verify-2fa', json={
            'stepToken': step, 'code': '000000',
        })
        assert r.status_code == 401

    def test_stepToken_invalido_401(self, client, au_totp_user):
        r = client.post('/api/auth/verify-2fa', json={
            'stepToken': 'no-jwt', 'code': '123456',
        })
        assert r.status_code == 401

    def test_codigo_demasiado_largo_401(self, client, au_totp_user):
        step = self._step_token(client, au_totp_user)
        r = client.post('/api/auth/verify-2fa', json={
            'stepToken': step, 'code': 'x' * 100,
        })
        assert r.status_code == 401


class TestStepTokenUnSoloUso:
    """El stepToken pre_2fa debe canjearse UNA sola vez.

    Es la prueba de "ya pasé el factor contraseña". Si sigue siendo canjeable
    después de completar el 2FA, quien lo obtenga (queda en sessionStorage del
    SPA y en el cuerpo de la request) puede acuñar sesiones extra durante los
    5 min de su TTL sin conocer la contraseña.

    El consumo se apoya en Redis; los tests corren con Redis apagado, así que
    inyectamos un doble mínimo que implementa solo `set(..., nx=, ex=)`.
    """

    class _RedisFalso:
        """Doble en memoria con lo mínimo que toca el flujo de verify-2fa:
        lockout de 2FA (ttl/incr/expire/delete), anti-replay del TOTP y consumo
        del stepToken (set nx/ex), y metadata de sesión (setex)."""

        def __init__(self):
            self.claves = {}

        def set(self, key, value, nx=False, ex=None):
            if nx and key in self.claves:
                return None
            self.claves[key] = value
            return True

        def setex(self, key, ttl, value):
            self.claves[key] = value
            return True

        def get(self, key):
            return self.claves.get(key)

        def ttl(self, key):
            return -2 if key not in self.claves else -1

        def incr(self, key):
            nuevo = int(self.claves.get(key) or 0) + 1
            self.claves[key] = nuevo
            return nuevo

        def expire(self, key, ttl):
            return True

        def delete(self, *keys):
            for k in keys:
                self.claves.pop(k, None)
            return True

    def _step_token(self, client, user):
        r = client.post('/api/auth/login', json={
            'username': user.username, 'password': 'SuperPass1!',
        })
        return r.get_json()['stepToken']

    def test_stepToken_no_se_puede_reusar(self, client, au_totp_user, monkeypatch):
        import time

        fake = self._RedisFalso()
        monkeypatch.setattr('app.extensions.get_redis', lambda: fake)

        totp = pyotp.TOTP(au_totp_user._test_totp_secret)
        # Dos códigos DISTINTOS, ambos válidos: el de la ventana actual y el de
        # la siguiente (el endpoint verifica con valid_window=1). Es importante
        # que difieran — si reusáramos el mismo código, el 401 vendría del
        # anti-replay del TOTP y el test pasaría sin probar nada del stepToken.
        code_a = totp.now()
        code_b = totp.at(time.time() + 30)
        assert code_a != code_b, 'los códigos deben diferir para aislar la causa del 401'

        step = self._step_token(client, au_totp_user)

        primera = client.post('/api/auth/verify-2fa', json={'stepToken': step, 'code': code_a})
        assert primera.status_code == 200, primera.get_json()
        assert 'token' in primera.get_json()

        # Mismo stepToken, código nuevo y válido: el único motivo posible de
        # rechazo es que el stepToken ya fue consumido.
        segunda = client.post('/api/auth/verify-2fa', json={'stepToken': step, 'code': code_b})
        assert segunda.status_code == 401
        assert 'ya fue usada' in (segunda.get_json().get('error') or '')

    def test_sin_el_fix_el_mismo_stepToken_acunaria_dos_sesiones(
        self, client, au_totp_user, monkeypatch,
    ):
        """Control negativo: con el consumo desactivado (Redis caído), el mismo
        stepToken sí acuña dos sesiones. Documenta exactamente qué cierra el fix
        y confirma que la degradación sin Redis es la esperada."""
        import time

        monkeypatch.setattr('app.extensions.get_redis', lambda: None)

        totp = pyotp.TOTP(au_totp_user._test_totp_secret)
        code_a = totp.now()
        code_b = totp.at(time.time() + 30)
        assert code_a != code_b

        step = self._step_token(client, au_totp_user)

        primera = client.post('/api/auth/verify-2fa', json={'stepToken': step, 'code': code_a})
        segunda = client.post('/api/auth/verify-2fa', json={'stepToken': step, 'code': code_b})
        assert primera.status_code == 200
        assert segunda.status_code == 200, 'sin Redis el consumo degrada a permitir (documentado)'

    def test_stepToken_de_version_anterior_sin_jti_sigue_funcionando(
        self, client, au_totp_user, monkeypatch, app,
    ):
        """Compatibilidad de deploy: un stepToken emitido por la versión previa
        no trae `jti`. Debe canjearse igual, no romper el login a media
        actualización."""
        import jwt as _jwt
        from datetime import datetime, timedelta, timezone

        fake = self._RedisFalso()
        monkeypatch.setattr('app.extensions.get_redis', lambda: fake)

        with app.app_context():
            ahora = datetime.now(timezone.utc)
            viejo = _jwt.encode(
                {
                    'sub': str(au_totp_user.id),
                    'pv': au_totp_user.password_version or 1,
                    'iat': int(ahora.timestamp()),
                    'exp': int((ahora + timedelta(seconds=300)).timestamp()),
                    'type': 'pre_2fa',
                    'iss': 'skilled-erp-api',
                    'aud': 'skilled-erp-spa',
                },
                app.config['JWT_SECRET_KEY'],
                algorithm='HS256',
            )

        code = pyotp.TOTP(au_totp_user._test_totp_secret).now()
        r = client.post('/api/auth/verify-2fa', json={'stepToken': viejo, 'code': code})
        assert r.status_code == 200, r.get_json()


class TestRateLimitKeyVerify2fa:
    """El bucket del rate limit de verify-2fa se deriva del stepToken crudo.

    Antes se decodificaba el JWT sin verificar la firma para leer `sub`; como
    ese valor lo controla por completo el atacante, podía inventar un `sub`
    distinto por intento y repartir la fuerza bruta entre buckets infinitos.
    """

    def _key_con(self, app, cuerpo):
        from app.routes.api_auth.login import _api_verify_2fa_user_key
        with app.test_request_context('/api/auth/verify-2fa', json=cuerpo):
            return _api_verify_2fa_user_key()

    def test_mismo_token_mismo_bucket(self, app):
        cuerpo = {'stepToken': 'abc.def.ghi', 'code': '111111'}
        assert self._key_con(app, cuerpo) == self._key_con(app, cuerpo)

    def test_tokens_distintos_buckets_distintos(self, app):
        a = self._key_con(app, {'stepToken': 'abc.def.ghi'})
        b = self._key_con(app, {'stepToken': 'abc.def.otro'})
        assert a != b

    def test_sub_falsificado_no_reparte_el_bucket(self, app):
        """Dos payloads sin firma con `sub` distinto pero MISMO token crudo
        caen en el mismo bucket — ya no se puede evadir variando el claim."""
        import base64
        import json as _json

        def token_con_sub(sub):
            header = base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').rstrip(b'=')
            payload = base64.urlsafe_b64encode(
                _json.dumps({'sub': sub, 'type': 'pre_2fa'}).encode()
            ).rstrip(b'=')
            return f'{header.decode()}.{payload.decode()}.firma-inventada'

        # Tokens distintos → buckets distintos, pero ninguno coincide con el
        # bucket de un usuario real: el atacante ya no puede apuntar al bucket
        # de la víctima ni escapar del suyo sin invalidar su propio stepToken.
        k1 = self._key_con(app, {'stepToken': token_con_sub('1')})
        k2 = self._key_con(app, {'stepToken': token_con_sub('999')})
        assert k1 != k2
        assert not k1.endswith(':1')
        assert not k2.endswith(':999')

    def test_sin_step_token_bucket_anon(self, app):
        assert self._key_con(app, {'code': '111111'}) == 'api_v2fa_user:anon'


class TestResilienciaRedisEnCaliente:
    """La API debe sobrevivir a que Redis se caiga MIENTRAS corre.

    El guard `if not r: return <default>` que había en todo el módulo solo
    cubría "Redis nunca estuvo disponible al arrancar". Si Redis estaba vivo y
    moría después, el singleton ya tenía un cliente (no era None, pasaba el
    guard) y la llamada lanzaba ConnectionError/TimeoutError.

    Como `_is_jti_revoked()` corre desde `jwt_required` en CADA request
    autenticada y nadie atrapaba esa excepción, una caída de Redis tumbaba la
    API entera: todo respondía 500. Estos tests fijan el contrato de que una
    incidencia de Redis degrada defensas pero nunca tira el servicio.
    """

    class _RedisMuerto:
        """Cliente que parece vivo (no es falsy) pero revienta en cada
        operación — exactamente lo que devuelve redis-py tras perder el
        servidor."""

        def __bool__(self):
            return True

        def _explotar(self, *a, **k):
            import redis
            raise redis.exceptions.ConnectionError('Redis se cayó')

        get = set = setex = ttl = incr = expire = delete = ping = _explotar

    @pytest.fixture
    def redis_muerto(self, monkeypatch):
        cliente = self._RedisMuerto()
        monkeypatch.setattr('app.extensions.get_redis', lambda: cliente)
        return cliente

    def test_request_autenticada_no_devuelve_500(self, client, au_admin, redis_muerto):
        """El caso crítico: jwt_required → _is_jti_revoked → r.get() revienta."""
        r = client.get('/api/auth/me', headers=_hdr(au_admin))
        assert r.status_code == 200, (
            f'Redis caído tumbó una request autenticada: {r.status_code} {r.get_json()}'
        )
        assert r.get_json()['username'] == 'au_admin'

    def test_login_sigue_funcionando(self, client, au_admin, redis_muerto):
        """El lockout escalado vive en Redis; sin él, el login debe seguir
        funcionando (degradar) y no bloquearse ni reventar."""
        r = client.post('/api/auth/login', json={
            'username': 'au_admin', 'password': 'SuperPass1!',
        })
        assert r.status_code == 200, r.get_json()
        assert 'token' in r.get_json()

    def test_login_fallido_sigue_devolviendo_401(self, client, au_admin, redis_muerto):
        r = client.post('/api/auth/login', json={
            'username': 'au_admin', 'password': 'incorrecta',
        })
        assert r.status_code == 401

    def test_login_con_2fa_sigue_funcionando(self, client, au_totp_user, redis_muerto):
        """Toca lockout de 2FA, anti-replay de TOTP y consumo del stepToken:
        los tres dependen de Redis y los tres deben degradar a permitir."""
        paso1 = client.post('/api/auth/login', json={
            'username': 'au_totp', 'password': 'SuperPass1!',
        })
        assert paso1.status_code == 200
        step = paso1.get_json()['stepToken']

        code = pyotp.TOTP(au_totp_user._test_totp_secret).now()
        paso2 = client.post('/api/auth/verify-2fa', json={'stepToken': step, 'code': code})
        assert paso2.status_code == 200, paso2.get_json()
        assert 'token' in paso2.get_json()

    def test_logout_no_revienta(self, client, au_admin, redis_muerto):
        """logout intenta blacklistear el jti en Redis. Sin Redis el JWT vive
        hasta su exp, pero el endpoint debe responder ok igual."""
        r = client.post(
            '/api/auth/logout',
            headers={**_hdr(au_admin), 'X-Requested-With': 'XMLHttpRequest'},
        )
        assert r.status_code == 200
        assert r.get_json()['ok'] is True

    def test_cliente_muerto_se_descarta_para_reconectar(self, au_admin, app, redis_muerto):
        """Tras un fallo, `redis_call` tira el cliente para que la próxima
        llamada reconecte sola cuando Redis vuelva (sin reiniciar la app)."""
        import app.extensions as ext
        ext._redis_client = redis_muerto
        with app.app_context():
            ext.redis_call(lambda r: r.get('lo-que-sea'), default='degradado')
        assert ext._redis_client is None, 'el cliente muerto debió descartarse'

    def test_el_cliente_de_redis_tiene_timeout(self, app, monkeypatch):
        """Un Redis colgado (vivo pero sin responder) no debe frenar la API.

        `redis_call` degrada ante errores, pero contra un cuelgue no puede
        hacer nada: sin timeout de socket, redis-py espera indefinidamente y no
        hay excepción que atrapar. Como Redis se consulta en cada petición
        autenticada, eso arrastraría a toda la API.

        Este test fija el contrato de que el cliente SIEMPRE se construye con
        timeouts, verificando los kwargs con los que se llama a `from_url`.
        """
        import app.extensions as ext

        capturado = {}

        class _ClienteFalso:
            def ping(self):
                return True

        def _from_url_espia(url, **kwargs):
            capturado.update(kwargs)
            return _ClienteFalso()

        monkeypatch.setattr(ext.redis, 'from_url', _from_url_espia)
        monkeypatch.setenv('REDIS_URL', 'redis://localhost:6379/0')
        ext._redis_client = None
        try:
            ext.get_redis()
        finally:
            ext._redis_client = None

        assert capturado.get('socket_timeout'), 'falta socket_timeout'
        assert capturado.get('socket_connect_timeout'), 'falta socket_connect_timeout'
        # Holgado contra un Redis local (<1 ms normalmente) pero acotado.
        assert capturado['socket_timeout'] <= 5
        assert capturado['socket_connect_timeout'] <= 5

    def test_setup_2fa_permanece_fail_closed(self, app, redis_muerto):
        """Los helpers de pinning del secret de 2FA son fail-CLOSED a propósito:
        sin Redis no se puede pinear y confirm-2fa debe rechazar, en vez de
        degradar al comportamiento vulnerable."""
        from app.routes.api_auth.twofa import (
            _peek_setup_2fa_secret, _pin_setup_2fa_secret,
        )
        with app.app_context():
            assert _pin_setup_2fa_secret(1, 'SECRETO') is False
            assert _peek_setup_2fa_secret(1) is None


class TestScopeCookieRefreshToken:
    """La cookie `rt_api` se acota a /api/auth y la vieja de path='/' se limpia.

    La migración es la parte delicada: si dejáramos viva la cookie de `path='/'`,
    el navegador mandaría DOS cookies `rt_api` a /api/auth/* y
    `request.cookies.get()` elegiría una de forma no determinista — el usuario
    quedaría deslogueado al azar.
    """

    def _cookies_rt(self, response):
        """Todos los Set-Cookie de rt_api, como lista de strings."""
        return [
            v for k, v in response.headers.items()
            if k.lower() == 'set-cookie' and v.startswith('rt_api=')
        ]

    def test_login_emite_la_cookie_acotada(self, client, au_admin):
        r = client.post('/api/auth/login', json={
            'username': 'au_admin', 'password': 'SuperPass1!',
        })
        assert r.status_code == 200
        emitidas = self._cookies_rt(r)
        nueva = [c for c in emitidas if 'Path=/api/auth' in c]
        assert nueva, f'no se emitió la cookie con Path=/api/auth: {emitidas}'
        assert 'HttpOnly' in nueva[0]

    def test_login_borra_la_cookie_vieja_de_path_raiz(self, client, au_admin):
        r = client.post('/api/auth/login', json={
            'username': 'au_admin', 'password': 'SuperPass1!',
        })
        borrado = [
            c for c in self._cookies_rt(r)
            if 'Path=/;' in c or c.rstrip().endswith('Path=/')
        ]
        assert borrado, 'no se limpió la cookie legacy de Path=/'
        # delete_cookie expira la cookie: Max-Age=0 o Expires en el pasado.
        assert 'Max-Age=0' in borrado[0] or 'Expires=Thu, 01 Jan 1970' in borrado[0]

    def test_el_ciclo_completo_de_sesion_sigue_funcionando(self, client, au_admin):
        """Lo que de verdad importa: login → refresh → logout sin romperse."""
        login = client.post('/api/auth/login', json={
            'username': 'au_admin', 'password': 'SuperPass1!',
        })
        assert login.status_code == 200

        refresh = client.post('/api/auth/refresh',
                              headers={'X-Requested-With': 'XMLHttpRequest'})
        assert refresh.status_code == 200, refresh.get_json()
        assert 'token' in refresh.get_json()

        logout = client.post('/api/auth/logout',
                             headers={'X-Requested-With': 'XMLHttpRequest'})
        assert logout.status_code == 200

        # Tras el logout el refresh token quedó revocado.
        post_logout = client.post('/api/auth/refresh',
                                  headers={'X-Requested-With': 'XMLHttpRequest'})
        assert post_logout.status_code == 401

    def test_logout_limpia_ambos_paths(self, client, au_admin):
        client.post('/api/auth/login', json={
            'username': 'au_admin', 'password': 'SuperPass1!',
        })
        r = client.post('/api/auth/logout',
                        headers={'X-Requested-With': 'XMLHttpRequest'})
        paths = self._cookies_rt(r)
        assert any('Path=/api/auth' in c for c in paths), paths
        assert any('Path=/;' in c or c.rstrip().endswith('Path=/') for c in paths), paths


class TestEstadoSeguridad:
    """El endpoint que hace visible si Redis está degradando defensas."""

    def test_requiere_autenticacion(self, client):
        assert client.get('/api/auth/estado-seguridad').status_code == 401

    def test_no_admin_recibe_403(self, client, au_coord):
        r = client.get('/api/auth/estado-seguridad', headers=_hdr(au_coord))
        assert r.status_code == 403

    def test_admin_ve_el_estado_y_las_defensas_caidas(self, client, au_admin):
        # En tests REDIS_URL está vacía, así que debe reportar degradado.
        r = client.get('/api/auth/estado-seguridad', headers=_hdr(au_admin))
        assert r.status_code == 200
        body = r.get_json()
        assert body['redis']['ok'] is False
        assert len(body['defensas_degradadas']) > 0
        assert any('jti' in d for d in body['defensas_degradadas'])

    def test_no_se_filtra_en_el_health_publico(self, client):
        """/health es público: no debe revelar qué componentes hay ni su estado."""
        r = client.get('/health')
        assert r.status_code == 200
        assert r.get_json() == {'status': 'ok'}


class TestPoliticaDeContrasenas:
    """Mínimo 12 caracteres + rechazo de contraseñas comunes.

    Solo aplica a contraseñas NUEVAS: las existentes siguen sirviendo para
    iniciar sesión (`is_strong_password` no se llama en el login).
    """

    def test_menos_de_12_se_rechaza(self):
        from app.utils import is_strong_password
        assert is_strong_password('Abcd123!') is False       # 8, antes válida
        assert is_strong_password('Abcd1234!01') is False    # 11
        assert is_strong_password('Abcd1234!012') is True    # 12

    def test_sigue_exigiendo_composicion(self):
        from app.utils import is_strong_password
        assert is_strong_password('abcdefghijklm') is False    # sin mayús/díg/símb
        assert is_strong_password('ABCDEFGHIJKLM1!') is False  # sin minúsculas
        assert is_strong_password('Abcdefghijklm!') is False   # sin dígitos
        assert is_strong_password('Abcdefghijk123') is False   # sin símbolos

    def test_rechaza_contrasenas_comunes_aunque_cumplan_las_reglas(self):
        from app.utils import is_strong_password
        # Cumple largo + mayús + minús + dígito + símbolo, pero es de las
        # primeras que prueba cualquier diccionario.
        assert is_strong_password('Password123!') is False
        assert is_strong_password('Bienvenido123!') is False
        assert is_strong_password('Skilled1234!') is False

    def test_none_no_revienta(self):
        from app.utils import is_strong_password
        assert is_strong_password(None) is False

    def test_el_login_no_aplica_la_politica_nueva(self, client, au_admin):
        """Regresión clave: subir el mínimo NO debe dejar fuera a quien ya tiene
        una contraseña corta. `SuperPass1!` son 11 caracteres."""
        r = client.post('/api/auth/login', json={
            'username': 'au_admin', 'password': 'SuperPass1!',
        })
        assert r.status_code == 200, 'una contraseña existente y corta dejó de funcionar'


class TestSeparacionDeLlaveJWT:
    """`JWT_SECRET_KEY` separa la firma de los JWT de la llave que Flask usa
    para la cookie de sesión y los tokens CSRF. Si no está definida, cae a
    SECRET_KEY — el comportamiento histórico, para no invalidar tokens vivos."""

    def test_fallback_a_secret_key(self, app):
        from app.routes.api_auth._core import _jwt_secret
        with app.app_context():
            assert _jwt_secret() == app.config['SECRET_KEY']

    def test_usa_jwt_secret_key_si_esta_definida(self, app):
        from app.routes.api_auth._core import _jwt_secret
        original = app.config.get('JWT_SECRET_KEY')
        try:
            app.config['JWT_SECRET_KEY'] = 'llave-solo-para-jwt'
            with app.app_context():
                assert _jwt_secret() == 'llave-solo-para-jwt'
                assert _jwt_secret() != app.config['SECRET_KEY']
        finally:
            app.config['JWT_SECRET_KEY'] = original


# ═══════════════════════════════════════════════════════════════════════════════
# 3. REFRESH
# ═══════════════════════════════════════════════════════════════════════════════

class TestRefresh:

    def test_sin_x_requested_with_403(self, client, au_admin):
        r = client.post('/api/auth/refresh')
        assert r.status_code == 403

    def test_sin_cookie_401(self, client):
        r = client.post('/api/auth/refresh', headers=_csrf_hdr())
        assert r.status_code == 401

    def test_cookie_invalida_401(self, client):
        _set_rt_cookie(client, 'cookie-inventada-que-no-existe')
        r = client.post('/api/auth/refresh', headers=_csrf_hdr())
        assert r.status_code == 401

    def test_cookie_revocada_401(self, client, db, au_admin):
        tok, raw = _crear_rt(db, au_admin, revoked=True)
        _set_rt_cookie(client, raw)
        r = client.post('/api/auth/refresh', headers=_csrf_hdr())
        # Fuera de la ventana de gracia: 401 (replay) — sin Redis, el endpoint
        # asume "race" y también devuelve 401 sin revocar familia. Ambos OK.
        assert r.status_code == 401

    def test_cookie_expirada_401(self, client, db, au_admin):
        tok, raw = _crear_rt(db, au_admin, expires_in_days=-1)
        _set_rt_cookie(client, raw)
        r = client.post('/api/auth/refresh', headers=_csrf_hdr())
        assert r.status_code == 401

    def test_refresh_exitoso_rota_token(self, client, db, au_admin):
        tok, raw = _crear_rt(db, au_admin)
        viejo_id = tok.id
        _set_rt_cookie(client, raw)
        r = client.post('/api/auth/refresh', headers=_csrf_hdr())
        assert r.status_code == 200, r.get_json()
        body = r.get_json()
        assert 'token' in body
        assert body['user']['id'] == au_admin.id
        # El RT viejo desaparece: el endpoint hace housekeeping que borra
        # los `revoked=True` viejos en el mismo commit del rollover.
        # (Si no se borrase, quedaría con `revoked=True` — ambas formas son
        # equivalentes para invalidar la sesión vieja.)
        viejo = RefreshToken.query.get(viejo_id)
        assert viejo is None or viejo.revoked is True
        # Hay un RT nuevo activo del mismo usuario
        nuevos = RefreshToken.query.filter_by(user_id=au_admin.id, revoked=False).all()
        assert len(nuevos) == 1
        assert nuevos[0].id != viejo_id


# ═══════════════════════════════════════════════════════════════════════════════
# 4. LOGOUT
# ═══════════════════════════════════════════════════════════════════════════════

class TestLogout:

    def test_sin_x_requested_with_403(self, client):
        r = client.post('/api/auth/logout')
        assert r.status_code == 403

    def test_logout_revoca_rt_cookie(self, client, db, au_admin):
        tok, raw = _crear_rt(db, au_admin)
        _set_rt_cookie(client, raw)
        r = client.post('/api/auth/logout', headers=_csrf_hdr(au_admin))
        assert r.status_code == 200
        flask_db.session.refresh(tok)
        assert tok.revoked is True

    def test_logout_sin_cookie_es_ok(self, client, au_admin):
        # Logout sin sesión: no falla — solo limpia y responde ok
        r = client.post('/api/auth/logout', headers=_csrf_hdr(au_admin))
        assert r.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════════
# 5. /me y /me/activity
# ═══════════════════════════════════════════════════════════════════════════════

class TestMe:

    def test_sin_token_401(self, client):
        r = client.get('/api/auth/me')
        assert r.status_code == 401

    def test_me_devuelve_datos_propios(self, client, au_admin):
        r = client.get('/api/auth/me', headers=_hdr(au_admin))
        assert r.status_code == 200
        body = r.get_json()
        assert body['id'] == au_admin.id
        assert body['username'] == 'au_admin'
        assert body['role'] == 'admin'

    def test_me_activity_devuelve_audit_log_propio(
        self, client, au_admin, db,
    ):
        # Insertamos algunas filas de audit log a nombre del usuario
        for i in range(3):
            db.session.add(AuditLog(user=au_admin.username, action=f'acc {i}',
                                     ip='127.0.0.1'))
        # Ruido de OTRO usuario, no debe aparecer
        db.session.add(AuditLog(user='alguien_mas', action='OTRA',
                                 ip='127.0.0.1'))
        db.session.commit()

        r = client.get('/api/auth/me/activity', headers=_hdr(au_admin))
        assert r.status_code == 200
        actions = [row['action'] for row in r.get_json()]
        assert all(a.startswith('acc ') for a in actions)
        assert 'OTRA' not in actions

    def test_me_activity_respeta_limit(self, client, au_admin, db):
        for i in range(30):
            db.session.add(AuditLog(user=au_admin.username, action=f'a{i}',
                                     ip='127.0.0.1'))
        db.session.commit()
        r = client.get('/api/auth/me/activity?limit=10', headers=_hdr(au_admin))
        assert len(r.get_json()) == 10


# ═══════════════════════════════════════════════════════════════════════════════
# 6. DIRECTORIO /users
# ═══════════════════════════════════════════════════════════════════════════════

class TestDirectorio:

    def test_listar_admin_ve_role(self, client, au_admin, au_otro):
        r = client.get('/api/auth/users', headers=_hdr(au_admin))
        assert r.status_code == 200
        users = r.get_json()
        # Admin recibe `role`, `totp_enabled`, `last_seen` en el payload
        sample = next(u for u in users if u['id'] == au_admin.id)
        assert 'role' in sample
        assert 'totp_enabled' in sample

    def test_listar_coord_recibe_vista_publica(
        self, client, au_coord, au_admin,
    ):
        r = client.get('/api/auth/users', headers=_hdr(au_coord))
        assert r.status_code == 200
        admin_obj = next(u for u in r.get_json() if u['id'] == au_admin.id)
        # HIGH-01: coord NO debe ver `role`, `totp_enabled`, `last_seen` de admin
        assert 'role' not in admin_obj
        assert 'totp_enabled' not in admin_obj
        assert 'last_seen' not in admin_obj

    def test_get_admin_ve_pii_completa(
        self, client, au_admin, au_otro,
    ):
        r = client.get(f'/api/auth/users/{au_otro.id}', headers=_hdr(au_admin))
        body = r.get_json()
        assert 'role' in body

    def test_get_otro_user_recibe_vista_publica(
        self, client, au_coord, au_otro,
    ):
        # coord viendo el perfil de inv → vista pública
        r = client.get(f'/api/auth/users/{au_otro.id}', headers=_hdr(au_coord))
        body = r.get_json()
        assert 'role' not in body

    def test_get_propio_devuelve_completo(self, client, au_coord):
        # Coord viendo SU PROPIO perfil → ve `role` aunque no sea admin
        r = client.get(f'/api/auth/users/{au_coord.id}', headers=_hdr(au_coord))
        body = r.get_json()
        assert body['role'] == 'coordinador'

    def test_get_inexistente_404(self, client, au_admin):
        r = client.get('/api/auth/users/99999', headers=_hdr(au_admin))
        assert r.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# 7. /users/<id>/foto
# ═══════════════════════════════════════════════════════════════════════════════

class TestUserFoto:

    def test_sin_foto_404(self, client, au_admin, au_otro):
        r = client.get(f'/api/auth/users/{au_otro.id}/foto', headers=_hdr(au_admin))
        assert r.status_code == 404

    def test_foto_default_404(self, client, au_admin, db):
        au_admin.profile_pic = 'default.png'
        db.session.commit()
        r = client.get(f'/api/auth/users/{au_admin.id}/foto', headers=_hdr(au_admin))
        assert r.status_code == 404

    def test_path_traversal_bloqueado_400(self, client, au_admin, db):
        au_admin.profile_pic = '../../etc/passwd'
        db.session.commit()
        r = client.get(f'/api/auth/users/{au_admin.id}/foto', headers=_hdr(au_admin))
        assert r.status_code == 400

    def test_archivo_no_existe_en_disco_404(self, client, au_admin, db):
        au_admin.profile_pic = 'noexiste.webp'
        db.session.commit()
        r = client.get(f'/api/auth/users/{au_admin.id}/foto', headers=_hdr(au_admin))
        assert r.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# 8. /profile (POST y DELETE foto)
# ═══════════════════════════════════════════════════════════════════════════════

class TestPerfilPropio:

    def test_actualiza_campos_snake(self, client, au_admin, db):
        r = client.post('/api/auth/profile', headers=_hdr(au_admin), data={
            'full_name': 'Aurora Admin',
            'area': 'TI',
            'position': 'Gerente',
        })
        assert r.status_code == 200
        db.session.refresh(au_admin)
        assert au_admin.full_name == 'Aurora Admin'
        assert au_admin.area == 'TI'

    def test_actualiza_campos_camel(self, client, au_admin, db):
        r = client.post('/api/auth/profile', headers=_hdr(au_admin), data={
            'fullName': 'Aurora Camel',
            'contactInfo': 'ext 1000',
        })
        assert r.status_code == 200
        db.session.refresh(au_admin)
        assert au_admin.full_name == 'Aurora Camel'
        assert au_admin.contact_info == 'ext 1000'

    def test_delete_foto_pone_null(self, client, au_admin, db):
        au_admin.profile_pic = 'algo.webp'
        db.session.commit()
        r = client.delete('/api/auth/profile/foto', headers=_hdr(au_admin))
        assert r.status_code == 200
        db.session.refresh(au_admin)
        assert au_admin.profile_pic is None


# ═══════════════════════════════════════════════════════════════════════════════
# 9. CHANGE PASSWORD (propio)
# ═══════════════════════════════════════════════════════════════════════════════

class TestChangePassword:

    def test_solo_propio_403(self, client, au_admin, au_otro):
        r = client.post(
            f'/api/auth/change-password/{au_otro.id}',
            headers=_hdr(au_admin),
            json={'current_password': 'X', 'new_password': 'StrongPass1!'},
        )
        assert r.status_code == 403

    def test_falta_campos_400(self, client, au_admin):
        r = client.post(
            f'/api/auth/change-password/{au_admin.id}',
            headers=_hdr(au_admin),
            json={'current_password': 'SuperPass1!'},
        )
        assert r.status_code == 400

    def test_current_password_incorrecta_401(self, client, au_admin):
        r = client.post(
            f'/api/auth/change-password/{au_admin.id}',
            headers=_hdr(au_admin),
            json={
                'current_password': 'mala',
                'new_password': 'OtraStrong1!',
            },
        )
        assert r.status_code == 401

    def test_password_debil_400(self, client, au_admin):
        r = client.post(
            f'/api/auth/change-password/{au_admin.id}',
            headers=_hdr(au_admin),
            json={
                'current_password': 'SuperPass1!',
                'new_password': '12345',
            },
        )
        assert r.status_code == 400

    def test_password_igual_a_actual_400(self, client, au_admin):
        r = client.post(
            f'/api/auth/change-password/{au_admin.id}',
            headers=_hdr(au_admin),
            json={
                'current_password': 'SuperPass1!',
                'new_password': 'SuperPass1!',
            },
        )
        assert r.status_code == 400

    def test_cambio_exitoso(self, client, au_admin, db):
        pv_antes = au_admin.password_version
        r = client.post(
            f'/api/auth/change-password/{au_admin.id}',
            headers=_hdr(au_admin),
            json={
                'current_password': 'SuperPass1!',
                'new_password': 'OtraStrong1!',
            },
        )
        assert r.status_code == 200
        db.session.refresh(au_admin)
        # password_version se incrementa → invalida JWT vivos
        assert au_admin.password_version == pv_antes + 1

    def test_con_2fa_requiere_totp(self, client, au_totp_user):
        r = client.post(
            f'/api/auth/change-password/{au_totp_user.id}',
            headers=_hdr(au_totp_user),
            json={
                'current_password': 'SuperPass1!',
                'new_password': 'OtraStrong1!',
            },
        )
        assert r.status_code == 401
        assert r.get_json().get('requires_totp') is True

    def test_con_2fa_totp_correcto(self, client, au_totp_user, db):
        code = pyotp.TOTP(au_totp_user._test_totp_secret).now()
        r = client.post(
            f'/api/auth/change-password/{au_totp_user.id}',
            headers=_hdr(au_totp_user),
            json={
                'current_password': 'SuperPass1!',
                'new_password': 'OtraStrong1!',
                'code': code,
            },
        )
        assert r.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════════
# 10. SESSIONS
# ═══════════════════════════════════════════════════════════════════════════════

class TestSessions:

    def test_lista_solo_propias_y_activas(
        self, client, au_admin, au_otro, db,
    ):
        # Tres RT del admin: 2 activos, 1 revocado
        _crear_rt(db, au_admin)
        _crear_rt(db, au_admin)
        _crear_rt(db, au_admin, revoked=True)
        # Y uno ajeno (no debe aparecer)
        _crear_rt(db, au_otro)

        r = client.get('/api/auth/sessions', headers=_hdr(au_admin))
        assert r.status_code == 200
        items = r.get_json()
        assert len(items) == 2

    def test_revoke_session_propia(self, client, au_admin, db):
        tok, _ = _crear_rt(db, au_admin)
        r = client.delete(
            f'/api/auth/sessions/{tok.id}',
            headers=_hdr(au_admin),
        )
        assert r.status_code == 200
        db.session.refresh(tok)
        assert tok.revoked is True

    def test_revoke_session_ajena_404(
        self, client, au_admin, au_otro, db,
    ):
        ajena, _ = _crear_rt(db, au_otro)
        r = client.delete(
            f'/api/auth/sessions/{ajena.id}',
            headers=_hdr(au_admin),
        )
        # No es del au_admin → 404 (no 403, para no confirmar existencia)
        assert r.status_code == 404
        db.session.refresh(ajena)
        assert ajena.revoked is False

    def test_revoke_session_inexistente_404(self, client, au_admin):
        r = client.delete('/api/auth/sessions/99999', headers=_hdr(au_admin))
        assert r.status_code == 404

    def test_revoke_all_panic(self, client, au_admin, au_otro, db):
        _crear_rt(db, au_admin); _crear_rt(db, au_admin)
        ajena, _ = _crear_rt(db, au_otro)
        pv_antes = au_admin.password_version

        r = client.delete('/api/auth/sessions/all', headers=_hdr(au_admin))
        assert r.status_code == 200

        # Todos los RT del au_admin quedan revocados
        activos = RefreshToken.query.filter_by(
            user_id=au_admin.id, revoked=False,
        ).count()
        assert activos == 0
        # password_version incrementa → JWT vivos invalidados
        db.session.refresh(au_admin)
        assert au_admin.password_version == pv_antes + 1
        # La sesión ajena NO se toca
        db.session.refresh(ajena)
        assert ajena.revoked is False
