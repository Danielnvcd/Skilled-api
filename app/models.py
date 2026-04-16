import uuid
from datetime import datetime, timezone
from app.extensions import db

def _now_utc():
    """Retorna el datetime actual en UTC con tzinfo. Usar como default en modelos."""
    return datetime.now(timezone.utc)

class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), default='user')
    totp_secret = db.Column(db.String(32), nullable=True)
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
    estantes = db.relationship('Estante', backref='almacen', lazy='dynamic', cascade='all, delete-orphan')

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
    stock_minimo = db.Column(db.Numeric(10, 2), default=0, nullable=False)
    imagen_url = db.Column(db.String(500), nullable=True)
    activo = db.Column(db.Boolean, default=True, index=True)
    created_at = db.Column(db.DateTime, default=_now_utc)
    updated_at = db.Column(db.DateTime, default=_now_utc, onupdate=_now_utc)
    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)

    created_by = db.relationship('User', foreign_keys=[created_by_id])

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
    producto_id = db.Column(db.Integer, db.ForeignKey('productos.id'), nullable=False)
    cantidad_solicitada = db.Column(db.Numeric(10, 2), nullable=False)
    cantidad_aprobada = db.Column(db.Numeric(10, 2), default=0)
    cantidad_entregada = db.Column(db.Numeric(10, 2), default=0)
    
    solicitud = db.relationship('SolicitudMaterial', backref=db.backref('detalles', lazy='selectin', cascade='all, delete-orphan'))
    producto = db.relationship('Producto')

