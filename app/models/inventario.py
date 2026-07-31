"""Modelos del dominio Inventario: Almacen, Estante, Producto, Stock, Tomas, Movimientos, CategoriaConfig."""
from app.extensions import db
from app.models._base import _now_utc


class Almacen(db.Model):
    __tablename__ = "almacenes"
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100), nullable=False)
    ubicacion = db.Column(db.String(250), nullable=True)
    qr_code = db.Column(db.String(100), unique=True, nullable=False, index=True)
    activo = db.Column(db.Boolean, default=True)
    # Relación con estantes
    estantes = db.relationship('Estante', backref='almacen', lazy='select', cascade='all, delete-orphan')


class Estante(db.Model):
    """Subdivisión física dentro de un almacén (estante, rack, zona, etc.)
    Tiene su propio QR para escanear desde el móvil. El stock sigue siendo
    global por Producto; el estante funciona como etiqueta de ubicación.
    """
    __tablename__ = "estantes"
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100), nullable=False)          # "Estante A-1", "Rack 3", etc.
    descripcion = db.Column(db.String(250), nullable=True)       # Nota opcional
    almacen_id = db.Column(db.Integer, db.ForeignKey('almacenes.id'), nullable=False, index=True)
    qr_code = db.Column(db.String(100), unique=True, nullable=False, index=True)
    activo = db.Column(db.Boolean, default=True)
    # Rejilla física del estante (Pausa 11 — vista visual + stock por celda).
    # filas=columnas=1 reproduce el comportamiento plano previo (sin rejilla).
    filas = db.Column(db.Integer, nullable=False, default=1, server_default='1')
    columnas = db.Column(db.Integer, nullable=False, default=1, server_default='1')
    created_at = db.Column(db.DateTime, default=_now_utc)


class Producto(db.Model):
    __tablename__ = "productos"
    id = db.Column(db.Integer, primary_key=True)
    codigo = db.Column(db.String(100), unique=True, nullable=False, index=True)
    descripcion = db.Column(db.String(250), nullable=False)
    categoria = db.Column(db.String(100), nullable=False, index=True)
    # Marca / fabricante del producto. Campo INDEPENDIENTE del proveedor default
    # (una marca no es un proveedor). Texto libre, nullable para no romper los
    # productos existentes (quedan con marca=NULL hasta que se capture).
    marca = db.Column(db.String(100), nullable=True)
    unidad = db.Column(db.String(50), nullable=False)  # pieza, caja, kg, etc.
    # Atributos específicos de CABLE (categoría que contiene "cable"). Nullable a
    # propósito: solo los productos de cable los usan; el resto quedan NULL sin
    # romper nada. La obligatoriedad (cuando la categoría es cable) se aplica en
    # la capa de aplicación — ver _es_categoria_cable / _normalizar_cable.
    cable_tipo = db.Column(db.String(60), nullable=True)     # THHN, THW, desnudo…
    cable_calibre = db.Column(db.String(40), nullable=True)  # "12", "2/0", "500 kcmil" (mm²/AWG)
    stock_actual = db.Column(db.Numeric(10, 2), default=0, nullable=False)
    # Pausa 2-bis: stock apartado por solicitudes APROBADAS no entregadas.
    # Se suma al APROBAR la solicitud, se resta al ENTREGAR/RECHAZAR/REABRIR.
    # `stock_disponible` (calculado abajo) es lo que sí se puede mover.
    stock_reservado = db.Column(db.Numeric(10, 2), default=0, nullable=False)
    stock_minimo = db.Column(db.Numeric(10, 2), default=0, nullable=False)
    # Precio unitario del catálogo. Fuente única para los costos por proyecto
    # (cantidad × precio_unitario). En vivo: cambiar el precio recalcula costos
    # pasados (decisión de producto, sin snapshot histórico por línea).
    precio_unitario = db.Column(db.Numeric(12, 2), default=0, nullable=False)

    @property
    def stock_disponible(self):
        """Stock que el almacenista puede mover sin tocar lo apartado."""
        from decimal import Decimal
        return (self.stock_actual or Decimal('0')) - (self.stock_reservado or Decimal('0'))

    imagen_url = db.Column(db.String(500), nullable=True)
    # ── Imagen en Cloudflare R2 (pipeline WebP) ──────────────────────────────
    # `imagen_url` es la URL que se PINTA en el catálogo. Cuando el pipeline de
    # R2 está activo (solo producción), las imágenes que llegan como URL externa
    # se descargan, se convierten a WebP y se suben a R2; entonces `imagen_url`
    # pasa a apuntar al dominio público de R2. Estos campos son el "libro mayor"
    # de ese proceso — todos nullable para no romper productos existentes:
    #   imagen_source_url: última URL externa de origen (lo que se capturó/importó)
    #   imagen_r2_key:     object key en R2 una vez subida
    #   imagen_estado:     None(sin imagen) | PENDIENTE | PROCESANDO | OK | ERROR
    #   imagen_error:      último error legible (para el reporte al SPA)
    imagen_source_url = db.Column(db.String(500), nullable=True)
    imagen_r2_key = db.Column(db.String(300), nullable=True)
    imagen_estado = db.Column(db.String(20), nullable=True)
    imagen_error = db.Column(db.String(300), nullable=True)
    # Pausa 9: proveedor default para Compras express. Campos texto, no tabla
    # aparte: si más adelante crece el módulo de proveedores se migra a FK.
    proveedor_default_nombre = db.Column(db.String(150), nullable=True)
    proveedor_default_contacto = db.Column(db.String(150), nullable=True)
    activo = db.Column(db.Boolean, default=True, index=True)
    created_at = db.Column(db.DateTime, default=_now_utc)
    updated_at = db.Column(db.DateTime, default=_now_utc, onupdate=_now_utc)
    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)

    created_by = db.relationship('User', foreign_keys=[created_by_id])


class NotificacionUmbral(db.Model):
    """Idempotencia diaria para alertas STOCK_BAJO (Pausa 5).
    Si ya hay una fila (producto_id, fecha=hoy), no volvemos a notificar.
    Tabla pequeña: se puede limpiar mensualmente sin perder información útil
    (los eventos viejos no influyen en la lógica)."""
    __tablename__ = "notificacion_umbral"
    producto_id = db.Column(db.Integer, db.ForeignKey('productos.id', ondelete='CASCADE'), primary_key=True)
    fecha = db.Column(db.Date, primary_key=True)
    creada_en = db.Column(db.DateTime, default=_now_utc)


class StockPorAlmacen(db.Model):
    """Stock real desglosado por almacén.
    Cambio introducido en Pausa 2 del plan de mejoras (2026-05-25).

    `Producto.stock_actual` se mantiene como **cache sumado** (denormalización
    intencional) para que las queries existentes tipo
    `WHERE stock_actual <= stock_minimo` sigan funcionando sin reescribir cada
    consumidor del modelo. La fuente de verdad es esta tabla — el cache se
    actualiza dentro de la misma transacción que modifica esta tabla.
    """
    __tablename__ = "stock_por_almacen"
    # ondelete FIJADOS a lo que ya existe en la base. No es cosmético: al
    # borrar un producto su stock se limpia en cascada, y un almacén con
    # stock NO se puede borrar (RESTRICT). Recrear estas FK sin estos
    # comportamientos cambiaría la integridad referencial en silencio.
    producto_id = db.Column(
        db.Integer,
        db.ForeignKey('productos.id', name='stock_por_almacen_producto_id_fkey',
                      ondelete='CASCADE'),
        primary_key=True,
    )
    almacen_id = db.Column(
        db.Integer,
        db.ForeignKey('almacenes.id', name='stock_por_almacen_almacen_id_fkey',
                      ondelete='RESTRICT'),
        primary_key=True,
    )
    cantidad = db.Column(db.Numeric(10, 2), default=0, nullable=False)
    updated_at = db.Column(db.DateTime, default=_now_utc, onupdate=_now_utc)

    producto = db.relationship('Producto', backref=db.backref('stocks_por_almacen', lazy='select', cascade='all, delete-orphan'))
    almacen = db.relationship('Almacen', backref=db.backref('stocks', lazy='select'))


class StockAlmacenProyecto(db.Model):
    """Stock físico desglosado por (producto, almacén, proyecto) — grano más
    fino que `StockPorAlmacen` (feature "stock por proyecto", 2026-07).

    `proyecto_id = NULL` significa stock GENERAL/libre (no apartado a ningún
    proyecto). Ésta es la nueva **fuente de verdad**; los dos caches previos se
    mantienen en la MISMA transacción que modifica esta tabla:
        Σ proyecto  ⇒  StockPorAlmacen.cantidad   (por almacén)
        Σ almacén   ⇒  Producto.stock_actual      (total del producto)
    Así todo el código que ya lee esos caches sigue funcionando sin cambios.

    El grano de proyecto NO se propaga a `producto_estante` (celdas): ese
    sub-libro sigue siendo por almacén, con su invariante Σceldas ≤ stock_almacén.

    `proyecto_id` no forma parte de la PK porque Postgres no admite NULL en PK;
    la unicidad de (producto, almacén, bucket) se garantiza con un índice único
    funcional sobre COALESCE(proyecto_id, 0) — el general (NULL) mapea al 0.
    """
    __tablename__ = "stock_almacen_proyecto"
    id = db.Column(db.Integer, primary_key=True)
    producto_id = db.Column(db.Integer, db.ForeignKey('productos.id', ondelete='CASCADE'), nullable=False, index=True)
    almacen_id = db.Column(db.Integer, db.ForeignKey('almacenes.id', ondelete='RESTRICT'), nullable=False, index=True)
    proyecto_id = db.Column(db.Integer, db.ForeignKey('proyectos.id', ondelete='RESTRICT'), nullable=True, index=True)
    cantidad = db.Column(db.Numeric(10, 2), default=0, nullable=False, server_default='0')
    updated_at = db.Column(db.DateTime, default=_now_utc, onupdate=_now_utc)

    producto = db.relationship('Producto', backref=db.backref('stocks_por_proyecto', lazy='select', cascade='all, delete-orphan'))
    almacen = db.relationship('Almacen', foreign_keys=[almacen_id])
    proyecto = db.relationship('Proyecto', foreign_keys=[proyecto_id])

    __table_args__ = (
        # Unicidad por bucket: general (NULL) ⇒ COALESCE→0. Funciona en Postgres
        # y SQLite (ambos soportan índices únicos por expresión).
        db.Index(
            'uq_stock_almacen_proyecto',
            'producto_id', 'almacen_id',
            db.text('coalesce(proyecto_id, 0)'),
            unique=True,
        ),
        db.Index('ix_sap_almacen_proyecto', 'almacen_id', 'proyecto_id'),
    )


# ─── Pausa 10 — Conteo físico / Toma de inventario ──────────────────────────

ESTADOS_TOMA = ['ABIERTA', 'CERRADA', 'CANCELADA']


class TomaInventario(db.Model):
    """Conteo físico de un almacén (Pausa 10).

    Al iniciar, snapshotea `StockPorAlmacen` para todos los productos del
    almacén — ese es el `cantidad_sistema`. El usuario captura `cantidad_fisica`
    línea por línea. Al cerrar, se generan AJUSTES via `_perform_movimiento`
    por cada diferencia, citando la toma en el motivo.

    Regla: solo una toma ABIERTA por almacén a la vez (partial unique index).
    """
    __tablename__ = "tomas_inventario"
    id = db.Column(db.Integer, primary_key=True)
    almacen_id = db.Column(db.Integer, db.ForeignKey('almacenes.id'), nullable=False, index=True)
    fecha_inicio = db.Column(db.DateTime, default=_now_utc, nullable=False)
    fecha_cierre = db.Column(db.DateTime, nullable=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    cerrada_por_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    estatus = db.Column(db.String(20), default='ABIERTA', nullable=False, index=True)
    notas = db.Column(db.Text, nullable=True)

    almacen = db.relationship('Almacen', foreign_keys=[almacen_id])
    usuario = db.relationship('User', foreign_keys=[usuario_id])
    cerrada_por = db.relationship('User', foreign_keys=[cerrada_por_id])

    __table_args__ = (
        # Solo una toma ABIERTA por almacén. Partial index para no bloquear
        # cerradas/canceladas.
        db.Index(
            'one_open_toma_per_almacen',
            'almacen_id',
            unique=True,
            postgresql_where=db.text("estatus = 'ABIERTA'"),
        ),
    )


class TomaInventarioDetalle(db.Model):
    """Una línea por (toma, producto). Snapshot del stock al iniciar +
    cantidad física capturada por el usuario."""
    __tablename__ = "tomas_inventario_detalle"
    id = db.Column(db.Integer, primary_key=True)
    toma_id = db.Column(db.Integer, db.ForeignKey('tomas_inventario.id', ondelete='CASCADE'), nullable=False, index=True)
    producto_id = db.Column(db.Integer, db.ForeignKey('productos.id'), nullable=False, index=True)
    cantidad_sistema = db.Column(db.Numeric(10, 2), default=0, nullable=False)
    cantidad_fisica = db.Column(db.Numeric(10, 2), nullable=True)
    capturado_por_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    capturado_en = db.Column(db.DateTime, nullable=True)

    toma = db.relationship('TomaInventario', backref=db.backref('detalles', lazy='select', cascade='all, delete-orphan'))
    producto = db.relationship('Producto', foreign_keys=[producto_id])
    capturado_por = db.relationship('User', foreign_keys=[capturado_por_id])

    __table_args__ = (
        db.UniqueConstraint('toma_id', 'producto_id', name='uq_toma_producto'),
    )

    @property
    def diferencia(self):
        from decimal import Decimal
        if self.cantidad_fisica is None:
            return None
        return Decimal(str(self.cantidad_fisica)) - Decimal(str(self.cantidad_sistema or 0))


class ProductoEstante(db.Model):
    """Ubicación de un producto dentro de un estante (Pausa 4 + 11).

    Pausa 4: mapping producto↔estante (qué producto se guarda aquí), usado por
    el scanner móvil.

    Pausa 11 — vista visual + stock por celda:
      - `fila`/`columna`: posición en la rejilla del estante (1-based). Ambas
        NULL = "asignado pero sin ubicar en la rejilla".
      - `cantidad`: unidades de este producto que físicamente están en esta
        celda. Es un SUB-LIBRO reconciliado de `StockPorAlmacen`: la fuente de
        verdad del stock sigue siendo `StockPorAlmacen` (a nivel almacén). El
        invariante que mantenemos es, por (producto, almacén):
            Σ ProductoEstante.cantidad ≤ StockPorAlmacen.cantidad
        El resto (stock − Σceldas) es "sin ubicar". Nunca dejamos cantidad
        negativa; si una salida manual baja el stock por debajo de Σceldas, se
        marca el estante como "necesita reacomodo" en lugar de corromper datos.

    Un mismo producto vive en a lo más una celda por estante (PK), pero puede
    estar en varios estantes; una celda (fila, columna) puede tener varios
    productos distintos.
    """
    __tablename__ = "producto_estante"
    producto_id = db.Column(db.Integer, db.ForeignKey('productos.id', ondelete='CASCADE'), primary_key=True)
    estante_id = db.Column(db.Integer, db.ForeignKey('estantes.id', ondelete='CASCADE'), primary_key=True)
    fila = db.Column(db.Integer, nullable=True)
    columna = db.Column(db.Integer, nullable=True)
    cantidad = db.Column(db.Numeric(10, 2), nullable=False, default=0, server_default='0')
    updated_at = db.Column(db.DateTime, default=_now_utc, onupdate=_now_utc)

    producto = db.relationship('Producto', backref=db.backref('estantes_asignados', lazy='select', cascade='all, delete-orphan'))
    estante = db.relationship('Estante', backref=db.backref('productos_asignados', lazy='select', cascade='all, delete-orphan'))


class MovimientoInventario(db.Model):
    __tablename__ = "movimientos_inventario"
    id = db.Column(db.Integer, primary_key=True)
    tipo = db.Column(db.String(50), nullable=False, index=True)  # ENTRADA, SALIDA, AJUSTE, TRASPASO, REASIGNACION
    producto_id = db.Column(db.Integer, db.ForeignKey('productos.id'), nullable=False, index=True)
    almacen_origen_id = db.Column(db.Integer, db.ForeignKey('almacenes.id'), nullable=True, index=True)
    almacen_destino_id = db.Column(db.Integer, db.ForeignKey('almacenes.id'), nullable=True, index=True)
    # Atribución de proyecto del movimiento (feature "stock por proyecto"). NULL
    # = bucket general. ENTRADA/AJUSTE+ usan proyecto_destino; SALIDA/AJUSTE− usan
    # proyecto_origen; TRASPASO conserva el mismo bucket en ambos; REASIGNACION
    # mueve de proyecto_origen a proyecto_destino dentro del mismo almacén.
    # ondelete y nombre FIJADOS a lo que ya existe en la base: al borrar un
    # proyecto, sus movimientos quedan con la referencia en NULL en vez de
    # bloquear el borrado. Sin declararlo, una migración autogenerada
    # recrearía la FK sin ese comportamiento.
    proyecto_origen_id = db.Column(
        db.Integer,
        db.ForeignKey('proyectos.id', name='fk_mov_proyecto_origen', ondelete='SET NULL'),
        nullable=True, index=True,
    )
    proyecto_destino_id = db.Column(
        db.Integer,
        db.ForeignKey('proyectos.id', name='fk_mov_proyecto_destino', ondelete='SET NULL'),
        nullable=True, index=True,
    )
    cantidad = db.Column(db.Numeric(10, 2), nullable=False)
    motivo = db.Column(db.String(250), nullable=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    fecha = db.Column(db.DateTime, default=_now_utc, index=True)
    # Partes del movimiento (feature "vale de movimiento"): quién ENTREGA y quién
    # RECIBE la mercancía, para el comprobante PDF y la trazabilidad. Cada parte
    # puede ser un trabajador del sistema (FK) o un nombre libre — mismo patrón
    # que el solicitante real de una entrega directa (ver SolicitudMaterial).
    # Todas nullable: los movimientos previos y los internos (AJUSTE/REASIGNACION)
    # no las necesitan. FK con SET NULL para no bloquear el borrado de trabajadores.
    entrega_trabajador_id = db.Column(db.Integer, db.ForeignKey('trabajadores.id', ondelete='SET NULL'), nullable=True)
    entrega_nombre = db.Column(db.String(200), nullable=True)
    recibe_trabajador_id = db.Column(db.Integer, db.ForeignKey('trabajadores.id', ondelete='SET NULL'), nullable=True)
    recibe_nombre = db.Column(db.String(200), nullable=True)

    producto = db.relationship('Producto', backref=db.backref('movimientos', lazy=True, cascade='all, delete-orphan'))
    almacen_origen = db.relationship('Almacen', foreign_keys=[almacen_origen_id])
    almacen_destino = db.relationship('Almacen', foreign_keys=[almacen_destino_id])
    proyecto_origen = db.relationship('Proyecto', foreign_keys=[proyecto_origen_id])
    proyecto_destino = db.relationship('Proyecto', foreign_keys=[proyecto_destino_id])
    usuario = db.relationship('User', foreign_keys=[usuario_id])
    entrega_trabajador = db.relationship('Trabajador', foreign_keys=[entrega_trabajador_id])
    recibe_trabajador = db.relationship('Trabajador', foreign_keys=[recibe_trabajador_id])

    @property
    def entrega_display(self):
        """Nombre de quien ENTREGA: el trabajador ligado o el nombre libre."""
        if self.entrega_trabajador:
            return self.entrega_trabajador.nombre_completo
        return self.entrega_nombre

    @property
    def recibe_display(self):
        """Nombre de quien RECIBE: el trabajador ligado o el nombre libre."""
        if self.recibe_trabajador:
            return self.recibe_trabajador.nombre_completo
        return self.recibe_nombre


class CategoriaConfig(db.Model):
    """Metadatos visuales para categorías de productos (imagen, etc.).
    El campo `nombre` se considera la PK lógica: coincide con `Producto.categoria`
    y se mantiene en sync con el catálogo. Una categoría sin productos puede
    existir aquí (registrada por el admin antes de capturar productos).
    """
    __tablename__ = "categorias_config"
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100), unique=True, nullable=False, index=True)
    imagen_url = db.Column(db.String(500), nullable=True)
    # Mismo pipeline de imágenes → R2 que Producto (ver comentario allá). La
    # imagen de categoría con URL externa se descarga, se convierte a WebP y se
    # sube a R2; `imagen_url` pasa a apuntar al dominio público de R2.
    imagen_source_url = db.Column(db.String(500), nullable=True)
    imagen_r2_key = db.Column(db.String(300), nullable=True)
    imagen_estado = db.Column(db.String(20), nullable=True)
    imagen_error = db.Column(db.String(300), nullable=True)
    created_at = db.Column(db.DateTime, default=_now_utc)
    updated_at = db.Column(db.DateTime, default=_now_utc, onupdate=_now_utc)
    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)

    created_by = db.relationship('User', foreign_keys=[created_by_id])
