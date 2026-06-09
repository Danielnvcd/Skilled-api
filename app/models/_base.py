"""Helpers compartidos entre todos los sub-módulos de models."""
from datetime import datetime, timezone


def _now_utc():
    """Retorna el datetime actual en UTC con tzinfo. Usar como default en modelos."""
    return datetime.now(timezone.utc)
