"""Identificadores internos de las unidades de herramienta."""
from sqlalchemy import func

from app.extensions import db
from app.models import HerramientaUnidad


def _next_codigo_interno() -> str:
    """Genera el siguiente codigo_interno tipo HRR-000123. Bloquea contra
    duplicados consultando el max actual y sumando 1 (suficiente con la
    constraint UNIQUE que falla cualquier colisión por race condition)."""
    last = (db.session.query(func.max(HerramientaUnidad.id)).scalar() or 0) + 1
    return f"HRR-{last:06d}"
