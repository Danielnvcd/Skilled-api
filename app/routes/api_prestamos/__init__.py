"""API JSON para Préstamos (SPA React)."""
from ._core import bp
from . import abonos, crud, excel  # noqa: F401

__all__ = ['bp']
