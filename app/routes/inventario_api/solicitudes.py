"""Endpoints de Solicitudes de material/herramienta + PDFs.

Cubre el ciclo completo:
  - Crear / listar / cambiar estado.
  - Editar `cantidad_aprobada` por línea (Pausa 8b).
  - Entregar total/parcial (Pausa 8b) con asignación de herramientas.
  - PDF de solicitud guardada + preview PDF sin persistir.
"""
import io
import os
import datetime
from decimal import Decimal

from flask import jsonify, request, send_file, render_template, current_app
from sqlalchemy.orm import joinedload, selectinload

from app.extensions import db, limiter, get_real_client_ip_flask
from app.models import (
    Producto, SolicitudMaterial, SolicitudMaterialDetalle,
    MovimientoInventario, Almacen, Estante, ProductoEstante,
    Trabajador, Herramienta, HerramientaUnidad, AsignacionHerramienta,
    crear_evento_herramienta,
)
from app.realtime import emit_to_role

from ._core import (
    bp,
    _es_error_de_lock,
    _require_login, _require_inventario_admin,
    _parse_or_422,
    SolicitudCreateSchema, SolicitudUpdateEstadoSchema,
    SolicitudDetallePatchSchema, EntregarSolicitudSchema,
    EntregaDirectaSchema,
    _solicitud_to_dict, _solicitud_detalle_to_dict,
    _audit,
    _almacen_default_id, _recalcular_caches,
    _consumir_proyecto_luego_general,
    _reservas_de_solicitud, _intentar_reservar, _liberar_reservas,
    _INV_ROLES, _SOL_ROLES,
)


# ─── Regla de decimales por unidad ────────────────────────────────────────────
# Unidades de medida "continuas" que admiten decimales (kg, metros, litros…).
# El resto (pieza, unidad, caja, par, rollo…) se piden en enteros. Las
# herramientas SIEMPRE son enteras, sin importar su unidad.
_UNIDADES_DECIMALES = {
    'kg', 'kgs', 'kilo', 'kilos', 'kilogramo', 'kilogramos',
    'g', 'gr', 'grs', 'gramo', 'gramos', 'mg',
    'ton', 'tonelada', 'toneladas',
    'l', 'lt', 'lts', 'litro', 'litros', 'ml', 'mililitro', 'mililitros',
    'gal', 'galon', 'galones',
    'm', 'mt', 'mts', 'metro', 'metros',
    'cm', 'centimetro', 'centimetros', 'mm', 'milimetro', 'milimetros',
    'km', 'kilometro', 'kilometros', 'm2', 'm3',
    'pulgada', 'pulgadas', 'in', 'ft', 'pie', 'pies', 'yarda', 'yardas',
    'oz', 'onza', 'onzas', 'lb', 'lbs', 'libra', 'libras',
}


def _unidad_permite_decimales(unidad) -> bool:
    import unicodedata
    u = unicodedata.normalize('NFD', str(unidad or '').strip().lower())
    u = ''.join(c for c in u if unicodedata.category(c) != 'Mn')  # quita acentos
    u = u.replace('²', '2').replace('³', '3').replace('.', '').replace(' ', '')
    return u in _UNIDADES_DECIMALES


def _es_categoria_cable(categoria) -> bool:
    """¿La categoría del producto es de cable? Detección por texto (contiene
    'cable', case-insensitive, sin acentos): 'Cable', 'Cables', 'Cable THHN'…
    Debe coincidir con esCategoriaCable() del frontend (utils/cable.js)."""
    import unicodedata
    c = unicodedata.normalize('NFD', str(categoria or '').strip().lower())
    c = ''.join(ch for ch in c if unicodedata.category(ch) != 'Mn')  # quita acentos
    return 'cable' in c


def _es_entero(d: Decimal) -> bool:
    return d == d.to_integral_value()


# ─── Solicitudes (CRUD + estado) ──────────────────────────────────────────────

@bp.route('/solicitudes/', methods=['POST'])
@limiter.limit(
    "10/minute",
    # key por IP real: ver comentario en create_movimiento sobre por qué el limiter
    # debe correr ANTES del check de auth (replica el patrón del FastAPI original).
    key_func=lambda: f"ip:{get_real_client_ip_flask()}",
)
@_require_login
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
    from app.models import Proyecto
    proyecto_id = data.get('proyecto_id')
    if proyecto_id is not None:
        proy = Proyecto.query.filter(Proyecto.id == proyecto_id).first()
        if not proy:
            return jsonify({'detail': f'Proyecto #{proyecto_id} no existe'}), 422
    else:
        proy = Proyecto.query.filter(Proyecto.numero_proyecto == proyecto).first()
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
        tipo = (det.get('tipo_item') or 'MATERIAL').upper()

        # XOR: cada línea es MATERIAL o HERRAMIENTA, no ambas.
        if tipo == 'MATERIAL':
            if not det.get('producto_id') or det.get('herramienta_id'):
                errores_detalle.append(f"Línea {idx+1}: MATERIAL requiere producto_id y no herramienta_id")
                continue
            producto = Producto.query.filter(Producto.id == det['producto_id'],
                                              Producto.activo == True).first()
            if not producto:
                errores_detalle.append(f"Línea {idx+1}: producto_id {det['producto_id']} no existe o inactivo")
                continue
            cant_mat = Decimal(str(det['cantidad_solicitada']))
            if not _unidad_permite_decimales(producto.unidad) and not _es_entero(cant_mat):
                errores_detalle.append(
                    f"Línea {idx+1}: '{producto.descripcion}' se pide en cantidades enteras "
                    f"(unidad: {producto.unidad or 'pieza'})"
                )
                continue
            db.session.add(SolicitudMaterialDetalle(
                solicitud_id=nueva.id,
                tipo_item='MATERIAL',
                producto_id=det['producto_id'],
                cantidad_solicitada=cant_mat,
                justificacion=det.get('justificacion'),
            ))
        elif tipo == 'HERRAMIENTA':
            if not det.get('herramienta_id') or det.get('producto_id'):
                errores_detalle.append(f"Línea {idx+1}: HERRAMIENTA requiere herramienta_id y no producto_id")
                continue
            herr = Herramienta.query.filter(Herramienta.id == det['herramienta_id'],
                                              Herramienta.activo == True).first()
            if not herr:
                errores_detalle.append(f"Línea {idx+1}: herramienta_id {det['herramienta_id']} no existe o inactiva")
                continue
            fi = det.get('fecha_uso_inicio')
            ff = det.get('fecha_uso_fin')
            if fi and ff and fi > ff:
                errores_detalle.append(f"Línea {idx+1}: fecha_uso_inicio > fecha_uso_fin")
                continue
            if fi and ff and (ff - fi).days > 365:
                errores_detalle.append(f"Línea {idx+1}: rango de uso mayor a 365 días")
                continue
            cant_herr = Decimal(str(det['cantidad_solicitada']))
            if not _es_entero(cant_herr):
                errores_detalle.append(f"Línea {idx+1}: las herramientas se piden en cantidades enteras")
                continue
            db.session.add(SolicitudMaterialDetalle(
                solicitud_id=nueva.id,
                tipo_item='HERRAMIENTA',
                herramienta_id=det['herramienta_id'],
                cantidad_solicitada=cant_herr,
                fecha_uso_inicio=fi,
                fecha_uso_fin=ff,
                justificacion=det.get('justificacion'),
                complementos=det.get('complementos'),
            ))

    if errores_detalle:
        db.session.rollback()
        return jsonify({'detail': errores_detalle}), 400

    _audit(user, f"Nueva solicitud — proyecto: {data.get('proyecto') or 'Sin proyecto'}")
    db.session.commit()
    db.session.refresh(nueva)
    emit_to_role(_SOL_ROLES, 'solicitud:changed', {
        'id': nueva.id, 'action': 'created',
    })

    return jsonify(_solicitud_to_dict(nueva))


@bp.route('/solicitudes/entrega-directa', methods=['POST'])
@limiter.limit(
    "20/minute",
    key_func=lambda: f"ip:{get_real_client_ip_flask()}",
)
@_require_inventario_admin
def create_entrega_directa():
    """Entrega directa de mostrador (solo inventario/admin).

    El almacenista surte material para un proyecto en el acto, sin solicitud
    previa del trabajador. Crea una SolicitudMaterial ya ENTREGADA + un
    movimiento SALIDA por línea, descontando del almacén origen. El solicitante
    real (trabajador o nombre libre) se guarda aparte del capturista, y el PDF
    lo muestra.

    Body: `{proyecto, proyecto_id?, solicitante_trabajador_id?,
             solicitante_nombre?, almacen_origen_id?, notas?, motivo?,
             detalles: [{producto_id, cantidad, estante_id?}]}`.

    Solo MATERIAL: no maneja herramientas (esas siguen por solicitud normal).
    No usa reservas: descuenta stock físico directo.
    """
    from app.models import Proyecto
    user = request.current_user

    data, err = _parse_or_422(EntregaDirectaSchema(), request.get_json(silent=True))
    if err: return err

    proyecto = (data.get('proyecto') or '').strip()
    if not proyecto:
        return jsonify({'detail': 'Debes seleccionar un proyecto'}), 422

    # Solicitante real: trabajador del sistema o nombre libre (al menos uno).
    trab_id = data.get('solicitante_trabajador_id')
    nombre_libre = (data.get('solicitante_nombre') or '').strip() or None
    trabajador = None
    if trab_id is not None:
        trabajador = Trabajador.query.filter(
            Trabajador.id == trab_id,
            Trabajador.activo == True,  # noqa: E712
        ).first()
        if not trabajador:
            return jsonify({'detail': f'Trabajador #{trab_id} no existe o está inactivo'}), 422
    if trabajador is None and not nombre_libre:
        return jsonify({
            'detail': 'Indica quién recibe el material: elige un trabajador o escribe un nombre.'
        }), 422

    # Resolver proyecto_id (igual que create_solicitud): explícito o por texto.
    proyecto_id = data.get('proyecto_id')
    if proyecto_id is not None:
        proy = Proyecto.query.filter(Proyecto.id == proyecto_id).first()
        if not proy:
            return jsonify({'detail': f'Proyecto #{proyecto_id} no existe'}), 422
    else:
        proy = Proyecto.query.filter(Proyecto.numero_proyecto == proyecto).first()
        proyecto_id = proy.id if proy else None

    # Almacén origen (explícito o default).
    almacen_id = data.get('almacen_origen_id') or _almacen_default_id()
    if not almacen_id:
        return jsonify({'detail': 'No hay bodegas registradas para descontar stock'}), 400
    almacen = Almacen.query.filter(Almacen.id == almacen_id, Almacen.activo == True).first()  # noqa: E712
    if not almacen:
        return jsonify({'detail': f'Almacén #{almacen_id} no existe o está inactivo'}), 404

    # Validar líneas: producto activo, regla de decimales por unidad, cantidad > 0.
    # Sumamos por producto (una línea por producto puede repetirse) para validar
    # y descontar stock una sola vez por producto.
    estante_por_producto: dict[int, int] = {}
    delta_por_producto: dict[int, Decimal] = {}
    productos_cache: dict[int, Producto] = {}
    errores = []
    for idx, det in enumerate(data['detalles']):
        pid = det['producto_id']
        prod = productos_cache.get(pid)
        if prod is None:
            prod = Producto.query.filter(Producto.id == pid, Producto.activo == True).first()  # noqa: E712
            if not prod:
                errores.append(f"Línea {idx+1}: producto #{pid} no existe o inactivo")
                continue
            productos_cache[pid] = prod
        cant = Decimal(str(det['cantidad']))
        if cant <= 0:
            errores.append(f"Línea {idx+1}: la cantidad debe ser mayor a 0")
            continue
        if not _unidad_permite_decimales(prod.unidad) and not _es_entero(cant):
            errores.append(
                f"Línea {idx+1}: '{prod.descripcion}' se entrega en cantidades enteras "
                f"(unidad: {prod.unidad or 'pieza'})"
            )
            continue
        delta_por_producto[pid] = delta_por_producto.get(pid, Decimal('0')) + cant
        # Si vienen varias líneas del mismo producto con distinto estante, nos
        # quedamos con el último indicado (best-effort para el sub-libro de celdas).
        if det.get('estante_id'):
            estante_por_producto[pid] = det['estante_id']

    if errores:
        return jsonify({'detail': errores}), 422
    if not delta_por_producto:
        return jsonify({'detail': 'Ninguna línea con cantidad mayor a 0'}), 422

    motivo_base = (data.get('motivo') or '').strip() or f'Entrega directa — {proyecto}'

    try:
        # Lock determinístico (id asc) sobre Producto + StockPorAlmacen, validar
        # stock en el almacén y descontar. Sin reservas: es entrega inmediata.
        for pid in sorted(delta_por_producto.keys()):
            cant_total = delta_por_producto[pid]
            producto = (
                Producto.query
                .with_for_update(nowait=True)
                .filter(Producto.id == pid)
                .first()
            )
            if not producto:
                db.session.rollback()
                return jsonify({'detail': f'Producto #{pid} no encontrado'}), 404
            productos_cache[pid] = producto

            # Consumo proyecto→general en el almacén de origen (feature stock por
            # proyecto): descuenta del bucket del proyecto de la entrega y el
            # remanente del general; nunca de otros proyectos.
            err = _consumir_proyecto_luego_general(pid, almacen_id, proyecto_id, cant_total)
            if err:
                db.session.rollback()
                return jsonify({
                    'detail': (
                        f'Stock insuficiente en {almacen.nombre} para {producto.codigo}: {err}.'
                    ),
                }), 409

        # Crear la solicitud ENTREGADA + sus líneas + las SALIDAs.
        nueva = SolicitudMaterial(
            solicitante_id=user.id,
            entrega_directa=True,
            solicitante_trabajador_id=(trabajador.id if trabajador else None),
            solicitante_nombre=(None if trabajador else nombre_libre),
            proyecto=proyecto,
            proyecto_id=proyecto_id,
            notas=data.get('notas'),
            estatus='ENTREGADA',
            fecha_cierre=datetime.datetime.now(),
            entregada_por_id=user.id,
        )
        db.session.add(nueva)
        db.session.flush()

        for pid in sorted(delta_por_producto.keys()):
            cant_total = delta_por_producto[pid]
            db.session.add(SolicitudMaterialDetalle(
                solicitud_id=nueva.id,
                tipo_item='MATERIAL',
                producto_id=pid,
                cantidad_solicitada=cant_total,
                cantidad_aprobada=cant_total,
                cantidad_entregada=cant_total,
            ))
            db.session.add(MovimientoInventario(
                tipo='SALIDA',
                producto_id=pid,
                cantidad=cant_total,
                almacen_origen_id=almacen_id,
                proyecto_origen_id=proyecto_id,
                motivo=motivo_base,
                usuario_id=user.id,
            ))

            # Descuento opcional del sub-libro de celdas (best-effort, clamp a 0).
            est_id = estante_por_producto.get(pid)
            if est_id:
                est = Estante.query.filter(Estante.id == est_id).first()
                if est and est.almacen_id == almacen_id:
                    pe = ProductoEstante.query.filter_by(
                        producto_id=pid, estante_id=est_id,
                    ).first()
                    if pe is not None:
                        nuevo_pe = Decimal(str(pe.cantidad or 0)) - cant_total
                        pe.cantidad = nuevo_pe if nuevo_pe > 0 else Decimal('0')

            _recalcular_caches(productos_cache[pid], almacen_id)

        quien = trabajador.nombre_completo if trabajador else nombre_libre
        _audit(
            user,
            f"Entrega directa #{nueva.id} → {quien} — proyecto: {proyecto} "
            f"({len(delta_por_producto)} productos, almacén #{almacen_id})",
        )
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        if _es_error_de_lock(exc):
            return jsonify({'detail': 'Stock bloqueado por otra operación, reintenta'}), 409
        raise

    db.session.refresh(nueva)
    _ = list(nueva.detalles)
    emit_to_role(_SOL_ROLES, 'solicitud:changed', {
        'id': nueva.id, 'action': 'entrega_directa',
    })
    emit_to_role(_INV_ROLES, 'movimiento:changed', {
        'origen': 'entrega_directa', 'solicitud_id': nueva.id,
    })
    return jsonify(_solicitud_to_dict(nueva))


@bp.route('/trabajadores-busqueda', methods=['GET'])
@_require_inventario_admin
def buscar_trabajadores_inventario():
    """Typeahead de trabajadores activos para la entrega directa (solo
    inventario/admin). Endpoint propio del módulo para no depender del scope
    de rol del módulo de Trabajadores. Devuelve id + nombre + nº empleado.

    Query params: q (texto), limit (1..50, default 20)."""
    from ._core import _int_arg
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
    from ._core import _int_arg
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


@bp.route('/solicitudes/<int:sol_id>/estado', methods=['PATCH'])
@_require_inventario_admin
def update_solicitud_estado(sol_id: int):
    """Cambia el estatus de una solicitud aplicando reservas de stock (Pausa 2-bis).

    Transiciones y efecto en stock_reservado:
      - PENDIENTE → APROBADA:  RESERVA (puede fallar 409 si no hay disponible).
      - APROBADA → RECHAZADA:  LIBERA reservas.
      - APROBADA → PENDIENTE:  LIBERA reservas (se re-aprobará después).
      - APROBADA → ENTREGADA:  LIBERA reservas. NO descuenta stock — la SALIDA
        real la registra el almacenista por separado en /movimientos. Cuando
        llegue Pausa 8b (entrega parcial), ese endpoint sí descuenta stock.
      - ENTREGADA → PENDIENTE: RE-RESERVA (puede fallar 409).
      - RECHAZADA → PENDIENTE: sin efecto (no había reserva).
      - PENDIENTE → RECHAZADA: sin efecto.
    """
    data, err = _parse_or_422(SolicitudUpdateEstadoSchema(), request.get_json(silent=True))
    if err: return err

    sol = (
        SolicitudMaterial.query
        .options(selectinload(SolicitudMaterial.detalles))
        .filter(SolicitudMaterial.id == sol_id)
        .first()
    )
    if not sol:
        return jsonify({'detail': 'Solicitud no encontrada'}), 404

    estado_previo = sol.estatus
    nuevo_estado = data['estatus']

    TRANSICIONES_VALIDAS = {
        'PENDIENTE':  {'APROBADA', 'RECHAZADA'},
        'APROBADA':   {'ENTREGADA', 'RECHAZADA', 'PENDIENTE'},
        'RECHAZADA':  {'PENDIENTE'},
        'ENTREGADA':  {'PENDIENTE'},
    }
    permitidas = TRANSICIONES_VALIDAS.get(estado_previo, set())
    if nuevo_estado != estado_previo and nuevo_estado not in permitidas:
        return jsonify({
            'detail': f"Transición inválida: {estado_previo} → {nuevo_estado}",
            'permitidas': sorted(permitidas),
        }), 409

    # Pausa 8b: al APROBAR, sembramos cantidad_aprobada = cantidad_solicitada en
    # cada línea MATERIAL que aún esté en 0 (default del modelo). Así la
    # reserva, la entrega parcial y el PATCH de detalle trabajan sobre un
    # campo explícito y dejamos de depender del fallback a cantidad_solicitada.
    if estado_previo == 'PENDIENTE' and nuevo_estado == 'APROBADA':
        for d in (sol.detalles or []):
            if d.tipo_item != 'MATERIAL' or not d.producto_id:
                continue
            if Decimal(str(d.cantidad_aprobada or 0)) == 0:
                d.cantidad_aprobada = Decimal(str(d.cantidad_solicitada or 0))

    # Reservas que esta solicitud "debería" tener apartadas.
    reservas = _reservas_de_solicitud(sol)

    try:
        # ── Aplicar efecto en stock_reservado según transición ──
        # La reserva/aprobación es GLOBAL por producto (como antes). El candado
        # por proyecto se aplica en la ENTREGA (consumo proyecto→general).
        if estado_previo == 'PENDIENTE' and nuevo_estado == 'APROBADA':
            errs = _intentar_reservar(reservas)
            if errs:
                db.session.rollback()
                return jsonify({
                    'detail': 'No se puede aprobar: stock insuficiente',
                    'errores': errs,
                }), 409

        elif estado_previo == 'ENTREGADA' and nuevo_estado == 'PENDIENTE':
            # Reabrir entregada: re-reservar. Si el stock ya se movió a otra
            # solicitud entre tanto, falla.
            errs = _intentar_reservar(reservas)
            if errs:
                db.session.rollback()
                return jsonify({
                    'detail': 'No se puede reabrir (entregada): stock ya no disponible',
                    'errores': errs,
                }), 409

        elif estado_previo == 'APROBADA' and nuevo_estado in ('RECHAZADA', 'PENDIENTE', 'ENTREGADA'):
            # Liberar lo que se había reservado al aprobar.
            _liberar_reservas(reservas)

        # Resto de transiciones (PENDIENTE↔RECHAZADA, RECHAZADA→PENDIENTE): sin efecto.

        sol.estatus = nuevo_estado
        if nuevo_estado == 'PENDIENTE':
            sol.fecha_cierre = None
        else:
            sol.fecha_cierre = datetime.datetime.now()

        # Trazabilidad de quién resolvió la solicitud.
        if nuevo_estado == 'APROBADA':
            sol.aprobada_por_id = request.current_user.id
            sol.entregada_por_id = None
        elif nuevo_estado == 'ENTREGADA':
            sol.entregada_por_id = request.current_user.id
        elif nuevo_estado in ('PENDIENTE', 'RECHAZADA'):
            # Al reabrir o rechazar deja de estar aprobada/entregada por alguien.
            sol.aprobada_por_id = None
            sol.entregada_por_id = None

        if estado_previo != nuevo_estado:
            _audit(request.current_user, f"Solicitud #{sol_id} estatus: {estado_previo} → {nuevo_estado}")

        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        if _es_error_de_lock(exc):
            return jsonify({'detail': 'Stock bloqueado por otra operación, reintenta'}), 409
        raise

    db.session.refresh(sol)
    _ = list(sol.detalles)
    if estado_previo != nuevo_estado:
        emit_to_role(_SOL_ROLES, 'solicitud:changed', {
            'id': sol.id, 'action': f'estado:{nuevo_estado}',
        })
    return jsonify(_solicitud_to_dict(sol))


# ─── Pausa 8b: edición de cantidad aprobada y entrega parcial ────────────────

@bp.route('/solicitudes/<int:sol_id>/detalles/<int:det_id>', methods=['PATCH'])
@_require_inventario_admin
def patch_solicitud_detalle(sol_id: int, det_id: int):
    """Edita `cantidad_aprobada` de una línea de solicitud APROBADA (Pausa 8b).

    Reglas:
      - Solicitud debe estar en APROBADA.
      - Línea debe ser MATERIAL con producto_id.
      - 0 ≤ cantidad_aprobada ≤ cantidad_solicitada.
      - cantidad_aprobada ≥ cantidad_entregada (no se aprueba menos de lo ya salido).
      - Ajusta `Producto.stock_reservado` por el delta:
          delta > 0 → intenta reservar (puede fallar 409 si no hay disponible).
          delta < 0 → libera.
    """
    data, err = _parse_or_422(SolicitudDetallePatchSchema(), request.get_json(silent=True))
    if err: return err

    det = (
        SolicitudMaterialDetalle.query
        .filter(
            SolicitudMaterialDetalle.id == det_id,
            SolicitudMaterialDetalle.solicitud_id == sol_id,
        )
        .first()
    )
    if not det:
        return jsonify({'detail': 'Detalle no encontrado'}), 404
    if (det.tipo_item or 'MATERIAL').upper() != 'MATERIAL' or not det.producto_id:
        return jsonify({'detail': 'Solo líneas de MATERIAL pueden editarse aquí'}), 422

    sol = SolicitudMaterial.query.filter(SolicitudMaterial.id == sol_id).first()
    if not sol:
        return jsonify({'detail': 'Solicitud no encontrada'}), 404
    if sol.estatus != 'APROBADA':
        return jsonify({
            'detail': f'Solo solicitudes APROBADAS permiten editar cantidad_aprobada (actual: {sol.estatus})'
        }), 409

    nueva_aprob = Decimal(str(data['cantidad_aprobada']))
    cant_sol = Decimal(str(det.cantidad_solicitada or 0))
    cant_ent = Decimal(str(det.cantidad_entregada or 0))
    cant_aprob_actual = Decimal(str(det.cantidad_aprobada or 0))

    unidad_mat = det.producto.unidad if det.producto else None
    if not _unidad_permite_decimales(unidad_mat) and not _es_entero(nueva_aprob):
        return jsonify({
            'detail': f'Este material se maneja en cantidades enteras (unidad: {unidad_mat or "pieza"})'
        }), 422

    if nueva_aprob > cant_sol:
        return jsonify({
            'detail': f'cantidad_aprobada ({nueva_aprob}) no puede exceder cantidad_solicitada ({cant_sol})'
        }), 422
    if nueva_aprob < cant_ent:
        return jsonify({
            'detail': f'cantidad_aprobada ({nueva_aprob}) no puede ser menor a cantidad_entregada ({cant_ent})'
        }), 422

    # Baseline para reserva previa: lo que el código antiguo asumía si cant_aprob=0.
    baseline_anterior = cant_aprob_actual if cant_aprob_actual > 0 else cant_sol
    reserva_anterior = baseline_anterior - cant_ent
    reserva_nueva = nueva_aprob - cant_ent
    delta = reserva_nueva - reserva_anterior

    try:
        if delta > 0:
            errs = _intentar_reservar({det.producto_id: delta})
            if errs:
                db.session.rollback()
                return jsonify({
                    'detail': 'No se puede aumentar la cantidad aprobada: stock insuficiente',
                    'errores': errs,
                }), 409
        elif delta < 0:
            _liberar_reservas({det.producto_id: -delta})

        det.cantidad_aprobada = nueva_aprob
        _audit(
            request.current_user,
            f"Solicitud #{sol_id} det #{det_id} cantidad_aprobada: {cant_aprob_actual} → {nueva_aprob}",
        )
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        if _es_error_de_lock(exc):
            return jsonify({'detail': 'Stock bloqueado por otra operación, reintenta'}), 409
        raise

    db.session.refresh(det)
    emit_to_role(_SOL_ROLES, 'solicitud:changed', {
        'id': sol_id, 'detalle_id': det_id, 'action': 'detalle_updated',
    })
    return jsonify(_solicitud_detalle_to_dict(det))


@bp.route('/solicitudes/<int:sol_id>/entregar', methods=['POST'])
@limiter.limit(
    "20/minute",
    key_func=lambda: f"ip:{get_real_client_ip_flask()}",
)
@_require_inventario_admin
def entregar_solicitud(sol_id: int):
    """Entrega total o parcial de una solicitud APROBADA (Pausa 8b).

    Body: `{ almacen_origen_id?, motivo?, entregas: [{detalle_id, cantidad_entregada}, ...] }`.

    Crea SALIDA por cada línea con cantidad > 0, libera la porción de
    `stock_reservado` correspondiente y descuenta el stock físico del almacén.

    Si tras la entrega todas las líneas MATERIAL tienen
    cantidad_entregada == cantidad_aprobada, la solicitud queda ENTREGADA.
    En caso contrario sigue APROBADA (entrega parcial).

    Reglas de validación:
      - sol.estatus debe ser 'APROBADA'.
      - Cada detalle debe pertenecer a la solicitud y ser MATERIAL.
      - cantidad_entregada por línea ≤ (cantidad_aprobada − cantidad_entregada actual).
      - Stock en el almacén origen ≥ suma de entregas por producto.
      - Lock con `with_for_update(nowait=True)` en orden determinístico (id asc).
    """
    data, err = _parse_or_422(EntregarSolicitudSchema(), request.get_json(silent=True))
    if err: return err

    sol = (
        SolicitudMaterial.query
        .options(selectinload(SolicitudMaterial.detalles))
        .filter(SolicitudMaterial.id == sol_id)
        .first()
    )
    if not sol:
        return jsonify({'detail': 'Solicitud no encontrada'}), 404
    if sol.estatus != 'APROBADA':
        return jsonify({
            'detail': f'Solo solicitudes APROBADAS pueden entregarse (actual: {sol.estatus})'
        }), 409

    detalles_por_id = {d.id: d for d in (sol.detalles or [])}
    # Pausa 11: de qué estante (celda) surte cada línea, si el almacenista lo eligió.
    estante_por_detalle = {item['detalle_id']: item.get('estante_id') for item in data['entregas']}
    vistos: set[int] = set()
    # MATERIAL: (det, delta, baseline). HERRAMIENTA: (det, delta_int, baseline_int).
    entregas_material: list[tuple[SolicitudMaterialDetalle, Decimal, Decimal]] = []
    entregas_herramienta: list[tuple[SolicitudMaterialDetalle, int, int]] = []

    for item in data['entregas']:
        det_id = item['detalle_id']
        if det_id in vistos:
            return jsonify({'detail': f'Detalle #{det_id} duplicado en el payload'}), 422
        vistos.add(det_id)

        det = detalles_por_id.get(det_id)
        if not det:
            return jsonify({'detail': f'Detalle #{det_id} no pertenece a la solicitud #{sol_id}'}), 422

        delta = Decimal(str(item['cantidad_entregada']))
        if delta < 0:
            return jsonify({'detail': f'Detalle #{det_id}: cantidad_entregada no puede ser negativa'}), 422
        if delta == 0:
            continue  # el front puede mandar 0 para "no entregar esta línea ahora"

        tipo = (det.tipo_item or 'MATERIAL').upper()

        if tipo == 'HERRAMIENTA':
            if not det.herramienta_id:
                return jsonify({
                    'detail': f'Detalle #{det_id}: línea HERRAMIENTA sin herramienta_id',
                }), 422
            # Herramientas son enteras (1 unidad física = 1 asignación).
            if delta != delta.to_integral_value():
                return jsonify({
                    'detail': f'Detalle #{det_id}: cantidad de herramientas debe ser entera',
                }), 422
            cant_aprob_h = int(det.cantidad_aprobada or 0)
            cant_ent_h = int(det.cantidad_entregada or 0)
            baseline_h = cant_aprob_h if cant_aprob_h > 0 else int(det.cantidad_solicitada or 0)
            pendiente_h = baseline_h - cant_ent_h
            delta_int = int(delta)
            if delta_int > pendiente_h:
                return jsonify({
                    'detail': (
                        f'Detalle #{det_id}: cantidad_entregada ({delta_int}) excede el '
                        f'pendiente ({pendiente_h}). Aprobada: {baseline_h}, '
                        f'ya entregada: {cant_ent_h}.'
                    ),
                }), 422
            entregas_herramienta.append((det, delta_int, baseline_h))
            continue

        # MATERIAL
        if not det.producto_id:
            return jsonify({
                'detail': f'Detalle #{det_id}: línea MATERIAL sin producto_id',
            }), 422

        unidad_mat = det.producto.unidad if det.producto else None
        if not _unidad_permite_decimales(unidad_mat) and not _es_entero(delta):
            return jsonify({
                'detail': f'Detalle #{det_id}: este material se entrega en cantidades enteras '
                          f'(unidad: {unidad_mat or "pieza"})',
            }), 422

        cant_aprob = Decimal(str(det.cantidad_aprobada or 0))
        cant_ent_actual = Decimal(str(det.cantidad_entregada or 0))
        # Compat con solicitudes pre-8b que aprobaron sin sembrar cantidad_aprobada.
        baseline = cant_aprob if cant_aprob > 0 else Decimal(str(det.cantidad_solicitada or 0))
        pendiente = baseline - cant_ent_actual
        if delta > pendiente:
            return jsonify({
                'detail': (
                    f'Detalle #{det_id}: cantidad_entregada ({delta}) excede el pendiente '
                    f'({pendiente}). Aprobada: {baseline}, ya entregada: {cant_ent_actual}.'
                ),
            }), 422

        entregas_material.append((det, delta, baseline))

    if not entregas_material and not entregas_herramienta:
        return jsonify({'detail': 'Ninguna línea con cantidad mayor a 0 para entregar'}), 422

    # Si hay líneas HERRAMIENTA, el solicitante DEBE tener un trabajador
    # asociado: la asignación se hace al Trabajador, no al User.
    trab_solicitante = None
    if entregas_herramienta:
        if not sol.solicitante or not sol.solicitante.trabajador_id:
            return jsonify({
                'detail': (
                    'El solicitante no tiene un trabajador asociado. '
                    'Liga la cuenta a un trabajador desde Usuarios para poder '
                    'entregar las herramientas, o asígnalas manualmente desde '
                    'Asignaciones de Herramienta.'
                ),
            }), 400
        trab_solicitante = Trabajador.query.filter(
            Trabajador.id == sol.solicitante.trabajador_id,
            Trabajador.activo == True,  # noqa: E712
        ).first()
        if not trab_solicitante:
            return jsonify({
                'detail': (
                    f'El trabajador #{sol.solicitante.trabajador_id} asociado al '
                    f'solicitante no existe o está inactivo.'
                ),
            }), 400

    # Resolver almacén (solo necesario si hay líneas MATERIAL). Fallback al
    # default. Si solo hay HERRAMIENTAS no exigimos bodega.
    almacen_id = None
    if entregas_material:
        almacen_id = data.get('almacen_origen_id') or _almacen_default_id()
        if not almacen_id:
            return jsonify({'detail': 'No hay bodegas registradas para descontar stock'}), 400
        almacen = Almacen.query.filter(Almacen.id == almacen_id, Almacen.activo == True).first()  # noqa: E712
        if not almacen:
            return jsonify({'detail': f'Almacén #{almacen_id} no existe o está inactivo'}), 404

    # Sumar deltas por producto para validar stock una sola vez por producto.
    delta_por_producto: dict[int, Decimal] = {}
    for det, delta, _ in entregas_material:
        delta_por_producto[det.producto_id] = (
            delta_por_producto.get(det.producto_id, Decimal('0')) + delta
        )

    # Sumar unidades por herramienta para validar disponibilidad una sola vez.
    delta_por_herramienta: dict[int, int] = {}
    for det, delta_int, _ in entregas_herramienta:
        delta_por_herramienta[det.herramienta_id] = (
            delta_por_herramienta.get(det.herramienta_id, 0) + delta_int
        )

    user = request.current_user
    motivo_base = (data.get('motivo') or '').strip() or f'Entrega solicitud #{sol_id}'
    fecha_dev_prevista = data.get('fecha_devolucion_prevista')

    try:
        # Lock determinístico (id asc) sobre Producto + StockPorAlmacen.
        productos_locked: dict[int, Producto] = {}
        for prod_id in sorted(delta_por_producto.keys()):
            producto = (
                Producto.query
                .with_for_update(nowait=True)
                .filter(Producto.id == prod_id)
                .first()
            )
            if not producto:
                db.session.rollback()
                return jsonify({'detail': f'Producto #{prod_id} no encontrado'}), 404
            productos_locked[prod_id] = producto

            cant_total = delta_por_producto[prod_id]
            # Consumo proyecto→general en el almacén de origen (feature stock por
            # proyecto): descuenta primero del bucket del proyecto de la solicitud
            # y el remanente del general; nunca de otros proyectos.
            err = _consumir_proyecto_luego_general(prod_id, almacen_id, sol.proyecto_id, cant_total)
            if err:
                db.session.rollback()
                return jsonify({
                    'detail': (
                        f'Stock insuficiente en bodega #{almacen_id} para {producto.codigo}: {err}.'
                    ),
                }), 409

            # Liberar la reserva equivalente (cache global; clamp a 0 por seguridad).
            reservado = Decimal(str(producto.stock_reservado or 0))
            nuevo_res = reservado - cant_total
            producto.stock_reservado = nuevo_res if nuevo_res > 0 else Decimal('0')

        # Reservar unidades DISPONIBLES por herramienta (id asc anti-deadlock).
        # Tomamos exactamente `cant_total` unidades por herramienta — si no hay
        # suficientes, rollback con 409.
        unidades_por_herramienta: dict[int, list[HerramientaUnidad]] = {}
        for h_id in sorted(delta_por_herramienta.keys()):
            cant_unidades = delta_por_herramienta[h_id]
            unidades = (
                HerramientaUnidad.query
                .with_for_update(nowait=True)
                .filter(
                    HerramientaUnidad.herramienta_id == h_id,
                    HerramientaUnidad.estado == 'DISPONIBLE',
                )
                .order_by(HerramientaUnidad.id.asc())
                .limit(cant_unidades)
                .all()
            )
            if len(unidades) < cant_unidades:
                db.session.rollback()
                herr = Herramienta.query.get(h_id)
                nombre = herr.descripcion if herr else f'#{h_id}'
                return jsonify({
                    'detail': (
                        f'No hay unidades suficientes DISPONIBLES de "{nombre}": '
                        f'requiere {cant_unidades}, disponibles {len(unidades)}.'
                    ),
                }), 409
            unidades_por_herramienta[h_id] = unidades

        # Aplicar deltas por línea MATERIAL + registrar movimientos SALIDA.
        for det, delta, baseline in entregas_material:
            # Si la línea era pre-8b (cant_aprob=0), formalizar la aprobación con
            # el baseline para que la lógica de completa/reservas sea consistente.
            if Decimal(str(det.cantidad_aprobada or 0)) == 0 and baseline > 0:
                det.cantidad_aprobada = baseline

            det.cantidad_entregada = Decimal(str(det.cantidad_entregada or 0)) + delta

            mov = MovimientoInventario(
                tipo='SALIDA',
                producto_id=det.producto_id,
                cantidad=delta,
                almacen_origen_id=almacen_id,
                proyecto_origen_id=sol.proyecto_id,
                motivo=motivo_base,
                usuario_id=user.id,
            )
            db.session.add(mov)

            # Pausa 11 — descuento opcional de la celda elegida. Best-effort:
            # el stock autoritativo ya bajó de StockPorAlmacen arriba; aquí solo
            # mantenemos el sub-libro de celdas al día. Clamp a 0; si el estante
            # no pertenece al almacén de origen o no hay celda, se ignora.
            est_id = estante_por_detalle.get(det.id)
            if est_id:
                est = Estante.query.filter(Estante.id == est_id).first()
                if est and est.almacen_id == almacen_id:
                    pe = ProductoEstante.query.filter_by(
                        producto_id=det.producto_id, estante_id=est_id,
                    ).first()
                    if pe is not None:
                        nuevo = Decimal(str(pe.cantidad or 0)) - delta
                        pe.cantidad = nuevo if nuevo > 0 else Decimal('0')

        # Refrescar caches (stock_por_almacen + stock_actual) del almacén de
        # origen por cada producto tocado.
        for producto in productos_locked.values():
            _recalcular_caches(producto, almacen_id)

        # Aplicar deltas por línea HERRAMIENTA: crear N asignaciones al
        # trabajador del solicitante, marcar unidades como ASIGNADA y registrar
        # evento de cambio de estado.
        asignaciones_creadas = 0
        for det, delta_int, baseline_h in entregas_herramienta:
            if int(det.cantidad_aprobada or 0) == 0 and baseline_h > 0:
                det.cantidad_aprobada = baseline_h

            # Consumimos del pool de unidades reservado arriba para esta herramienta.
            pool = unidades_por_herramienta[det.herramienta_id]
            for _ in range(delta_int):
                unidad = pool.pop(0)
                estado_anterior = unidad.estado
                asig = AsignacionHerramienta(
                    unidad_id=unidad.id,
                    trabajador_id=trab_solicitante.id,
                    solicitud_id=sol.id,
                    proyecto=(sol.proyecto or None),
                    fecha_entrega=datetime.datetime.utcnow(),
                    fecha_devolucion_prevista=fecha_dev_prevista,
                    estado='ACTIVA',
                    condicion_entrega='BUENA',
                    observaciones_entrega=motivo_base,
                    entregado_por_id=user.id,
                )
                db.session.add(asig)
                db.session.flush()  # para tener asig.id en el evento

                unidad.estado = 'ASIGNADA'
                unidad.asignado_trabajador_id = trab_solicitante.id
                crear_evento_herramienta(
                    unidad, 'ASIGNACION', user,
                    observaciones=(
                        f'Entrega solicitud #{sol_id} → '
                        f'{trab_solicitante.nombre_completo}'
                    ),
                    estado_anterior=estado_anterior, estado_nuevo='ASIGNADA',
                    referencia_id=asig.id, referencia_tipo='asignacion',
                )
                asignaciones_creadas += 1

            det.cantidad_entregada = int(det.cantidad_entregada or 0) + delta_int

        # ¿Quedó completa? Ahora considera AMBOS tipos: cada línea debe tener
        # entregada ≥ aprobada (cuando aprobada > 0).
        completa = True
        for d in (sol.detalles or []):
            aprob = Decimal(str(d.cantidad_aprobada or 0))
            ent = Decimal(str(d.cantidad_entregada or 0))
            if aprob > 0 and ent < aprob:
                completa = False
                break

        if completa:
            sol.estatus = 'ENTREGADA'
            sol.fecha_cierre = datetime.datetime.now()
            sol.entregada_por_id = user.id
            # Por seguridad liberamos cualquier reserva sobrante (debería ser 0).
            restos = _reservas_de_solicitud(sol)
            if restos:
                _liberar_reservas(restos)

        total_lineas = len(entregas_material) + len(entregas_herramienta)
        _audit(
            user,
            (
                f"Solicitud #{sol_id} {'ENTREGADA' if completa else 'entrega parcial'} "
                f"({total_lineas} líneas: {len(entregas_material)} mat, "
                f"{asignaciones_creadas} herr asignadas"
                f"{f', almacén #{almacen_id}' if almacen_id else ''})"
            ),
        )
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        if _es_error_de_lock(exc):
            return jsonify({'detail': 'Stock bloqueado por otra operación, reintenta'}), 409
        raise

    db.session.refresh(sol)
    _ = list(sol.detalles)
    emit_to_role(_SOL_ROLES, 'solicitud:changed', {
        'id': sol.id, 'action': 'entregada' if sol.estatus == 'ENTREGADA' else 'entrega_parcial',
    })
    # Una entrega real genera SALIDAs de material → notificar también que
    # cambió stock para que el catalogo/bajo-minimo refresquen.
    if entregas_material:
        emit_to_role(_INV_ROLES, 'movimiento:changed', {
            'origen': 'solicitud_entrega', 'solicitud_id': sol.id,
        })
    return jsonify(_solicitud_to_dict(sol))


# ─── PDF de solicitudes ──────────────────────────────────────────────────────

def _render_solicitud_pdf(*, folio, fecha_str, solicitante, proyecto, notas, materiales, herramientas,
                          estatus=None, estado_label=None, mostrar_entrega=False):
    """Helper común: ya recibe los dicts normalizados y devuelve BytesIO con el PDF.

    `mostrar_entrega`/`estado_label` solo se pasan al imprimir una solicitud ya
    guardada (muestra columnas Aprobada/Entregada/Pendiente y el estado, p. ej.
    entrega parcial). El preview del carrito los omite (default off)."""
    try:
        from xhtml2pdf import pisa
    except ImportError:
        return None
    # API-only: `static_folder=None`. Resolvemos el logo contra BASE_DIR.
    base_dir = current_app.config.get('BASE_DIR') or os.path.dirname(current_app.root_path)
    logo_path = os.path.join(base_dir, 'static', 'imagenes', 'skilled (1).png')
    if not os.path.exists(logo_path):
        logo_path = None
    html_salida = render_template(
        'solicitud_pedido_pdf.html',
        folio=folio,
        fecha=fecha_str,
        solicitante=solicitante,
        proyecto=proyecto,
        notas=notas,
        materiales=materiales,
        herramientas=herramientas,
        estatus=estatus,
        estado_label=estado_label,
        mostrar_entrega=mostrar_entrega,
        logo_path=logo_path if os.path.exists(logo_path) else None,
    )
    buf = io.BytesIO()
    status = pisa.CreatePDF(io.BytesIO(html_salida.encode('utf-8')), dest=buf)
    if status.err:
        return None
    buf.seek(0)
    return buf


@bp.route('/solicitudes/<int:sol_id>/pdf', methods=['GET'])
@_require_login
def imprimir_solicitud(sol_id: int):
    """Genera el PDF de una solicitud ya guardada. Solicitante solo puede
    imprimir las suyas; inventario/admin/super_admin pueden imprimir todas."""
    user = request.current_user
    sol = (
        SolicitudMaterial.query
        .options(
            joinedload(SolicitudMaterial.solicitante),
            selectinload(SolicitudMaterial.detalles).joinedload(SolicitudMaterialDetalle.producto),
            selectinload(SolicitudMaterial.detalles).joinedload(SolicitudMaterialDetalle.herramienta),
        )
        .filter(SolicitudMaterial.id == sol_id).first()
    )
    if not sol:
        return jsonify({'detail': 'Solicitud no encontrada'}), 404

    # AuthZ: solicitante y coordinador solo pueden imprimir las suyas.
    if user.role in ('solicitante_material', 'coordinador') and sol.solicitante_id != user.id:
        return jsonify({'detail': 'Forbidden'}), 403
    if user.role not in ('solicitante_material', 'coordinador', 'inventario', 'admin', 'super_admin'):
        return jsonify({'detail': 'Forbidden'}), 403

    # Las columnas Aprobada/Entregada/Pendiente solo tienen sentido una vez que la
    # solicitud fue aprobada o entregada (en PENDIENTE/RECHAZADA aún no hay nada).
    mostrar_entrega = sol.estatus in ('APROBADA', 'ENTREGADA')

    def _q(v):
        v = float(v or 0)
        return int(v) if v % 1 == 0 else round(v, 2)

    hay_entregas = False
    hay_pendiente = False
    materiales, herramientas = [], []
    for d in sol.detalles:
        tipo = (d.tipo_item or 'MATERIAL').upper()
        sol_c = float(d.cantidad_solicitada or 0)
        aprob_c = float(d.cantidad_aprobada or 0)
        ent_c = float(d.cantidad_entregada or 0)
        baseline = aprob_c if aprob_c > 0 else sol_c
        pend_c = max(0.0, baseline - ent_c)
        if ent_c > 0:
            hay_entregas = True
        if mostrar_entrega and pend_c > 0:
            hay_pendiente = True
        entrega = {
            'aprobada': _q(aprob_c),
            'entregada': _q(ent_c),
            'pendiente': _q(pend_c),
        }
        if tipo == 'HERRAMIENTA':
            herramientas.append({
                'descripcion': (d.herramienta.descripcion if d.herramienta else 'Herramienta eliminada')[:250],
                'sku': (d.herramienta.sku if d.herramienta else '---')[:50],
                'cantidad': _q(sol_c),
                'fecha_uso_inicio': d.fecha_uso_inicio.isoformat() if d.fecha_uso_inicio else '',
                'fecha_uso_fin': d.fecha_uso_fin.isoformat() if d.fecha_uso_fin else '',
                'justificacion': (d.justificacion or '')[:2000],
                'complementos': (d.complementos or '')[:500],
                **entrega,
            })
        else:
            materiales.append({
                'descripcion': (d.producto.descripcion if d.producto else 'Producto eliminado')[:250],
                'codigo': (d.producto.codigo if d.producto else '---')[:50],
                'categoria': (d.producto.categoria if d.producto else '')[:100],
                'unidad': (d.producto.unidad if d.producto else '')[:50],
                'cable_tipo': (d.producto.cable_tipo if d.producto else '') or '',
                'cable_calibre': (d.producto.cable_calibre if d.producto else '') or '',
                'cantidad': _q(sol_c),
                'justificacion': (d.justificacion or '')[:2000],
                **entrega,
            })

    # Etiqueta de estado legible (incluye el caso de entrega parcial, que antes el
    # PDF no reflejaba en absoluto).
    if sol.estatus == 'ENTREGADA' and sol.entrega_directa:
        estado_label = 'ENTREGA DIRECTA — surtido en mostrador'
    elif sol.estatus == 'ENTREGADA':
        estado_label = 'ENTREGADA — surtido completo'
    elif sol.estatus == 'APROBADA' and hay_entregas and hay_pendiente:
        estado_label = 'ENTREGA PARCIAL — faltan piezas por surtir'
    elif sol.estatus == 'APROBADA' and hay_entregas:
        estado_label = 'APROBADA — entrega parcial registrada'
    elif sol.estatus == 'APROBADA':
        estado_label = 'APROBADA — pendiente de entrega'
    elif sol.estatus == 'PENDIENTE':
        estado_label = 'PENDIENTE de aprobación'
    elif sol.estatus == 'RECHAZADA':
        estado_label = 'RECHAZADA'
    else:
        estado_label = sol.estatus

    folio = f'SOL-{sol.id:06d}'
    fecha_str = sol.fecha_creacion.strftime('%d/%m/%Y %H:%M') if sol.fecha_creacion else ''
    # Nombre del solicitante REAL (trabajador / texto libre en entregas directas;
    # el capturista en solicitudes normales).
    solicitante = sol.solicitante_display

    pdf = _render_solicitud_pdf(
        folio=folio, fecha_str=fecha_str, solicitante=solicitante,
        proyecto=sol.proyecto or '', notas=sol.notas or '',
        materiales=materiales, herramientas=herramientas,
        estatus=sol.estatus, estado_label=estado_label, mostrar_entrega=mostrar_entrega,
    )
    if pdf is None:
        return jsonify({'detail': 'Error al generar el PDF'}), 500

    return send_file(pdf, mimetype='application/pdf', as_attachment=False, download_name=f'{folio}.pdf')


# ─── PDF preview de solicitud (sin persistir) ────────────────────────────────

@bp.route('/solicitudes/preview-pdf', methods=['POST'])
@limiter.limit('10/minute', key_func=lambda: f"ip:{get_real_client_ip_flask()}")
@_require_login
def preview_solicitud_pdf():
    """Genera un PDF a partir del carrito actual del usuario, SIN guardar la
    solicitud. Sirve para que el solicitante pueda imprimir/firmar antes de
    enviar al almacén. Mismo mecanismo que prenómina: xhtml2pdf → send_file.
    """
    user = request.current_user
    if user.role not in ('solicitante_material', 'coordinador', 'inventario', 'admin', 'super_admin'):
        return jsonify({'detail': 'Forbidden'}), 403

    payload = request.get_json(silent=True) or {}
    materiales_raw = payload.get('materiales') or []
    herramientas_raw = payload.get('herramientas') or []

    if not materiales_raw and not herramientas_raw:
        return jsonify({'detail': 'Agrega al menos un material o herramienta'}), 422
    if len(materiales_raw) > 500 or len(herramientas_raw) > 500:
        return jsonify({'detail': 'Demasiados ítems en una sola solicitud'}), 422

    # Normalizar / sanitizar (xhtml2pdf escapa automáticamente vía Jinja autoescape)
    def _clean(s, maxlen=500):
        return (str(s or '')[:maxlen]).strip()

    materiales = []
    for m in materiales_raw:
        try:
            cantidad = float(m.get('cantidad') or 0)
        except (TypeError, ValueError):
            cantidad = 0
        materiales.append({
            'descripcion': _clean(m.get('descripcion'), 250),
            'codigo': _clean(m.get('codigo'), 50),
            'categoria': _clean(m.get('categoria'), 100),
            'unidad': _clean(m.get('unidad'), 50),
            'cable_tipo': _clean(m.get('cable_tipo'), 60),
            'cable_calibre': _clean(m.get('cable_calibre'), 40),
            'cantidad': cantidad if cantidad % 1 else int(cantidad),
            'justificacion': _clean(m.get('justificacion'), 2000),
        })

    herramientas = []
    for h in herramientas_raw:
        try:
            cantidad = int(float(h.get('cantidad') or 0))
        except (TypeError, ValueError):
            cantidad = 0
        herramientas.append({
            'descripcion': _clean(h.get('descripcion'), 250),
            'sku': _clean(h.get('sku'), 50),
            'cantidad': cantidad,
            'fecha_uso_inicio': _clean(h.get('fecha_uso_inicio'), 20),
            'fecha_uso_fin': _clean(h.get('fecha_uso_fin'), 20),
            'justificacion': _clean(h.get('justificacion'), 2000),
            'complementos': _clean(h.get('complementos'), 500),
        })

    proyecto = _clean(payload.get('proyecto'), 200)
    notas = _clean(payload.get('notas'), 2000)
    folio = 'SOL-' + datetime.datetime.now().strftime('%y%m%d%H%M%S')
    fecha_str = datetime.datetime.now().strftime('%d/%m/%Y %H:%M')
    solicitante = user.full_name or user.username

    pdf = _render_solicitud_pdf(
        folio=folio, fecha_str=fecha_str, solicitante=solicitante,
        proyecto=proyecto, notas=notas,
        materiales=materiales, herramientas=herramientas,
    )
    if pdf is None:
        return jsonify({'detail': 'Error al generar el PDF (xhtml2pdf no disponible)'}), 500

    _audit(user, f"Vista previa PDF solicitud ({len(materiales)} mat, {len(herramientas)} herr)")
    db.session.commit()

    return send_file(pdf, mimetype='application/pdf', as_attachment=False, download_name=f'{folio}.pdf')
