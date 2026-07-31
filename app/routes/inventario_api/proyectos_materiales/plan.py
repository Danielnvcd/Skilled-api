"""Escritura del plan de materiales de un proyecto (upsert y borrado)."""
from decimal import Decimal

from flask import jsonify, request

from app.extensions import db
from app.models import (
    Producto, Proyecto, ProyectoMaterialPlan, ProyectoPlanHistorial,
)
from app.realtime import emit_to_role

from .._core import (
    bp,
    _require_plan_materiales,
    _parse_or_422,
    ProyectoPlanUpsertSchema,
    _audit,
    _INV_ROLES,
)
from ._core import _denegar_si_ajeno, _es_unidad_entera, _f
from .consulta import get_proyecto_materiales_detalle


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
