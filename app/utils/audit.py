"""Audit log helper compartido por todos los blueprints."""
import logging

from flask import g

from app.extensions import db
from app.models import AuditLog
from app.utils.security import _safe_log_value

logger = logging.getLogger(__name__)


def log_action(action):
    try:
        # Usar el helper que valida que CF-Connecting-IP venga realmente de un rango Cloudflare.
        # Sin esta validación un atacante puede spoofear el header y envenenar el log de auditoría.
        from app.extensions import get_real_client_ip_flask
        ip = get_real_client_ip_flask()

        # API-only: el usuario lo pone `jwt_required` en `g._jwt_user`. No hay
        # sesión Flask que consultar (las rutas UI server-side se borraron).
        jwt_user = getattr(g, '_jwt_user', None)
        username = jwt_user.username if jwt_user is not None else 'anon'

        log = AuditLog(
            user=_safe_log_value(username, 80),
            action=_safe_log_value(action, 200),  # MED-03: anti CRLF / log forging
            ip=ip,
        )
        db.session.add(log)
        db.session.commit()
    except Exception as e:
        logger.warning(f"Error guardando log de auditoría (acción: {_safe_log_value(action, 100)}): {e}")
