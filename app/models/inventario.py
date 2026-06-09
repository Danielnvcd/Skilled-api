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
    created_at = db.Column(db.DateTime, default=_now_utc)


class Producto(db.Model):
    __tablename__ = "productos"
    id = db.Column(db.Integer, primary_key=True)
    codigo = db.Column(db.String(100), unique=True, nullable=False, index=True)
    descripcion = db.Column(db.String(250), nullable=False)
    categoria = db.Column(db.String(100), nullable=False, index=True)
    unidad = db.Column(db.String(50), nullable=False)  # pieza, caja, kg, etc.
    stock_actual = db.Column(db.Numeric(10, 2), default=0, nullable=False)
    # Pausa 2-bis: stock apartado por solicitudes APROBADAS no entregadas.
    # Se suma al APROBAR la solicitud, se resta al ENTREGAR/RECHAZAR/REABRIR.
    # `stock_disponible` (calculado abajo) es lo que sí se puede mover.
    stock_reservado = db.Column(db.Numeric(10, 2), default=0, nullable=False)
    stock_minimo = db.Column(db.Numeric(10, 2), default=0, nullable=False)

    @property
    def stock_disponible(self):
        """Stock que el almacenista puede mover sin tocar lo apartado."""
        from decimal import Decimal
        return (self.stock_actual or Decimal('0')) - (self.stock_reservado or Decimal('0'))

    imagen_url = db.Column(db.String(500), nullable=True)
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
    producto_id = db.Column(db.Integer, db.ForeignKey('productos.id'), primary_key=True)
    almacen_id = db.Column(db.Integer, db.ForeignKey('almacenes.id'), primary_key=True)
    cantidad = db.Column(db.Numeric(10, 2), default=0, nullable=False)
    updated_at = db.Column(db.DateTime, default=_now_utc, onupdate=_now_utc)

    producto = db.relationship('Producto', backref=db.backref('stocks_por_almacen', lazy='select', cascade='all, delete-orphan'))
    almacen = db.relationship('Almacen', backref=db.backref('stocks', lazy='select'))


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
    """Mapping puro producto↔estante (Pausa 4 — scanner móvil).

    No guarda cantidades: dice "este producto se ubica en estos estantes"
    para que al escanear el QR del estante se vea qué hay ahí. El stock real
    sigue viviendo en `StockPorAlmacen` (al nivel de almacén). Un mismo
    producto puede vivir en varios estantes y un estante puede tener varios
    productos.
    """
    __tablename__ = "producto_estante"
    producto_id = db.Column(db.Integer, db.ForeignKey('productos.id', ondelete='CASCADE'), primary_key=True)
    estante_id = db.Column(db.Integer, db.ForeignKey('estantes.id', ondelete='CASCADE'), primary_key=True)
    updated_at = db.Column(db.DateTime, default=_now_utc, onupdate=_now_utc)

    producto = db.relationship('Producto', backref=db.backref('estantes_asignados', lazy='select', cascade='all, delete-orphan'))
    estante = db.relationship('Estante', backref=db.backref('productos_asignados', lazy='select', cascade='all, delete-orphan'))


class MovimientoInventario(db.Model):
    __tablename__ = "movimientos_inventario"
    id = db.Column(db.Integer, primary_key=True)
    tipo = db.Column(db.String(50), nullable=False, index=True)  # ENTRADA, SALIDA, AJUSTE, TRASPASO
    producto_id = db.Column(db.Integer, db.ForeignKey('productos.id'), nullable=False, index=True)
    almacen_origen_id = db.Column(db.Integer, db.ForeignKey('almacenes.id'), nullable=True, index=True)
    almacen_destino_id = db.Column(db.Integer, db.ForeignKey('almacenes.id'), nullable=True, index=True)
    cantidad = db.Column(db.Numeric(10, 2), nullable=False)
    motivo = db.Column(db.String(250), nullable=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    fecha = db.Column(db.DateTime, default=_now_utc, index=True)

    producto = db.relationship('Producto', backref=db.backref('movimientos', lazy=True, cascade='all, delete-orphan'))
    almacen_origen = db.relationship('Almacen', foreign_keys=[almacen_origen_id])
    almacen_destino = db.relationship('Almacen', foreign_keys=[almacen_destino_id])
    usuario = db.relationship('User', foreign_keys=[usuario_id])


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
    created_at = db.Column(db.DateTime, default=_now_utc)
    updated_at = db.Column(db.DateTime, default=_now_utc, onupdate=_now_utc)
    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)

    created_by = db.relationship('User', foreign_keys=[created_by_id])
