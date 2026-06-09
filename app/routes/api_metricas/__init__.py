"""API JSON para la página de Métricas (SPA React).

Solo admin / super_admin pueden consultar.
"""
from ._core import bp
from . import endpoints  # noqa: F401

__all__ = ['bp']
