"""Alta, consulta y ubicación de solicitudes de material/herramienta."""
from decimal import Decimal

from flask import jsonify, request
from sqlalchemy.orm import joinedload, selectinload

from app.extensions import db, limiter, get_real_client_ip_flask
from app.models import (
    Almacen, Estante, Herramienta, Producto, ProductoEstante,
    SolicitudMaterial, SolicitudMaterialDetalle, Trabajador,
)
from app.realtime import emit_to_role

from .._core import (
    bp,
    ErrorDeNegocio, transaccion_de_stock,
    _require_login, _require_inventario_admin,
    _parse_or_422, _int_arg,
    SolicitudCreateSchema,
    _solicitud_to_dict,
    _audit,
    _unidad_permite_decimales, _es_entero,
    resolver_proyecto,
    _SOL_ROLES,
)


# ─── Construcción de líneas de solicitud ─────────────────────────────────────
#
# Cada builder valida UNA línea del payload y devuelve `(detalle, error)`: el
# `SolicitudMaterialDetalle` listo para agregar, o el mensaje de error de esa
# línea (el endpoint las junta todas y responde 400 con la lista completa, así
# el usuario corrige todo de una pasada en vez de línea por línea).

def _construir_detalle_material(idx: int, det: dict, solicitud_id: int):
    etiqueta = f"Línea {idx + 1}"
    if not det.get('producto_id') or det.get('herramienta_id'):
        return None, f"{etiqueta}: MATERIAL requiere producto_id y no herramienta_id"

    producto = Producto.query.filter(
        Producto.id == det['producto_id'],
        Producto.activo == True,  # noqa: E712
    ).first()
    if not producto:
        return None, f"{etiqueta}: producto_id {det['producto_id']} no existe o inactivo"

    cantidad = Decimal(str(det['cantidad_solicitada']))
    if not _unidad_permite_decimales(producto.unidad) and not _es_entero(cantidad):
        return None, (
            f"{etiqueta}: '{producto.descripcion}' se pide en cantidades enteras "
            f"(unidad: {producto.unidad or 'pieza'})"
        )

    return SolicitudMaterialDetalle(
        solicitud_id=solicitud_id,
        tipo_item='MATERIAL',
        producto_id=det['producto_id'],
        cantidad_solicitada=cantidad,
        justificacion=det.get('justificacion'),
    ), None


def _construir_detalle_herramienta(idx: int, det: dict, solicitud_id: int):
    etiqueta = f"Línea {idx + 1}"
    if not det.get('herramienta_id') or det.get('producto_id'):
        return None, f"{etiqueta}: HERRAMIENTA requiere herramienta_id y no producto_id"

    herr = Herramienta.query.filter(
        Herramienta.id == det['herramienta_id'],
        Herramienta.activo == True,  # noqa: E712
    ).first()
    if not herr:
        return None, f"{etiqueta}: herramienta_id {det['herramienta_id']} no existe o inactiva"

    fecha_inicio = det.get('fecha_uso_inicio')
    fecha_fin = det.get('fecha_uso_fin')
    if fecha_inicio and fecha_fin:
        if fecha_inicio > fecha_fin:
            return None, f"{etiqueta}: fecha_uso_inicio > fecha_uso_fin"
        if (fecha_fin - fecha_inicio).days > 365:
            return None, f"{etiqueta}: rango de uso mayor a 365 días"

    # Las herramientas SIEMPRE son enteras (1 unidad física = 1 asignación),
    # sin importar la unidad de medida que tengan capturada.
    cantidad = Decimal(str(det['cantidad_solicitada']))
    if not _es_entero(cantidad):
        return None, f"{etiqueta}: las herramientas se piden en cantidades enteras"

    return SolicitudMaterialDetalle(
        solicitud_id=solicitud_id,
        tipo_item='HERRAMIENTA',
        herramienta_id=det['herramienta_id'],
        cantidad_solicitada=cantidad,
        fecha_uso_inicio=fecha_inicio,
        fecha_uso_fin=fecha_fin,
        justificacion=det.get('justificacion'),
        complementos=det.get('complementos'),
    ), None


# ─── Solicitudes (CRUD + estado) ──────────────────────────────────────────────

@bp.route('/solicitudes/', methods=['POST'])
@limiter.limit(
    "10/minute",
    # key por IP real: ver comentario en create_movimiento sobre por qué el limiter
    # debe correr ANTES del check de auth (replica el patrón del FastAPI original).
    key_func=lambda: f"ip:{get_real_client_ip_flask()}",
)
@_require_login
@transaccion_de_stock
def create_solicitud():
    user = request.current_user
    if user.role not in ['solicitante_material', 'coordinador', 'admin', 'inventario']:
        return jsonify({'detail': 'No tienes permiso para crear solicitudes'}), 403

    data, err = _parse_or_422(SolicitudCreateSchema(), request.get_json(silent=True))
    if err: return err

    proyecto = (data.get('proyecto') or '').strip()
    if not proyecto:
        return jsonify({'detail': 'Debes seleccionar un proyecto para la solicitud'}), 422

    # FK opcional al proyecto: si el SPA la manda, validamos que exista para
    # poder atribuir el consumo en el panel de Inventario → Proyectos. Si no
    # viene, intentamos resolverla por el texto (número de proyecto) para que
    # solicitudes nuevas queden ligadas aunque el cliente sea viejo.
    proy = resolver_proyecto(data.get('proyecto_id'), proyecto)
    proyecto_id = proy.id if proy else None

    # Scoping por dueño: un coordinador no puede ligar una solicitud al proyecto
    # de OTRO coordinador. Refleja en backend el filtro del selector del SPA
    # (getProyectosPlanificables) para que no se pueda saltar por API mandando el
    # proyecto_id (o el número) de un proyecto ajeno. Texto libre sin proyecto
    # ligado (proy=None) se permite igual que antes (no atribuye consumo a nadie).
    if user.role == 'coordinador' and proy is not None and proy.coordinador_id != user.id:
        return jsonify({'detail': 'Solo puedes crear solicitudes para tus propios proyectos'}), 403

    nueva = SolicitudMaterial(
        solicitante_id=user.id,
        proyecto=proyecto,
        proyecto_id=proyecto_id,
        notas=data.get('notas'),
        estatus='PENDIENTE',
    )
    db.session.add(nueva)
    db.session.flush()  # Necesario para obtener nueva.id antes de crear detalles

    errores_detalle = []
    for idx, det in enumerate(data['detalles']):
        # XOR: cada línea es MATERIAL o HERRAMIENTA, no ambas (el schema ya
        # restringe `tipo_item` a esos dos valores).
        tipo = (det.get('tipo_item') or 'MATERIAL').upper()
        construir = (
            _construir_detalle_herramienta if tipo == 'HERRAMIENTA'
            else _construir_detalle_material
        )
        linea, error = construir(idx, det, nueva.id)
        if error:
            errores_detalle.append(error)
            continue
        db.session.add(linea)

    if errores_detalle:
        raise ErrorDeNegocio(errores_detalle, 400)

    _audit(user, f"Nueva solicitud — proyecto: {data.get('proyecto') or 'Sin proyecto'}")
    db.session.commit()
    db.session.refresh(nueva)
    emit_to_role(_SOL_ROLES, 'solicitud:changed', {
        'id': nueva.id, 'action': 'created',
    })

    return jsonify(_solicitud_to_dict(nueva))


@bp.route('/trabajadores-busqueda', methods=['GET'])
@_require_inventario_admin
def buscar_trabajadores_inventario():
    """Typeahead de trabajadores activos para la entrega directa (solo
    inventario/admin). Endpoint propio del módulo para no depender del scope
    de rol del módulo de Trabajadores. Devuelve id + nombre + nº empleado.

    Query params: q (texto), limit (1..50, default 20)."""
    q = (request.args.get('q') or '').strip()
    limit, err = _int_arg('limit', 20, 1, 50)
    if err: return err

    query = Trabajador.query.filter(Trabajador.activo == True)  # noqa: E712
    if q:
        like = f"%{q}%"
        query = query.filter(db.or_(
            Trabajador.nombre.ilike(like),
            Trabajador.nombre_apellidos.ilike(like),
            Trabajador.no_empleado.ilike(like),
        ))
    trabajadores = query.order_by(Trabajador.nombre.asc()).limit(limit).all()
    return jsonify([
        {
            'id': t.id,
            'nombre_completo': t.nombre_completo,
            'no_empleado': t.no_empleado,
            'puesto': t.puesto,
        }
        for t in trabajadores
    ])


@bp.route('/solicitudes/', methods=['GET'])
@_require_login
def get_solicitudes():
    user = request.current_user
    skip, err = _int_arg('skip', 0, 0, 1_000_000)
    if err: return err
    limit, err = _int_arg('limit', 200, 0, 2000)
    if err: return err

    query = SolicitudMaterial.query
    # solicitante_material y coordinador solo ven sus propias solicitudes.
    # inventario/admin/super_admin ven todas. Otros roles no entran.
    if user.role in ('solicitante_material', 'coordinador'):
        query = query.filter(SolicitudMaterial.solicitante_id == user.id)
    elif user.role not in ('inventario', 'admin', 'super_admin'):
        return jsonify({'detail': 'No tienes permiso'}), 403

    solicitudes = (
        query
        .options(
            joinedload(SolicitudMaterial.solicitante),
            selectinload(SolicitudMaterial.detalles).joinedload(SolicitudMaterialDetalle.producto),
            selectinload(SolicitudMaterial.detalles).joinedload(SolicitudMaterialDetalle.herramienta),
        )
        .order_by(SolicitudMaterial.fecha_creacion.desc())
        .offset(skip).limit(limit)
        .all()
    )
    return jsonify([_solicitud_to_dict(s) for s in solicitudes])


@bp.route('/solicitudes/<int:sol_id>/ubicaciones', methods=['GET'])
@_require_inventario_admin
def solicitud_ubicaciones(sol_id: int):
    """Dónde está cada material de la solicitud, para surtir más rápido (Pausa 11).

    Por cada línea MATERIAL devuelve sus colocaciones (almacén → estante →
    fila/columna → cantidad en la celda), ordenadas para recorrer el almacén.
    Solo lectura: no toca stock ni reservas.
    """
    sol = (
        SolicitudMaterial.query
        .options(selectinload(SolicitudMaterial.detalles))
        .filter(SolicitudMaterial.id == sol_id)
        .first()
    )
    if not sol:
        return jsonify({'detail': 'Solicitud no encontrada'}), 404

    pids = sorted({
        d.producto_id for d in (sol.detalles or [])
        if (d.tipo_item or 'MATERIAL').upper() == 'MATERIAL' and d.producto_id
    })

    por_producto: dict[str, dict] = {}
    if pids:
        filas = (
            db.session.query(ProductoEstante, Estante, Almacen, Producto)
            .join(Estante, Estante.id == ProductoEstante.estante_id)
            .join(Almacen, Almacen.id == Estante.almacen_id)
            .join(Producto, Producto.id == ProductoEstante.producto_id)
            .filter(
                ProductoEstante.producto_id.in_(pids),
                Estante.activo == True,  # noqa: E712
            )
            .order_by(Almacen.nombre.asc(), Estante.nombre.asc(),
                      ProductoEstante.fila.asc(), ProductoEstante.columna.asc())
            .all()
        )
        for pe, est, alm, prod in filas:
            entry = por_producto.setdefault(str(pe.producto_id), {
                'producto': {
                    'id': prod.id, 'codigo': prod.codigo,
                    'descripcion': prod.descripcion, 'unidad': prod.unidad,
                    'stock_actual': float(prod.stock_actual or 0),
                },
                'ubicaciones': [],
            })
            entry['ubicaciones'].append({
                'almacen_id': alm.id,
                'almacen_nombre': alm.nombre,
                'estante_id': est.id,
                'estante_nombre': est.nombre,
                'fila': pe.fila,
                'columna': pe.columna,
                'cantidad': float(pe.cantidad or 0),
            })

    return jsonify({'por_producto': por_producto})
