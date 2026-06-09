"""Núcleo del paquete `api_search`. Blueprint + roles + item serializers."""
from functools import wraps

from flask import Blueprint, g, request

from app.models import (
    Herramienta, Producto, Proyecto, SolicitudMaterial, Trabajador,
)
from app.routes.api_auth import jwt_required


bp = Blueprint('api_search', __name__, url_prefix='/api/v1')


# Roles ya conocidos (heredados de inventario_api.py).
_ROLES_INVENTARIO = {'inventario', 'admin', 'super_admin'}
_ROLES_ADMIN = {'admin', 'super_admin'}
_ROLES_SOLICITANTE = {'solicitante_material', 'inventario', 'admin', 'super_admin'}


def _require_login(view):
    @wraps(view)
    @jwt_required
    def wrapper(*args, **kwargs):
        request.current_user = g._jwt_user
        return view(*args, **kwargs)
    return wrapper


def _producto_item(p: Producto) -> dict:
    return {
        'tipo': 'producto',
        'id': p.id,
        'label': f'{p.codigo} — {p.descripcion}',
        'subtitle': f'{p.categoria} · stock {float(p.stock_actual or 0):g} {p.unidad}',
        # No hay ruta de detalle de producto: caemos al catálogo con filtro.
        'url': f'/inventario/catalogo?q={p.codigo}',
    }


def _solicitud_item(s: SolicitudMaterial) -> dict:
    return {
        'tipo': 'solicitud',
        'id': s.id,
        'label': f'SOL-{s.id:06d}',
        'subtitle': f"{s.estatus} · {(s.proyecto or 'Sin proyecto')}",
        'url': f'/inventario/solicitudes?folio={s.id}',
    }


def _categoria_item(nombre: str) -> dict:
    return {
        'tipo': 'categoria',
        'id': nombre,
        'label': nombre,
        'subtitle': 'Categoría de inventario',
        'url': f'/inventario/catalogo?categoria={nombre}',
    }


def _trabajador_item(t: Trabajador) -> dict:
    return {
        'tipo': 'trabajador',
        'id': t.id,
        'label': f'#{t.no_empleado} — {t.nombre} {t.nombre_apellidos}',
        'subtitle': (t.puesto or '') + (f' · {t.area}' if t.area else ''),
        'url': f'/empleados/{t.id}',
    }


def _herramienta_item(h: Herramienta) -> dict:
    return {
        'tipo': 'herramienta',
        'id': h.id,
        'label': f'{h.sku} — {h.descripcion}',
        'subtitle': f'Herramienta · {h.unidad or "pza"}',
        'url': f'/inventario/herramientas?q={h.sku}',
    }


def _proyecto_item(p: Proyecto) -> dict:
    return {
        'tipo': 'proyecto',
        'id': p.id,
        'label': f'{p.numero_proyecto} — {p.nombre or "(sin nombre)"}',
        'subtitle': 'Proyecto',
        'url': f'/proyectos?q={p.numero_proyecto}',
    }
