"""Contabilidad de existencias del módulo de Inventario.

Jerarquía de la verdad:

  1. `stock_almacen_proyecto` — fuente de verdad, grano (producto, almacén,
     proyecto|NULL=general).
  2. `stock_por_almacen`      — cache por almacén (Σ buckets de proyecto).
  3. `Producto.stock_actual`  — cache global (Σ almacenes).

Los dos caches se recalculan con `_recalcular_caches` dentro de la MISMA
transacción que movió los buckets, así nunca quedan desfasados en un commit
exitoso. Todas las lecturas para mutar usan `FOR UPDATE NOWAIT`: si la fila está
tomada se levanta y el llamador responde 409 'reintenta'.
"""
from decimal import Decimal

from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import (
    Almacen, Estante, MovimientoInventario, Producto, ProductoEstante,
    StockAlmacenProyecto, StockPorAlmacen, User,
)


def _dec(v) -> Decimal:
    """Decimal seguro desde float/str/None (None → 0)."""
    return Decimal(str(v or 0))


def _almacen_default_id() -> int | None:
    """Devuelve el id del almacén activo de menor id. Sirve como fallback cuando
    un movimiento llega sin almacén ni estante (clientes viejos del SPA antes
    del refactor a stock por almacén). Devuelve None si no hay ninguno."""
    row = (
        db.session.query(Almacen.id)
        .filter(Almacen.activo == True)  # noqa: E712
        .order_by(Almacen.id.asc())
        .first()
    )
    return row[0] if row else None


def _crear_o_releer(leer, construir):
    """Crea la fila que `leer()` no encontró, tolerando la carrera del INSERT.

    `SELECT ... FOR UPDATE` no bloquea filas inexistentes, así que dos
    transacciones concurrentes pueden ver `None` a la vez y ambas insertar: la
    segunda choca con el índice único (`IntegrityError`). Antes ese error salía
    como 500 porque el guard de los endpoints solo reconocía el error de lock.

    Aislamos el INSERT en un savepoint: si choca, lo descartamos sin tocar el
    resto de la transacción y releemos la fila que dejó el ganador (ya
    commiteada, así que el FOR UPDATE la toma sin esperar).
    """
    try:
        with db.session.begin_nested():
            fila = construir()
            db.session.add(fila)
            db.session.flush()
        return fila
    except IntegrityError:
        fila = leer()
        if fila is None:
            # No debería pasar: el único motivo de la violación es que ya existe.
            raise
        return fila


# ─── Cache por almacén ───────────────────────────────────────────────────────

def _lock_stock(producto_id: int, almacen_id: int) -> StockPorAlmacen:
    """SELECT ... FOR UPDATE sobre la fila (producto, almacen). Crea la fila
    en 0 si no existe — útil para ENTRADAs hacia bodegas nuevas que aún no
    tienen registro de este producto."""
    def _leer():
        return (
            db.session.query(StockPorAlmacen)
            .with_for_update(nowait=True)
            .filter(
                StockPorAlmacen.producto_id == producto_id,
                StockPorAlmacen.almacen_id == almacen_id,
            )
            .first()
        )

    fila = _leer()
    if fila is None:
        fila = _crear_o_releer(
            _leer,
            lambda: StockPorAlmacen(
                producto_id=producto_id, almacen_id=almacen_id, cantidad=Decimal('0'),
            ),
        )
    return fila


def _producto_almacen_stock(producto_ids, almacen_id: int) -> dict[int, Decimal]:
    """Stock por almacén de varios productos: {producto_id: cantidad}.
    Devuelve 0 para los productos sin fila en StockPorAlmacen para ese almacén.
    Usado para validar el invariante Σceldas ≤ stock_almacen (Pausa 11)."""
    ids = [int(p) for p in set(producto_ids)]
    out: dict[int, Decimal] = {pid: Decimal('0') for pid in ids}
    if not ids:
        return out
    rows = (
        db.session.query(StockPorAlmacen.producto_id, StockPorAlmacen.cantidad)
        .filter(
            StockPorAlmacen.almacen_id == almacen_id,
            StockPorAlmacen.producto_id.in_(ids),
        )
        .all()
    )
    for pid, cant in rows:
        out[pid] = _dec(cant)
    return out


def _cantidad_en_celdas_almacen(producto_id: int, almacen_id: int,
                                excluir_estante_id: int | None = None) -> Decimal:
    """Σ ProductoEstante.cantidad de un producto en los estantes (activos) de un
    almacén, opcionalmente excluyendo un estante (el que se está editando)."""
    q = (
        db.session.query(db.func.coalesce(db.func.sum(ProductoEstante.cantidad), 0))
        .join(Estante, Estante.id == ProductoEstante.estante_id)
        .filter(
            ProductoEstante.producto_id == producto_id,
            Estante.almacen_id == almacen_id,
            Estante.activo == True,  # noqa: E712
        )
    )
    if excluir_estante_id is not None:
        q = q.filter(ProductoEstante.estante_id != excluir_estante_id)
    return _dec(q.scalar())


def _recalcular_cache_stock(producto: Producto):
    """Actualiza el cache denormalizado `Producto.stock_actual` con la suma
    de todas las filas de stock_por_almacen del producto. Se llama dentro de
    la misma transacción que modificó stock_por_almacen, así nunca queda
    desfasado en commits exitosos."""
    total = (
        db.session.query(db.func.coalesce(db.func.sum(StockPorAlmacen.cantidad), 0))
        .filter(StockPorAlmacen.producto_id == producto.id)
        .scalar()
    )
    producto.stock_actual = _dec(total)


# ─── Buckets por proyecto (fuente de verdad) ─────────────────────────────────

def _lock_stock_proyecto(producto_id: int, almacen_id: int,
                         proyecto_id: int | None) -> StockAlmacenProyecto:
    """SELECT ... FOR UPDATE sobre la fila (producto, almacén, proyecto|NULL).
    Crea la fila en 0 si no existe. `proyecto_id=None` = bucket general.
    nowait=True: si otra transacción la tiene, se levanta y el caller devuelve
    409 'reintenta' (mismo patrón que `_lock_stock`)."""
    def _leer():
        q = (
            db.session.query(StockAlmacenProyecto)
            .with_for_update(nowait=True)
            .filter(
                StockAlmacenProyecto.producto_id == producto_id,
                StockAlmacenProyecto.almacen_id == almacen_id,
            )
        )
        return _filtrar_por_proyecto(q, proyecto_id).first()

    fila = _leer()
    if fila is None:
        fila = _crear_o_releer(
            _leer,
            lambda: StockAlmacenProyecto(
                producto_id=producto_id, almacen_id=almacen_id,
                proyecto_id=proyecto_id, cantidad=Decimal('0'),
            ),
        )
    return fila


def _filtrar_por_proyecto(query, proyecto_id: int | None):
    """Restringe un query de StockAlmacenProyecto al bucket de un proyecto, o al
    bucket general cuando `proyecto_id` es None (IS NULL, no `= NULL`)."""
    if proyecto_id is None:
        return query.filter(StockAlmacenProyecto.proyecto_id.is_(None))
    return query.filter(StockAlmacenProyecto.proyecto_id == proyecto_id)


def _recalcular_caches(producto: Producto, almacen_id: int):
    """Recalcula, en cascada, los dos caches tras mutar stock_almacen_proyecto:
      1. stock_por_almacen(producto, almacen) = Σ buckets de proyecto del almacén.
      2. Producto.stock_actual = Σ almacenes (vía `_recalcular_cache_stock`).
    Se llama dentro de la misma transacción que modificó los buckets.

    El lock del cache se toma ANTES de sumar los buckets, y ese orden es la
    razón de ser de esta función: dos transacciones que tocan buckets DISTINTOS
    del mismo (producto, almacén) no se bloquean entre sí —son filas distintas
    de stock_almacen_proyecto—, así que ambas llegaban aquí, cada una sumaba sin
    ver el bucket no commiteado de la otra, y la última en commitear dejaba el
    cache sin el movimiento de la primera. Un lost update clásico: la fuente de
    verdad quedaba bien y `stock_por_almacen` / `Producto.stock_actual` —lo que
    ve el usuario— se desviaban en silencio. Bloquear el cache primero serializa
    ese tramo: la segunda transacción recibe el 409 'reintenta' del NOWAIT, que
    es lo que ya hace el resto del módulo.
    """
    # `_lock_stock` hace FOR UPDATE NOWAIT y crea la fila en 0 si no existe
    # (tolerando la carrera del INSERT vía `_crear_o_releer`).
    fila = _lock_stock(producto.id, almacen_id)
    total_alm = _dec(
        db.session.query(db.func.coalesce(db.func.sum(StockAlmacenProyecto.cantidad), 0))
        .filter(
            StockAlmacenProyecto.producto_id == producto.id,
            StockAlmacenProyecto.almacen_id == almacen_id,
        )
        .scalar()
    )
    fila.cantidad = total_alm
    db.session.flush()  # que el sum de _recalcular_cache_stock vea el cache nuevo
    _recalcular_cache_stock(producto)


def _depositar(producto_id: int, almacen_id: int, proyecto_id: int | None,
               cantidad) -> StockAlmacenProyecto:
    """Suma `cantidad` al bucket (producto, almacén, proyecto|general)."""
    fila = _lock_stock_proyecto(producto_id, almacen_id, proyecto_id)
    fila.cantidad = _dec(fila.cantidad) + Decimal(str(cantidad))
    return fila


def _consumir_proyecto_luego_general(producto_id: int, almacen_id: int,
                                     proyecto_id: int | None, cantidad) -> str | None:
    """Descuenta `cantidad` del almacén dado aplicando la regla PROYECTO→GENERAL:
    primero del bucket del proyecto, el remanente del general. NUNCA toca el
    stock etiquetado a OTROS proyectos. Con `proyecto_id=None` sale solo del
    general. Devuelve None si ok, o un string de error si el físico no alcanza.
    Bloquea las filas con FOR UPDATE."""
    cantidad = Decimal(str(cantidad))
    if cantidad <= 0:
        return None
    fila_gen = _lock_stock_proyecto(producto_id, almacen_id, None)
    disp_gen = _dec(fila_gen.cantidad)
    fila_proj = None
    disp_proj = Decimal('0')
    if proyecto_id is not None:
        fila_proj = _lock_stock_proyecto(producto_id, almacen_id, proyecto_id)
        disp_proj = _dec(fila_proj.cantidad)
    if disp_proj + disp_gen < cantidad:
        return (
            f'requiere {cantidad}, disponible {disp_proj + disp_gen} en el almacén '
            f'(proyecto {disp_proj} + general {disp_gen})'
        )
    if proyecto_id is not None:
        take_proj = min(disp_proj, cantidad)
        fila_proj.cantidad = disp_proj - take_proj
        resto = cantidad - take_proj
        if resto > 0:
            fila_gen.cantidad = disp_gen - resto
    else:
        fila_gen.cantidad = disp_gen - cantidad
    return None


def _consumir_bucket_exacto(producto_id: int, almacen_id: int,
                            proyecto_id: int | None, cantidad) -> str | None:
    """Descuenta `cantidad` EXACTAMENTE del bucket indicado, sin fallback a
    general. Para TRASPASO/REASIGNACION, que mueven un bucket específico y deben
    dejar cuadrado el bucket destino. Devuelve None si ok o un error string."""
    cantidad = Decimal(str(cantidad))
    if cantidad <= 0:
        return None
    fila = _lock_stock_proyecto(producto_id, almacen_id, proyecto_id)
    disp = _dec(fila.cantidad)
    if disp < cantidad:
        etiqueta = 'general' if proyecto_id is None else f'proyecto #{proyecto_id}'
        return f'el bucket {etiqueta} solo tiene {disp} en el almacén (requiere {cantidad})'
    fila.cantidad = disp - cantidad
    return None


def _consumir_reconciliando(producto_id: int, almacen_id: int, cantidad) -> str | None:
    """Descuenta `cantidad` del almacén tomando de CUALQUIER bucket: general
    primero, luego los de proyecto (mayor cantidad primero). Se usa SOLO para
    reconciliar una TOMA física a nivel almacén: el conteo es la verdad, así que
    si físicamente hay menos se reduce de donde haya. A diferencia del consumo
    normal, NO respeta la etiqueta de proyecto (el faltante físico ya ocurrió).
    Devuelve None o un error si el total del almacén no alcanza."""
    restante = Decimal(str(cantidad))
    if restante <= 0:
        return None
    fila_gen = _lock_stock_proyecto(producto_id, almacen_id, None)
    disp_gen = _dec(fila_gen.cantidad)
    toma_gen = min(disp_gen, restante)
    if toma_gen > 0:
        fila_gen.cantidad = disp_gen - toma_gen
        restante -= toma_gen
    if restante > 0:
        filas = (
            db.session.query(StockAlmacenProyecto)
            .with_for_update(nowait=True)
            .filter(
                StockAlmacenProyecto.producto_id == producto_id,
                StockAlmacenProyecto.almacen_id == almacen_id,
                StockAlmacenProyecto.proyecto_id.isnot(None),
                StockAlmacenProyecto.cantidad > 0,
            )
            .order_by(StockAlmacenProyecto.cantidad.desc())
            .all()
        )
        for f in filas:
            if restante <= 0:
                break
            disp = _dec(f.cantidad)
            toma = min(disp, restante)
            f.cantidad = disp - toma
            restante -= toma
    if restante > 0:
        return f'requiere {cantidad}, faltan {restante} en el almacén (todos los buckets)'
    return None


def _stock_proyecto_total(producto_id: int, proyecto_id: int | None) -> Decimal:
    """Σ cantidad del bucket (producto, proyecto|general) sobre TODOS los
    almacenes. El almacén se resuelve en la entrega, así que la disponibilidad
    a nivel de aprobación es cross-almacén (como la reserva)."""
    q = (
        db.session.query(db.func.coalesce(db.func.sum(StockAlmacenProyecto.cantidad), 0))
        .filter(StockAlmacenProyecto.producto_id == producto_id)
    )
    return _dec(_filtrar_por_proyecto(q, proyecto_id).scalar())


def _ajustar_bucket(producto: Producto, almacen_id: int, proyecto_id, delta: Decimal,
                    user: User, motivo: str) -> str | None:
    """Aplica un AJUSTE de `delta` (±) al bucket (producto, almacén, proyecto|general)
    SIN commitear — el caller controla la transacción (editor de stock por bucket).

    Reusa los helpers de stock por proyecto: `_depositar` para deltas positivos y
    `_consumir_bucket_exacto` (bucket exacto, sin fallback a general) para los
    negativos, luego `_recalcular_caches`. Registra un MovimientoInventario
    tipo AJUSTE con la atribución de proyecto correspondiente. Devuelve None si ok
    o un string de error (bucket insuficiente). No valida reservas aquí: el caller
    hace el guard global tras aplicar todos los buckets."""
    delta = Decimal(str(delta))
    if delta == 0:
        return None
    if delta > 0:
        _depositar(producto.id, almacen_id, proyecto_id, delta)
        mov_proy_destino, mov_proy_origen = proyecto_id, None
    else:
        err = _consumir_bucket_exacto(producto.id, almacen_id, proyecto_id, -delta)
        if err:
            return err
        mov_proy_destino, mov_proy_origen = None, proyecto_id
    _recalcular_caches(producto, almacen_id)
    db.session.add(MovimientoInventario(
        tipo='AJUSTE',
        producto_id=producto.id,
        cantidad=delta,
        almacen_origen_id=(almacen_id if delta < 0 else None),
        almacen_destino_id=(almacen_id if delta > 0 else None),
        proyecto_origen_id=mov_proy_origen,
        proyecto_destino_id=mov_proy_destino,
        motivo=(motivo or 'Ajuste manual desde edición')[:250],
        usuario_id=user.id,
    ))
    return None
