"""Decorador `jwt_required` — importado por TODOS los blueprints `api_*`.

Solo importa de `_core` y `tokens` para evitar ciclos con los módulos de
endpoints. Si esto se cruza con `login.py`/`twofa.py` el arranque revienta.
"""
from datetime import datetime
from functools import wraps

from flask import g, jsonify, request

from app.extensions import db
from app.models import User

from .tokens import _decode_token, _is_jti_revoked


def jwt_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        auth = request.headers.get('Authorization', '')
        if not auth.startswith('Bearer '):
            return jsonify({'error': 'Token requerido'}), 401
        token = auth.split(' ', 1)[1].strip()
        payload = _decode_token(token, 'access')
        if not payload:
            return jsonify({'error': 'Token inválido o expirado'}), 401

        # Blacklist check: si el jti fue revocado (logout, admin force-out),
        # rechazamos el token aunque su firma y exp sean válidos.
        jti = payload.get('jti')
        if jti and _is_jti_revoked(jti):
            return jsonify({'error': 'Sesión cerrada'}), 401

        # Validación robusta del sub: el JWT puede llevar cualquier string.
        # Si no es convertible a int (porque viene corrupto o de otro service),
        # debe ser 401 — no 500 — para no leakear stacks.
        try:
            uid = int(payload['sub'])
        except (TypeError, ValueError, KeyError):
            return jsonify({'error': 'Token inválido'}), 401

        user = db.session.get(User, uid)
        if not user:
            return jsonify({'error': 'Usuario no encontrado'}), 401
        # Invalidar JWT si la contraseña cambió desde que se emitió.
        if (user.password_version or 1) != payload.get('pv', 1):
            return jsonify({'error': 'Sesión inválida, vuelve a iniciar sesión'}), 401
        # Borrado lógico: una cuenta desactivada no puede usar la API aunque su
        # token siga vigente. Cubre todos los endpoints `api_*` de una sola vez.
        if not user.activo:
            return jsonify({'error': 'Cuenta desactivada'}), 401
        # Mantener `last_seen` fresco para el indicador "en línea". Throttled
        # a 60s para no commitear en cada request. La conexión Socket.IO y su
        # handler 'heartbeat' cubren el caso de lectura pura (sin requests).
        try:
            now = datetime.now()
            if not user.last_seen or (now - user.last_seen).total_seconds() > 60:
                user.last_seen = now
                db.session.commit()
        except Exception:
            try:
                db.session.rollback()
            except Exception:
                pass
        g._jwt_user = user
        return fn(*args, **kwargs)

    return wrapper
