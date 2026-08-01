"""Schemas Marshmallow de entrada del módulo de Inventario.

Solo validación de forma (tipos, rangos, longitudes). Las reglas que dependen
de otros campos o de la base (p. ej. que un producto de cable traiga tipo y
calibre, o que el almacén exista) se validan en la vista.
"""
from marshmallow import EXCLUDE, Schema, fields, pre_load, validate

CODIGO_REGEX = r'^[A-Za-z0-9\-_\.\/]+$'

# Anti-SSRF/phishing: cuando guardamos URLs de imagen (categorías, productos) la
# UI las pinta como <img src=...>. Si dejamos cualquier URL, un admin malicioso
# podría meter `javascript:`, `data:text/html`, URLs a otros dominios para
# tracking pixels, o intranet (SSRF si el browser corre detrás de un proxy).
# Forzamos HTTPS + dominios públicos o paths relativos al propio backend.
_IMAGEN_URL_REGEX = r'^(?:https://[A-Za-z0-9.\-_]+(?::\d+)?(?:/[^\s<>"\']*)?|/[A-Za-z0-9.\-_/]+\.(?:png|jpe?g|webp|gif|svg))$'

_IMAGEN_URL_ERROR = 'imagen_url debe ser HTTPS o un path absoluto a imagen local'


def _campo_imagen_url():
    """Campo `imagen_url` opcional con las validaciones anti-XSS/SSRF. Fábrica
    (no constante) porque un `fields.Str` no puede compartirse entre schemas."""
    return fields.Str(load_default=None, allow_none=True, validate=[
        validate.Length(max=500),
        validate.Regexp(_IMAGEN_URL_REGEX, error=_IMAGEN_URL_ERROR),
    ])


class _BaseSchema(Schema):
    class Meta:
        # Coincide con el comportamiento por defecto de Pydantic: ignora campos extra.
        unknown = EXCLUDE

    @pre_load
    def _blank_imagen_url_to_none(self, data, **kwargs):
        # Un `imagen_url` vacío ('') significa "sin imagen": la UI lo manda así
        # cuando dejas el campo opcional en blanco. Sin esto, el Regexp de
        # seguridad lo rechaza (`allow_none` solo cubre null, no ''), y editar un
        # producto sin imagen falla con 422 como si la imagen fuera obligatoria.
        if isinstance(data, dict):
            v = data.get('imagen_url')
            if isinstance(v, str) and not v.strip():
                data = {**data, 'imagen_url': None}
        return data


# ─── Productos ───────────────────────────────────────────────────────────────

class ProductoCreateSchema(_BaseSchema):
    codigo = fields.Str(required=True, validate=[
        validate.Length(min=1, max=50),
        validate.Regexp(CODIGO_REGEX),
    ])
    descripcion = fields.Str(required=True, validate=validate.Length(min=1, max=250))
    categoria = fields.Str(required=True, validate=validate.Length(min=1, max=100))
    # Marca / fabricante (opcional, independiente del proveedor).
    marca = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=100))
    unidad = fields.Str(required=True, validate=validate.Length(min=1, max=50))
    # Atributos de cable (obligatorios SOLO cuando la categoría es cable — se
    # valida en la ruta, no aquí, porque depende del valor de `categoria`).
    cable_tipo = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=60))
    cable_calibre = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=40))
    stock_actual = fields.Float(load_default=0.0, validate=validate.Range(min=0, max=1_000_000))
    stock_minimo = fields.Float(load_default=0.0, validate=validate.Range(min=0, max=1_000_000))
    precio_unitario = fields.Float(load_default=0.0, validate=validate.Range(min=0, max=100_000_000))
    # Destino del stock inicial (feature stock por proyecto). Opcionales: si no
    # vienen, el stock inicial cae en el almacén default y bucket general (compat).
    stock_inicial_almacen_id = fields.Int(load_default=None, allow_none=True)
    stock_inicial_proyecto_id = fields.Int(load_default=None, allow_none=True)
    imagen_url = _campo_imagen_url()
    # Pausa 9: proveedor default (opcional al crear)
    proveedor_default_nombre = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=150))
    proveedor_default_contacto = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=150))


class ProductoUpdateSchema(_BaseSchema):
    codigo = fields.Str(load_default=None, allow_none=True, validate=[
        validate.Length(min=1, max=50),
        validate.Regexp(CODIGO_REGEX),
    ])
    descripcion = fields.Str(load_default=None, allow_none=True, validate=validate.Length(min=1, max=250))
    categoria = fields.Str(load_default=None, allow_none=True, validate=validate.Length(min=1, max=100))
    marca = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=100))
    unidad = fields.Str(load_default=None, allow_none=True, validate=validate.Length(min=1, max=50))
    cable_tipo = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=60))
    cable_calibre = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=40))
    stock_actual = fields.Float(load_default=None, allow_none=True, validate=validate.Range(min=0, max=1_000_000))
    stock_minimo = fields.Float(load_default=None, allow_none=True, validate=validate.Range(min=0, max=1_000_000))
    precio_unitario = fields.Float(load_default=None, allow_none=True, validate=validate.Range(min=0, max=100_000_000))
    imagen_url = _campo_imagen_url()
    proveedor_default_nombre = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=150))
    proveedor_default_contacto = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=150))


# ─── Almacenes y estantes ────────────────────────────────────────────────────

class AlmacenCreateSchema(_BaseSchema):
    nombre = fields.Str(required=True, validate=validate.Length(min=1, max=100))
    ubicacion = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=250))
    activo = fields.Bool(load_default=True)


class AlmacenUpdateSchema(_BaseSchema):
    nombre = fields.Str(load_default=None, allow_none=True, validate=validate.Length(min=1, max=100))
    ubicacion = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=250))
    activo = fields.Bool(load_default=None, allow_none=True)


class EstanteCreateSchema(_BaseSchema):
    nombre = fields.Str(required=True, validate=validate.Length(min=1, max=100))
    descripcion = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=250))
    almacen_id = fields.Int(required=True)
    # Pausa 11 — rejilla física. 1×1 = comportamiento plano previo.
    filas = fields.Int(load_default=1, validate=validate.Range(min=1, max=50))
    columnas = fields.Int(load_default=1, validate=validate.Range(min=1, max=50))


class EstanteUpdateSchema(_BaseSchema):
    nombre = fields.Str(load_default=None, allow_none=True, validate=validate.Length(min=1, max=100))
    descripcion = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=250))
    almacen_id = fields.Int(load_default=None, allow_none=True)
    filas = fields.Int(load_default=None, allow_none=True, validate=validate.Range(min=1, max=50))
    columnas = fields.Int(load_default=None, allow_none=True, validate=validate.Range(min=1, max=50))


class EstanteLayoutItemSchema(_BaseSchema):
    """Una colocación de producto en la rejilla del estante.
    fila/columna ambos None = asignado pero sin ubicar (no en una celda)."""
    producto_id = fields.Int(required=True)
    fila = fields.Int(load_default=None, allow_none=True, validate=validate.Range(min=1, max=50))
    columna = fields.Int(load_default=None, allow_none=True, validate=validate.Range(min=1, max=50))
    cantidad = fields.Float(load_default=0.0, validate=validate.Range(min=0, max=1_000_000))


class EstanteLayoutSchema(_BaseSchema):
    posiciones = fields.List(
        fields.Nested(EstanteLayoutItemSchema),
        required=True,
        validate=validate.Length(max=500),
    )


# ─── Movimientos y ajustes de bucket ─────────────────────────────────────────

class MovimientoCreateSchema(_BaseSchema):
    tipo = fields.Str(required=True, validate=validate.OneOf(['ENTRADA', 'SALIDA', 'AJUSTE', 'TRASPASO', 'REASIGNACION']))
    producto_id = fields.Int(required=True)
    cantidad = fields.Float(required=True, validate=validate.Range(min=-100_000, max=100_000))
    almacen_origen_id = fields.Int(load_default=None, allow_none=True)
    almacen_destino_id = fields.Int(load_default=None, allow_none=True)
    estante_id = fields.Int(load_default=None, allow_none=True)
    # Bucket de proyecto del movimiento (None = general/sin proyecto):
    #   ENTRADA / AJUSTE+ → proyecto destino    SALIDA / AJUSTE− → proyecto origen
    #   TRASPASO          → bucket que se mueve entre almacenes
    proyecto_id = fields.Int(load_default=None, allow_none=True)
    # Solo REASIGNACION: mueve stock del bucket origen al destino en el MISMO
    # almacén (cualquiera de los dos puede ser None = general).
    proyecto_origen_id = fields.Int(load_default=None, allow_none=True)
    proyecto_destino_id = fields.Int(load_default=None, allow_none=True)
    motivo = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=250))
    # Partes del movimiento para el comprobante/PDF: quién
    # ENTREGA y quién RECIBE. Cada parte = trabajador del sistema (…_trabajador_id)
    # o nombre libre (…_nombre). Opcionales: no bloquean el flujo actual.
    entrega_trabajador_id = fields.Int(load_default=None, allow_none=True)
    entrega_nombre = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=200))
    recibe_trabajador_id = fields.Int(load_default=None, allow_none=True)
    recibe_nombre = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=200))


class MovimientoLoteItemSchema(_BaseSchema):
    """Una línea del lote: solo lo que cambia de un producto a otro."""
    producto_id = fields.Int(required=True)
    cantidad = fields.Float(required=True, validate=validate.Range(min=-100_000, max=100_000))


class MovimientoLoteSchema(_BaseSchema):
    """N movimientos del MISMO tipo, bodega, proyecto y partes, en UNA petición.

    Existe porque la alternativa —que el cliente mande N veces POST
    /movimientos/— tiene tres problemas que no se arreglan desde el navegador:
    no es atómica (un fallo a media lista deja stock movido y el resto no),
    consume N veces el rate limit del endpoint, y produce N comprobantes PDF para lo
    que el almacenista entiende como UNA entrega.

    Los campos compartidos van arriba y solo `items` varía por producto: en un
    lote, tipo/bodega/proyecto/quién recibe son los mismos por definición.
    """
    tipo = fields.Str(required=True, validate=validate.OneOf(['ENTRADA', 'SALIDA', 'AJUSTE', 'TRASPASO', 'REASIGNACION']))
    almacen_origen_id = fields.Int(load_default=None, allow_none=True)
    almacen_destino_id = fields.Int(load_default=None, allow_none=True)
    estante_id = fields.Int(load_default=None, allow_none=True)
    proyecto_id = fields.Int(load_default=None, allow_none=True)
    proyecto_origen_id = fields.Int(load_default=None, allow_none=True)
    proyecto_destino_id = fields.Int(load_default=None, allow_none=True)
    motivo = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=250))
    entrega_trabajador_id = fields.Int(load_default=None, allow_none=True)
    entrega_nombre = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=200))
    recibe_trabajador_id = fields.Int(load_default=None, allow_none=True)
    recibe_nombre = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=200))
    # Tope alineado con el del comprobante en lote: más líneas de las que caben en un
    # comprobante que alguien va a firmar no sirven para nada.
    items = fields.List(
        fields.Nested(MovimientoLoteItemSchema),
        required=True,
        validate=validate.Length(min=1, max=100),
    )


class AjusteBucketItemSchema(_BaseSchema):
    """Un bucket (almacén, proyecto|general) con su cantidad OBJETIVO — la que
    debe quedar tras el ajuste. `proyecto_id=None` = bucket general/libre."""
    almacen_id = fields.Int(required=True)
    proyecto_id = fields.Int(load_default=None, allow_none=True)
    cantidad_objetivo = fields.Float(required=True, validate=validate.Range(min=0, max=1_000_000))


class AjusteBucketsSchema(_BaseSchema):
    """Editor de stock por bodega+proyecto: fija la cantidad objetivo de cada
    bucket. El backend calcula el delta contra lo que hay y genera un AJUSTE por
    cada bucket que cambió (fuente de verdad = stock_almacen_proyecto)."""
    buckets = fields.List(
        fields.Nested(AjusteBucketItemSchema),
        required=True,
        validate=validate.Length(min=1, max=200),
    )
    motivo = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=250))


# ─── Solicitudes de material / herramienta ───────────────────────────────────

class SolicitudDetalleCreateSchema(_BaseSchema):
    # XOR a nivel app: si tipo_item=MATERIAL → producto_id; si HERRAMIENTA → herramienta_id
    tipo_item = fields.Str(load_default='MATERIAL', validate=validate.OneOf(['MATERIAL', 'HERRAMIENTA']))
    producto_id = fields.Int(load_default=None, allow_none=True)
    herramienta_id = fields.Int(load_default=None, allow_none=True)
    cantidad_solicitada = fields.Float(required=True, validate=validate.Range(min=0.0001, max=10_000))
    fecha_uso_inicio = fields.Date(load_default=None, allow_none=True)
    fecha_uso_fin = fields.Date(load_default=None, allow_none=True)
    justificacion = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=2000))
    complementos = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=500))


class SolicitudCreateSchema(_BaseSchema):
    # Proyecto obligatorio: no se permiten solicitudes sin proyecto asociado.
    proyecto = fields.Str(required=True, validate=validate.Length(min=1, max=200))
    # FK opcional al proyecto del sistema. El SPA la manda desde el dropdown;
    # si viene, liga la solicitud para atribuir consumo en el panel de proyectos.
    proyecto_id = fields.Int(load_default=None, allow_none=True)
    notas = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=2000))
    detalles = fields.List(
        fields.Nested(SolicitudDetalleCreateSchema),
        required=True,
        validate=validate.Length(min=1, max=500),
    )


class EntregaDirectaItemSchema(_BaseSchema):
    """Una línea de material de la entrega directa (mostrador)."""
    producto_id = fields.Int(required=True)
    cantidad = fields.Float(required=True, validate=validate.Range(min=0.0001, max=100_000))
    # Pausa 11 — opcional: de qué estante (celda) se surte, para descontar el
    # sub-libro de celdas. None = no toca celdas.
    estante_id = fields.Int(load_default=None, allow_none=True)


class EntregaDirectaSchema(_BaseSchema):
    """Entrega directa de mostrador: el de inventario surte material en el acto.

    El solicitante real es un trabajador del sistema (solicitante_trabajador_id)
    o un nombre libre (solicitante_nombre); al menos uno es obligatorio (se
    valida en la vista). Genera una SolicitudMaterial ya ENTREGADA + SALIDAs.
    """
    proyecto = fields.Str(required=True, validate=validate.Length(min=1, max=200))
    proyecto_id = fields.Int(load_default=None, allow_none=True)
    solicitante_trabajador_id = fields.Int(load_default=None, allow_none=True)
    solicitante_nombre = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=200))
    almacen_origen_id = fields.Int(load_default=None, allow_none=True)
    notas = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=2000))
    motivo = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=250))
    detalles = fields.List(
        fields.Nested(EntregaDirectaItemSchema),
        required=True,
        validate=validate.Length(min=1, max=500),
    )


class SolicitudUpdateEstadoSchema(_BaseSchema):
    estatus = fields.Str(required=True, validate=validate.OneOf(['APROBADA', 'RECHAZADA', 'ENTREGADA', 'PENDIENTE']))


# Pausa 8b — Editar cantidad_aprobada de una línea.
class SolicitudDetallePatchSchema(_BaseSchema):
    cantidad_aprobada = fields.Float(required=True, validate=validate.Range(min=0, max=100_000))


# Pausa 8b — Entrega total o parcial de una solicitud APROBADA.
class EntregaItemSchema(_BaseSchema):
    detalle_id = fields.Int(required=True)
    cantidad_entregada = fields.Float(required=True, validate=validate.Range(min=0, max=100_000))
    # Pausa 11 — opcional: de qué estante (celda) se surte esta línea, para
    # descontar el sub-libro de celdas. None = no toca celdas (comportamiento previo).
    estante_id = fields.Int(load_default=None, allow_none=True)


class EntregarSolicitudSchema(_BaseSchema):
    almacen_origen_id = fields.Int(load_default=None, allow_none=True)
    motivo = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=250))
    # Para líneas HERRAMIENTA: fecha de devolución prevista que se copia a cada
    # AsignacionHerramienta generada. Opcional.
    fecha_devolucion_prevista = fields.DateTime(load_default=None, allow_none=True)
    entregas = fields.List(
        fields.Nested(EntregaItemSchema),
        required=True,
        validate=validate.Length(min=1, max=500),
    )


# ─── Categorías ──────────────────────────────────────────────────────────────

class CategoriaConfigUpsertSchema(_BaseSchema):
    imagen_url = _campo_imagen_url()


# ─── Plan de materiales por proyecto (Inventario → Proyectos) ────────────────

class ProyectoPlanLineaSchema(_BaseSchema):
    producto_id = fields.Int(required=True)
    cantidad_planeada = fields.Float(required=True, validate=validate.Range(min=0, max=1_000_000))
    notas = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=500))


class ProyectoPlanUpsertSchema(_BaseSchema):
    # Reemplaza el plan completo del proyecto con estas líneas (upsert por
    # producto). Una lista vacía deja el plan sin líneas.
    lineas = fields.List(
        fields.Nested(ProyectoPlanLineaSchema),
        required=True,
        validate=validate.Length(max=500),
    )
