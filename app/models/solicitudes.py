"""Modelos del dominio Solicitudes de Material/Herramienta."""
from app.extensions import db
from app.models._base import _now_utc


class SolicitudMaterial(db.Model):
    __tablename__ = "solicitudes_material"
    id = db.Column(db.Integer, primary_key=True)
    solicitante_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    proyecto = db.Column(db.String(200), nullable=True)
    estatus = db.Column(db.String(50), default='PENDIENTE', nullable=False, index=True)  # PENDIENTE, APROBADA, RECHAZADA, ENTREGADA
    fecha_creacion = db.Column(db.DateTime, default=_now_utc, index=True)
    fecha_cierre = db.Column(db.DateTime, nullable=True)

    solicitante = db.relationship('User', foreign_keys=[solicitante_id])


class SolicitudMaterialDetalle(db.Model):
    __tablename__ = "solicitudes_material_detalle"
    id = db.Column(db.Integer, primary_key=True)
    solicitud_id = db.Column(db.Integer, db.ForeignKey('solicitudes_material.id'), nullable=False, index=True)
    # producto_id es NULLABLE: una línea es MATERIAL (producto_id) o HERRAMIENTA (herramienta_id), XOR.
    producto_id = db.Column(db.Integer, db.ForeignKey('productos.id'), nullable=True)
    cantidad_solicitada = db.Column(db.Numeric(10, 2), nullable=False)
    cantidad_aprobada = db.Column(db.Numeric(10, 2), default=0)
    cantidad_entregada = db.Column(db.Numeric(10, 2), default=0)

    # Extensión para soportar solicitudes de herramientas
    tipo_item = db.Column(db.String(20), nullable=False, default='MATERIAL', server_default='MATERIAL', index=True)
    herramienta_id = db.Column(db.Integer, db.ForeignKey('herramientas.id'), nullable=True)
    fecha_uso_inicio = db.Column(db.Date, nullable=True)
    fecha_uso_fin = db.Column(db.Date, nullable=True)
    justificacion = db.Column(db.Text, nullable=True)
    complementos = db.Column(db.String(500), nullable=True)

    solicitud = db.relationship('SolicitudMaterial', backref=db.backref('detalles', lazy='selectin', cascade='all, delete-orphan'))
    producto = db.relationship('Producto')
    herramienta = db.relationship('Herramienta', foreign_keys=[herramienta_id])
