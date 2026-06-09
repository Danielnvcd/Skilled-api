"""API JSON para el módulo de Histórico de nóminas (SPA React)."""
from ._core import bp
from . import export, listado  # noqa: F401

__all__ = ['bp']
