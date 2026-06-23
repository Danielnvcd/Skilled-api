"""Endpoints de Movimientos de inventario.

Expone `_perform_movimiento` para que el módulo `tomas` lo use al cerrar una
toma física (genera AJUSTES automáticos).
"""
import datetime
from decimal import Decimal

from flask import jsonify, request, current_app
from sqlalchemy.orm import joinedload

from app.extensions import db, limiter, get_real_client_ip_flask
from app.models import (
    Producto, Estante, MovimientoInventario, NotificacionUmbral,
    crear_notif_inventario,
)
from app.realtime import emit_to_role

from ._core import (
    bp,
    _require_inventario_admin,
    _parse_or_422, _int_arg,
    MovimientoCreateSchema,
    _movimiento_to_dict,
    _audit,
    _almacen_default_id, _lock_stock, _recalcular_cache_stock,
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


def _perform_movimiento(data: dict, user):
    """Lógica central de creación de movimiento — usada por POST /movimientos/
    y POST /movimientos/rapido. `data` ya viene validado por
    MovimientoCreateSchema."""
    tipo = data['tipo']
    cantidad_raw = data['cantidad']

    # ENTRADA/SALIDA/TRASPASO requieren cantidad estrictamente positiva.
    # AJUSTE permite negativo (mermas) — eso lo controla la lógica de stock más abajo.
    if tipo in ['ENTRADA', 'SALIDA', 'TRASPASO'] and cantidad_raw <= 0:
        return jsonify({'detail': 'La cantidad debe ser positiva para este tipo de movimiento'}), 422
    if tipo not in ('ENTRADA', 'SALIDA', 'AJUSTE', 'TRASPASO'):
        return jsonify({'detail': 'Tipo de movimiento inválido'}), 400

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

    # 3) Verificar que el producto existe (sin lock todavía; el lock va sobre
    # StockPorAlmacen, que es la fuente de verdad).
    producto = Producto.query.filter(Producto.id == data['producto_id']).first()
    if not producto:
        return jsonify({'detail': 'Producto no encontrado'}), 404

    cantidad_decimal = Decimal(str(cantidad_raw))

    # Capturar stock previo para detectar cruce del umbral mínimo (Pausa 5).
    # TRASPASO no altera el total → no puede cruzar.
    stock_antes = Decimal(str(producto.stock_actual or 0))
    stock_minimo = Decimal(str(producto.stock_minimo or 0))

    try:
        # 4) Lock + alteración de filas de stock_por_almacen.
        # with_for_update previene over-selling cuando dos requests intentan
        # reducir el mismo stock al mismo tiempo.
        if tipo == 'ENTRADA':
            stock_dest = _lock_stock(producto.id, almacen_destino_id)
            stock_dest.cantidad = (stock_dest.cantidad or Decimal('0')) + cantidad_decimal

        elif tipo == 'SALIDA':
            stock_orig = _lock_stock(producto.id, almacen_origen_id)
            if (stock_orig.cantidad or Decimal('0')) < cantidad_decimal:
                db.session.rollback()
                return jsonify({
                    'detail': f'Stock insuficiente en bodega #{almacen_origen_id}. '
                              f'Disponible: {stock_orig.cantidad}'
                }), 400
            # Pausa 2-bis: respetar reservas globales (no sacar lo apartado).
            reservado = Decimal(str(producto.stock_reservado or 0))
            disponible_global = stock_antes - reservado
            if disponible_global < cantidad_decimal:
                db.session.rollback()
                return jsonify({
                    'detail': (
                        f'Hay {reservado} {producto.unidad} apartados por solicitudes aprobadas. '
                        f'Disponible para sacar: {disponible_global}. '
                        f'Libera/rechaza solicitudes antes o entrega vía el flujo de solicitudes.'
                    ),
                }), 409
            stock_orig.cantidad = stock_orig.cantidad - cantidad_decimal

        elif tipo == 'AJUSTE':
            # Positivo → sube destino; negativo → baja origen (sin pasar de 0).
            if cantidad_decimal >= 0:
                stock_dest = _lock_stock(producto.id, almacen_destino_id)
                stock_dest.cantidad = (stock_dest.cantidad or Decimal('0')) + cantidad_decimal
            else:
                stock_orig = _lock_stock(producto.id, almacen_origen_id)
                disponible = stock_orig.cantidad or Decimal('0')
                if disponible + cantidad_decimal < 0:
                    db.session.rollback()
                    return jsonify({
                        'detail': f'Ajuste provocaría stock negativo en bodega #{almacen_origen_id}'
                    }), 400
                # Pausa 2-bis: respetar reservas globales también.
                reservado = Decimal(str(producto.stock_reservado or 0))
                disponible_global_post = stock_antes + cantidad_decimal - reservado
                if disponible_global_post < 0:
                    db.session.rollback()
                    return jsonify({
                        'detail': (
                            f'Ajuste invadiría stock apartado. Reservado: {reservado}, '
                            f'stock tras ajuste: {stock_antes + cantidad_decimal}.'
                        ),
                    }), 409
                stock_orig.cantidad = disponible + cantidad_decimal

        elif tipo == 'TRASPASO':
            # Lock en orden determinístico (menor id primero) para evitar deadlocks
            # entre dos TRASPASOs cruzados (A→B y B→A simultáneos).
            ids = sorted([almacen_origen_id, almacen_destino_id])
            stock_a = _lock_stock(producto.id, ids[0])
            stock_b = _lock_stock(producto.id, ids[1])
            stock_orig = stock_a if ids[0] == almacen_origen_id else stock_b
            stock_dest = stock_b if ids[0] == almacen_origen_id else stock_a
            if (stock_orig.cantidad or Decimal('0')) < cantidad_decimal:
                db.session.rollback()
                return jsonify({
                    'detail': f'Stock insuficiente para traspaso en bodega #{almacen_origen_id}. '
                              f'Disponible: {stock_orig.cantidad}'
                }), 400
            stock_orig.cantidad = stock_orig.cantidad - cantidad_decimal
            stock_dest.cantidad = (stock_dest.cantidad or Decimal('0')) + cantidad_decimal

    except Exception as exc:
        db.session.rollback()
        # nowait=True levanta si la fila ya estaba bloqueada por otra transacción.
        # Devolvemos 409 para que el cliente reintente.
        if 'could not obtain lock' in str(exc).lower():
            return jsonify({'detail': 'Stock bloqueado por otra operación, reintenta'}), 409
        raise

    # 5) Actualizar cache desnormalizado Producto.stock_actual. Lo hacemos
    # DENTRO de la misma transacción para que nunca quede desfasado en
    # commits exitosos. TRASPASO no cambia el total, pero igual recalculamos
    # por seguridad (el costo es despreciable).
    _recalcular_cache_stock(producto)

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
        motivo=data.get('motivo') or (f"Estante #{estante_id}" if estante_id else None),
        usuario_id=user.id,
    )
    db.session.add(nuevo_mov)
    _audit(user, f"Movimiento {tipo} — producto #{data['producto_id']} — cantidad: {cantidad_raw}")
    db.session.commit()
    db.session.refresh(nuevo_mov)
    emit_to_role(_INV_ROLES, 'movimiento:changed', {
        'id': nuevo_mov.id, 'producto_id': nuevo_mov.producto_id, 'tipo': tipo,
    })
    return jsonify(_movimiento_to_dict(nuevo_mov))


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
