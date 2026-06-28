"""Modelo Proyecto + tabla M2M proyecto_trabajador."""
from app.extensions import db
from app.models._base import _now_utc


proyecto_trabajador = db.Table(
    'proyecto_trabajador',
    db.Column('proyecto_id', db.Integer, db.ForeignKey('proyectos.id'), primary_key=True),
    db.Column('trabajador_id', db.Integer, db.ForeignKey('trabajadores.id'), primary_key=True),
)


class Proyecto(db.Model):
    __tablename__ = "proyectos"
    id = db.Column(db.Integer, primary_key=True)
    numero_proyecto = db.Column(db.String(100), unique=True, nullable=False)
    nombre = db.Column(db.String(250), nullable=True)
    activo = db.Column(db.Boolean, default=True, index=True)  # Si es False, "ya no se le deberían cargar horas"

    coordinador_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    coordinador = db.relationship('User', foreign_keys=[coordinador_id])

    participantes = db.relationship('Trabajador', secondary=proyecto_trabajador, backref=db.backref('proyectos', lazy='dynamic'))

    created_at = db.Column(db.DateTime, default=_now_utc)


class ProyectoMaterialPlan(db.Model):
    """Plan/presupuesto de materiales de un proyecto (lo captura el rol Inventario).

    Es la línea base contra la que se mide el consumo real. El consumo NO vive
    aquí: se deriva de `SolicitudMaterialDetalle.cantidad_entregada` de las
    solicitudes ligadas al proyecto (`SolicitudMaterial.proyecto_id`). Así el
    panel compara planeado vs. entregado, el % ocupado y si se pasó o no.

    El costo es en vivo desde el catálogo: cantidad × `Producto.precio_unitario`
    (no se guarda precio aquí, ver decisión en el plan de la feature).
    """
    __tablename__ = "proyecto_material_plan"
    id = db.Column(db.Integer, primary_key=True)
    proyecto_id = db.Column(db.Integer, db.ForeignKey('proyectos.id', ondelete='CASCADE'), nullable=False, index=True)
    producto_id = db.Column(db.Integer, db.ForeignKey('productos.id'), nullable=False, index=True)
    cantidad_planeada = db.Column(db.Numeric(10, 2), default=0, nullable=False)
    notas = db.Column(db.String(500), nullable=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=_now_utc)
    updated_at = db.Column(db.DateTime, default=_now_utc, onupdate=_now_utc)

    proyecto = db.relationship('Proyecto', backref=db.backref('plan_materiales', lazy='select', cascade='all, delete-orphan'))
    producto = db.relationship('Producto', foreign_keys=[producto_id])
    created_by = db.relationship('User', foreign_keys=[created_by_id])

    __table_args__ = (
        db.UniqueConstraint('proyecto_id', 'producto_id', name='uq_proyecto_producto_plan'),
    )


class ProyectoPlanHistorial(db.Model):
    """Bitácora de cambios del plan de materiales (una fila por guardado).

    Registra quién y cuándo editó el plan, con el desglose estructurado de
    cambios (agregados / modificados / eliminados) para el panel de historial.
    `usuario` queda denormalizado (username) para sobrevivir el borrado del User.
    """
    __tablename__ = "proyecto_plan_historial"
    id = db.Column(db.Integer, primary_key=True)
    proyecto_id = db.Column(db.Integer, db.ForeignKey('proyectos.id', ondelete='CASCADE'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    usuario = db.Column(db.String(80), nullable=True)
    resumen = db.Column(db.String(500), nullable=True)            # "+[..] ~[..] -[..]"
    cambios = db.Column(db.JSON, nullable=True)                   # {agregados, modificados, eliminados}
    n_agregados = db.Column(db.Integer, default=0, nullable=False)
    n_modificados = db.Column(db.Integer, default=0, nullable=False)
    n_eliminados = db.Column(db.Integer, default=0, nullable=False)
    created_at = db.Column(db.DateTime, default=_now_utc, index=True)

    proyecto = db.relationship('Proyecto', backref=db.backref('plan_historial', lazy='select', cascade='all, delete-orphan'))
    user = db.relationship('User', foreign_keys=[user_id])
