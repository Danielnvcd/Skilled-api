from flask_sqlalchemy import SQLAlchemy
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFProtect
from flask_migrate import Migrate
from flask_mail import Mail
from flask import session, request as flask_request
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
                _redis_client = redis.from_url(redis_url, decode_responses=True)
                _redis_client.ping()
            except Exception:
                _redis_client = None
    return _redis_client

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
    """Clave de rate limit: user_id autenticado o IP real del cliente."""
    return str(session.get("user_id", get_real_client_ip_flask()))

limiter = Limiter(
    key_func=rate_limit_key
)
