"""Inventario → Proyectos: plan de materiales, consumo real y costos.

El rol Inventario captura un **plan** de materiales por proyecto
(`ProyectoMaterialPlan`). El **consumo** se deriva en vivo de las solicitudes
ENTREGADAS ligadas al proyecto (`SolicitudMaterial.proyecto_id` +
`SolicitudMaterialDetalle.cantidad_entregada`). El **costo** sale del catálogo
(`Producto.precio_unitario`): planeado = cantidad_planeada × precio,
consumido = cantidad_entregada × precio.

Endpoints:
  GET    /proyectos-materiales/                 resumen por proyecto
  GET    /proyectos-materiales/<id>             detalle (líneas planeado vs. entregado)
  POST   /proyectos-materiales/<id>/plan        upsert del plan completo
  DELETE /proyectos-materiales/<id>/plan/<lid>  quitar una línea del plan
"""
from decimal import Decimal

from flask import jsonify, request
from sqlalchemy.orm import joinedload, selectinload

from app.extensions import db
from app.models import (
    Producto, Proyecto, ProyectoMaterialPlan, ProyectoPlanHistorial,
    SolicitudMaterial, SolicitudMaterialDetalle,
)
from app.realtime import emit_to_role

from ._core import (
    bp,
    _require_plan_materiales,
    _parse_or_422, _int_arg,
    ProyectoPlanUpsertSchema,
    _audit, _INV_ROLES,
    _solicitud_to_dict,
)


def _f(v) -> float:
    return float(v or 0)


# Unidades "contables": no tiene sentido planear 2.5 piezas. El resto de
# unidades (kg, m, l, m2, m3, rollo…) sí admite decimales. Mantener en sintonía
# con `ENTERO_UNIDS` del frontend (ProyectoInventarioDetalle.jsx).
_UNIDADES_ENTERAS = {
    'pza', 'pz', 'pieza', 'piezas', 'pieza(s)',
    'unidad', 'unidades', 'u', 'und', 'uds', 'pieza',
    'caja', 'cajas',
}


def _es_unidad_entera(unidad: str | None) -> bool:
    return (unidad or '').strip().lower() in _UNIDADES_ENTERAS


def _consumo_por_producto(proyecto_id: int) -> dict[int, Decimal]:
    """Suma `cantidad_entregada` (consumo real) por producto para las
    solicitudes MATERIAL ligadas a este proyecto. Una sola query agregada."""
    rows = (
        db.session.query(
            SolicitudMaterialDetalle.producto_id,
            db.func.coalesce(db.func.sum(SolicitudMaterialDetalle.cantidad_entregada), 0),
        )
        .join(SolicitudMaterial, SolicitudMaterial.id == SolicitudMaterialDetalle.solicitud_id)
        .filter(
            SolicitudMaterial.proyecto_id == proyecto_id,
            SolicitudMaterialDetalle.producto_id != None,  # noqa: E711  solo MATERIAL
        )
        .group_by(SolicitudMaterialDetalle.producto_id)
        .all()
    )
    return {pid: Decimal(str(cant or 0)) for pid, cant in rows}


def _denegar_si_ajeno(proyecto: Proyecto):
    """Scoping por dueño: el coordinador solo puede tocar SUS proyectos
    (`Proyecto.coordinador_id`). Para inventario/admin/super_admin no aplica.

    Devuelve una respuesta 403 si un coordinador intenta un proyecto ajeno, o
    None si está permitido. 403 (no 404) es consistente con `api_proyectos`: el
    proyecto existe pero no es suyo."""
    user = request.current_user
    if user.role == 'coordinador' and proyecto.coordinador_id != user.id:
        return jsonify({'detail': 'No eres el coordinador de este proyecto'}), 403
    return None


def _scope_proyectos_query(query):
    """Restringe una query de `Proyecto` al alcance del usuario: el coordinador
    solo ve los suyos; inventario/admin/super_admin ven todos."""
    user = request.current_user
    if user.role == 'coordinador':
        query = query.filter(Proyecto.coordinador_id == user.id)
    return query


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


@bp.route('/proyectos-materiales/<int:proyecto_id>/plan', methods=['POST'])
@_require_plan_materiales
def upsert_proyecto_plan(proyecto_id: int):
    """Reemplaza el plan de materiales del proyecto con las líneas enviadas.

    Upsert por producto: las líneas existentes se actualizan, las nuevas se
    crean y las que ya no vienen se eliminan. Valida que cada producto exista,
    esté activo y no se repita en el payload.
    """
    proyecto = Proyecto.query.filter(Proyecto.id == proyecto_id).first()
    if not proyecto:
        return jsonify({'detail': 'Proyecto no encontrado'}), 404
    deneg = _denegar_si_ajeno(proyecto)
    if deneg:
        return deneg

    data, err = _parse_or_422(ProyectoPlanUpsertSchema(), request.get_json(silent=True))
    if err:
        return err

    lineas = data['lineas']
    # Validar productos: sin duplicados, existentes y activos.
    vistos: set[int] = set()
    prod_ids = []
    for ln in lineas:
        pid = ln['producto_id']
        if pid in vistos:
            return jsonify({'detail': f'Producto #{pid} repetido en el plan'}), 422
        vistos.add(pid)
        prod_ids.append(pid)

    productos = {}
    if prod_ids:
        productos = {
            p.id: p for p in Producto.query.filter(
                Producto.id.in_(prod_ids), Producto.activo == True  # noqa: E712
            ).all()
        }
        faltantes = [pid for pid in prod_ids if pid not in productos]
        if faltantes:
            return jsonify({
                'detail': f'Productos inexistentes o inactivos: {faltantes}'
            }), 422

    # Las unidades contables (pza, caja…) no admiten fracciones.
    for ln in lineas:
        prod = productos[ln['producto_id']]
        cant = float(ln['cantidad_planeada'])
        if _es_unidad_entera(prod.unidad) and cant != int(cant):
            return jsonify({
                'detail': f'"{prod.codigo}" se mide en {prod.unidad}: '
                          f'usa una cantidad entera (sin decimales).'
            }), 422

    user = request.current_user
    existentes = {
        pl.producto_id: pl for pl in ProyectoMaterialPlan.query.filter(
            ProyectoMaterialPlan.proyecto_id == proyecto_id
        ).all()
    }

    # Desglose estructurado de cambios para la bitácora/historial.
    agregados, modificados, eliminados = [], [], []

    def _info(pid, prod) -> tuple[str, str]:
        return (
            (prod.codigo if prod else None) or f'#{pid}',
            (prod.descripcion if prod else None) or 'Producto eliminado',
        )

    nuevos_ids = set(vistos)
    # Eliminar líneas que ya no vienen.
    for pid, pl in existentes.items():
        if pid not in nuevos_ids:
            cod, desc = _info(pid, pl.producto)
            eliminados.append({'codigo': cod, 'descripcion': desc})
            db.session.delete(pl)

    # Crear / actualizar.
    for ln in lineas:
        pid = ln['producto_id']
        cant = Decimal(str(ln['cantidad_planeada']))
        notas = (ln.get('notas') or None)
        cod, desc = _info(pid, productos.get(pid))
        pl = existentes.get(pid)
        if pl:
            cant_cambio = pl.cantidad_planeada != cant
            notas_cambio = (pl.notas or None) != notas
            if cant_cambio or notas_cambio:
                item = {'codigo': cod, 'descripcion': desc}
                if cant_cambio:
                    item['antes'] = _f(pl.cantidad_planeada)
                    item['despues'] = _f(cant)
                if notas_cambio:
                    item['notas_antes'] = pl.notas or ''
                    item['notas_despues'] = notas or ''
                modificados.append(item)
            pl.cantidad_planeada = cant
            pl.notas = notas
        else:
            agregados.append({'codigo': cod, 'descripcion': desc, 'cantidad': _f(cant)})
            db.session.add(ProyectoMaterialPlan(
                proyecto_id=proyecto_id,
                producto_id=pid,
                cantidad_planeada=cant,
                notas=notas,
                created_by_id=user.id,
            ))

    # Resumen legible "+[..] ~[..] -[..]" para la bitácora.
    def _mod_label(m):
        det = []
        if 'antes' in m:
            det.append(f"{m['antes']}→{m['despues']}")
        if 'notas_antes' in m:
            det.append('notas')
        return f"{m['codigo']}({', '.join(det)})"

    partes = []
    if agregados:
        partes.append("+[" + ', '.join(f"{a['codigo']}({a['cantidad']})" for a in agregados) + "]")
    if modificados:
        partes.append("~[" + ', '.join(_mod_label(m) for m in modificados) + "]")
    if eliminados:
        partes.append("-[" + ', '.join(e['codigo'] for e in eliminados) + "]")
    resumen = ' '.join(partes) if partes else 'sin cambios'

    # Una fila de historial por cada guardado con cambios reales.
    if agregados or modificados or eliminados:
        db.session.add(ProyectoPlanHistorial(
            proyecto_id=proyecto_id,
            user_id=user.id,
            usuario=user.username,
            resumen=resumen[:500],
            cambios={'agregados': agregados, 'modificados': modificados, 'eliminados': eliminados},
            n_agregados=len(agregados),
            n_modificados=len(modificados),
            n_eliminados=len(eliminados),
        ))

    _audit(user, f"Plan materiales proyecto #{proyecto_id}: {resumen}")
    db.session.commit()
    emit_to_role(_INV_ROLES, 'proyecto_material:changed', {
        'proyecto_id': proyecto_id, 'action': 'plan_updated',
    })
    return get_proyecto_materiales_detalle(proyecto_id)


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


@bp.route('/proyectos-materiales/<int:proyecto_id>/plan/<int:linea_id>', methods=['DELETE'])
@_require_plan_materiales
def delete_proyecto_plan_linea(proyecto_id: int, linea_id: int):
    """Quita una línea del plan de materiales del proyecto."""
    pl = (
        ProyectoMaterialPlan.query
        .filter(
            ProyectoMaterialPlan.id == linea_id,
            ProyectoMaterialPlan.proyecto_id == proyecto_id,
        )
        .first()
    )
    if not pl:
        return jsonify({'detail': 'Línea de plan no encontrada'}), 404
    deneg = _denegar_si_ajeno(pl.proyecto)
    if deneg:
        return deneg

    db.session.delete(pl)
    _audit(request.current_user, f"Plan proyecto #{proyecto_id}: línea #{linea_id} eliminada")
    db.session.commit()
    emit_to_role(_INV_ROLES, 'proyecto_material:changed', {
        'proyecto_id': proyecto_id, 'action': 'linea_deleted', 'linea_id': linea_id,
    })
    return jsonify({'ok': True})


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
