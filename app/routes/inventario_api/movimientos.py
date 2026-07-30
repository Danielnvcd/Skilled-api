"""Endpoints de Movimientos de inventario.

Expone `_perform_movimiento` para que el módulo `tomas` lo use al cerrar una
toma física (genera AJUSTES automáticos).
"""
import datetime
import io
import os
from decimal import Decimal

from flask import jsonify, request, current_app, render_template, send_file
from sqlalchemy.orm import joinedload

from app.extensions import db, limiter, get_real_client_ip_flask
from app.models import (
    Producto, Estante, Proyecto, MovimientoInventario, NotificacionUmbral,
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


def _perform_movimiento(data: dict, user):
    """Lógica central de creación de movimiento — usada por POST /movimientos/
    y POST /movimientos/rapido. `data` ya viene validado por
    MovimientoCreateSchema.

    Consciente del bucket de PROYECTO (feature stock por proyecto): cada tipo
    deposita/consume en `stock_almacen_proyecto` (proyecto|general) y luego
    recalcula los caches `stock_por_almacen` y `Producto.stock_actual` vía
    `_recalcular_caches`. La regla de consumo es proyecto→general para
    SALIDA/AJUSTE−, y bucket exacto para TRASPASO/REASIGNACION."""
    tipo = data['tipo']
    cantidad_raw = data['cantidad']
    proyecto_id = data.get('proyecto_id')
    proyecto_origen_id = data.get('proyecto_origen_id')
    proyecto_destino_id = data.get('proyecto_destino_id')

    # Partes del vale (quién entrega / quién recibe). Opcionales; se validan antes
    # de tocar stock para fallar limpio con 422 sin dejar la transacción a medias.
    partes, err_partes = _resolver_partes(data)
    if err_partes:
        return err_partes

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
                db.session.rollback()
                return jsonify({'detail': f'No se puede reasignar: {err}'}), 409
            _depositar(producto.id, almacen_id, proyecto_destino_id, cantidad_decimal)
            _recalcular_caches(producto, almacen_id)
        except Exception as exc:
            db.session.rollback()
            if 'could not obtain lock' in str(exc).lower():
                return jsonify({'detail': 'Stock bloqueado por otra operación, reintenta'}), 409
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
        db.session.commit()
        db.session.refresh(nuevo_mov)
        emit_to_role(_INV_ROLES, 'movimiento:changed', {
            'id': nuevo_mov.id, 'producto_id': producto.id, 'tipo': 'REASIGNACION',
        })
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
                db.session.rollback()
                return jsonify({'detail': f'Stock insuficiente en bodega #{almacen_origen_id}: {err}'}), 400
            # 2º) Guard global de reservas (no sacar lo apartado) → 409. Usa
            # stock_antes (total previo), por eso el orden no altera el cálculo.
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
                    db.session.rollback()
                    return jsonify({'detail': f'Ajuste provocaría stock negativo en bodega #{almacen_origen_id}: {err}'}), 400
                # 2º) Guard global de reservas (no invadir lo apartado) → 409.
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
                mov_proy_origen = proyecto_id
                almacenes_tocados = [almacen_origen_id]

        elif tipo == 'TRASPASO':
            # Mueve el bucket `proyecto_id` (exacto, sin fallback) de un almacén
            # a otro conservando la etiqueta de proyecto.
            err = _consumir_bucket_exacto(producto.id, almacen_origen_id, proyecto_id, cantidad_decimal)
            if err:
                db.session.rollback()
                return jsonify({'detail': f'Stock insuficiente para traspaso en bodega #{almacen_origen_id}: {err}'}), 400
            _depositar(producto.id, almacen_destino_id, proyecto_id, cantidad_decimal)
            mov_proy_origen = proyecto_id
            mov_proy_destino = proyecto_id
            almacenes_tocados = [almacen_origen_id, almacen_destino_id]

    except Exception as exc:
        db.session.rollback()
        # nowait=True levanta si la fila ya estaba bloqueada por otra transacción.
        # Devolvemos 409 para que el cliente reintente.
        if 'could not obtain lock' in str(exc).lower():
            return jsonify({'detail': 'Stock bloqueado por otra operación, reintenta'}), 409
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


# ─── Vale (PDF) de un movimiento ─────────────────────────────────────────────

_TIPO_LABEL = {
    'ENTRADA': 'Entrada de mercancía',
    'SALIDA': 'Salida de mercancía',
    'AJUSTE': 'Ajuste de inventario',
    'TRASPASO': 'Traspaso entre bodegas',
    'REASIGNACION': 'Reasignación entre proyectos',
}


def _render_movimiento_pdf(mov: MovimientoInventario):
    """Genera el vale PDF de un movimiento con xhtml2pdf → BytesIO (o None si la
    librería no está). Mismo mecanismo y logo que `_render_solicitud_pdf`."""
    try:
        from xhtml2pdf import pisa
    except ImportError:
        return None
    base_dir = current_app.config.get('BASE_DIR') or os.path.dirname(current_app.root_path)
    logo_path = os.path.join(base_dir, 'static', 'imagenes', 'skilled (1).png')
    if not os.path.exists(logo_path):
        logo_path = None

    def _q(v):
        v = float(v or 0)
        return int(v) if v % 1 == 0 else round(v, 2)

    prod = mov.producto
    contexto = {
        'folio': f'MOV-{mov.id:06d}',
        'tipo': mov.tipo,
        'tipo_label': _TIPO_LABEL.get(mov.tipo, mov.tipo),
        'fecha': mov.fecha.strftime('%d/%m/%Y %H:%M') if mov.fecha else '',
        'producto_codigo': prod.codigo if prod else '—',
        'producto_descripcion': prod.descripcion if prod else 'Producto eliminado',
        'cable_tipo': (prod.cable_tipo if prod else None) or '',
        'cable_calibre': (prod.cable_calibre if prod else None) or '',
        'cantidad': _q(mov.cantidad),
        'unidad': prod.unidad if prod else '',
        'almacen_origen': mov.almacen_origen.nombre if mov.almacen_origen else None,
        'almacen_destino': mov.almacen_destino.nombre if mov.almacen_destino else None,
        'proyecto_origen': mov.proyecto_origen.numero_proyecto if mov.proyecto_origen else None,
        'proyecto_destino': mov.proyecto_destino.numero_proyecto if mov.proyecto_destino else None,
        'motivo': mov.motivo or '',
        'entrega': mov.entrega_display or '',
        'recibe': mov.recibe_display or '',
        'logo_path': logo_path,
    }
    html_salida = render_template('movimiento_vale_pdf.html', **contexto)
    buf = io.BytesIO()
    status = pisa.CreatePDF(io.BytesIO(html_salida.encode('utf-8')), dest=buf)
    if status.err:
        return None
    buf.seek(0)
    return buf


@bp.route('/movimientos/<int:mov_id>/pdf', methods=['GET'])
@_require_inventario_admin
def imprimir_movimiento(mov_id: int):
    """Vale PDF de un movimiento ya registrado: producto, cantidad, bodegas,
    proyecto, motivo y las partes (entrega/recibe) con espacio de firmas."""
    mov = (
        MovimientoInventario.query
        .options(
            joinedload(MovimientoInventario.producto),
            joinedload(MovimientoInventario.almacen_origen),
            joinedload(MovimientoInventario.almacen_destino),
            joinedload(MovimientoInventario.proyecto_origen),
            joinedload(MovimientoInventario.proyecto_destino),
            joinedload(MovimientoInventario.entrega_trabajador),
            joinedload(MovimientoInventario.recibe_trabajador),
        )
        .filter(MovimientoInventario.id == mov_id)
        .first()
    )
    if not mov:
        return jsonify({'detail': 'Movimiento no encontrado'}), 404
    pdf = _render_movimiento_pdf(mov)
    if pdf is None:
        return jsonify({'detail': 'Error al generar el PDF'}), 500
    return send_file(
        pdf, mimetype='application/pdf', as_attachment=False,
        download_name=f'MOV-{mov.id:06d}.pdf',
    )
