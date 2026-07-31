from flask_sqlalchemy import SQLAlchemy
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFProtect
from flask_migrate import Migrate
from flask_mail import Mail
from flask import request as flask_request
from sqlalchemy import String
from sqlalchemy.types import TypeDecorator
import ipaddress
import redis
import os


# ── Cifrado de campos sensibles en BD ────────────────────────────────────────
class EncryptedString(TypeDecorator):
    """Cifra el valor con Fernet al persistir y lo descifra al leer.

    Requiere TOTP_ENCRYPTION_KEY en las variables de entorno.
    Genera la clave con:
        python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

    Compatibilidad de migración: si un valor almacenado no puede descifrarse
    (dato legacy sin cifrar), se retorna como está para no romper registros existentes.
    """
    impl = String(500)
    cache_ok = True

    def _fernet(self):
        key = os.environ.get('TOTP_ENCRYPTION_KEY', '').strip()
        if not key:
            raise RuntimeError(
                "CRÍTICO: TOTP_ENCRYPTION_KEY no configurada. "
                "Genera una clave con: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
            )
        from cryptography.fernet import Fernet
        return Fernet(key.encode() if isinstance(key, str) else key)

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return self._fernet().encrypt(value.encode()).decode()

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        try:
            return self._fernet().decrypt(value.encode()).decode()
        except Exception:
            return value  # valor legacy sin cifrar durante período de migración


db = SQLAlchemy()
csrf = CSRFProtect()
migrate = Migrate()
mail = Mail()


# ── Rangos CIDR de Cloudflare ─────────────────────────────────────────────────
# Se intentan cargar desde la API oficial al arranque (timeout 5 s).
# Si la petición falla se usa la lista hardcodeada como fallback.
_CF_CIDR_FALLBACK = [
    "103.21.244.0/22", "103.22.200.0/22", "103.31.4.0/22",
    "104.16.0.0/13",   "104.24.0.0/14",   "108.162.192.0/18",
    "131.0.72.0/22",   "141.101.64.0/18", "162.158.0.0/15",
    "172.64.0.0/13",   "173.245.48.0/20", "188.114.96.0/20",
    "190.93.240.0/20", "197.234.240.0/22","198.41.128.0/17",
]

def _load_cloudflare_cidrs() -> list:
    try:
        import urllib.request
        with urllib.request.urlopen('https://www.cloudflare.com/ips-v4', timeout=5) as resp:
            lines = resp.read().decode().strip().splitlines()
            valid = [l.strip() for l in lines if '/' in l.strip()]
            if valid:
                return valid
    except Exception:
        pass
    return _CF_CIDR_FALLBACK

_CF_CIDR_LIST = _load_cloudflare_cidrs()
_CF_NETWORKS = [ipaddress.ip_network(cidr, strict=False) for cidr in _CF_CIDR_LIST]

def is_cloudflare_ip(ip: str) -> bool:
    """Devuelve True si la IP pertenece a los rangos oficiales de Cloudflare."""
    try:
        addr = ipaddress.ip_address(ip)
        return any(addr in net for net in _CF_NETWORKS)
    except ValueError:
        return False

_redis_client = None

def get_redis():
    """Obtiene el cliente Redis (singleton). Retorna None si no está disponible."""
    global _redis_client
    if _redis_client is None:
        redis_url = os.environ.get('REDIS_URL')
        if redis_url:
            try:
                _redis_client = redis.from_url(
                    redis_url,
                    decode_responses=True,
                    # ── Timeouts: obligatorios, no opcionales ──────────────
                    # Por defecto redis-py espera INDEFINIDAMENTE. Con eso, un
                    # Redis "vivo pero colgado" (partición de red, swap, una
                    # tormenta de evicciones) no lanza ninguna excepción: la
                    # llamada simplemente nunca vuelve.
                    #
                    # `redis_call` degrada ante ERRORES, pero no puede hacer
                    # nada contra un CUELGUE — no hay nada que atrapar. Y como
                    # Redis se consulta en el camino de toda petición
                    # autenticada (`_is_jti_revoked`) y de todo registro de
                    # observabilidad, un cuelgue arrastraría a la API entera
                    # hasta que gunicorn matara a los workers.
                    #
                    # Con timeout, un cuelgue se convierte en TimeoutError, que
                    # `redis_call` sí sabe degradar. 2 s es holgadísimo contra
                    # un Redis local (lo normal es <1 ms) y aun así acota el
                    # daño a algo imperceptible.
                    socket_connect_timeout=2,
                    socket_timeout=2,
                    # Revalida conexiones ociosas antes de usarlas: evita
                    # gastar un intento en un socket que el sistema ya cerró.
                    health_check_interval=30,
                    retry_on_timeout=False,
                )
                _redis_client.ping()
            except Exception:
                _redis_client = None
    return _redis_client


def redis_call(operacion, default=None):
    """Ejecuta una operación contra Redis tolerando que el servidor se caiga.

    ── El problema que resuelve ────────────────────────────────────────────────
    Todo el módulo de auth estaba escrito así:

        r = get_redis()
        if not r:
            return <default>       # degradación cuando NO hay Redis
        return r.get(...)          # ← sin protección

    Ese guard solo cubre "Redis nunca estuvo disponible al arrancar": ahí
    `get_redis()` devuelve None y se degrada bien. Pero si Redis estaba VIVO y
    se cae después, el singleton ya tiene un cliente — no es None, pasa el
    guard — y la llamada lanza ConnectionError/TimeoutError.

    Como `_is_jti_revoked()` se invoca desde `jwt_required` en CADA request
    autenticada y la excepción no se atrapaba en ningún lado, el resultado era
    que una caída de Redis tumbaba la API entera: todas las requests caían al
    handler global y respondían 500. La degradación que los comentarios del
    módulo prometían nunca se activaba en ese escenario.

    ── Cómo lo resuelve ────────────────────────────────────────────────────────
    `operacion` recibe el cliente y hace la llamada. Si algo falla, devolvemos
    exactamente el mismo `default` que la rama "no hay Redis" — es decir, una
    caída en caliente se comporta igual que un arranque sin Redis, que es el
    camino de degradación que el módulo ya tenía pensado y probado.

    Además descartamos el cliente muerto para que la siguiente llamada
    reconecte sola cuando Redis vuelva, sin reiniciar la app.

    Uso:
        redis_call(lambda r: r.get(f'jwt_revoked:{jti}'), default=False)
    """
    global _redis_client
    r = get_redis()
    if r is None:
        return default
    try:
        return operacion(r)
    except Exception:
        # Cliente muerto: lo tiramos para forzar reconexión en la próxima
        # llamada. No relanzamos — una incidencia de Redis degrada defensas,
        # pero jamás debe convertirse en una caída total de la API.
        _redis_client = None
        try:
            import logging
            logging.getLogger(__name__).warning(
                'Redis no respondió; degradando esta operación al default (%r). '
                'Mientras dure, se pierden: blacklist de jti, lockout escalado, '
                'anti-replay de TOTP y consumo del stepToken.', default,
            )
        except Exception:
            pass
        return default

def get_real_client_ip_flask() -> str:
    """
    Devuelve la IP real del cliente considerando Cloudflare Tunnel.
    Solo confía en CF-Connecting-IP si la conexión directa proviene de un IP de Cloudflare,
    evitando que un atacante externo falsifique ese header.
    """
    remote = flask_request.remote_addr or ""
    cf_ip = flask_request.headers.get("CF-Connecting-IP")
    if cf_ip and is_cloudflare_ip(remote):
        return cf_ip.strip()
    return get_remote_address()

def rate_limit_key() -> str:
    """Clave de rate limit: IP real del cliente.

    API-only: no consultamos `session.get("user_id")` porque los endpoints JWT
    no inicializan la sesión Flask. El bucketing per-user lo hacen los
    `key_func=` locales en endpoints específicos (ver `_api_login_user`,
    `_api_verify_2fa_user_key`, `_api_refresh_user_key` en api_auth).
    """
    return get_real_client_ip_flask()

limiter = Limiter(
    key_func=rate_limit_key
)
