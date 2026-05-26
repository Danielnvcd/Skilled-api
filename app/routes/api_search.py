"""Búsqueda global para el SPA (Cmd+K / Ctrl+K).

Endpoint único `GET /api/v1/buscar?q=<term>` que busca en productos, solicitudes,
categorías, herramientas, trabajadores y proyectos. Devuelve los resultados
agrupados por tipo, ya filtrados por el rol del usuario autenticado.

Diseño:
  - Solo carga columnas necesarias (lightweight queries para responder rápido).
  - ILIKE case-insensitive con `%q%`. Limit chico por tipo (default 6, máx 10).
  - Mínimo 2 caracteres en `q` (evita DoS por queries cortas).
  - Rate limit de 30/min por usuario (las búsquedas son frecuentes pero no
    deberían martillar la DB).
  - Cada item incluye `url` precalculada del SPA, así el frontend no tiene que
    inventar rutas — si una ruta cambia en el SPA, se ajusta acá y todos los
    consumidores quedan en sync.
"""
from functools import wraps

from flask import Blueprint, g, jsonify, request
from sqlalchemy import or_

from app.extensions import db, limiter, get_real_client_ip_flask
from app.models import (
    Producto, SolicitudMaterial, CategoriaConfig, Trabajador,
    Herramienta, Proyecto,
)
from app.routes.api_auth import jwt_required

bp = Blueprint('api_search', __name__, url_prefix='/api/v1')


def _require_login(view):
    @wraps(view)
    @jwt_required
    def wrapper(*args, **kwargs):
        request.current_user = g._jwt_user
        return view(*args, **kwargs)
    return wrapper


# Roles ya conocidos (heredados de inventario_api.py).
_ROLES_INVENTARIO = {'inventario', 'admin', 'super_admin'}
_ROLES_ADMIN = {'admin', 'super_admin'}
_ROLES_SOLICITANTE = {'solicitante_material', 'inventario', 'admin', 'super_admin'}


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


@bp.route('/buscar', methods=['GET'])
@limiter.limit('30/minute', key_func=lambda: f'ip:{get_real_client_ip_flask()}')
@_require_login
def buscar_global():
    q = (request.args.get('q') or '').strip()
    if len(q) < 2:
        return jsonify({
            'productos': [], 'solicitudes': [], 'categorias': [],
            'trabajadores': [], 'herramientas': [], 'proyectos': [],
        })
    if len(q) > 80:
        return jsonify({'detail': 'Término de búsqueda demasiado largo'}), 422

    try:
        limit = int(request.args.get('limit') or 6)
    except (TypeError, ValueError):
        limit = 6
    limit = max(1, min(limit, 10))

    user = request.current_user
    role = user.role
    like = f'%{q}%'

    out = {
        'productos': [], 'solicitudes': [], 'categorias': [],
        'trabajadores': [], 'herramientas': [], 'proyectos': [],
    }

    # ── Productos: visibles para cualquier rol con acceso a inventario.
    if role in (_ROLES_INVENTARIO | {'solicitante_material'}):
        productos = (
            Producto.query
            .filter(
                Producto.activo == True,  # noqa: E712
                or_(
                    Producto.codigo.ilike(like),
                    Producto.descripcion.ilike(like),
                    Producto.categoria.ilike(like),
                ),
            )
            .order_by(Producto.codigo)
            .limit(limit)
            .all()
        )
        out['productos'] = [_producto_item(p) for p in productos]

    # ── Solicitudes: solicitantes solo las propias.
    if role in _ROLES_SOLICITANTE:
        sol_q = SolicitudMaterial.query
        if role == 'solicitante_material':
            sol_q = sol_q.filter(SolicitudMaterial.solicitante_id == user.id)
        # Búsqueda por proyecto o por folio numérico (SOL-000123 -> 123).
        folio_int = None
        try:
            folio_int = int(q.lstrip('SOL-').lstrip('sol-').lstrip('0') or '0')
        except (TypeError, ValueError):
            folio_int = None
        filtros = [SolicitudMaterial.proyecto.ilike(like)]
        if folio_int:
            filtros.append(SolicitudMaterial.id == folio_int)
        sol_q = sol_q.filter(or_(*filtros))
        solicitudes = sol_q.order_by(SolicitudMaterial.id.desc()).limit(limit).all()
        out['solicitudes'] = [_solicitud_item(s) for s in solicitudes]

    # ── Categorías: tomamos de CategoriaConfig (es la fuente canónica desde
    # la mejora de importación). Lectura abierta como en /categorias-config/.
    cats = (
        db.session.query(CategoriaConfig.nombre)
        .filter(CategoriaConfig.nombre.ilike(like))
        .order_by(CategoriaConfig.nombre)
        .limit(limit)
        .all()
    )
    out['categorias'] = [_categoria_item(nombre) for (nombre,) in cats]

    # ── Herramientas: visibles para inventario y solicitantes.
    if role in (_ROLES_INVENTARIO | {'solicitante_material'}):
        try:
            herramientas = (
                Herramienta.query
                .filter(
                    Herramienta.activo == True,  # noqa: E712
                    or_(
                        Herramienta.sku.ilike(like),
                        Herramienta.descripcion.ilike(like),
                    ),
                )
                .order_by(Herramienta.sku)
                .limit(limit)
                .all()
            )
            out['herramientas'] = [_herramienta_item(h) for h in herramientas]
        except Exception:
            # Si el módulo de herramientas no está disponible en este entorno,
            # no rompemos la búsqueda global.
            out['herramientas'] = []

    # ── Trabajadores: dato sensible, solo admin/super_admin/inventario.
    if role in (_ROLES_INVENTARIO | {'admin', 'super_admin'}):
        trabajadores = (
            Trabajador.query
            .filter(
                Trabajador.activo == True,  # noqa: E712
                or_(
                    Trabajador.no_empleado.ilike(like),
                    Trabajador.nombre.ilike(like),
                    Trabajador.nombre_apellidos.ilike(like),
                ),
            )
            .order_by(Trabajador.no_empleado)
            .limit(limit)
            .all()
        )
        out['trabajadores'] = [_trabajador_item(t) for t in trabajadores]

    # ── Proyectos: cualquier usuario autenticado puede buscarlos (son
    # referencia común en solicitudes y reportes).
    proyectos = (
        Proyecto.query
        .filter(
            Proyecto.activo == True,  # noqa: E712
            or_(
                Proyecto.numero_proyecto.ilike(like),
                Proyecto.nombre.ilike(like),
            ),
        )
        .order_by(Proyecto.numero_proyecto)
        .limit(limit)
        .all()
    )
    out['proyectos'] = [_proyecto_item(p) for p in proyectos]

    out['total'] = sum(len(v) for v in out.values() if isinstance(v, list))
    return jsonify(out)
