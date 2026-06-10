"""Modelos del dominio Trabajador: Trabajador, CredencialPlanta, DocumentoTrabajador."""
from app.extensions import db
from app.models._base import _now_utc


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
            'fecha_caducidad': self.fecha_caducidad.isoformat() if self.fecha_caducidad else None,
        }


class NotaTrabajador(db.Model):
    """Nota interna sobre un trabajador (el "chatter" de la ficha).

    Texto libre escrito por admin/coordinador: acuerdos verbales, incidencias
    informales, contexto que no cabe en campos estructurados. Se crea/borra
    vía /api/trabajadores/<id>/notas y se pushea por Socket.IO (nota:changed).
    """
    __tablename__ = "trabajador_notas"
    id = db.Column(db.Integer, primary_key=True)
    trabajador_id = db.Column(db.Integer, db.ForeignKey('trabajadores.id'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    texto = db.Column(db.String(2000), nullable=False)
    created_at = db.Column(db.DateTime, default=_now_utc, index=True)

    user = db.relationship('User', foreign_keys=[user_id])

    def to_dict(self):
        return {
            'id': self.id,
            'trabajador_id': self.trabajador_id,
            'user_id': self.user_id,
            'autor': getattr(self.user, 'full_name', None) or getattr(self.user, 'username', None),
            'texto': self.texto,
            'created_at': self.created_at.isoformat() if self.created_at else None,
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
            'fecha_fin': self.fecha_fin.isoformat() if self.fecha_fin else None,
        }
