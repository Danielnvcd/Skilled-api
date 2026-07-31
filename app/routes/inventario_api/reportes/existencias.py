"""Reporte de inventario actual: existencias y valor por producto."""
import datetime

from flask import request

from app.extensions import db, limiter, get_real_client_ip_flask
from app.models import Producto, StockAlmacenProyecto

from .._core import bp, _require_inventario_admin, _audit
from ._excel import REPORTES_MAX_FILAS, _stream_excel


@bp.route('/reportes/inventario-actual.xlsx', methods=['GET'])
@limiter.limit(
    "10/minute",
    key_func=lambda: f"ip:{get_real_client_ip_flask()}",
)
@_require_inventario_admin
def reporte_inventario_actual():
    """Reporte de stock actual de todos los productos activos.

    Query params:
      - categoria: filtra por categoría exacta (opcional).
      - solo_bajo_minimo: 1 → solo productos con stock_actual ≤ stock_minimo.
    """
    q = Producto.query.filter(Producto.activo == True)  # noqa: E712
    categoria = (request.args.get('categoria') or '').strip()
    if categoria:
        q = q.filter(Producto.categoria == categoria)
    if request.args.get('solo_bajo_minimo') in ('1', 'true', 'True'):
        q = q.filter(Producto.stock_actual <= Producto.stock_minimo)
    productos = q.order_by(Producto.categoria, Producto.codigo).limit(REPORTES_MAX_FILAS).all()

    # Cuánto del stock está APARTADO a obras y cuánto queda libre. El reporte
    # daba solo el total, y con `stock_almacen_proyecto` como fuente de verdad
    # eso deja fuera la única pregunta que importa para reponer: de estas 500,
    # ¿cuántas puedo comprometer? Una sola consulta agregada, no una por fila.
    ids = [p.id for p in productos]
    apartado_por_producto = {}
    if ids:
        apartado_por_producto = {
            pid: float(cant or 0)
            for pid, cant in db.session.query(
                StockAlmacenProyecto.producto_id,
                db.func.sum(StockAlmacenProyecto.cantidad),
            ).filter(
                StockAlmacenProyecto.producto_id.in_(ids),
                StockAlmacenProyecto.proyecto_id.isnot(None),
                StockAlmacenProyecto.cantidad > 0,
            ).group_by(StockAlmacenProyecto.producto_id).all()
        }

    rows = []
    for p in productos:
        actual = float(p.stock_actual or 0)
        reservado = float(p.stock_reservado or 0)
        minimo = float(p.stock_minimo or 0)
        apartado = apartado_por_producto.get(p.id, 0.0)
        rows.append({
            'Código': p.codigo,
            'Descripción': p.descripcion,
            'Categoría': p.categoria,
            'Unidad': p.unidad,
            'Stock actual': actual,
            # Dos formas distintas de que el stock no esté disponible, y no hay
            # que sumarlas: «apartado» es material físicamente etiquetado a una
            # obra; «reservado» es una solicitud aprobada aún sin entregar. Un
            # mismo kilo puede estar en las dos.
            'Apartado a proyectos': apartado,
            'Libre (sin proyecto)': actual - apartado,
            'Reservado por solicitudes': reservado,
            'Disponible': actual - reservado,
            'Mínimo': minimo,
            'Diferencia vs mínimo': actual - minimo,
            'Estado': 'BAJO' if actual <= minimo else 'OK',
        })

    _audit(request.current_user, f"Reporte inventario actual ({len(rows)} filas)")
    db.session.commit()

    ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    return _stream_excel(
        {'Inventario': rows},
        f'inventario_actual_{ts}.xlsx',
    )
