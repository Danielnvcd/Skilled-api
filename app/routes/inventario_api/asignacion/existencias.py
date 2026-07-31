"""Resúmenes de existencias: tarjetas por proyecto y stock libre (General)."""
from decimal import Decimal

from flask import jsonify, request

from app.extensions import db
from app.models import Almacen, Producto, Proyecto, StockAlmacenProyecto

from .._core import bp, _require_inventario_admin
from ._comun import _dec, _f


# ── Resumen para las tarjetas de la pantalla principal ──────────────────────

@bp.route('/proyectos-materiales/resumen-asignacion', methods=['GET'])
@_require_inventario_admin
def resumen_asignacion():
    """Cuánto material tiene apartado cada proyecto, y cuánto queda libre.

    Alimenta las tarjetas de la sección «Material por proyecto». General va
    SIEMPRE primero: no es un proyecto más, es el stock libre del que sale casi
    toda asignación y el punto de referencia contra el que se leen los demás.

    Toda la agregación ocurre en SQL — no se baja el catálogo al cliente para
    contarlo.
    """
    filas = (
        db.session.query(
            StockAlmacenProyecto.proyecto_id,
            db.func.count(db.distinct(StockAlmacenProyecto.producto_id)),
            db.func.sum(StockAlmacenProyecto.cantidad),
            db.func.sum(StockAlmacenProyecto.cantidad * Producto.precio_unitario),
        )
        .join(Producto, Producto.id == StockAlmacenProyecto.producto_id)
        .join(Almacen, Almacen.id == StockAlmacenProyecto.almacen_id)
        .filter(
            StockAlmacenProyecto.cantidad > 0,
            Producto.activo == True,   # noqa: E712
            Almacen.activo == True,    # noqa: E712
        )
        .group_by(StockAlmacenProyecto.proyecto_id)
        .all()
    )
    por_proyecto = {
        pid: {'materiales': int(n or 0), 'unidades': _f(u), 'valor': _f(v)}
        for pid, n, u, v in filas
    }

    vacio = {'materiales': 0, 'unidades': 0.0, 'valor': 0.0}
    tarjetas = [{
        'proyecto_id': None,
        'numero_proyecto': 'General',
        'nombre': 'Stock libre, sin apartar',
        'es_general': True,
        **por_proyecto.get(None, vacio),
    }]

    # Se listan TODOS los proyectos activos, incluso sin material: poder ver
    # que una obra no tiene nada apartado es información, y además es el punto
    # de partida natural para asignarle.
    for p in (Proyecto.query
              .filter(Proyecto.activo == True)  # noqa: E712
              .order_by(Proyecto.numero_proyecto)
              .all()):
        tarjetas.append({
            'proyecto_id': p.id,
            'numero_proyecto': p.numero_proyecto,
            'nombre': p.nombre or '',
            'es_general': False,
            **por_proyecto.get(p.id, vacio),
        })

    return jsonify({
        'tarjetas': tarjetas,
        'total_apartado': _f(sum(
            d['unidades'] for pid, d in por_proyecto.items() if pid is not None
        )),
    })


# ── Stock libre (General) ───────────────────────────────────────────────────

@bp.route('/proyectos-materiales/general/existencias', methods=['GET'])
@_require_inventario_admin
def existencias_general():
    """Material libre —sin apartar a ninguna obra—, desglosado por bodega.

    Es el espejo de `/<id>/existencias`, pero para el bucket sin proyecto. Hace
    falta porque el sentido natural del flujo es General → obra: quien tiene
    material libre quiere mandarlo a un proyecto, y hasta ahora el único camino
    era entrar primero al proyecto y buscar de vuelta el material.

    A diferencia del de un proyecto, este SÍ pagina: General suele tener el
    catálogo casi entero, y bajarlo completo al navegador sería regalar unos
    cuantos megabytes por cada visita a la pantalla.
    """
    q = (request.args.get('q') or '').strip()
    try:
        pagina = max(1, int(request.args.get('page', 1)))
    except (TypeError, ValueError):
        pagina = 1
    try:
        por_pagina = min(200, max(1, int(request.args.get('per_page', 50))))
    except (TypeError, ValueError):
        por_pagina = 50

    base = (
        db.session.query(StockAlmacenProyecto.producto_id)
        .join(Producto, Producto.id == StockAlmacenProyecto.producto_id)
        .join(Almacen, Almacen.id == StockAlmacenProyecto.almacen_id)
        .filter(
            StockAlmacenProyecto.proyecto_id.is_(None),
            StockAlmacenProyecto.cantidad > 0,
            Producto.activo == True,   # noqa: E712
            Almacen.activo == True,    # noqa: E712
        )
    )
    if q:
        patron = f'%{q}%'
        base = base.filter(db.or_(Producto.codigo.ilike(patron),
                                  Producto.descripcion.ilike(patron)))

    # Se pagina por PRODUCTO, no por bucket: una fila de la tabla es un material
    # con sus bodegas al lado. Paginar por bucket partiría un material a la
    # mitad entre dos páginas.
    ids_q = base.group_by(StockAlmacenProyecto.producto_id).subquery()
    total = db.session.query(db.func.count()).select_from(ids_q).scalar() or 0

    ids = [
        pid for (pid,) in
        base.group_by(StockAlmacenProyecto.producto_id)
        .order_by(db.func.sum(StockAlmacenProyecto.cantidad).desc())
        .limit(por_pagina).offset((pagina - 1) * por_pagina).all()
    ]

    materiales, bodegas = [], {}
    if ids:
        filas = (
            db.session.query(
                Producto.id, Producto.codigo, Producto.descripcion, Producto.unidad,
                Producto.precio_unitario, Almacen.id, Almacen.nombre,
                StockAlmacenProyecto.cantidad,
            )
            .join(Producto, Producto.id == StockAlmacenProyecto.producto_id)
            .join(Almacen, Almacen.id == StockAlmacenProyecto.almacen_id)
            .filter(
                StockAlmacenProyecto.proyecto_id.is_(None),
                StockAlmacenProyecto.cantidad > 0,
                StockAlmacenProyecto.producto_id.in_(ids),
                Almacen.activo == True,  # noqa: E712
            )
            .all()
        )
        acumulado = {}
        for pid, codigo, desc, unidad, precio, aid, anombre, cant in filas:
            bodegas.setdefault(aid, anombre)
            m = acumulado.setdefault(pid, {
                'producto_id': pid, 'codigo': codigo, 'descripcion': desc,
                'unidad': unidad, 'precio_unitario': _f(precio or 0),
                'total': Decimal('0'), 'por_almacen': {},
            })
            c = _dec(cant)
            m['total'] += c
            m['por_almacen'][aid] = _f(_dec(m['por_almacen'].get(aid, 0)) + c)

        orden = {pid: i for i, pid in enumerate(ids)}
        for m in sorted(acumulado.values(), key=lambda r: orden[r['producto_id']]):
            total_m = m.pop('total')
            materiales.append({
                **m,
                'total': _f(total_m),
                'valor': _f(total_m * _dec(m['precio_unitario'])),
            })

    return jsonify({
        'almacenes': [{'id': aid, 'nombre': n}
                      for aid, n in sorted(bodegas.items(), key=lambda kv: kv[1])],
        'materiales': materiales,
        'total': int(total),
        'page': pagina,
        'per_page': por_pagina,
        'pages': max(1, (int(total) + por_pagina - 1) // por_pagina),
    })
