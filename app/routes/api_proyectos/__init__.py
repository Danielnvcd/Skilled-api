"""API JSON para el módulo de Proyectos (SPA React)."""
from ._core import bp
from . import escritura, lectura  # noqa: F401

__all__ = ['bp']
