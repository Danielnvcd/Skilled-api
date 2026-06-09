"""Modelos del dominio Prenómina: Prenomina, DescuentoPrenomina, DepositoExtra."""
from app.extensions import db
from app.models._base import _now_utc


class Prenomina(db.Model):
    __tablename__ = "prenominas"
    id = db.Column(db.Integer, primary_key=True)
    reporte_semanal_id = db.Column(db.Integer, db.ForeignKey('reportes_semanales.id'), nullable=True)  # Ligado a horas
    trabajador_id = db.Column(db.Integer, db.ForeignKey('trabajadores.id'), nullable=False, index=True)
    trabajador = db.relationship('Trabajador')

    # Identificación Semanal
    fecha_inicio = db.Column(db.Date, nullable=False, index=True)
    fecha_fin = db.Column(db.Date, nullable=False)

    # PERCEPCIONES
    salario_base = db.Column(db.Numeric(10, 2), default=0)  # Según "Semanal", "Por hora", "Cuadrado"
    pago_horas_extras = db.Column(db.Numeric(10, 2), default=0)
    pago_viaticos = db.Column(db.Numeric(10, 2), default=0)  # (días * costo precargado)
    pago_festivos = db.Column(db.Numeric(10, 2), default=0)
    depositos_otros = db.Column(db.Numeric(10, 2), default=0)  # Reembolsos
    depositos_prestamos = db.Column(db.Numeric(10, 2), default=0)

    # DEDUCCIONES
    descuento_infonavit = db.Column(db.Numeric(10, 2), default=0)
    ajuste_inbursa = db.Column(db.Numeric(10, 2), default=0)
    descuentos_otros = db.Column(db.Numeric(10, 2), default=0)
    descuento_prestamos = db.Column(db.Numeric(10, 2), default=0)
    descuento_incidencias = db.Column(db.Numeric(10, 2), default=0)  # Faltas, retardos

    # DEDUCCIONES EXTRAORDINARIAS
    recuperacion_manual = db.Column(db.Numeric(10, 2), default=0)  # Préstamos directos dirección

    # TOTALES FINALES
    total_percepciones = db.Column(db.Numeric(10, 2), default=0)
    total_deducciones = db.Column(db.Numeric(10, 2), default=0)
    total_a_pagar = db.Column(db.Numeric(10, 2), default=0)

    # Metadatos de Pago
    tipo_pago = db.Column(db.String(50))  # EFECTIVO o TRANSFERENCIA
    estado = db.Column(db.String(20), default='PENDIENTE', index=True)  # 'PENDIENTE', 'ABIERTA', 'APROBADO'


class DescuentoPrenomina(db.Model):
    """Descuentos granulares aplicados a una prenómina (incidencias, manuales, préstamos)."""
    __tablename__ = "descuentos_prenomina"
    id = db.Column(db.Integer, primary_key=True)
    prenomina_id = db.Column(db.Integer, db.ForeignKey('prenominas.id'), nullable=False)
    trabajador_id = db.Column(db.Integer, db.ForeignKey('trabajadores.id'), nullable=False, index=True)
    tipo = db.Column(db.String(20), nullable=False)  # INCIDENCIA, MANUAL, PRESTAMO
    concepto = db.Column(db.String(250), nullable=False)
    monto = db.Column(db.Numeric(10, 2), nullable=False)
    fecha_incidencia = db.Column(db.Date, nullable=True)
    created_at = db.Column(db.DateTime, default=_now_utc)

    prenomina = db.relationship('Prenomina', backref=db.backref('descuentos_detalle', lazy=True, cascade='all, delete-orphan'))
    trabajador = db.relationship('Trabajador')


class DepositoExtra(db.Model):
    """Depósitos adicionales aplicados a una prenómina."""
    __tablename__ = "depositos_extra"
    id = db.Column(db.Integer, primary_key=True)
    prenomina_id = db.Column(db.Integer, db.ForeignKey('prenominas.id'), nullable=False)
    trabajador_id = db.Column(db.Integer, db.ForeignKey('trabajadores.id'), nullable=False, index=True)
    monto = db.Column(db.Numeric(10, 2), nullable=False)
    concepto = db.Column(db.String(250), nullable=False)
    created_at = db.Column(db.DateTime, default=_now_utc)

    prenomina = db.relationship('Prenomina', backref=db.backref('depositos_detalle', lazy=True, cascade='all, delete-orphan'))
    trabajador = db.relationship('Trabajador')
