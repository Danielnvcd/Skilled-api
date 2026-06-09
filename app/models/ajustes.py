"""Modelos del dominio Ajustes Inbursa: AjustePeriodo, AjusteTrabajadorPeriodo, AjusteDescuento."""
from app.extensions import db
from app.models._base import _now_utc


class AjustePeriodo(db.Model):
    """Periodo de ajuste Inbursa (mensual). Agrupa los descuentos de recuperación de depósitos adelantados."""
    __tablename__ = "ajuste_periodos"
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100), nullable=False)  # ej. "Febrero 2026"
    fecha_inicio = db.Column(db.Date, nullable=False)
    fecha_fin = db.Column(db.Date, nullable=False)
    estado = db.Column(db.String(20), default='ABIERTO', index=True)  # ABIERTO, CERRADO
    created_at = db.Column(db.DateTime, default=_now_utc)


class AjusteTrabajadorPeriodo(db.Model):
    """Vincula un trabajador a un periodo de ajuste con su monto meta (depósito adelantado)."""
    __tablename__ = "ajuste_trabajadores_periodo"
    id = db.Column(db.Integer, primary_key=True)
    periodo_id = db.Column(db.Integer, db.ForeignKey('ajuste_periodos.id'), nullable=False, index=True)
    trabajador_id = db.Column(db.Integer, db.ForeignKey('trabajadores.id'), nullable=False, index=True)
    monto_meta = db.Column(db.Numeric(10, 2), nullable=False)  # Lo que se le depositó por adelantado

    periodo = db.relationship('AjustePeriodo', backref=db.backref('trabajadores_periodo', lazy=True, cascade='all, delete-orphan'))
    trabajador = db.relationship('Trabajador')


class AjusteDescuento(db.Model):
    """Descuento individual de ajuste Inbursa por trabajador y fecha."""
    __tablename__ = "ajuste_descuentos"
    id = db.Column(db.Integer, primary_key=True)
    periodo_id = db.Column(db.Integer, db.ForeignKey('ajuste_periodos.id'), nullable=False, index=True)
    trabajador_id = db.Column(db.Integer, db.ForeignKey('trabajadores.id'), nullable=False, index=True)
    monto = db.Column(db.Numeric(10, 2), nullable=False)
    fecha_descuento = db.Column(db.Date, nullable=False)
    notas = db.Column(db.String(250), nullable=True)
    cobrado = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=_now_utc)

    periodo = db.relationship('AjustePeriodo', backref=db.backref('descuentos', lazy=True, cascade='all, delete-orphan'))
    trabajador = db.relationship('Trabajador')
