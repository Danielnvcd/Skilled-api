from flask_sqlalchemy import SQLAlchemy
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFProtect
from flask_migrate import Migrate
from flask import session
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

def rate_limit_key():
    return str(session.get("user_id", get_remote_address()))

limiter = Limiter(
    key_func=rate_limit_key
)
