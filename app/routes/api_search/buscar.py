"""Búsqueda global multi-recurso (Cmd+K / Ctrl+K del SPA)."""
from flask import jsonify, request
from sqlalchemy import or_

from app.extensions import db, limiter, get_real_client_ip_flask
from app.models import (
    CategoriaConfig, Herramienta, Producto, Proyecto, SolicitudMaterial, Trabajador,
)

from ._core import (
    _ROLES_INVENTARIO,
    _ROLES_SOLICITANTE,
    _categoria_item,
    _herramienta_item,
    _producto_item,
    _proyecto_item,
    _require_login,
    _solicitud_item,
    _trabajador_item,
    bp,
)


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
