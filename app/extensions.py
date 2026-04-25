from flask_sqlalchemy import SQLAlchemy
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFProtect
from flask_migrate import Migrate
from flask_mail import Mail
from flask import session, request as flask_request
import ipaddress
import redis
import os

db = SQLAlchemy()
csrf = CSRFProtect()
migrate = Migrate()
mail = Mail()

# Rangos CIDR oficiales de Cloudflare (actualizar periódicamente desde https://www.cloudflare.com/ips/)
_CF_CIDR_LIST = [
    "103.21.244.0/22", "103.22.200.0/22", "103.31.4.0/22",
    "104.16.0.0/13",   "104.24.0.0/14",   "108.162.192.0/18",
    "131.0.72.0/22",   "141.101.64.0/18", "162.158.0.0/15",
    "172.64.0.0/13",   "173.245.48.0/20", "188.114.96.0/20",
    "190.93.240.0/20", "197.234.240.0/22","198.41.128.0/17",
]
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
