"""Registro de movimientos de inventario: listado, alta y atajo de PWA.

`_perform_movimiento` es la lógica central que comparten la API y el cierre de
una toma física, y es el ÚNICO camino por el que se altera stock desde aquí.
"""
import datetime
from decimal import Decimal

from flask import jsonify, request, current_app
from sqlalchemy.orm import joinedload

from app.extensions import db, limiter, get_real_client_ip_flask
from app.models import (
    Estante, MovimientoInventario, NotificacionUmbral, Producto, Proyecto,
    crear_notif_inventario,
)
from app.realtime import emit_to_role

from .._core import (
    bp,
    _es_error_de_lock, respuesta_lock, MENSAJE_LOCK,
    _require_inventario_admin,
    _parse_or_422, _int_arg,
    MovimientoCreateSchema, MovimientoLoteSchema,
    _movimiento_to_dict,
    _audit,
    _almacen_default_id,
    _depositar, _consumir_proyecto_luego_general, _consumir_bucket_exacto,
    _consumir_reconciliando, _recalcular_caches,
    _resolver_partes,
    _INV_ROLES,
)


@bp.route('/movimientos/', methods=['GET'])
@_require_inventario_admin
def get_movimientos():
    producto_id = request.args.get('producto_id', type=int)
    tipo = request.args.get('tipo', type=str)
    limit, err = _int_arg('limit', 200, 0, 1000)
    if err: return err

    q = MovimientoInventario.query.options(joinedload(MovimientoInventario.producto))
    if producto_id:
        q = q.filter(MovimientoInventario.producto_id == producto_id)
    if tipo:
        if len(tipo) > 20:
            return jsonify({'detail': "Parámetro 'tipo' demasiado largo"}), 422
        q = q.filter(MovimientoInventario.tipo == tipo.upper())

    # Rango de fechas (YYYY-MM-DD). Server-side a propósito: el filtro client-side
    # solo veía los últimos `limit` movimientos; con rango el usuario alcanza
    # movimientos viejos sin levantar el tope. `hasta` es inclusivo (día completo).
    def _parse_dia(nombre):
        v = (request.args.get(nombre) or '').strip()
        if not v:
            return None
        try:
            return datetime.datetime.strptime(v, '%Y-%m-%d').date()
        except ValueError:
            return 'ERR'
    desde = _parse_dia('desde')
    hasta = _parse_dia('hasta')
    if desde == 'ERR' or hasta == 'ERR':
        return jsonify({'detail': "Fecha inválida: usa formato YYYY-MM-DD"}), 422
    if desde:
        q = q.filter(MovimientoInventario.fecha >= datetime.datetime.combine(desde, datetime.time.min))
    if hasta:
        q = q.filter(MovimientoInventario.fecha < datetime.datetime.combine(hasta, datetime.time.min) + datetime.timedelta(days=1))

    movs = q.order_by(MovimientoInventario.fecha.desc()).limit(limit).all()
    return jsonify([_movimiento_to_dict(m) for m in movs])


@bp.route('/movimientos/', methods=['POST'])
@limiter.limit(
    "20/minute",
    # key por IP real para que requests sin sesión también cuenten al contador.
    # Si pusiéramos @_require_inventario antes, los 401 no incrementarían el contador
    # y un atacante anónimo podría martillear el endpoint sin freno hasta el límite global.
    key_func=lambda: f"ip:{get_real_client_ip_flask()}",
)
@_require_inventario_admin
def create_movimiento():
    """Crea un movimiento de inventario alterando StockPorAlmacen (Pausa 2).

    Reglas de almacén por tipo:
      - ENTRADA: requiere almacen_destino (donde llega el stock).
      - SALIDA:  requiere almacen_origen (de donde sale).
      - AJUSTE:  usa almacen_destino si cantidad>0, almacen_origen si <0.
      - TRASPASO: requiere ambos y deben ser distintos.

    Compatibilidad con clientes viejos: si no se manda ni almacén ni estante,
    se infiere de la bodega activa de menor id (no rompemos integraciones que
    aún tratan al stock como global). Loguea warning para detectar quién sigue
    sin mandar almacén.
    """
    data, err = _parse_or_422(MovimientoCreateSchema(), request.get_json(silent=True))
    if err: return err
    return _perform_movimiento(data, request.current_user)


def _perform_movimiento(data: dict, user, autocommit: bool = True):
    """Lógica central de creación de movimiento — usada por POST /movimientos/
    y POST /movimientos/rapido. `data` ya viene validado por
    MovimientoCreateSchema.

    Consciente del bucket de PROYECTO (feature stock por proyecto): cada tipo
    deposita/consume en `stock_almacen_proyecto` (proyecto|general) y luego
    recalcula los caches `stock_por_almacen` y `Producto.stock_actual` vía
    `_recalcular_caches`. La regla de consumo es proyecto→general para
    SALIDA/AJUSTE−, y bucket exacto para TRASPASO/REASIGNACION.

    `autocommit=False` deja el control de la transacción al llamador: no hace
    commit, ni rollback, ni emite el evento — solo `flush()` para que el
    movimiento obtenga id. Lo usa `tomas.cerrar_toma`, que necesita aplicar N
    ajustes de forma ATÓMICA (todos o ninguno) dentro de una sola transacción.
    Sin esto, cada ajuste se commiteaba por separado y un fallo a medias dejaba
    la toma ABIERTA con stock ya movido — al reintentar se volvía a aplicar."""
    tipo = data['tipo']

    def _rollback():
        """Solo revierte si somos dueños de la transacción. Con autocommit=False
        el caller la controla (savepoints), y un rollback aquí se llevaría por
        delante el trabajo de las demás líneas."""
        if autocommit:
            db.session.rollback()

    cantidad_raw = data['cantidad']
    proyecto_id = data.get('proyecto_id')
    proyecto_origen_id = data.get('proyecto_origen_id')
    proyecto_destino_id = data.get('proyecto_destino_id')

    # Partes del comprobante (quién entrega / quién recibe). Opcionales; se validan antes
    # de tocar stock para fallar limpio con 422 sin dejar la transacción a medias.
    partes, err_partes = _resolver_partes(data)
    if err_partes:
        return err_partes

    # Un movimiento de 0 no es un movimiento: no altera stock y solo deja un
    # renglón vacío en el kardex. En AJUSTE además pasaba de largo —es el único
    # tipo que admite negativos, así que el guard de abajo no lo veía— y llegaba
    # a `_depositar`, que crea el bucket en 0 al bloquearlo. Se rechaza aquí, el
    # único punto por el que pasan TODOS los caminos (individual, lote, rápido y
    # el cierre de tomas), para que ninguno acepte lo que otro rechaza.
    if cantidad_raw == 0:
        return jsonify({'detail': 'La cantidad no puede ser 0'}), 422

    # ENTRADA/SALIDA/TRASPASO/REASIGNACION requieren cantidad estrictamente
    # positiva. AJUSTE permite negativo (mermas) — lo controla la lógica de stock.
    if tipo in ['ENTRADA', 'SALIDA', 'TRASPASO', 'REASIGNACION'] and cantidad_raw <= 0:
        return jsonify({'detail': 'La cantidad debe ser positiva para este tipo de movimiento'}), 422
    if tipo not in ('ENTRADA', 'SALIDA', 'AJUSTE', 'TRASPASO', 'REASIGNACION'):
        return jsonify({'detail': 'Tipo de movimiento inválido'}), 400

    # Validar existencia de proyectos referenciados (422 legible antes del FK).
    proy_ids = {p for p in (proyecto_id, proyecto_origen_id, proyecto_destino_id) if p}
    if proy_ids:
        encontrados = {p.id for p in Proyecto.query.filter(Proyecto.id.in_(proy_ids)).all()}
        faltan = proy_ids - encontrados
        if faltan:
            return jsonify({'detail': f'Proyecto(s) inexistente(s): {sorted(faltan)}'}), 422

    producto = Producto.query.filter(Producto.id == data['producto_id']).first()
    if not producto:
        return jsonify({'detail': 'Producto no encontrado'}), 404
    cantidad_decimal = Decimal(str(cantidad_raw))

    # ── REASIGNACION: mover un bucket de proyecto a otro DENTRO del mismo
    # almacén. No cambia el total del almacén ni el global, solo el desglose. ──
    if tipo == 'REASIGNACION':
        almacen_id = data.get('almacen_origen_id') or data.get('almacen_destino_id')
        estante_id = data.get('estante_id')
        if not almacen_id and estante_id:
            est = Estante.query.filter(Estante.id == estante_id).first()
            almacen_id = est.almacen_id if est else None
        if not almacen_id:
            almacen_id = _almacen_default_id()
        if not almacen_id:
            return jsonify({'detail': 'La reasignación requiere un almacén'}), 422
        if (proyecto_origen_id or None) == (proyecto_destino_id or None):
            return jsonify({'detail': 'La reasignación requiere proyecto origen y destino distintos'}), 422
        try:
            err = _consumir_bucket_exacto(producto.id, almacen_id, proyecto_origen_id, cantidad_decimal)
            if err:
                _rollback()
                return jsonify({'detail': f'No se puede reasignar: {err}'}), 409
            _depositar(producto.id, almacen_id, proyecto_destino_id, cantidad_decimal)
            _recalcular_caches(producto, almacen_id)
        except Exception as exc:
            _rollback()
            if _es_error_de_lock(exc):
                return respuesta_lock()
            raise
        nuevo_mov = MovimientoInventario(
            tipo='REASIGNACION', producto_id=producto.id, cantidad=cantidad_decimal,
            almacen_origen_id=almacen_id, almacen_destino_id=almacen_id,
            proyecto_origen_id=proyecto_origen_id, proyecto_destino_id=proyecto_destino_id,
            motivo=data.get('motivo'), usuario_id=user.id,
            **partes,
        )
        db.session.add(nuevo_mov)
        _audit(user, (
            f"REASIGNACION producto #{producto.id} {cantidad_raw} "
            f"proy {proyecto_origen_id or 'general'}→{proyecto_destino_id or 'general'} "
            f"(almacén #{almacen_id})"
        ))
        if autocommit:
            db.session.commit()
            db.session.refresh(nuevo_mov)
            emit_to_role(_INV_ROLES, 'movimiento:changed', {
                'id': nuevo_mov.id, 'producto_id': producto.id, 'tipo': 'REASIGNACION',
            })
        else:
            db.session.flush()  # el caller commitea; flush da id al movimiento
        return jsonify(_movimiento_to_dict(nuevo_mov))

    # 1) Resolver almacenes (con inferencia desde estante si solo vino estante_id).
    almacen_destino_id = data.get('almacen_destino_id')
    almacen_origen_id = data.get('almacen_origen_id')
    estante_id = data.get('estante_id')
    if estante_id and (not almacen_destino_id and not almacen_origen_id):
        estante = Estante.query.filter(Estante.id == estante_id).first()
        if estante:
            if tipo in ('ENTRADA',) or (tipo == 'AJUSTE' and cantidad_raw > 0):
                almacen_destino_id = estante.almacen_id
            else:
                almacen_origen_id = estante.almacen_id

    # Fallback compat: si todavía no hay almacén, usar la bodega default.
    if not (almacen_origen_id or almacen_destino_id):
        default_id = _almacen_default_id()
        if not default_id:
            return jsonify({'detail': 'No hay bodegas registradas. Crea un almacén antes de mover stock.'}), 400
        current_app.logger.warning(
            "Movimiento %s sin almacén explícito; usando bodega default #%s (producto=%s)",
            tipo, default_id, data['producto_id'],
        )
        if tipo == 'ENTRADA' or (tipo == 'AJUSTE' and cantidad_raw > 0):
            almacen_destino_id = default_id
        else:
            almacen_origen_id = default_id

    # 2) Validar combinación tipo/almacén.
    if tipo == 'ENTRADA' and not almacen_destino_id:
        return jsonify({'detail': 'ENTRADA requiere almacen_destino_id'}), 422
    if tipo == 'SALIDA' and not almacen_origen_id:
        return jsonify({'detail': 'SALIDA requiere almacen_origen_id'}), 422
    if tipo == 'TRASPASO':
        if not (almacen_origen_id and almacen_destino_id):
            return jsonify({'detail': 'TRASPASO requiere almacen_origen_id y almacen_destino_id'}), 422
        if almacen_origen_id == almacen_destino_id:
            return jsonify({'detail': 'TRASPASO requiere bodegas distintas'}), 422

    # Capturar stock previo para detectar cruce del umbral mínimo (Pausa 5).
    # TRASPASO no altera el total → no puede cruzar.
    stock_antes = Decimal(str(producto.stock_actual or 0))
    stock_minimo = Decimal(str(producto.stock_minimo or 0))

    # Buckets de proyecto que quedarán registrados en el movimiento, y almacenes
    # cuyos caches hay que recalcular.
    mov_proy_origen = None
    mov_proy_destino = None
    almacenes_tocados: list[int] = []

    try:
        # 4) Lock + alteración de buckets de stock_almacen_proyecto (fuente de
        # verdad). with_for_update (en los helpers) previene over-selling.
        if tipo == 'ENTRADA':
            _depositar(producto.id, almacen_destino_id, proyecto_id, cantidad_decimal)
            mov_proy_destino = proyecto_id
            almacenes_tocados = [almacen_destino_id]

        elif tipo == 'SALIDA':
            # 1º) Físico insuficiente en el bucket del almacén → 400 (igual que antes).
            err = _consumir_proyecto_luego_general(producto.id, almacen_origen_id, proyecto_id, cantidad_decimal)
            if err:
                _rollback()
                return jsonify({'detail': f'Stock insuficiente en bodega #{almacen_origen_id}: {err}'}), 400
            # 2º) Guard global de reservas (no sacar lo apartado) → 409. Usa
            # stock_antes (total previo), por eso el orden no altera el cálculo.
            reservado = Decimal(str(producto.stock_reservado or 0))
            disponible_global = stock_antes - reservado
            if disponible_global < cantidad_decimal:
                _rollback()
                return jsonify({
                    'detail': (
                        f'Hay {reservado} {producto.unidad} apartados por solicitudes aprobadas. '
                        f'Disponible para sacar: {disponible_global}. '
                        f'Libera/rechaza solicitudes antes o entrega vía el flujo de solicitudes.'
                    ),
                }), 409
            mov_proy_origen = proyecto_id
            almacenes_tocados = [almacen_origen_id]

        elif tipo == 'AJUSTE':
            # Positivo → sube destino; negativo → baja origen (sin pasar de 0).
            if cantidad_decimal >= 0:
                _depositar(producto.id, almacen_destino_id, proyecto_id, cantidad_decimal)
                mov_proy_destino = proyecto_id
                almacenes_tocados = [almacen_destino_id]
            else:
                # 1º) Físico: el bucket no puede quedar negativo → 400 (como antes).
                # `reconciliar` (solo lo pasa el cierre de TOMA física, no la API
                # pública: el schema lo excluye) toma de cualquier bucket para
                # cuadrar el conteo a nivel almacén.
                if data.get('reconciliar'):
                    err = _consumir_reconciliando(producto.id, almacen_origen_id, -cantidad_decimal)
                else:
                    err = _consumir_proyecto_luego_general(producto.id, almacen_origen_id, proyecto_id, -cantidad_decimal)
                if err:
                    _rollback()
                    return jsonify({'detail': f'Ajuste provocaría stock negativo en bodega #{almacen_origen_id}: {err}'}), 400
                # 2º) Guard global de reservas (no invadir lo apartado) → 409.
                reservado = Decimal(str(producto.stock_reservado or 0))
                disponible_global_post = stock_antes + cantidad_decimal - reservado
                if disponible_global_post < 0:
                    _rollback()
                    return jsonify({
                        'detail': (
                            f'Ajuste invadiría stock apartado. Reservado: {reservado}, '
                            f'stock tras ajuste: {stock_antes + cantidad_decimal}.'
                        ),
                    }), 409
                mov_proy_origen = proyecto_id
                almacenes_tocados = [almacen_origen_id]

        elif tipo == 'TRASPASO':
            # Mueve el bucket `proyecto_id` (exacto, sin fallback) de un almacén
            # a otro conservando la etiqueta de proyecto.
            err = _consumir_bucket_exacto(producto.id, almacen_origen_id, proyecto_id, cantidad_decimal)
            if err:
                _rollback()
                return jsonify({'detail': f'Stock insuficiente para traspaso en bodega #{almacen_origen_id}: {err}'}), 400
            _depositar(producto.id, almacen_destino_id, proyecto_id, cantidad_decimal)
            mov_proy_origen = proyecto_id
            mov_proy_destino = proyecto_id
            almacenes_tocados = [almacen_origen_id, almacen_destino_id]

    except Exception as exc:
        _rollback()
        # nowait=True levanta si la fila ya estaba bloqueada por otra transacción.
        # Devolvemos 409 para que el cliente reintente.
        if _es_error_de_lock(exc):
            return respuesta_lock()
        raise

    # 5) Recalcular caches (stock_por_almacen + Producto.stock_actual) de los
    # almacenes tocados, DENTRO de la misma transacción.
    for alm in {a for a in almacenes_tocados if a}:
        _recalcular_caches(producto, alm)

    # 5b) Pausa 5: si este movimiento CRUZÓ el umbral mínimo, notificar a
    # inventario. Solo notifica al cruzar (de OK a bajo mínimo); movimientos
    # adicionales bajo mínimo no spamean. Idempotencia diaria con tabla
    # NotificacionUmbral. Try/except: la notif no debe romper el movimiento.
    stock_despues = Decimal(str(producto.stock_actual or 0))
    cruzo_umbral = (
        stock_minimo > 0
        and stock_antes > stock_minimo
        and stock_despues <= stock_minimo
    )
    if cruzo_umbral:
        try:
            hoy = datetime.date.today()
            ya_notificado = db.session.get(NotificacionUmbral, (producto.id, hoy))
            if not ya_notificado:
                db.session.add(NotificacionUmbral(producto_id=producto.id, fecha=hoy))
                crear_notif_inventario(
                    tipo='STOCK_BAJO',
                    titulo=f'Stock bajo: {producto.codigo}',
                    mensaje=(
                        f'{producto.descripcion} quedó en {stock_despues} {producto.unidad} '
                        f'(mínimo: {stock_minimo}).'
                    ),
                    url='/inventario/bajo-minimo',
                )
        except Exception:
            current_app.logger.warning("No se pudo crear notificación STOCK_BAJO", exc_info=True)

    # 6) Registrar el movimiento histórico.
    nuevo_mov = MovimientoInventario(
        tipo=tipo,
        producto_id=data['producto_id'],
        cantidad=cantidad_decimal,
        almacen_origen_id=almacen_origen_id,
        almacen_destino_id=almacen_destino_id,
        proyecto_origen_id=mov_proy_origen,
        proyecto_destino_id=mov_proy_destino,
        motivo=data.get('motivo') or (f"Estante #{estante_id}" if estante_id else None),
        usuario_id=user.id,
        **partes,
    )
    db.session.add(nuevo_mov)
    _audit(user, f"Movimiento {tipo} — producto #{data['producto_id']} — cantidad: {cantidad_raw}")
    if autocommit:
        db.session.commit()
        db.session.refresh(nuevo_mov)
        emit_to_role(_INV_ROLES, 'movimiento:changed', {
            'id': nuevo_mov.id, 'producto_id': nuevo_mov.producto_id, 'tipo': tipo,
        })
    else:
        db.session.flush()  # el caller commitea; flush da id al movimiento
    return jsonify(_movimiento_to_dict(nuevo_mov))


@bp.route('/movimientos/lote', methods=['POST'])
@limiter.limit(
    # Una tanda = UNA petición, así que el límite se cuenta por tanda y no por
    # línea: mover 30 productos ya no consume 30 del cupo de /movimientos/.
    "10/minute",
    key_func=lambda: f"ip:{get_real_client_ip_flask()}",
)
@_require_inventario_admin
def create_movimientos_lote():
    """Registra N movimientos del mismo tipo/bodega/proyecto de forma ATÓMICA.

    Todos o ninguno. Cada línea va en su savepoint —igual que el cierre de una
    toma física— para poder recolectar TODOS los errores y decirle al usuario
    qué productos fallaron, en vez de morir en el primero; si al final hubo
    alguno, el rollback deshace también las líneas que sí habían pasado.

    Sin esto, el cliente tenía que mandar N peticiones: un fallo a media lista
    dejaba stock movido sin forma de saber dónde se quedó, y reintentar volvía
    a aplicar lo que ya se había aplicado.
    """
    data, err = _parse_or_422(MovimientoLoteSchema(), request.get_json(silent=True))
    if err: return err
    items = data.pop('items')

    # Un mismo producto dos veces en la tanda casi siempre es un cliente con un
    # bug (una lista sin deduplicar), y aplicarlo dos veces es justo el error
    # que más caro sale: doble salida silenciosa.
    ids = [it['producto_id'] for it in items]
    duplicados = sorted({i for i in ids if ids.count(i) > 1})
    if duplicados:
        return jsonify({
            'detail': f'Productos repetidos en el lote: {duplicados}. Manda una sola línea por producto.',
        }), 422

    user = request.current_user
    creados = []
    errores = []
    for it in items:
        linea = {**data, 'producto_id': it['producto_id'], 'cantidad': it['cantidad']}
        punto = db.session.begin_nested()
        try:
            resp = _perform_movimiento(linea, user, autocommit=False)
        except Exception as exc:
            punto.rollback()
            # Un lock ocupado es un fallo esperado: se reporta como línea con
            # problema y la tanda sigue recolectando el resto de errores.
            if _es_error_de_lock(exc):
                errores.append({'producto_id': it['producto_id'], 'detail': MENSAJE_LOCK})
                continue
            # Cualquier otra excepción es un bug, no un rechazo de negocio: los
            # rechazos ya vienen por la rama de tuplas de abajo. Devolver
            # `str(exc)` filtraba el mensaje crudo del driver (SQL, nombres de
            # tabla y constraint) y disfrazaba un 500 de 409, que es justo lo
            # que `transaccion_de_stock` evita en el resto del módulo.
            db.session.rollback()
            raise
        # `_perform_movimiento` devuelve la Response a secas si todo fue bien, y
        # una tupla (response, status) cuando rechazó la línea.
        if isinstance(resp, tuple):
            body_resp, status = resp
            if status >= 400:
                punto.rollback()
                errores.append({
                    'producto_id': it['producto_id'],
                    'detail': (body_resp.get_json() or {}).get('detail', 'Movimiento rechazado'),
                })
                continue
            resp = body_resp
        punto.commit()   # libera el savepoint; sigue dentro de la transacción
        creados.append(resp.get_json())

    if errores:
        db.session.rollback()   # ahora sí deshace: nada se había commiteado
        return jsonify({
            'detail': f'No se registró ningún movimiento: {len(errores)} línea(s) con problema',
            'errores': errores,
        }), 409

    _audit(user, f"Lote {data['tipo']} — {len(creados)} movimientos")
    db.session.commit()   # único commit: las N líneas, atómicas
    # Las líneas no emitieron (autocommit=False), así que se avisa una vez aquí.
    emit_to_role(_INV_ROLES, 'movimiento:changed', {
        'origen': 'lote', 'tipo': data['tipo'], 'total': len(creados),
    })
    return jsonify({
        'movimientos': creados,
        'ids': [m['id'] for m in creados],
        'total': len(creados),
    })


@bp.route('/movimientos/rapido', methods=['POST'])
@limiter.limit(
    "30/minute",
    key_func=lambda: f"ip:{get_real_client_ip_flask()}",
)
@_require_inventario_admin
def create_movimiento_rapido():
    """Atajo de PWA: registra movimiento resolviendo producto por su código/QR
    y almacén por estante_qr (Pausa 4).

    Body: `{producto_qr: str, estante_qr?: str, tipo: ENTRADA|SALIDA|AJUSTE,
            cantidad: number, motivo?: str}`.

    - `producto_qr` se resuelve contra `Producto.codigo`.
    - `estante_qr` se resuelve contra `Estante.qr_code` para inferir el almacén.
      Si no se manda, se usa el almacén default.
    - Para AJUSTE acepta cantidades negativas.

    Devuelve el mismo shape que POST /movimientos/.
    """
    body = request.get_json(silent=True) or {}
    producto_qr = (body.get('producto_qr') or '').strip()
    estante_qr = (body.get('estante_qr') or '').strip()
    tipo = (body.get('tipo') or '').strip().upper()
    cantidad_raw = body.get('cantidad')
    motivo = (body.get('motivo') or 'Movimiento rápido desde PWA').strip()[:250]

    if not producto_qr:
        return jsonify({'detail': 'producto_qr es requerido'}), 422
    if tipo not in ('ENTRADA', 'SALIDA', 'AJUSTE'):
        return jsonify({'detail': "tipo debe ser ENTRADA, SALIDA o AJUSTE"}), 422

    producto = Producto.query.filter(Producto.codigo == producto_qr, Producto.activo == True).first()
    if not producto:
        return jsonify({'detail': f'Producto con código {producto_qr} no encontrado'}), 404

    almacen_id = None
    if estante_qr:
        est = Estante.query.filter(Estante.qr_code == estante_qr, Estante.activo == True).first()
        if not est:
            return jsonify({'detail': f'Estante {estante_qr} no encontrado'}), 404
        almacen_id = est.almacen_id
    else:
        almacen_id = _almacen_default_id()
        if not almacen_id:
            return jsonify({'detail': 'No hay almacén default configurado'}), 422

    try:
        cant_decimal = Decimal(str(cantidad_raw))
    except Exception:
        return jsonify({'detail': 'cantidad inválida'}), 422

    data = {
        'tipo': tipo,
        'producto_id': producto.id,
        'cantidad': cant_decimal,
        'motivo': motivo,
    }
    if tipo == 'ENTRADA' or (tipo == 'AJUSTE' and cant_decimal >= 0):
        data['almacen_destino_id'] = almacen_id
    else:
        data['almacen_origen_id'] = almacen_id

    return _perform_movimiento(data, request.current_user)
