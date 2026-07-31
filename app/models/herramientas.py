"""Modelos del dominio Herramientas: catálogo, unidades, asignaciones, mantenimientos,
incidencias, solicitudes de baja, eventos y media. Incluye helpers y catálogos de
constantes (ESTADOS_*, TIPO_*).
"""
from app.extensions import db
from app.models._base import _now_utc


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

    # Nombres FIJADOS a los que ya existen en la base. Sin esto,
    # SQLAlchemy los bautizaría `ix_<tabla>_<columna>` y Alembic los
    # vería como índices distintos: `flask db migrate` generaría una
    # migración que los borra y recrea, bloqueando escrituras sin
    # ninguna ganancia. Mismo índice, mismas columnas, solo el nombre.
    # Índices con los nombres y la forma que YA tienen en la base. Declararlos
    # aquí evita que `flask db migrate` proponga borrarlos y recrearlos.
    __table_args__ = (
        db.Index('ix_h_unid_asig_trab', 'asignado_trabajador_id'),
        db.Index('ix_h_unid_codigo_interno', 'codigo_interno', unique=True),
        db.Index('ix_h_unid_estado', 'estado'),
        db.Index('ix_h_unid_herramienta_id', 'herramienta_id'),
        db.Index('ix_h_unid_herr_estado', 'herramienta_id', 'estado'),
        db.Index('ix_h_unid_no_serie', 'no_serie', unique=True),
        db.Index('ix_h_unid_qr_code', 'qr_code', unique=True),
    )
    id = db.Column(db.Integer, primary_key=True)
    herramienta_id = db.Column(db.Integer, db.ForeignKey('herramientas.id'), nullable=False)
    no_serie = db.Column(db.String(100), nullable=True)
    codigo_interno = db.Column(db.String(50), nullable=False)
    qr_code = db.Column(db.String(100), nullable=False)
    estado = db.Column(db.String(20), default='DISPONIBLE', nullable=False)
    almacen_id = db.Column(db.Integer, db.ForeignKey('almacenes.id'), nullable=True)
    estante_id = db.Column(db.Integer, db.ForeignKey('estantes.id'), nullable=True)
    asignado_trabajador_id = db.Column(db.Integer, db.ForeignKey('trabajadores.id'), nullable=True)
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

    # Nombres FIJADOS a los que ya existen en la base. Sin esto,
    # SQLAlchemy los bautizaría `ix_<tabla>_<columna>` y Alembic los
    # vería como índices distintos: `flask db migrate` generaría una
    # migración que los borra y recrea, bloqueando escrituras sin
    # ninguna ganancia. Mismo índice, mismas columnas, solo el nombre.
    # Índices con los nombres y la forma que YA tienen en la base. Declararlos
    # aquí evita que `flask db migrate` proponga borrarlos y recrearlos.
    __table_args__ = (
        db.Index('ix_asig_estado', 'estado'),
        db.Index('ix_asig_trabajador_id', 'trabajador_id'),
        db.Index('ix_asig_unidad_id', 'unidad_id'),
        db.Index('ix_asig_unidad_estado', 'unidad_id', 'estado'),
    )
    id = db.Column(db.Integer, primary_key=True)
    unidad_id = db.Column(db.Integer, db.ForeignKey('herramienta_unidades.id'), nullable=False)
    trabajador_id = db.Column(db.Integer, db.ForeignKey('trabajadores.id'), nullable=False)
    solicitud_id = db.Column(db.Integer, db.ForeignKey('solicitudes_material.id'), nullable=True)
    proyecto = db.Column(db.String(200), nullable=True)
    fecha_entrega = db.Column(db.DateTime, default=_now_utc, nullable=False)
    fecha_devolucion_prevista = db.Column(db.DateTime, nullable=True)
    fecha_devolucion_real = db.Column(db.DateTime, nullable=True)
    estado = db.Column(db.String(20), default='ACTIVA', nullable=False)
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

    # Nombres FIJADOS a los que ya existen en la base. Sin esto,
    # SQLAlchemy los bautizaría `ix_<tabla>_<columna>` y Alembic los
    # vería como índices distintos: `flask db migrate` generaría una
    # migración que los borra y recrea, bloqueando escrituras sin
    # ninguna ganancia. Mismo índice, mismas columnas, solo el nombre.
    __table_args__ = (
        db.Index('ix_mant_estado', 'estado'),
        db.Index('ix_mant_unidad_id', 'unidad_id'),
    )
    id = db.Column(db.Integer, primary_key=True)
    unidad_id = db.Column(db.Integer, db.ForeignKey('herramienta_unidades.id'), nullable=False)
    tipo = db.Column(db.String(20), nullable=False)        # TIPO_MANTENIMIENTO
    motivo = db.Column(db.String(250), nullable=False)
    proveedor = db.Column(db.String(150), nullable=True)
    fecha_inicio = db.Column(db.DateTime, default=_now_utc, nullable=False)
    fecha_fin = db.Column(db.DateTime, nullable=True)
    costo = db.Column(db.Numeric(10, 2), nullable=True)
    observaciones = db.Column(db.Text, nullable=True)
    estado_final_unidad = db.Column(db.String(20), nullable=True)   # DISPONIBLE o DAÑADA al cerrar
    estado = db.Column(db.String(20), default='ABIERTO', nullable=False)
    abierto_por_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    cerrado_por_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)

    unidad = db.relationship('HerramientaUnidad',
                              backref=db.backref('mantenimientos', lazy='select', order_by='MantenimientoHerramienta.fecha_inicio.desc()'))
    abierto_por = db.relationship('User', foreign_keys=[abierto_por_id])
    cerrado_por = db.relationship('User', foreign_keys=[cerrado_por_id])


class IncidenciaHerramienta(db.Model):
    __tablename__ = "incidencias_herramienta"

    # Nombres FIJADOS a los que ya existen en la base. Sin esto,
    # SQLAlchemy los bautizaría `ix_<tabla>_<columna>` y Alembic los
    # vería como índices distintos: `flask db migrate` generaría una
    # migración que los borra y recrea, bloqueando escrituras sin
    # ninguna ganancia. Mismo índice, mismas columnas, solo el nombre.
    __table_args__ = (
        db.Index('ix_inc_estado', 'estado'),
        db.Index('ix_inc_reportado', 'reportado_por_id'),
        db.Index('ix_inc_unidad_id', 'unidad_id'),
    )
    id = db.Column(db.Integer, primary_key=True)
    unidad_id = db.Column(db.Integer, db.ForeignKey('herramienta_unidades.id'), nullable=False)
    reportado_por_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    tipo = db.Column(db.String(30), nullable=False)         # TIPO_INCIDENCIA
    descripcion = db.Column(db.Text, nullable=False)
    estado = db.Column(db.String(20), default='ABIERTA', nullable=False)
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

    # Nombres FIJADOS a los que ya existen en la base. Sin esto,
    # SQLAlchemy los bautizaría `ix_<tabla>_<columna>` y Alembic los
    # vería como índices distintos: `flask db migrate` generaría una
    # migración que los borra y recrea, bloqueando escrituras sin
    # ninguna ganancia. Mismo índice, mismas columnas, solo el nombre.
    __table_args__ = (
        db.Index('ix_sbh_estado', 'estado'),
        db.Index('ix_sbh_solicitante', 'solicitante_id'),
        db.Index('ix_sbh_unidad_id', 'unidad_id'),
    )
    id = db.Column(db.Integer, primary_key=True)
    unidad_id = db.Column(db.Integer, db.ForeignKey('herramienta_unidades.id'), nullable=False)
    solicitante_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    motivo = db.Column(db.Text, nullable=False)
    estado = db.Column(db.String(20), default='PENDIENTE', nullable=False)
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

    # Nombres FIJADOS a los que ya existen en la base. Sin esto,
    # SQLAlchemy los bautizaría `ix_<tabla>_<columna>` y Alembic los
    # vería como índices distintos: `flask db migrate` generaría una
    # migración que los borra y recrea, bloqueando escrituras sin
    # ninguna ganancia. Mismo índice, mismas columnas, solo el nombre.
    # Índices con los nombres y la forma que YA tienen en la base. Declararlos
    # aquí evita que `flask db migrate` proponga borrarlos y recrearlos.
    __table_args__ = (
        db.Index('ix_evt_tipo', 'tipo_evento'),
        db.Index('ix_evt_unidad_fecha', 'unidad_id', 'fecha'),
    )
    id = db.Column(db.Integer, primary_key=True)
    unidad_id = db.Column(db.Integer, db.ForeignKey('herramienta_unidades.id'), nullable=False)
    tipo_evento = db.Column(db.String(40), nullable=False)  # TIPO_EVENTO_HERRAMIENTA
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

    # Nombres FIJADOS a los que ya existen en la base. Sin esto,
    # SQLAlchemy los bautizaría `ix_<tabla>_<columna>` y Alembic los
    # vería como índices distintos: `flask db migrate` generaría una
    # migración que los borra y recrea, bloqueando escrituras sin
    # ninguna ganancia. Mismo índice, mismas columnas, solo el nombre.
    __table_args__ = (
        db.Index('ix_media_tipo', 'tipo'),
        db.Index('ix_media_unidad_id', 'unidad_id'),
    )
    id = db.Column(db.Integer, primary_key=True)
    unidad_id = db.Column(db.Integer, db.ForeignKey('herramienta_unidades.id'), nullable=False)
    evento_id = db.Column(db.Integer, db.ForeignKey('eventos_herramienta.id'), nullable=True)
    tipo = db.Column(db.String(30), nullable=False)   # 'FOTO_HERRAMIENTA' o 'EVIDENCIA_EVENTO'
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
