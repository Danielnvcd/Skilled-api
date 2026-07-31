"""Categorías del catálogo y sus metadatos visuales (CategoriaConfig).

Una categoría no es una tabla: es el valor del campo `Producto.categoria`. La
tabla `CategoriaConfig` solo guarda sus metadatos (imagen), así que una
categoría puede existir sin config y viceversa.
"""
from decimal import Decimal

from flask import jsonify, request, Response
from sqlalchemy import distinct as sql_distinct

from app.extensions import db
from app.models import (
    CategoriaConfig, Producto, SolicitudMaterial, SolicitudMaterialDetalle,
)
from app.realtime import emit_to_role

from .._core import (
    bp,
    _require_login, _require_inventario, _require_inventario_admin,
    _parse_or_422,
    CategoriaConfigUpsertSchema,
    _audit,
    _INV_ROLES,
)


# ─── Categorías ───────────────────────────────────────────────────────────────

@bp.route('/categorias/', methods=['GET'])
@_require_inventario
def get_categorias():
    """Devuelve la unión de categorías presentes en el catálogo de productos
    y las registradas en `categorias_config` (admin pudo crear categorías sin
    haber capturado aún ningún producto)."""
    prod_rows = (
        db.session.query(sql_distinct(Producto.categoria))
        .filter(Producto.activo == True, Producto.categoria != None, Producto.categoria != '')
        .all()
    )
    cfg_rows = db.session.query(CategoriaConfig.nombre).all()
    nombres = {r[0] for r in prod_rows} | {r[0] for r in cfg_rows}
    return jsonify(sorted(nombres))


@bp.route('/categorias/resumen', methods=['GET'])
@_require_inventario
def get_categorias_resumen():
    """Conteo de productos por categoría (total + cuántos bajo mínimo) para las
    tarjetas del catálogo. Server-side: evita descargar miles de productos al
    cliente solo para contarlos. Incluye categorías de `categorias_config` que
    aún no tienen productos (total 0)."""
    rows = (
        db.session.query(
            Producto.categoria,
            db.func.count(Producto.id),
            db.func.coalesce(
                db.func.sum(
                    db.case((Producto.stock_actual <= Producto.stock_minimo, 1), else_=0)
                ), 0,
            ),
        )
        .filter(Producto.activo == True, Producto.categoria != None, Producto.categoria != '')  # noqa: E711,E712
        .group_by(Producto.categoria)
        .all()
    )
    resumen = {
        nombre: {'nombre': nombre, 'total': int(total or 0), 'bajo_minimo': int(bajos or 0)}
        for nombre, total, bajos in rows
    }
    # Categorías registradas en config pero sin productos todavía.
    for (nombre,) in db.session.query(CategoriaConfig.nombre).all():
        if nombre and nombre not in resumen:
            resumen[nombre] = {'nombre': nombre, 'total': 0, 'bajo_minimo': 0}

    return jsonify(sorted(resumen.values(), key=lambda r: r['nombre'].lower()))


# ─── CategoriaConfig (metadatos visuales por categoría) ──────────────────────

def _categoria_config_to_dict(c: CategoriaConfig) -> dict:
    return {
        'nombre': c.nombre,
        'imagen_url': c.imagen_url,
        'imagen_estado': c.imagen_estado,
        'updated_at': c.updated_at.isoformat() if c.updated_at else None,
    }


@bp.route('/categorias-config/', methods=['GET'])
@_require_login
def get_categorias_config():
    """Lista todas las configuraciones (imagen, etc.) por nombre de categoría.
    Lectura abierta a cualquier usuario autenticado: el dashboard de inventario
    también lo consume desde el rol solicitante_material."""
    rows = CategoriaConfig.query.order_by(CategoriaConfig.nombre).all()
    return jsonify([_categoria_config_to_dict(c) for c in rows])


# `path` en vez de `string`: hay categorías con barra en el nombre
# ("Tubería/Accesorios"). El convertidor `string` NO acepta barras, y como la
# capa WSGI decodifica el %2F que manda el navegador ANTES del enrutado, la
# ruta dejaba de coincidir y Flask respondía 404 desde el router — sin llegar
# nunca al endpoint. `path` sí las acepta.
#
# Es seguro: `nombre` solo se usa para consultar la base de datos, nunca para
# construir rutas de archivos, así que aceptar barras no abre path traversal.
@bp.route('/categorias-config/<path:nombre>', methods=['PUT'])
@_require_inventario_admin
def upsert_categoria_config(nombre: str):
    """Crea o actualiza la config de la categoría con `nombre`. Si imagen_url
    viene null o vacío, persiste null (UI lo trata como "quitar imagen")."""
    nombre = (nombre or '').strip()
    if not nombre or len(nombre) > 100:
        return jsonify({'detail': "Nombre de categoría inválido (1..100 caracteres)"}), 422

    data, err = _parse_or_422(CategoriaConfigUpsertSchema(), request.get_json(silent=True))
    if err: return err

    imagen = (data.get('imagen_url') or '').strip() or None

    cfg = CategoriaConfig.query.filter(CategoriaConfig.nombre == nombre).first()
    if cfg is None:
        cfg = CategoriaConfig(
            nombre=nombre,
            imagen_url=imagen,
            created_by_id=request.current_user.id,
        )
        db.session.add(cfg)
        _audit(request.current_user, f"Categoría '{nombre}' creada/actualizada")
    else:
        cfg.imagen_url = imagen
        _audit(request.current_user, f"Categoría '{nombre}' actualizada")

    db.session.commit()
    db.session.refresh(cfg)
    # Pipeline de imágenes → R2: si la imagen de categoría es URL externa, se
    # marca y se encola su descarga a WebP+R2 (no-op salvo producción con R2).
    from ..imagenes import marcar_para_sync, encolar_sync
    if marcar_para_sync(cfg, cfg.imagen_url):
        db.session.commit()
        encolar_sync(request.current_user.id, [('categoria', cfg.id)])
    return jsonify(_categoria_config_to_dict(cfg))


@bp.route('/categorias-config/<path:nombre>', methods=['DELETE'])
@_require_inventario_admin
def delete_categoria_config(nombre: str):
    """Elimina una categoría.

    Por defecto solo borra la fila de config (metadatos visuales) y NO afecta
    productos. Para eliminar también todos los productos de la categoría —el
    flujo destructivo que el SPA debe confirmar con una advertencia— pasar
    `?con_productos=1`. En ese caso:
      - Se hace soft-delete (activo=False) de cada producto, igual que
        `delete_producto`, para preservar el histórico de movimientos y
        solicitudes (no se rompe el kardex ni los folios).
      - Se liberan las reservas pendientes de stock de esos productos para que
        no queden unidades apartadas por solicitudes sobre un producto muerto.
      - Se borra también la config de la categoría.
    """
    nombre = (nombre or '').strip()
    con_productos = (request.args.get('con_productos') or '').lower() in ('1', 'true', 'yes')

    cfg = CategoriaConfig.query.filter(CategoriaConfig.nombre == nombre).first()

    if not con_productos:
        if not cfg:
            return jsonify({'detail': 'Categoría no encontrada en config'}), 404
        db.session.delete(cfg)
        _audit(request.current_user, f"Categoría '{nombre}' config eliminada")
        db.session.commit()
        return Response(status=204)

    # Borrado en cascada: productos + config.
    productos = (
        Producto.query
        .filter(Producto.categoria == nombre, Producto.activo == True)  # noqa: E712
        .all()
    )
    # Si no hay ni productos ni config, la categoría no existe.
    if not productos and not cfg:
        return jsonify({'detail': 'Categoría no encontrada'}), 404

    # Guardia: no borrar productos que tengan entregas pendientes. Una solicitud
    # APROBADA y no entregada apartó stock y el solicitante la espera; si
    # desactiváramos el producto, la entrega posterior generaría una SALIDA
    # sobre un producto muerto y dejaría la solicitud colgada. Bloqueamos con
    # 409 y devolvemos qué solicitudes resolver primero (opción conservadora:
    # el almacenista decide, no cancelamos pedidos ajenos automáticamente).
    prod_ids = [p.id for p in productos]
    if prod_ids:
        # pendiente por línea = base − entregada, con base = aprobada (o
        # solicitada si aún no se tocó la aprobación, caso pre-8b). Mismo
        # criterio que `_reservas_de_solicitud`.
        base = db.func.coalesce(
            db.func.nullif(SolicitudMaterialDetalle.cantidad_aprobada, 0),
            SolicitudMaterialDetalle.cantidad_solicitada,
        )
        pendiente = base - db.func.coalesce(SolicitudMaterialDetalle.cantidad_entregada, 0)
        filas_bloqueo = (
            db.session.query(
                SolicitudMaterial.id,
                Producto.codigo,
                Producto.descripcion,
            )
            .join(SolicitudMaterialDetalle, SolicitudMaterialDetalle.solicitud_id == SolicitudMaterial.id)
            .join(Producto, Producto.id == SolicitudMaterialDetalle.producto_id)
            .filter(
                SolicitudMaterial.estatus == 'APROBADA',
                SolicitudMaterialDetalle.producto_id.in_(prod_ids),
                pendiente > 0,
            )
            .distinct()
            .all()
        )
        if filas_bloqueo:
            solicitudes_ids = sorted({r.id for r in filas_bloqueo})
            return jsonify({
                'detail': (
                    'No se puede eliminar la categoría: tiene productos con entregas '
                    f'pendientes en {len(solicitudes_ids)} solicitud(es) aprobada(s). '
                    'Entrega o rechaza esas solicitudes antes de borrar.'
                ),
                'codigo': 'ENTREGAS_PENDIENTES',
                'solicitudes': [f'SOL-{sid:06d}' for sid in solicitudes_ids],
                'productos': sorted({f'{r.codigo} — {r.descripcion}' for r in filas_bloqueo}),
            }), 409

    eliminados = 0
    for prod in productos:
        prod.activo = False  # Soft delete: conserva histórico (igual que delete_producto)
        # Libera la reserva que esté apartando para que no quede stock fantasma
        # apartado por solicitudes sobre un producto desactivado.
        if prod.stock_reservado:
            prod.stock_reservado = Decimal('0')
        eliminados += 1

    if cfg:
        db.session.delete(cfg)

    _audit(
        request.current_user,
        f"Categoría '{nombre}' eliminada con sus productos ({eliminados} desactivados)",
    )
    db.session.commit()

    # Websockets-first: refresca catálogos abiertos en otras sesiones. El front
    # invalida el namespace completo de productos con un solo emit (igual que la
    # importación masiva) y refresca las tarjetas de categorías.
    emit_to_role(_INV_ROLES, 'producto:changed', {
        'action': 'bulk_delete', 'categoria': nombre, 'count': eliminados,
    })

    return jsonify({
        'categoria': nombre,
        'productos_eliminados': eliminados,
        'config_eliminada': cfg is not None,
    })
