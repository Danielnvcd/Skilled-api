"""Aplicar la asignación y devolver material del proyecto.

Ambas operaciones pasan por los helpers de stock del núcleo y generan un
`MovimientoInventario` por línea: no hay vía paralela de tocar existencias.
"""
from flask import jsonify, request

from app.extensions import db
from app.models import MovimientoInventario, Producto, Proyecto
from app.realtime import emit_to_role

from .._core import (
    bp,
    transaccion_de_stock,
    _require_inventario_admin,
    _audit,
    _depositar, _recalcular_caches, _consumir_bucket_exacto,
    _INV_ROLES,
)
from ._comun import _consumir_o_abortar, _dec
from .validacion import _leer_peticion, _resolver_lineas, _resumen


@bp.route('/proyectos-materiales/<int:proyecto_id>/asignar/previsualizar', methods=['POST'])
@_require_inventario_admin
def previsualizar_asignacion(proyecto_id: int):
    """Simula la asignación. NO escribe absolutamente nada.

    Es el paso que hace segura la decisión de «asignar lo disponible cuando no
    alcanza»: el usuario ve el ajuste antes de aplicarlo, así que nunca es una
    sorpresa.
    """
    ctx, err = _leer_peticion(proyecto_id)
    if err:
        return err
    proyecto, lineas, origen, modo, _ = ctx

    plan = _resolver_lineas(proyecto.id, lineas, origen, modo)
    return jsonify({
        'proyecto': {'id': proyecto.id, 'numero_proyecto': proyecto.numero_proyecto,
                     'nombre': proyecto.nombre or ''},
        'origen': origen,
        'modo': modo,
        'lineas': plan,
        'resumen': _resumen(plan),
    })


@bp.route('/proyectos-materiales/<int:proyecto_id>/asignar', methods=['POST'])
@_require_inventario_admin
@transaccion_de_stock
def aplicar_asignacion(proyecto_id: int):
    """Aplica la asignación. Todo o nada: una sola transacción.

    Las líneas con error se OMITEN (no abortan el lote); las de aviso se aplican
    ajustadas. Es el mismo resultado que mostró la previsualización porque ambas
    usan `_resolver_lineas`.
    """
    ctx, err = _leer_peticion(proyecto_id)
    if err:
        return err
    proyecto, lineas, origen, modo, data = ctx

    plan = _resolver_lineas(proyecto.id, lineas, origen, modo)
    aplicables = [f for f in plan if f['estado'] != 'error' and f['cantidad_aplicada'] != 0]
    if not aplicables:
        return jsonify({
            'ok': True, 'aplicadas': 0, 'lineas': plan, 'resumen': _resumen(plan),
            'detail': 'No hubo nada que aplicar',
        })

    from app.routes._api_helpers import current_user
    user = current_user()
    motivo = (data.get('motivo') or '').strip() or None

    for f in aplicables:
        delta = _dec(f['cantidad_aplicada'])
        pid, aid = f['producto_id'], f['almacen_id']
        producto = db.session.get(Producto, pid)

        if delta > 0:
            if origen == 'general':
                # Mover de General al proyecto: REASIGNACION.
                _consumir_o_abortar(pid, aid, None, delta, f['sku'])
                _depositar(pid, aid, proyecto.id, delta)
                tipo, p_org, p_dst = 'REASIGNACION', None, proyecto.id
            else:
                # Material que llega de fuera directo al proyecto: ENTRADA.
                _depositar(pid, aid, proyecto.id, delta)
                tipo, p_org, p_dst = 'ENTRADA', None, proyecto.id
        else:
            # Sobrante en modo reemplazar: vuelve a General.
            _consumir_o_abortar(pid, aid, proyecto.id, -delta, f['sku'])
            _depositar(pid, aid, None, -delta)
            tipo, p_org, p_dst = 'REASIGNACION', proyecto.id, None

        _recalcular_caches(producto, aid)
        db.session.add(MovimientoInventario(
            tipo=tipo, producto_id=pid, cantidad=abs(delta),
            almacen_origen_id=aid if tipo == 'REASIGNACION' else None,
            almacen_destino_id=aid,
            proyecto_origen_id=p_org, proyecto_destino_id=p_dst,
            motivo=motivo or f'Asignación a {proyecto.numero_proyecto}',
            usuario_id=user.id,
        ))

    db.session.commit()

    _audit(user, (
        f'Asignó {len(aplicables)} materiales al proyecto '
        f'{proyecto.numero_proyecto} (origen: {origen}, modo: {modo})'
    ))
    db.session.commit()
    emit_to_role(_INV_ROLES, 'producto:changed', {'action': 'asignacion_proyecto',
                                                  'proyecto_id': proyecto.id})

    return jsonify({
        'ok': True,
        'aplicadas': len(aplicables),
        'lineas': plan,
        'resumen': _resumen(plan),
    })


@bp.route('/proyectos-materiales/<int:proyecto_id>/devolver', methods=['POST'])
@_require_inventario_admin
@transaccion_de_stock
def devolver_material(proyecto_id: int):
    """Saca material del proyecto: a General o a otro proyecto.

    `destino_proyecto_id` ausente o null = General. Siempre es una REASIGNACION,
    nunca una salida: el material no se va del almacén, solo cambia de etiqueta.
    """
    proyecto = db.session.get(Proyecto, proyecto_id)
    if not proyecto:
        return jsonify({'detail': 'Proyecto no encontrado'}), 404

    data = request.get_json(silent=True) or {}
    lineas = data.get('lineas')
    if not isinstance(lineas, list) or not lineas:
        return jsonify({'detail': 'Se requiere al menos una línea'}), 422

    destino_id = data.get('destino_proyecto_id')
    if destino_id:
        destino = db.session.get(Proyecto, destino_id)
        if not destino:
            return jsonify({'detail': 'Proyecto destino no encontrado'}), 404
        if destino.id == proyecto.id:
            return jsonify({'detail': 'El destino debe ser distinto del origen'}), 422
        etiqueta_destino = destino.numero_proyecto
    else:
        destino_id, etiqueta_destino = None, 'General'

    from app.routes._api_helpers import current_user
    user = current_user()
    aplicadas, problemas = 0, []

    for cruda in lineas:
        pid = cruda.get('producto_id')
        aid = cruda.get('almacen_id')
        cantidad = _dec(cruda.get('cantidad'))
        if not pid or not aid or cantidad <= 0:
            problemas.append({'producto_id': pid, 'motivo': 'Línea incompleta'})
            continue
        producto = db.session.get(Producto, pid)
        if not producto:
            problemas.append({'producto_id': pid, 'motivo': 'Material no encontrado'})
            continue

        # Aquí un bucket insuficiente NO aborta todo: se reporta por línea y las
        # demás devoluciones siguen su curso.
        e = _consumir_bucket_exacto(pid, aid, proyecto.id, cantidad)
        if e:
            problemas.append({'producto_id': pid, 'sku': producto.codigo, 'motivo': e})
            continue
        _depositar(pid, aid, destino_id, cantidad)
        _recalcular_caches(producto, aid)
        db.session.add(MovimientoInventario(
            tipo='REASIGNACION', producto_id=pid, cantidad=cantidad,
            almacen_origen_id=aid, almacen_destino_id=aid,
            proyecto_origen_id=proyecto.id, proyecto_destino_id=destino_id,
            motivo=(data.get('motivo') or '').strip()
                   or f'Devolución de {proyecto.numero_proyecto} a {etiqueta_destino}',
            usuario_id=user.id,
        ))
        aplicadas += 1

    db.session.commit()

    _audit(user, (
        f'Devolvió {aplicadas} materiales de {proyecto.numero_proyecto} '
        f'a {etiqueta_destino}'
    ))
    db.session.commit()
    emit_to_role(_INV_ROLES, 'producto:changed', {'action': 'devolucion_proyecto',
                                                  'proyecto_id': proyecto.id})

    return jsonify({'ok': True, 'aplicadas': aplicadas, 'problemas': problemas,
                    'destino': etiqueta_destino})
