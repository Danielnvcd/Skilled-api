from flask_sqlalchemy import SQLAlchemy
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFProtect
from flask_migrate import Migrate
from flask import session

db = SQLAlchemy()
csrf = CSRFProtect()
migrate = Migrate()

def rate_limit_key():
    return str(session.get("user_id", get_remote_address()))

limiter = Limiter(
    key_func=rate_limit_key
)
