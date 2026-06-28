"""Modelos del dominio Solicitudes de Compra (procura).

Distinto de `SolicitudMaterial` (que surte material/herramienta del stock que ya
existe) y de la "OC express" (un PDF desechable que no se persiste): este módulo
registra de forma **persistente** qué se pide comprar, a qué proveedor, si ya se
ordenó y si ya llegó/fue atendido.

Ciclo de vida (estatus):
    PENDIENTE  → recién creada, aún no se manda al proveedor.
    ORDENADA   → ya se envió la orden al proveedor (esperando recepción).
    RECIBIDA   → llegó todo lo solicitado (atendida por completo).
    CANCELADA  → se descartó.

La recepción puede ser parcial: una solicitud ORDENADA va acumulando
`cantidad_recibida` por línea; cuando todas las líneas con producto alcanzan su
`cantidad_solicitada`, la solicitud pasa a RECIBIDA automáticamente. Al recibir
material ligado a un producto del catálogo, el endpoint de recepción genera una
ENTRADA real al almacén (sube el stock) — el detalle de esa lógica vive en
`app/routes/inventario_api/compras.py`.
"""
from app.extensions import db
from app.models._base import _now_utc


ESTADOS_COMPRA = ['PENDIENTE', 'ORDENADA', 'RECIBIDA', 'CANCELADA']
PRIORIDADES_COMPRA = ['BAJA', 'MEDIA', 'ALTA', 'URGENTE']


class SolicitudCompra(db.Model):
    __tablename__ = "solicitudes_compra"
    id = db.Column(db.Integer, primary_key=True)
    solicitado_por_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    # Proveedor sugerido (texto libre, igual que en OC express: aún no hay tabla
    # de proveedores). Opcional al crear.
    proveedor_sugerido = db.Column(db.String(150), nullable=True)
    proveedor_contacto = db.Column(db.String(150), nullable=True)
    # FK opcional al proyecto que origina la compra (para atribuir el gasto).
    proyecto_id = db.Column(db.Integer, db.ForeignKey('proyectos.id'), nullable=True, index=True)
    prioridad = db.Column(db.String(20), default='MEDIA', nullable=False)
    estatus = db.Column(db.String(20), default='PENDIENTE', nullable=False, index=True)
    notas = db.Column(db.Text, nullable=True)
    fecha_creacion = db.Column(db.DateTime, default=_now_utc, index=True)
    fecha_orden = db.Column(db.DateTime, nullable=True)   # cuándo pasó a ORDENADA
    fecha_cierre = db.Column(db.DateTime, nullable=True)  # cuándo se RECIBIÓ/CANCELÓ

    solicitado_por = db.relationship('User', foreign_keys=[solicitado_por_id])
    proyecto_ref = db.relationship('Proyecto', foreign_keys=[proyecto_id])

    @property
    def folio(self) -> str:
        return f'SC-{self.id:06d}'


class SolicitudCompraDetalle(db.Model):
    __tablename__ = "solicitudes_compra_detalle"
    id = db.Column(db.Integer, primary_key=True)
    solicitud_compra_id = db.Column(
        db.Integer, db.ForeignKey('solicitudes_compra.id'), nullable=False, index=True
    )
    # producto_id es NULLABLE: una línea puede ser un producto del catálogo
    # (producto_id) o un ítem de texto libre que aún no existe en catálogo
    # (descripcion_libre). Al menos uno de los dos debe venir (validado en app).
    producto_id = db.Column(db.Integer, db.ForeignKey('productos.id'), nullable=True)
    descripcion_libre = db.Column(db.String(250), nullable=True)
    unidad = db.Column(db.String(50), nullable=True)
    cantidad_solicitada = db.Column(db.Numeric(10, 2), nullable=False)
    # Se acumula al recibir; cuando recibida >= solicitada la línea está completa.
    cantidad_recibida = db.Column(db.Numeric(10, 2), default=0, nullable=False)
    precio_estimado = db.Column(db.Numeric(12, 2), nullable=True)
    notas = db.Column(db.String(500), nullable=True)

    solicitud = db.relationship(
        'SolicitudCompra',
        backref=db.backref('detalles', lazy='selectin', cascade='all, delete-orphan'),
    )
    producto = db.relationship('Producto')
