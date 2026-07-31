"""Modelos del dominio Horas: ReporteSemanal, RegistroDiarioHoras, Ausencia, SaldoVacaciones."""
from app.extensions import db
from app.models._base import _now_utc


class ReporteSemanal(db.Model):
    __tablename__ = "reportes_semanales"
    # Índices con los nombres y la forma que YA tienen en la base. Declararlos
    # aquí evita que `flask db migrate` proponga borrarlos y recrearlos.
    __table_args__ = (
        db.Index('ix_reportes_fecha_estado', 'fecha_inicio_semana', 'estado'),
    )

    id = db.Column(db.Integer, primary_key=True)
    # Por regla: Inicia martes, termina lunes. Se guarda la fecha de inicio.
    fecha_inicio_semana = db.Column(db.Date, nullable=False, index=True)
    fecha_fin_semana = db.Column(db.Date, nullable=False)
    proyecto_id = db.Column(db.Integer, db.ForeignKey('proyectos.id'), nullable=False)
    proyecto = db.relationship('Proyecto')
    estado = db.Column(db.String(20), default='BORRADOR', index=True)  # 'BORRADOR' o 'TERMINADO'
    creado_por_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, default=_now_utc)


class RegistroDiarioHoras(db.Model):
    __tablename__ = "registros_diarios_horas"
    # Índices con los nombres y la forma que YA tienen en la base. Declararlos
    # aquí evita que `flask db migrate` proponga borrarlos y recrearlos.
    __table_args__ = (
        db.Index('ix_rdh_reporte_fecha', 'reporte_id', 'fecha'),
        db.Index('ix_rdh_reporte_trabajador', 'reporte_id', 'trabajador_id'),
    )

    id = db.Column(db.Integer, primary_key=True)
    reporte_id = db.Column(db.Integer, db.ForeignKey('reportes_semanales.id'), nullable=False, index=True)
    trabajador_id = db.Column(db.Integer, db.ForeignKey('trabajadores.id'), nullable=False, index=True)

    fecha = db.Column(db.Date, nullable=False, index=True)

    # Horarios
    hora_entrada = db.Column(db.Time, nullable=True)
    hora_salida = db.Column(db.Time, nullable=True)

    # Comida
    tomo_comida = db.Column(db.Boolean, default=False)

    # Viáticos y Día Festivo (toggles por registro)
    aplica_viaticos = db.Column(db.Boolean, default=False)
    monto_viaticos_manual = db.Column(db.Numeric(10, 2), nullable=True)  # None = usar monto del perfil del trabajador
    aplica_dia_festivo = db.Column(db.Boolean, default=False)

    # Incidencias
    incidencia = db.Column(db.String(100), nullable=True)

    # Tipo de Nómina al momento del registro
    tipo_nomina = db.Column(db.String(50))  # Semanal, Por hora, Cuadrado

    # Horas productivas calculadas (en formato número)
    horas_productivas = db.Column(db.Numeric(5, 2), nullable=True)

    # Sincronización offline (cliente kiosko RFID):
    # client_record_id = UUID generado por el cliente; permite idempotencia en reintentos.
    # modificado_en = timestamp del último cambio, usado para LWW al sincronizar.
    client_record_id = db.Column(db.String(36), unique=True, nullable=True, index=True)
    modificado_en = db.Column(db.DateTime(timezone=True), default=_now_utc, onupdate=_now_utc)

    # Relaciones
    reporte = db.relationship('ReporteSemanal', backref=db.backref('registros', lazy=True, cascade="all, delete-orphan"))
    trabajador = db.relationship('Trabajador')


# --- AUSENCIAS Y VACACIONES ---

class SaldoVacaciones(db.Model):
    """
    Guarda el resumen anual/histórico de días de vacaciones de un trabajador.
    """
    __tablename__ = "saldo_vacaciones"
    id = db.Column(db.Integer, primary_key=True)
    trabajador_id = db.Column(db.Integer, db.ForeignKey('trabajadores.id'), nullable=False, index=True)
    trabajador = db.relationship('Trabajador', backref=db.backref('saldo_vacaciones', uselist=False, cascade="all, delete-orphan"))

    dias_totales_asignados = db.Column(db.Integer, default=0, nullable=False)
    dias_disfrutados = db.Column(db.Integer, default=0, nullable=False)

    updated_at = db.Column(db.DateTime, default=_now_utc, onupdate=_now_utc)


class Ausencia(db.Model):
    """
    El registro maestro para cuando un trabajador ausenta un turno programado.
    Sirve para Vacaciones, Incapacidades, Permisos, etc.
    """
    __tablename__ = "ausencias"
    id = db.Column(db.Integer, primary_key=True)
    trabajador_id = db.Column(db.Integer, db.ForeignKey('trabajadores.id'), nullable=False, index=True)
    trabajador = db.relationship('Trabajador', backref=db.backref('ausencias_registros', lazy='dynamic', cascade="all, delete-orphan"))

    fecha_inicio = db.Column(db.Date, nullable=False, index=True)
    fecha_fin = db.Column(db.Date, nullable=False, index=True)

    tipo_ausencia = db.Column(db.String(50), nullable=False)
    estado = db.Column(db.String(20), default='PROGRAMADA', nullable=False)  # PROGRAMADA, EN_CURSO, FINALIZADA, CANCELADA

    dias_solicitados = db.Column(db.Integer, default=1)
    motivo = db.Column(db.Text, nullable=True)

    creado_por_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=_now_utc)
