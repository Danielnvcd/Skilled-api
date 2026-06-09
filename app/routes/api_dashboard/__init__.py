"""API JSON para el Dashboard / página de inicio (SPA React)."""
from ._core import bp
from . import alertas, dashboard  # noqa: F401

__all__ = ['bp']
