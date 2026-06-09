"""Modelos del dominio Préstamos: Prestamo, AbonoPrestamo."""
from app.extensions import db
from app.models._base import _now_utc


class Prestamo(db.Model):
    """Modelo para controlar los depósitos de préstamos y la programación de sus plazos/descuentos."""
    __tablename__ = "prestamos"
    id = db.Column(db.Integer, primary_key=True)
    trabajador_id = db.Column(db.Integer, db.ForeignKey('trabajadores.id'), nullable=False, index=True)
    trabajador = db.relationship('Trabajador', backref=db.backref('prestamos', lazy=True))
    monto_total = db.Column(db.Numeric(10, 2), nullable=False)
    plazo_semanas = db.Column(db.Integer, nullable=False)
    descuento_semanal = db.Column(db.Numeric(10, 2), nullable=False)
    monto_restante = db.Column(db.Numeric(10, 2), nullable=False)
    motivo = db.Column(db.String(250), nullable=True)
    frecuencia = db.Column(db.String(50), default='semanal')  # semanal, quincenal, mensual
    fecha_inicio = db.Column(db.Date, nullable=True)
    estado = db.Column(db.String(20), default='ACTIVO', index=True)  # ACTIVO, LIQUIDADO
    activo = db.Column(db.Boolean, default=True)
    creado_en = db.Column(db.DateTime, default=_now_utc)


class AbonoPrestamo(db.Model):
    """Registro individual de cada pago realizado a un préstamo, ya sea automático por prenómina o abono manual."""
    __tablename__ = "abonos_prestamo"
    id = db.Column(db.Integer, primary_key=True)
    prestamo_id = db.Column(db.Integer, db.ForeignKey('prestamos.id'), nullable=False)
    monto = db.Column(db.Numeric(10, 2), nullable=False)
    fecha_abono = db.Column(db.Date, nullable=False)
    tipo = db.Column(db.String(50), default='NOMINA')  # NOMINA, MANUAL
    registrado_por_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)  # opcional, para saber quién lo registró si fue manual
    notas = db.Column(db.String(250), nullable=True)

    prestamo = db.relationship('Prestamo', backref=db.backref('abonos', lazy=True, cascade="all, delete-orphan", order_by='AbonoPrestamo.fecha_abono.desc()'))
    registrado_por = db.relationship('User')
