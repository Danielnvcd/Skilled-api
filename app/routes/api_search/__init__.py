"""Búsqueda global para el SPA (Cmd+K / Ctrl+K).

Endpoint único `GET /api/v1/buscar?q=<term>` que busca en productos, solicitudes,
categorías, herramientas, trabajadores y proyectos. Devuelve los resultados
agrupados por tipo, ya filtrados por el rol del usuario autenticado.
"""
from ._core import bp
from . import buscar  # noqa: F401

__all__ = ['bp']
