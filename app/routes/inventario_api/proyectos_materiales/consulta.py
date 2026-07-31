"""Lectura del plan: listado de proyectos, detalle, historial y pedidos."""
from decimal import Decimal

from flask import jsonify
from sqlalchemy.orm import joinedload, selectinload

from app.extensions import db
from app.models import (
    Producto, Proyecto, ProyectoMaterialPlan, ProyectoPlanHistorial,
    SolicitudMaterial, SolicitudMaterialDetalle,
)

from .._core import (
    bp,
    _require_plan_materiales,
    _int_arg,
    _solicitud_to_dict,
)
from ._core import (
    _consumo_por_producto, _denegar_si_ajeno, _f, _scope_proyectos_query,
)



@bp.route('/proyectos-materiales/proyectos', methods=['GET'])
@_require_plan_materiales
def get_proyectos_planificables():
    """Proyectos activos que el usuario puede planear, para el selector
    "crear/abrir plan". Coordinador: solo los suyos; inventario/admin: todos.

    Endpoint aparte de `/proyectos/` (catálogo) a propósito: aquel es genérico y
    lo comparten Pedidos/Entrega directa; este aplica el scoping por dueño."""
    proyectos = (
        _scope_proyectos_query(Proyecto.query.filter(Proyecto.activo == True))  # noqa: E712
        .order_by(Proyecto.numero_proyecto)
        .all()
    )
    return jsonify([
        {'id': p.id, 'numero_proyecto': p.numero_proyecto, 'nombre': p.nombre or ''}
        for p in proyectos
    ])


@bp.route('/proyectos-materiales/', methods=['GET'])
@_require_plan_materiales
def get_proyectos_materiales():
    """Resumen por proyecto activo: totales planeados/entregados, % consumido y
    costos. Solo cuenta proyectos con plan o con consumo (los demás se omiten
    para no llenar la lista con proyectos sin actividad de materiales)."""
    # El coordinador solo ve el resumen de SUS proyectos; inventario/admin, todos.
    proyectos = (
        _scope_proyectos_query(Proyecto.query.filter(Proyecto.activo == True))  # noqa: E712
        .order_by(Proyecto.numero_proyecto)
        .all()
    )

    # Plan agregado por proyecto: cantidad y costo (cantidad × precio del catálogo).
    plan_rows = (
        db.session.query(
            ProyectoMaterialPlan.proyecto_id,
            db.func.coalesce(db.func.sum(ProyectoMaterialPlan.cantidad_planeada), 0),
            db.func.coalesce(
                db.func.sum(ProyectoMaterialPlan.cantidad_planeada * Producto.precio_unitario), 0
            ),
            db.func.count(ProyectoMaterialPlan.id),
        )
        .join(Producto, Producto.id == ProyectoMaterialPlan.producto_id)
        .group_by(ProyectoMaterialPlan.proyecto_id)
        .all()
    )
    plan_por_proy = {
        pid: {'cant': Decimal(str(cant or 0)), 'costo': Decimal(str(costo or 0)), 'lineas': int(n or 0)}
        for pid, cant, costo, n in plan_rows
    }

    # Consumo agregado por proyecto: cantidad entregada y costo.
    consumo_rows = (
        db.session.query(
            SolicitudMaterial.proyecto_id,
            db.func.coalesce(db.func.sum(SolicitudMaterialDetalle.cantidad_entregada), 0),
            db.func.coalesce(
                db.func.sum(SolicitudMaterialDetalle.cantidad_entregada * Producto.precio_unitario), 0
            ),
        )
        .join(SolicitudMaterialDetalle, SolicitudMaterialDetalle.solicitud_id == SolicitudMaterial.id)
        .join(Producto, Producto.id == SolicitudMaterialDetalle.producto_id)
        .filter(SolicitudMaterial.proyecto_id != None)  # noqa: E711
        .group_by(SolicitudMaterial.proyecto_id)
        .all()
    )
    consumo_por_proy = {
        pid: {'cant': Decimal(str(cant or 0)), 'costo': Decimal(str(costo or 0))}
        for pid, cant, costo in consumo_rows
    }

    out = []
    for p in proyectos:
        plan = plan_por_proy.get(p.id)
        cons = consumo_por_proy.get(p.id)
        if not plan and not cons:
            continue  # sin actividad de materiales: no lo mostramos
        cant_plan = plan['cant'] if plan else Decimal('0')
        cant_cons = cons['cant'] if cons else Decimal('0')
        costo_plan = plan['costo'] if plan else Decimal('0')
        costo_cons = cons['costo'] if cons else Decimal('0')
        pct = float(cant_cons / cant_plan * 100) if cant_plan > 0 else None
        out.append({
            'id': p.id,
            'numero_proyecto': p.numero_proyecto,
            'nombre': p.nombre or '',
            'lineas_plan': plan['lineas'] if plan else 0,
            'cantidad_planeada': _f(cant_plan),
            'cantidad_consumida': _f(cant_cons),
            'porcentaje_consumido': round(pct, 1) if pct is not None else None,
            'costo_planeado': _f(costo_plan),
            'costo_consumido': _f(costo_cons),
            'sobre_presupuesto': bool(cant_plan > 0 and cant_cons > cant_plan),
        })
    return jsonify(out)


@bp.route('/proyectos-materiales/<int:proyecto_id>', methods=['GET'])
@_require_plan_materiales
def get_proyecto_materiales_detalle(proyecto_id: int):
    """Detalle por material: une las líneas planeadas con lo consumido (aunque
    un material se haya consumido sin estar en el plan). Por cada material:
    planeado, entregado, %, diferencia (sobre/bajo), costo planeado y consumido."""
    proyecto = Proyecto.query.filter(Proyecto.id == proyecto_id).first()
    if not proyecto:
        return jsonify({'detail': 'Proyecto no encontrado'}), 404
    deneg = _denegar_si_ajeno(proyecto)
    if deneg:
        return deneg

    plan_lineas = (
        ProyectoMaterialPlan.query
        .options(joinedload(ProyectoMaterialPlan.producto))
        .filter(ProyectoMaterialPlan.proyecto_id == proyecto_id)
        .all()
    )
    plan_por_prod = {pl.producto_id: pl for pl in plan_lineas}
    consumo = _consumo_por_producto(proyecto_id)

    # Productos consumidos que no están en el plan: los cargamos para nombrarlos.
    extra_ids = [pid for pid in consumo if pid not in plan_por_prod]
    extra_prods = {}
    if extra_ids:
        extra_prods = {
            p.id: p for p in Producto.query.filter(Producto.id.in_(extra_ids)).all()
        }

    filas = []
    tot_plan_cant = tot_cons_cant = tot_plan_costo = tot_cons_costo = Decimal('0')

    def _fila(prod, plan_linea, cant_plan: Decimal, cant_cons: Decimal):
        precio = Decimal(str(prod.precio_unitario or 0)) if prod else Decimal('0')
        costo_plan = cant_plan * precio
        costo_cons = cant_cons * precio
        pct = float(cant_cons / cant_plan * 100) if cant_plan > 0 else None
        return {
            'linea_id': plan_linea.id if plan_linea else None,
            'producto_id': prod.id if prod else None,
            'codigo': prod.codigo if prod else '---',
            'descripcion': prod.descripcion if prod else 'Producto eliminado',
            'imagen_url': prod.imagen_url if prod else None,
            'unidad': prod.unidad if prod else '',
            'precio_unitario': _f(precio),
            'cantidad_planeada': _f(cant_plan),
            'cantidad_consumida': _f(cant_cons),
            'porcentaje_consumido': round(pct, 1) if pct is not None else None,
            'diferencia': _f(cant_cons - cant_plan),  # >0 = se ocupó más que el plan
            'en_plan': plan_linea is not None,
            'costo_planeado': _f(costo_plan),
            'costo_consumido': _f(costo_cons),
            'notas': plan_linea.notas if plan_linea else None,
        }

    # Líneas planeadas (incluye las que aún no se consumen).
    for pl in plan_lineas:
        cant_plan = Decimal(str(pl.cantidad_planeada or 0))
        cant_cons = consumo.get(pl.producto_id, Decimal('0'))
        filas.append(_fila(pl.producto, pl, cant_plan, cant_cons))
        tot_plan_cant += cant_plan
        tot_cons_cant += cant_cons

    # Consumido sin plan.
    for pid in extra_ids:
        cant_cons = consumo.get(pid, Decimal('0'))
        filas.append(_fila(extra_prods.get(pid), None, Decimal('0'), cant_cons))
        tot_cons_cant += cant_cons

    # Totales de costo (recalculados desde las filas para no duplicar precio lookups).
    for f in filas:
        tot_plan_costo += Decimal(str(f['costo_planeado']))
        tot_cons_costo += Decimal(str(f['costo_consumido']))

    # Orden: primero las del plan, luego extras; dentro, por código.
    filas.sort(key=lambda f: (not f['en_plan'], (f['codigo'] or '').lower()))

    pct_total = float(tot_cons_cant / tot_plan_cant * 100) if tot_plan_cant > 0 else None
    return jsonify({
        'proyecto': {
            'id': proyecto.id,
            'numero_proyecto': proyecto.numero_proyecto,
            'nombre': proyecto.nombre or '',
            'activo': bool(proyecto.activo),
        },
        'materiales': filas,
        'totales': {
            'cantidad_planeada': _f(tot_plan_cant),
            'cantidad_consumida': _f(tot_cons_cant),
            'porcentaje_consumido': round(pct_total, 1) if pct_total is not None else None,
            'costo_planeado': _f(tot_plan_costo),
            'costo_consumido': _f(tot_cons_costo),
            'sobre_presupuesto': bool(tot_plan_cant > 0 and tot_cons_cant > tot_plan_cant),
        },
    })


@bp.route('/proyectos-materiales/<int:proyecto_id>/historial', methods=['GET'])
@_require_plan_materiales
def get_proyecto_plan_historial(proyecto_id: int):
    """Bitácora de cambios del plan de materiales (más reciente primero).

    Cada entrada: quién, cuándo, conteos por tipo de cambio y el desglose
    estructurado (agregados/modificados/eliminados). Soporta `?limit=` (máx 100).
    """
    proyecto = Proyecto.query.filter(Proyecto.id == proyecto_id).first()
    if not proyecto:
        return jsonify({'detail': 'Proyecto no encontrado'}), 404
    deneg = _denegar_si_ajeno(proyecto)
    if deneg:
        return deneg

    limit, lim_err = _int_arg('limit', 50, 1, 100)
    if lim_err:
        return lim_err
    entradas = (
        ProyectoPlanHistorial.query
        .filter(ProyectoPlanHistorial.proyecto_id == proyecto_id)
        .order_by(ProyectoPlanHistorial.created_at.desc(), ProyectoPlanHistorial.id.desc())
        .limit(limit)
        .all()
    )
    return jsonify([
        {
            'id': h.id,
            'usuario': h.usuario or '—',
            'resumen': h.resumen or '',
            'cambios': h.cambios or {'agregados': [], 'modificados': [], 'eliminados': []},
            'n_agregados': h.n_agregados,
            'n_modificados': h.n_modificados,
            'n_eliminados': h.n_eliminados,
            'created_at': h.created_at.isoformat() if h.created_at else None,
        }
        for h in entradas
    ])


@bp.route('/proyectos-materiales/<int:proyecto_id>/pedidos', methods=['GET'])
@_require_plan_materiales
def get_proyecto_pedidos(proyecto_id: int):
    """Solicitudes (pedidos) ligadas al proyecto, con toda su info (solicitante,
    estatus, fechas, detalle de items y cantidades). El PDF de cada una se
    descarga del endpoint existente `/solicitudes/<id>/pdf`."""
    proyecto = Proyecto.query.filter(Proyecto.id == proyecto_id).first()
    if not proyecto:
        return jsonify({'detail': 'Proyecto no encontrado'}), 404
    deneg = _denegar_si_ajeno(proyecto)
    if deneg:
        return deneg

    solicitudes = (
        SolicitudMaterial.query
        .options(
            joinedload(SolicitudMaterial.solicitante),
            selectinload(SolicitudMaterial.detalles).joinedload(SolicitudMaterialDetalle.producto),
            selectinload(SolicitudMaterial.detalles).joinedload(SolicitudMaterialDetalle.herramienta),
        )
        .filter(SolicitudMaterial.proyecto_id == proyecto_id)
        .order_by(SolicitudMaterial.fecha_creacion.desc())
        .all()
    )
    return jsonify([_solicitud_to_dict(s) for s in solicitudes])


@bp.route('/proyectos-materiales/<int:proyecto_id>/existencias', methods=['GET'])
@_require_plan_materiales
def get_proyecto_existencias(proyecto_id: int):
    """Material FÍSICO apartado a este proyecto, por bodega.

    ── Qué lo distingue del resto de vistas del proyecto ──────────────────────
    El detalle del proyecto compara PLANEADO contra CONSUMIDO: cuánto se pensaba
    usar y cuánto ya se entregó. Eso no dice cuánto material hay **ahora mismo
    guardado** a nombre del proyecto, que es una pregunta distinta y sin
    respuesta hasta ahora: había que entrar bodega por bodega y sumar a mano.

    Esta vista lee `stock_almacen_proyecto`, la fuente de verdad del stock por
    proyecto, y devuelve una fila por material con el desglose por bodega.

    Se incluye `cantidad_planeada` cuando el material está en el plan, para
    poder contrastar existencia contra lo previsto. Si el proyecto no tiene
    plan, el campo llega en 0 y la interfaz simplemente no pinta la comparación
    — no se inventa un porcentaje sobre un plan que no existe.
    """
    from app.models import Almacen, StockAlmacenProyecto

    proyecto = Proyecto.query.get(proyecto_id)
    if not proyecto:
        return jsonify({'detail': 'Proyecto no encontrado'}), 404

    # Buckets con existencia de ESTE proyecto, en bodegas activas.
    filas = (
        db.session.query(
            Producto.id, Producto.codigo, Producto.descripcion, Producto.unidad,
            Producto.precio_unitario,
            Almacen.id, Almacen.nombre,
            StockAlmacenProyecto.cantidad,
        )
        .join(Producto, Producto.id == StockAlmacenProyecto.producto_id)
        .join(Almacen, Almacen.id == StockAlmacenProyecto.almacen_id)
        .filter(
            StockAlmacenProyecto.proyecto_id == proyecto_id,
            StockAlmacenProyecto.cantidad > 0,
            Almacen.activo == True,   # noqa: E712
            Producto.activo == True,  # noqa: E712
        )
        .all()
    )

    # Plan del proyecto, para contrastar. Una sola consulta, no una por material.
    planeado = {
        pid: Decimal(str(cant or 0))
        for pid, cant in db.session.query(
            ProyectoMaterialPlan.producto_id, ProyectoMaterialPlan.cantidad_planeada,
        ).filter(ProyectoMaterialPlan.proyecto_id == proyecto_id).all()
    }

    # Columnas: solo las bodegas donde este proyecto tiene algo. Mostrar todas
    # llenaría la tabla de columnas vacías.
    bodegas = {}
    materiales = {}
    for pid, codigo, desc, unidad, precio, aid, anombre, cant in filas:
        bodegas.setdefault(aid, anombre)
        m = materiales.setdefault(pid, {
            'producto_id': pid,
            'codigo': codigo,
            'descripcion': desc,
            'unidad': unidad,
            'precio_unitario': _f(precio or 0),
            'total': Decimal('0'),
            'por_almacen': {},
        })
        c = Decimal(str(cant or 0))
        m['total'] += c
        m['por_almacen'][aid] = _f(m['por_almacen'].get(aid, 0) + float(c))

    salida = []
    for m in materiales.values():
        total = m.pop('total')
        plan = planeado.get(m['producto_id'], Decimal('0'))
        salida.append({
            **m,
            'total': _f(total),
            'cantidad_planeada': _f(plan),
            'valor': _f(total * Decimal(str(m['precio_unitario']))),
            # Porcentaje de cobertura del plan. `None` cuando no hay plan: es
            # distinto de 0 % y la interfaz debe poder diferenciarlo.
            'cobertura': _f(total / plan * 100) if plan > 0 else None,
        })
    salida.sort(key=lambda r: r['total'], reverse=True)

    return jsonify({
        'proyecto': {
            'id': proyecto.id,
            'numero_proyecto': proyecto.numero_proyecto,
            'nombre': proyecto.nombre or '',
        },
        'almacenes': [
            {'id': aid, 'nombre': nombre}
            for aid, nombre in sorted(bodegas.items(), key=lambda kv: kv[1])
        ],
        'materiales': salida,
        'totales': {
            'materiales': len(salida),
            'unidades': _f(sum(Decimal(str(m['total'])) for m in salida)),
            'valor': _f(sum(Decimal(str(m['valor'])) for m in salida)),
        },
    })
