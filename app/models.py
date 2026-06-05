import uuid
from datetime import datetime, timezone
from app.extensions import db, EncryptedString

def _now_utc():
    """Retorna el datetime actual en UTC con tzinfo. Usar como default en modelos."""
    return datetime.now(timezone.utc)

class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), default='user')
    totp_secret = db.Column(EncryptedString(500), nullable=True)
    # Incrementado cada vez que se cambia la contraseña; invalida todas las sesiones anteriores.
    password_version = db.Column(db.Integer, nullable=False, default=1, server_default='1')

    # Profile Fields
    full_name = db.Column(db.String(150), nullable=True)
    area = db.Column(db.String(100), nullable=True)
    position = db.Column(db.String(100), nullable=True)
    factory = db.Column(db.String(100), nullable=True)
    contact_info = db.Column(db.String(200), nullable=True)
    profile_pic = db.Column(db.String(255), nullable=True, default='default.png')
    last_seen = db.Column(db.DateTime, nullable=True, default=None)

    # Link opcional a Trabajador (RRHH). Permite que un `solicitante_material`
    # vea sus propias herramientas asignadas sin re-implementar mapeo username↔no_empleado.
    trabajador_id = db.Column(db.Integer, db.ForeignKey('trabajadores.id'), nullable=True, index=True)
    trabajador = db.relationship('Trabajador', foreign_keys=[trabajador_id])

class RefreshToken(db.Model):
    """Token de refresco de sesión (HttpOnly cookie 'rt').
    Solo se almacena el hash SHA-256; nunca el token crudo.
    Al hacer logout o cambiar contraseña se revoca.
    """
    __tablename__ = "refresh_tokens"
    id = db.Column(db.Integer, primary_key=True)
    token_hash = db.Column(db.String(64), unique=True, nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)
    revoked = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=_now_utc)

    user = db.relationship('User', backref=db.backref('refresh_tokens', lazy=True, cascade='all, delete-orphan'))


class TwoFactorBackupCode(db.Model):
    """Códigos de respaldo para 2FA TOTP.

    Permite al usuario recuperar acceso cuando pierde el dispositivo
    autenticador. Cada código es one-shot (al consumirse queda marcado);
    regenerar invalida todos los anteriores.

    Almacenamos solo SHA-256 del código (no bcrypt) porque los códigos son
    aleatorios de 12+ chars urlsafe — equivalentes a >70 bits, no necesitan
    estiramiento. Bcrypt sería bcrypteo gratuito sin defensa adicional.
    """
    __tablename__ = "totp_backup_codes"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    # SHA-256 hex = 64 chars
    code_hash = db.Column(db.String(64), nullable=False, index=True)
    consumed_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=_now_utc)

    user = db.relationship('User', backref=db.backref('backup_codes', lazy=True, cascade='all, delete-orphan'))


class AuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user = db.Column(db.String(80))
    action = db.Column(db.String(200))
    ip = db.Column(db.String(45), index=True)
    created_at = db.Column(db.DateTime, default=_now_utc)

class Trabajador(db.Model):
    __tablename__ = "trabajadores"
    
    # Identificadores
    id = db.Column(db.Integer, primary_key=True)
    no_empleado = db.Column(db.String(50), unique=True, nullable=False)
    qr_code = db.Column(db.String(100), unique=True, nullable=True, index=True)
    rfid_uid = db.Column(db.String(64), unique=True, nullable=True, index=True)

    # Credenciales de plantas (One-to-Many)
    credenciales = db.relationship('CredencialPlanta', backref='trabajador', lazy=True, cascade="all, delete-orphan")
    
    # Documentos (One-to-Many)
    documentos = db.relationship('DocumentoTrabajador', backref='trabajador', lazy=True, cascade="all, delete-orphan")
    
    nombre_apellidos = db.Column(db.String(250), nullable=False)
    nombre = db.Column(db.String(250), nullable=False)
    foto_perfil = db.Column(db.String(500), nullable=True)
    
    @property
    def nombre_completo(self):
        return f"{self.nombre} {self.nombre_apellidos}".strip()
    
    # Administrativo / Contratación
    tipo_mov = db.Column(db.String(100))
    tipo_cont = db.Column(db.String(100))
    area = db.Column(db.String(150))
    puesto = db.Column(db.String(150))
    fecha_ingreso = db.Column(db.Date)
    tipo_jornada = db.Column(db.String(100))
    descripcion_servicio = db.Column(db.Text)
    inicio = db.Column(db.Date)
    termino_prueba = db.Column(db.Date)
    fecha_baja = db.Column(db.Date)
    activo = db.Column(db.Boolean, default=True, index=True)
    
    # Datos Personales Generales
    curp = db.Column(db.String(18))
    largo_curp = db.Column(db.Integer)
    rfc = db.Column(db.String(13))
    largo_rfc = db.Column(db.Integer)
    nss = db.Column(db.String(20))
    largo_nss = db.Column(db.Integer)
    domicilio = db.Column(db.Text)
    fecha_nacimiento = db.Column(db.Date)
    letra_fecha_nac = db.Column(db.String(50))
    edad = db.Column(db.Integer)
    sexo = db.Column(db.String(20))
    nacionalidad = db.Column(db.String(100))
    estado_civil = db.Column(db.String(50))
    
    # Contacto
    correo = db.Column(db.String(150))
    celular = db.Column(db.String(20))
    
    # Datos Médicos / Emergencia
    tipo_sangre = db.Column(db.String(10))
    alergias = db.Column(db.Text)
    enfermedades_cronicas = db.Column(db.Text)
    contacto_emergencia = db.Column(db.String(200))
    parentesco_contacto = db.Column(db.String(100))
    numero_contacto_emerg = db.Column(db.String(20))
    lentes = db.Column(db.String(20))
    licencia_conducir = db.Column(db.String(50))
    estatura = db.Column(db.String(20))
    
    # Salarios y Deducciones (Financiero)
    sb = db.Column(db.Numeric(10, 2))
    sdi = db.Column(db.Numeric(10, 2))
    letra = db.Column(db.String(100))
    salario_real_pactado_x_sem = db.Column(db.Numeric(10, 2))
    hr_extra = db.Column(db.Numeric(10, 2))
    infonavit = db.Column(db.Numeric(10, 2))
    ajuste_inbursa = db.Column(db.Numeric(10, 2))
    caja_ahorro = db.Column(db.Numeric(10, 2))
    viaticos = db.Column(db.Numeric(10, 2))
    pago_dia_festivo = db.Column(db.Numeric(10, 2))  # Monto por día festivo trabajado
    pagos_efectivo = db.Column(db.Numeric(10, 2))
    folio_mov_idse = db.Column(db.String(100))
    tipo_pago = db.Column(db.String(100))
    tipo_nomina = db.Column(db.String(50))
    
    # Ubicación y Operación
    no_proyecto = db.Column(db.String(100))
    coord_a_cargo = db.Column(db.String(150))
    ubicacion_actual = db.Column(db.String(150))
    ubicacion_estado = db.Column(db.String(100))
    observaciones = db.Column(db.Text)

class CredencialPlanta(db.Model):
    __tablename__ = "credenciales_plantas"
    id = db.Column(db.Integer, primary_key=True)
    trabajador_id = db.Column(db.Integer, db.ForeignKey('trabajadores.id'), nullable=False, index=True)
    planta = db.Column(db.String(100), nullable=False)
    credencial_id = db.Column(db.String(40), nullable=False)
    fecha_caducidad = db.Column(db.Date, nullable=True)
    
    def to_dict(self):
        return {
            'planta': self.planta,
            'credencial_id': self.credencial_id,
            'fecha_caducidad': self.fecha_caducidad.isoformat() if self.fecha_caducidad else None
        }

class DocumentoTrabajador(db.Model):
    __tablename__ = "documentos_trabajador"
    id = db.Column(db.Integer, primary_key=True)
    trabajador_id = db.Column(db.Integer, db.ForeignKey('trabajadores.id'), nullable=False, index=True)
    nombre_archivo = db.Column(db.String(250), nullable=False)
    ruta_archivo = db.Column(db.String(500), nullable=False)
    tipo_documento = db.Column(db.String(100), nullable=True)
    fecha_subida = db.Column(db.DateTime, default=_now_utc)
    
    fecha_inicio = db.Column(db.Date, nullable=True)
    fecha_fin = db.Column(db.Date, nullable=True)
    
    def to_dict(self):
        return {
            'id': self.id,
            'nombre_archivo': self.nombre_archivo,
            'ruta_archivo': self.ruta_archivo,
            'tipo_documento': self.tipo_documento,
            'fecha_subida': self.fecha_subida.isoformat() if self.fecha_subida else None,
            'fecha_inicio': self.fecha_inicio.isoformat() if self.fecha_inicio else None,
            'fecha_fin': self.fecha_fin.isoformat() if self.fecha_fin else None
        }

proyecto_trabajador = db.Table('proyecto_trabajador',
    db.Column('proyecto_id', db.Integer, db.ForeignKey('proyectos.id'), primary_key=True),
    db.Column('trabajador_id', db.Integer, db.ForeignKey('trabajadores.id'), primary_key=True)
)

class Proyecto(db.Model):
    __tablename__ = "proyectos"
    id = db.Column(db.Integer, primary_key=True)
    numero_proyecto = db.Column(db.String(100), unique=True, nullable=False)
    nombre = db.Column(db.String(250), nullable=True)
    activo = db.Column(db.Boolean, default=True, index=True) # Si es False, "ya no se le deberían cargar horas"
    
    coordinador_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    coordinador = db.relationship('User', foreign_keys=[coordinador_id])
    
    participantes = db.relationship('Trabajador', secondary=proyecto_trabajador, backref=db.backref('proyectos', lazy='dynamic'))
    
    created_at = db.Column(db.DateTime, default=_now_utc)

class ReporteSemanal(db.Model):
    __tablename__ = "reportes_semanales"
    id = db.Column(db.Integer, primary_key=True)
    # Por regla: Inicia martes, termina lunes. Se guarda la fecha de inicio.
    fecha_inicio_semana = db.Column(db.Date, nullable=False, index=True)
    fecha_fin_semana = db.Column(db.Date, nullable=False)
    proyecto_id = db.Column(db.Integer, db.ForeignKey('proyectos.id'), nullable=False)
    proyecto = db.relationship('Proyecto')
    estado = db.Column(db.String(20), default='BORRADOR', index=True) # 'BORRADOR' o 'TERMINADO'
    creado_por_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, default=_now_utc)

class RegistroDiarioHoras(db.Model):
    __tablename__ = "registros_diarios_horas"
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
    tipo_nomina = db.Column(db.String(50)) # Semanal, Por hora, Cuadrado
    
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

class Prenomina(db.Model):
    __tablename__ = "prenominas"
    id = db.Column(db.Integer, primary_key=True)
    reporte_semanal_id = db.Column(db.Integer, db.ForeignKey('reportes_semanales.id'), nullable=True) # Ligado a horas
    trabajador_id = db.Column(db.Integer, db.ForeignKey('trabajadores.id'), nullable=False, index=True)
    trabajador = db.relationship('Trabajador')
    
    # Identificación Semanal
    fecha_inicio = db.Column(db.Date, nullable=False, index=True)
    fecha_fin = db.Column(db.Date, nullable=False)
    
    # PERCEPCIONES
    salario_base = db.Column(db.Numeric(10, 2), default=0) # Según "Semanal", "Por hora", "Cuadrado"
    pago_horas_extras = db.Column(db.Numeric(10, 2), default=0)
    pago_viaticos = db.Column(db.Numeric(10, 2), default=0) # (días * costo precargado)
    pago_festivos = db.Column(db.Numeric(10, 2), default=0)
    depositos_otros = db.Column(db.Numeric(10, 2), default=0) # Reembolsos
    depositos_prestamos = db.Column(db.Numeric(10, 2), default=0)
    
    # DEDUCCIONES
    descuento_infonavit = db.Column(db.Numeric(10, 2), default=0)
    ajuste_inbursa = db.Column(db.Numeric(10, 2), default=0)
    descuentos_otros = db.Column(db.Numeric(10, 2), default=0)
    descuento_prestamos = db.Column(db.Numeric(10, 2), default=0)
    descuento_incidencias = db.Column(db.Numeric(10, 2), default=0) # Faltas, retardos
    
    # DEDUCCIONES EXTRAORDINARIAS
    recuperacion_manual = db.Column(db.Numeric(10, 2), default=0) # Préstamos directos dirección
    
    # TOTALES FINALES
    total_percepciones = db.Column(db.Numeric(10, 2), default=0)
    total_deducciones = db.Column(db.Numeric(10, 2), default=0)
    total_a_pagar = db.Column(db.Numeric(10, 2), default=0)
    
    # Metadatos de Pago
    tipo_pago = db.Column(db.String(50)) # EFECTIVO o TRANSFERENCIA
    estado = db.Column(db.String(20), default='PENDIENTE', index=True) # 'PENDIENTE', 'ABIERTA', 'APROBADO'

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
    frecuencia = db.Column(db.String(50), default='semanal') # semanal, quincenal, mensual
    fecha_inicio = db.Column(db.Date, nullable=True)
    estado = db.Column(db.String(20), default='ACTIVO', index=True) # ACTIVO, LIQUIDADO
    activo = db.Column(db.Boolean, default=True)
    creado_en = db.Column(db.DateTime, default=_now_utc)

class AbonoPrestamo(db.Model):
    """Registro individual de cada pago realizado a un préstamo, ya sea automático por prenómina o abono manual."""
    __tablename__ = "abonos_prestamo"
    id = db.Column(db.Integer, primary_key=True)
    prestamo_id = db.Column(db.Integer, db.ForeignKey('prestamos.id'), nullable=False)
    monto = db.Column(db.Numeric(10, 2), nullable=False)
    fecha_abono = db.Column(db.Date, nullable=False)
    tipo = db.Column(db.String(50), default='NOMINA') # NOMINA, MANUAL
    registrado_por_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True) # opcional, para saber quién lo registró si fue manual
    notas = db.Column(db.String(250), nullable=True)
    
    prestamo = db.relationship('Prestamo', backref=db.backref('abonos', lazy=True, cascade="all, delete-orphan", order_by='AbonoPrestamo.fecha_abono.desc()'))
    registrado_por = db.relationship('User')


class DescuentoPrenomina(db.Model):
    """Descuentos granulares aplicados a una prenómina (incidencias, manuales, préstamos)."""
    __tablename__ = "descuentos_prenomina"
    id = db.Column(db.Integer, primary_key=True)
    prenomina_id = db.Column(db.Integer, db.ForeignKey('prenominas.id'), nullable=False)
    trabajador_id = db.Column(db.Integer, db.ForeignKey('trabajadores.id'), nullable=False, index=True)
    tipo = db.Column(db.String(20), nullable=False) # INCIDENCIA, MANUAL, PRESTAMO
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

# --- MÉTODOS DE AUSENCIAS Y VACACIONES ---

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
    estado = db.Column(db.String(20), default='PROGRAMADA', nullable=False) # PROGRAMADA, EN_CURSO, FINALIZADA, CANCELADA
    
    dias_solicitados = db.Column(db.Integer, default=1)
    motivo = db.Column(db.Text, nullable=True)
    
    creado_por_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=_now_utc)

# --- MÓDULO DE INVENTARIO ---

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
    unidad = db.Column(db.String(50), nullable=False) # pieza, caja, kg, etc.
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
    tipo = db.Column(db.String(50), nullable=False, index=True) # ENTRADA, SALIDA, AJUSTE, TRASPASO
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

class SolicitudMaterial(db.Model):
    __tablename__ = "solicitudes_material"
    id = db.Column(db.Integer, primary_key=True)
    solicitante_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    proyecto = db.Column(db.String(200), nullable=True)
    estatus = db.Column(db.String(50), default='PENDIENTE', nullable=False, index=True) # PENDIENTE, APROBADA, RECHAZADA, ENTREGADA
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


class Notificacion(db.Model):
    __tablename__ = "notificaciones"
    id = db.Column(db.Integer, primary_key=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    tipo = db.Column(db.String(50), nullable=False, index=True)  # REPORTE_CERRADO | PRENOMINA_CERRADA | ACTUALIZACION
    titulo = db.Column(db.String(200), nullable=False)
    mensaje = db.Column(db.String(500), nullable=False)
    url = db.Column(db.String(500), nullable=True)
    leida = db.Column(db.Boolean, default=False, index=True)
    referencia = db.Column(db.String(200), nullable=True, index=True)  # clave única para updates (evitar duplicados)
    created_at = db.Column(db.DateTime, default=_now_utc, index=True)

    usuario = db.relationship('User', foreign_keys=[usuario_id])


def crear_notif_admins(tipo, titulo, mensaje, url=None):
    """Crea una notificación para cada admin/super_admin. El caller debe hacer commit."""
    admins = User.query.filter(
        User.role.in_(['admin', 'super_admin']),
    ).all()
    for admin in admins:
        db.session.add(Notificacion(
            usuario_id=admin.id,
            tipo=tipo,
            titulo=titulo,
            mensaje=mensaje,
            url=url,
        ))


def crear_notif_inventario(tipo, titulo, mensaje, url=None):
    """Notifica a todos los usuarios con rol 'inventario' y a admins.
    Usado por flujos de herramientas (incidencia, solicitud de baja, etc.)."""
    usuarios = User.query.filter(User.role.in_(['inventario', 'admin', 'super_admin'])).all()
    for u in usuarios:
        db.session.add(Notificacion(
            usuario_id=u.id,
            tipo=tipo,
            titulo=titulo,
            mensaje=mensaje,
            url=url,
        ))


# ─── MÓDULO DE HERRAMIENTAS ────────────────────────────────────────────────

# Catálogos de valores válidos. Se validan en backend (no FK a tabla aparte
# para que el catálogo sea evolutivo sin migraciones).
ESTADOS_UNIDAD = ['DISPONIBLE', 'ASIGNADA', 'EN_MANTENIMIENTO', 'DAÑADA', 'EXTRAVIADA', 'DADA_DE_BAJA']
ESTADOS_ASIGNACION = ['ACTIVA', 'DEVUELTA', 'VENCIDA']
ESTADOS_MANTENIMIENTO = ['ABIERTO', 'EN_PROCESO', 'CERRADO']
ESTADOS_INCIDENCIA = ['ABIERTA', 'REVISION', 'RESUELTA', 'RECHAZADA']
ESTADOS_SOLICITUD_BAJA = ['PENDIENTE', 'APROBADA', 'RECHAZADA', 'EJECUTADA']
TIPO_ITEM_SOLICITUD = ['MATERIAL', 'HERRAMIENTA']
USO_HERRAMIENTA = ['MANUAL', 'ELÉCTRICA', 'NEUMÁTICA', 'HIDRÁULICA', 'MEDICIÓN', 'SEGURIDAD', 'OTRO']
TIPO_INCIDENCIA = ['DAÑO', 'EXTRAVIO', 'MAL_FUNCIONAMIENTO', 'OTRO']
TIPO_MANTENIMIENTO = ['PREVENTIVO', 'CORRECTIVO']
CONDICION_HERRAMIENTA = ['BUENA', 'REGULAR', 'MALA']
TIPO_EVENTO_HERRAMIENTA = [
    'ALTA', 'EDICION', 'ASIGNACION', 'DEVOLUCION',
    'MANTENIMIENTO_IN', 'MANTENIMIENTO_OUT', 'INCIDENCIA',
    'BAJA_SOLICITUD', 'BAJA_APROBADA', 'BAJA_RECHAZADA', 'BAJA_EJECUTADA',
    'CAMBIO_ESTADO', 'TRASLADO',
]


class HerramientaCategoria(db.Model):
    """Metadatos visuales para una clasificación de herramienta (icono, color, imagen).
    Coexiste con el campo libre `Herramienta.clasificacion`: el nombre es la PK lógica."""
    __tablename__ = "herramienta_categorias"
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100), unique=True, nullable=False, index=True)
    imagen_url = db.Column(db.String(500), nullable=True)
    icono = db.Column(db.String(50), nullable=True)   # nombre de icono lucide-react
    color = db.Column(db.String(20), nullable=True)   # tono tailwind: 'blue'/'red'/...
    created_at = db.Column(db.DateTime, default=_now_utc)
    updated_at = db.Column(db.DateTime, default=_now_utc, onupdate=_now_utc)
    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)

    created_by = db.relationship('User', foreign_keys=[created_by_id])


class Herramienta(db.Model):
    """Catálogo (tipo) de herramienta. Una fila representa el modelo abstracto
    (ej. "Taladro DeWalt DCD777"); las unidades físicas viven en HerramientaUnidad."""
    __tablename__ = "herramientas"
    id = db.Column(db.Integer, primary_key=True)
    sku = db.Column(db.String(50), unique=True, nullable=False, index=True)
    descripcion = db.Column(db.String(250), nullable=False)
    clasificacion = db.Column(db.String(100), nullable=False, index=True)
    categoria_id = db.Column(db.Integer, db.ForeignKey('herramienta_categorias.id'), nullable=True)
    marca = db.Column(db.String(100), nullable=True)
    modelo = db.Column(db.String(100), nullable=True)
    uso = db.Column(db.String(50), nullable=True)        # USO_HERRAMIENTA
    unidad = db.Column(db.String(50), nullable=False)    # 'pieza', 'juego', 'kit'
    piezas = db.Column(db.Integer, default=1, nullable=False)
    serializada = db.Column(db.Boolean, default=True, nullable=False)
    imagen_url = db.Column(db.String(500), nullable=True)
    activo = db.Column(db.Boolean, default=True, nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=_now_utc)
    updated_at = db.Column(db.DateTime, default=_now_utc, onupdate=_now_utc)
    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)

    categoria = db.relationship('HerramientaCategoria', foreign_keys=[categoria_id])
    created_by = db.relationship('User', foreign_keys=[created_by_id])
    unidades = db.relationship('HerramientaUnidad', backref='herramienta',
                                lazy='select', cascade='all, delete-orphan')


class HerramientaUnidad(db.Model):
    """Instancia física rastreable. Lleva no_serie (si serializada), estado,
    ubicación y asignación actual. Toda operación genera EventoHerramienta."""
    __tablename__ = "herramienta_unidades"
    id = db.Column(db.Integer, primary_key=True)
    herramienta_id = db.Column(db.Integer, db.ForeignKey('herramientas.id'), nullable=False, index=True)
    no_serie = db.Column(db.String(100), unique=True, nullable=True, index=True)
    codigo_interno = db.Column(db.String(50), unique=True, nullable=False, index=True)
    qr_code = db.Column(db.String(100), unique=True, nullable=False, index=True)
    estado = db.Column(db.String(20), default='DISPONIBLE', nullable=False, index=True)
    almacen_id = db.Column(db.Integer, db.ForeignKey('almacenes.id'), nullable=True)
    estante_id = db.Column(db.Integer, db.ForeignKey('estantes.id'), nullable=True)
    asignado_trabajador_id = db.Column(db.Integer, db.ForeignKey('trabajadores.id'), nullable=True, index=True)
    cantidad = db.Column(db.Numeric(10, 2), default=1, nullable=False)
    complementos = db.Column(db.String(500), nullable=True)
    fecha_adquisicion = db.Column(db.Date, nullable=True)
    costo_adquisicion = db.Column(db.Numeric(10, 2), nullable=True)
    vida_util_meses = db.Column(db.Integer, nullable=True)
    observaciones = db.Column(db.Text, nullable=True)
    fecha_baja = db.Column(db.DateTime, nullable=True)
    motivo_baja = db.Column(db.String(250), nullable=True)
    created_at = db.Column(db.DateTime, default=_now_utc)
    updated_at = db.Column(db.DateTime, default=_now_utc, onupdate=_now_utc)

    almacen = db.relationship('Almacen', foreign_keys=[almacen_id])
    estante = db.relationship('Estante', foreign_keys=[estante_id])
    asignado_trabajador = db.relationship('Trabajador', foreign_keys=[asignado_trabajador_id])


class AsignacionHerramienta(db.Model):
    """Préstamo/entrega de una unidad a un trabajador. La unidad pasa a ASIGNADA
    mientras la asignación esté ACTIVA."""
    __tablename__ = "asignaciones_herramienta"
    id = db.Column(db.Integer, primary_key=True)
    unidad_id = db.Column(db.Integer, db.ForeignKey('herramienta_unidades.id'), nullable=False, index=True)
    trabajador_id = db.Column(db.Integer, db.ForeignKey('trabajadores.id'), nullable=False, index=True)
    solicitud_id = db.Column(db.Integer, db.ForeignKey('solicitudes_material.id'), nullable=True)
    proyecto = db.Column(db.String(200), nullable=True)
    fecha_entrega = db.Column(db.DateTime, default=_now_utc, nullable=False)
    fecha_devolucion_prevista = db.Column(db.DateTime, nullable=True)
    fecha_devolucion_real = db.Column(db.DateTime, nullable=True)
    estado = db.Column(db.String(20), default='ACTIVA', nullable=False, index=True)
    condicion_entrega = db.Column(db.String(20), nullable=True)
    condicion_devolucion = db.Column(db.String(20), nullable=True)
    observaciones_entrega = db.Column(db.Text, nullable=True)
    observaciones_devolucion = db.Column(db.Text, nullable=True)
    entregado_por_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    recibido_por_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)

    unidad = db.relationship('HerramientaUnidad',
                              backref=db.backref('asignaciones', lazy='select', order_by='AsignacionHerramienta.fecha_entrega.desc()'))
    trabajador = db.relationship('Trabajador', foreign_keys=[trabajador_id])
    solicitud = db.relationship('SolicitudMaterial', foreign_keys=[solicitud_id])
    entregado_por = db.relationship('User', foreign_keys=[entregado_por_id])
    recibido_por = db.relationship('User', foreign_keys=[recibido_por_id])


class MantenimientoHerramienta(db.Model):
    __tablename__ = "mantenimientos_herramienta"
    id = db.Column(db.Integer, primary_key=True)
    unidad_id = db.Column(db.Integer, db.ForeignKey('herramienta_unidades.id'), nullable=False, index=True)
    tipo = db.Column(db.String(20), nullable=False)        # TIPO_MANTENIMIENTO
    motivo = db.Column(db.String(250), nullable=False)
    proveedor = db.Column(db.String(150), nullable=True)
    fecha_inicio = db.Column(db.DateTime, default=_now_utc, nullable=False)
    fecha_fin = db.Column(db.DateTime, nullable=True)
    costo = db.Column(db.Numeric(10, 2), nullable=True)
    observaciones = db.Column(db.Text, nullable=True)
    estado_final_unidad = db.Column(db.String(20), nullable=True)   # DISPONIBLE o DAÑADA al cerrar
    estado = db.Column(db.String(20), default='ABIERTO', nullable=False, index=True)
    abierto_por_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    cerrado_por_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)

    unidad = db.relationship('HerramientaUnidad',
                              backref=db.backref('mantenimientos', lazy='select', order_by='MantenimientoHerramienta.fecha_inicio.desc()'))
    abierto_por = db.relationship('User', foreign_keys=[abierto_por_id])
    cerrado_por = db.relationship('User', foreign_keys=[cerrado_por_id])


class IncidenciaHerramienta(db.Model):
    __tablename__ = "incidencias_herramienta"
    id = db.Column(db.Integer, primary_key=True)
    unidad_id = db.Column(db.Integer, db.ForeignKey('herramienta_unidades.id'), nullable=False, index=True)
    reportado_por_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    tipo = db.Column(db.String(30), nullable=False)         # TIPO_INCIDENCIA
    descripcion = db.Column(db.Text, nullable=False)
    estado = db.Column(db.String(20), default='ABIERTA', nullable=False, index=True)
    fecha_reporte = db.Column(db.DateTime, default=_now_utc, nullable=False)
    atendido_por_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    resolucion = db.Column(db.Text, nullable=True)
    fecha_cierre = db.Column(db.DateTime, nullable=True)

    unidad = db.relationship('HerramientaUnidad',
                              backref=db.backref('incidencias', lazy='select', order_by='IncidenciaHerramienta.fecha_reporte.desc()'))
    reportado_por = db.relationship('User', foreign_keys=[reportado_por_id])
    atendido_por = db.relationship('User', foreign_keys=[atendido_por_id])


class SolicitudBajaHerramienta(db.Model):
    __tablename__ = "solicitudes_baja_herramienta"
    id = db.Column(db.Integer, primary_key=True)
    unidad_id = db.Column(db.Integer, db.ForeignKey('herramienta_unidades.id'), nullable=False, index=True)
    solicitante_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    motivo = db.Column(db.Text, nullable=False)
    estado = db.Column(db.String(20), default='PENDIENTE', nullable=False, index=True)
    autorizado_por_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    ejecutado_por_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    fecha_solicitud = db.Column(db.DateTime, default=_now_utc, nullable=False)
    fecha_autorizacion = db.Column(db.DateTime, nullable=True)
    fecha_ejecucion = db.Column(db.DateTime, nullable=True)
    observaciones = db.Column(db.Text, nullable=True)

    unidad = db.relationship('HerramientaUnidad',
                              backref=db.backref('solicitudes_baja', lazy='select', order_by='SolicitudBajaHerramienta.fecha_solicitud.desc()'))
    solicitante = db.relationship('User', foreign_keys=[solicitante_id])
    autorizado_por = db.relationship('User', foreign_keys=[autorizado_por_id])
    ejecutado_por = db.relationship('User', foreign_keys=[ejecutado_por_id])


class EventoHerramienta(db.Model):
    """Bitácora funcional por unidad. Distinta de AuditLog (auditoría seguridad)
    para permitir queries rápidas del timeline sin escanear AuditLog completo."""
    __tablename__ = "eventos_herramienta"
    id = db.Column(db.Integer, primary_key=True)
    unidad_id = db.Column(db.Integer, db.ForeignKey('herramienta_unidades.id'), nullable=False, index=True)
    tipo_evento = db.Column(db.String(40), nullable=False, index=True)  # TIPO_EVENTO_HERRAMIENTA
    estado_anterior = db.Column(db.String(20), nullable=True)
    estado_nuevo = db.Column(db.String(20), nullable=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    observaciones = db.Column(db.Text, nullable=True)
    referencia_id = db.Column(db.Integer, nullable=True)       # id de la asignación/mant/inc/baja relacionada
    referencia_tipo = db.Column(db.String(40), nullable=True)  # 'asignacion'/'mantenimiento'/'incidencia'/'solicitud_baja'
    fecha = db.Column(db.DateTime, default=_now_utc, nullable=False)

    unidad = db.relationship('HerramientaUnidad',
                              backref=db.backref('eventos', lazy='select', order_by='EventoHerramienta.fecha.desc()'))
    usuario = db.relationship('User', foreign_keys=[usuario_id])


class MediaHerramienta(db.Model):
    """Foto principal de la unidad (evento_id=NULL, tipo='FOTO_HERRAMIENTA')
    o evidencia de un evento (evento_id=N, tipo='EVIDENCIA_EVENTO')."""
    __tablename__ = "media_herramienta"
    id = db.Column(db.Integer, primary_key=True)
    unidad_id = db.Column(db.Integer, db.ForeignKey('herramienta_unidades.id'), nullable=False, index=True)
    evento_id = db.Column(db.Integer, db.ForeignKey('eventos_herramienta.id'), nullable=True)
    tipo = db.Column(db.String(30), nullable=False, index=True)   # 'FOTO_HERRAMIENTA' o 'EVIDENCIA_EVENTO'
    ruta_archivo = db.Column(db.String(500), nullable=False)
    nombre_original = db.Column(db.String(250), nullable=True)
    mime = db.Column(db.String(50), nullable=True)
    tamano_bytes = db.Column(db.Integer, nullable=True)
    subido_por_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=_now_utc, nullable=False)

    unidad = db.relationship('HerramientaUnidad',
                              backref=db.backref('media', lazy='select', order_by='MediaHerramienta.created_at.desc()'))
    evento = db.relationship('EventoHerramienta',
                              backref=db.backref('media', lazy='select'))
    subido_por = db.relationship('User', foreign_keys=[subido_por_id])


def crear_evento_herramienta(unidad, tipo_evento, usuario, observaciones=None,
                              estado_anterior=None, estado_nuevo=None,
                              referencia_id=None, referencia_tipo=None):
    """Helper: registra evento sin hacer commit (deja al caller decidir el batch).
    Centraliza el patrón para que toda operación de herramienta deje trazabilidad."""
    evt = EventoHerramienta(
        unidad_id=unidad.id,
        tipo_evento=tipo_evento,
        estado_anterior=estado_anterior,
        estado_nuevo=estado_nuevo,
        usuario_id=usuario.id,
        observaciones=observaciones,
        referencia_id=referencia_id,
        referencia_tipo=referencia_tipo,
    )
    db.session.add(evt)
    return evt

