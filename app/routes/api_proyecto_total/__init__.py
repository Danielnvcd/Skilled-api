"""API JSON para la vista de Proyecto Total (SPA React)."""
from ._core import bp
from . import export, listado  # noqa: F401

__all__ = ['bp']
