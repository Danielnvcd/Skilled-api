from flask_sqlalchemy import SQLAlchemy
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFProtect
from flask_migrate import Migrate
from flask import session, request as flask_request
import redis
import os

db = SQLAlchemy()
csrf = CSRFProtect()
migrate = Migrate()

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
    Cloudflare añade CF-Connecting-IP con la IP real del navegador.
    ProxyFix ya normaliza X-Forwarded-For, pero CF-Connecting-IP es más fiable
    detrás de un tunnel donde el origen solo recibe tráfico de Cloudflare.
    """
    cf_ip = flask_request.headers.get("CF-Connecting-IP")
    if cf_ip:
        return cf_ip.strip()
    return get_remote_address()

def rate_limit_key() -> str:
    """Clave de rate limit: user_id autenticado o IP real del cliente."""
    return str(session.get("user_id", get_real_client_ip_flask()))

limiter = Limiter(
    key_func=rate_limit_key
)
