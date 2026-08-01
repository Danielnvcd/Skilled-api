"""Historial de importaciones y DESHACER una importación completa.

Una carga masiva toca cientos de productos de una vez: si el archivo venía mal,
corregirlo a mano no es opción. `importar.py` deja registrado cada lote con los
valores que tenía cada campo ANTES; aquí se usan para revertirlo.

La regla que hace esto seguro es una sola: **solo se revierte lo que sigue tal
como lo dejó la importación**. Si alguien editó ese campo después, o ya se movió
el stock del producto nuevo, esa parte se respeta y se reporta. Deshacer nunca
debe pisar trabajo posterior.
"""
import datetime
import json

from flask import jsonify, request
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import (
    ImportacionCatalogo, ImportacionCatalogoCambio, MovimientoInventario,
    Producto, ProductoEstante, StockAlmacenProyecto, StockPorAlmacen,
)
from app.realtime import emit_to_role

from .._core import (
    bp, _require_inventario_admin, transaccion_de_stock,
    _audit, _INV_ROLES, _int_arg,
)
from .importar import _mismo_valor, _valor_restaurado


def _borrar_en_bloque(producto_ids: list[int]):
    """Borra productos y lo que cuelga de ellos con sentencias en bloque.

    Se hace a mano, sin `session.delete()`, porque el ORM cargaría las
    colecciones de cada producto (stock, estantes, movimientos) para aplicar sus
    cascadas: una consulta por colección y por producto. Aquí solo se llega con
    altas recién importadas y sin movimientos, así que basta con vaciar sus
    tablas hijas conocidas; cualquier otra referencia hace saltar el
    IntegrityError que el llamador usa para caer al camino producto por producto.
    """
    filtro = {'synchronize_session': False}
    StockAlmacenProyecto.query.filter(
        StockAlmacenProyecto.producto_id.in_(producto_ids)).delete(**filtro)
    StockPorAlmacen.query.filter(
        StockPorAlmacen.producto_id.in_(producto_ids)).delete(**filtro)
    ProductoEstante.query.filter(
        ProductoEstante.producto_id.in_(producto_ids)).delete(**filtro)
    # El registro de importación sobrevive al producto: se queda con el código
    # como testimonio, pero sin apuntar a una fila que ya no existe.
    ImportacionCatalogoCambio.query.filter(
        ImportacionCatalogoCambio.producto_id.in_(producto_ids)
    ).update({'producto_id': None}, **filtro)
    Producto.query.filter(Producto.id.in_(producto_ids)).delete(**filtro)
    db.session.flush()


def _lote_to_dict(lote: ImportacionCatalogo) -> dict:
    return {
        'id': lote.id,
        'fecha': lote.fecha.isoformat() if lote.fecha else None,
        'archivo': lote.archivo or '',
        'usuario': getattr(lote.usuario, 'username', None),
        'creados': lote.creados,
        'actualizados': lote.actualizados,
        'sin_cambios': lote.sin_cambios,
        'errores': lote.errores,
        'estado': lote.estado,
        'revertida_at': lote.revertida_at.isoformat() if lote.revertida_at else None,
        'revertida_por': getattr(lote.revertida_por, 'username', None),
        'revertida_notas': lote.revertida_notas or '',
        'puede_deshacerse': lote.estado == 'APLICADA' and (lote.creados + lote.actualizados) > 0,
    }


@bp.route('/productos/importaciones', methods=['GET'])
@_require_inventario_admin
def listar_importaciones():
    """Últimas importaciones, para ver qué se cargó y poder deshacerlo."""
    limit, err = _int_arg('limit', 20, 1, 100)
    if err: return err
    # joinedload: sin esto cada lote dispara dos consultas más (quién importó y
    # quién revirtió) solo para pintar dos nombres.
    from sqlalchemy.orm import joinedload
    lotes = (
        ImportacionCatalogo.query
        .options(joinedload(ImportacionCatalogo.usuario),
                 joinedload(ImportacionCatalogo.revertida_por))
        .order_by(ImportacionCatalogo.fecha.desc(), ImportacionCatalogo.id.desc())
        .limit(limit)
        .all()
    )
    return jsonify([_lote_to_dict(l) for l in lotes])


@bp.route('/productos/importaciones/<int:importacion_id>', methods=['GET'])
@_require_inventario_admin
def detalle_importacion(importacion_id: int):
    """Detalle: qué productos se crearon y qué cambió en los actualizados."""
    lote = db.session.get(ImportacionCatalogo, importacion_id)
    if not lote:
        return jsonify({'detail': 'Importación no encontrada'}), 404

    creados, actualizados = [], []
    for c in lote.cambios.limit(1000).all():
        if c.accion == 'CREADO':
            creados.append({'codigo': c.codigo, 'producto_id': c.producto_id,
                            'stock_inicial': float(c.stock_inicial or 0)})
        else:
            antes = json.loads(c.antes) if c.antes else {}
            despues = json.loads(c.despues) if c.despues else {}
            actualizados.append({
                'codigo': c.codigo, 'producto_id': c.producto_id,
                'campos': [
                    {'campo': k, 'antes': antes.get(k), 'despues': despues.get(k)}
                    for k in despues
                ],
            })
    data = _lote_to_dict(lote)
    data['detalle'] = {'creados': creados, 'actualizados': actualizados}
    return jsonify(data)


@bp.route('/productos/importaciones/<int:importacion_id>/deshacer', methods=['POST'])
@_require_inventario_admin
@transaccion_de_stock
def deshacer_importacion(importacion_id: int):
    """Revierte una importación aplicada.

    - Producto ACTUALIZADO: cada campo vuelve a su valor anterior **solo si hoy
      sigue teniendo el valor que puso la importación**. Si alguien lo editó
      después, se deja como está y se reporta.
    - Producto CREADO: se elimina si sigue siendo "recién nacido" — sin
      movimientos y con el stock exactamente donde lo dejó la importación. Si ya
      se usó, no se borra: se desactiva (baja lógica) para no perder su
      histórico, y también se reporta.

    Nunca falla a medias: todo ocurre en una transacción.
    """
    # Se toma el lote con candado: dos clics en "Deshacer" a la vez pasarían los
    # dos la comprobación de estado y se revertiría dos veces.
    lote = (
        ImportacionCatalogo.query
        .filter(ImportacionCatalogo.id == importacion_id)
        .with_for_update()
        .first()
    )
    if not lote:
        return jsonify({'detail': 'Importación no encontrada'}), 404
    if lote.estado != 'APLICADA':
        return jsonify({'detail': 'Esta importación ya fue revertida'}), 400

    user = request.current_user
    restaurados = 0        # productos que volvieron a su estado anterior
    campos_omitidos = 0    # campos que alguien editó después de importar
    eliminados = 0         # altas que se borraron por completo
    desactivados = 0       # altas que ya se usaron → baja lógica
    notas = []
    borrables = []         # altas intactas: se borran todas juntas al final

    cambios = lote.cambios.all()

    # Precarga en lotes: deshacer una carga de miles de productos haría un SELECT
    # por producto (y otro por cada alta, para ver si tuvo movimientos). Es el
    # mismo N+1 que se quitó del importador; aquí se evita igual.
    ids = [c.producto_id for c in cambios if c.producto_id]
    productos_por_id: dict[int, Producto] = {}
    con_movimientos: set[int] = set()
    LOTE_IDS = 900
    for i in range(0, len(ids), LOTE_IDS):
        trozo = ids[i:i + LOTE_IDS]
        for p in Producto.query.filter(Producto.id.in_(trozo)).all():
            productos_por_id[p.id] = p
        con_movimientos.update(
            pid for (pid,) in db.session.query(MovimientoInventario.producto_id)
            .filter(MovimientoInventario.producto_id.in_(trozo))
            .distinct()
            .all()
        )

    for c in cambios:
        prod = productos_por_id.get(c.producto_id) if c.producto_id else None
        if prod is None:
            notas.append(f'{c.codigo}: ya no existe, se omitió')
            continue

        if c.accion == 'ACTUALIZADO':
            antes = json.loads(c.antes) if c.antes else {}
            despues = json.loads(c.despues) if c.despues else {}
            algo = False
            for campo, valor_importado in despues.items():
                actual = getattr(prod, campo, None)
                # ¿Sigue como lo dejó la importación? Si no, alguien lo cambió
                # después y su trabajo manda.
                if not _mismo_valor(actual, _valor_restaurado(campo, valor_importado)):
                    campos_omitidos += 1
                    continue
                setattr(prod, campo, _valor_restaurado(campo, antes.get(campo)))
                algo = True
            if algo:
                restaurados += 1
            continue

        # ── Alta: solo se borra si nadie la tocó ─────────────────────────────
        tiene_movimientos = prod.id in con_movimientos
        stock_esperado = c.stock_inicial or 0
        stock_actual_ok = _mismo_valor(prod.stock_actual, stock_esperado)

        if tiene_movimientos or not stock_actual_ok:
            prod.activo = False
            desactivados += 1
            notas.append(
                f'{c.codigo}: ya tenía movimientos o su stock cambió — se desactivó '
                'en vez de borrarse, para no perder el histórico'
            )
            continue

        borrables.append(prod)

    # ── Borrado de las altas, en bloque ──────────────────────────────────────
    # `session.delete()` por producto haría que el ORM cargue sus colecciones
    # (stock, estantes, movimientos) una por una: deshacer una carga de miles de
    # altas serían decenas de miles de consultas. Se borra con sentencias en
    # bloque dentro de un savepoint; si alguna referencia lo impide (una
    # solicitud, una compra, un estante…), se descarta el intento y se resuelve
    # producto por producto, que es el camino lento pero seguro.
    if borrables:
        ids_borrar = [p.id for p in borrables]
        try:
            with db.session.begin_nested():
                _borrar_en_bloque(ids_borrar)
            eliminados += len(borrables)
            for p in borrables:
                db.session.expunge(p)
        except IntegrityError:
            for prod in borrables:
                try:
                    with db.session.begin_nested():
                        _borrar_en_bloque([prod.id])
                    db.session.expunge(prod)
                    eliminados += 1
                except IntegrityError:
                    prod = db.session.get(Producto, prod.id)
                    if prod is None:
                        continue
                    prod.activo = False
                    desactivados += 1
                    notas.append(
                        f'{prod.codigo}: está referenciado en otros registros — se '
                        'desactivó en vez de borrarse'
                    )

    if campos_omitidos:
        notas.append(
            f'{campos_omitidos} campo(s) se editaron después de la importación y '
            'se dejaron como están'
        )

    lote.estado = 'REVERTIDA'
    lote.revertida_at = datetime.datetime.now(datetime.timezone.utc)
    lote.revertida_por_id = user.id
    lote.revertida_notas = ' · '.join(notas)[:4000] if notas else None

    _audit(user, (f'Deshizo la importación #{lote.id} ({lote.archivo or "sin nombre"}): '
                  f'{restaurados} restaurados, {eliminados} eliminados, '
                  f'{desactivados} desactivados'))
    db.session.commit()

    if restaurados or eliminados or desactivados:
        emit_to_role(_INV_ROLES, 'producto:changed', {
            'action': 'import_undo', 'count': restaurados + eliminados + desactivados,
        })

    return jsonify({
        'importacion_id': lote.id,
        'restaurados': restaurados,
        'eliminados': eliminados,
        'desactivados': desactivados,
        'campos_omitidos': campos_omitidos,
        # Tope en la respuesta: deshacer una carga de miles podría generar una
        # nota por producto y la respuesta pesaría más que el propio trabajo.
        'notas': notas[:100],
        'notas_total': len(notas),
    })
