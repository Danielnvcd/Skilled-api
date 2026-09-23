"""Modelos de la integración con lectores biométricos Hikvision (ISAPI).

Dos tablas, ambas nuevas: el dispositivo configurado en el ERP y el estado de
sincronización de cada empleado en cada dispositivo. Ninguna lógica de nómina
vive aquí — esto solo registra QUÉ se mandó al lector y CÓMO le fue.

Por qué una tabla de estado y no un par de columnas en `trabajadores`:
la relación es N a N (un empleado puede estar en varios lectores, y cada lector
tiene su propio resultado para ese empleado). Meterlo en `trabajadores` obligaría
a suponer que solo existirá un lector, que es justo lo que hay que evitar.
"""
from app.extensions import EncryptedString, db
from app.models._base import _now_utc


# Estado de un empleado respecto de UN lector.
#   PENDIENTE    seleccionado pero todavía no enviado (o marcado para reenviar)
#   SINCRONIZADO usuario + rostro confirmados en el equipo
#   ERROR        el último intento falló; `ultimo_error` dice por qué
ESTADOS_SYNC_HIKVISION = ('PENDIENTE', 'SINCRONIZADO', 'ERROR')

# Límites REALES del equipo, leídos de
# GET /ISAPI/AccessControl/UserInfo/capabilities?format=json en un
# DS-K1T342MFWX-E1 con firmware V3.16.1. No son supuestos de la documentación:
# el firmware es la autoridad y `client.probar()` los revalida al conectar.
EMPLOYEE_NO_MAX = 32
NOMBRE_REMOTO_MAX = 128
# Tope de filas por página en UserInfo/Search y AcsEvent. El equipo lo rechaza
# si se pide más, así que el paginador nunca debe subirlo.
MAX_RESULTS_PAGINA = 30


# Segundos sin latido tras los cuales se considera caída la escucha. El lector
# manda una señal de vida cada ~30 s; con 90 se tolera perder dos seguidas.
LATIDO_MAX_S = 90


class DispositivoHikvision(db.Model):
    """Un lector Hikvision configurado en el ERP.

    La contraseña se cifra con Fernet (`EncryptedString`, la misma mecánica que
    el `totp_secret` de User) y NUNCA sale en `to_dict()`, en logs ni en
    mensajes de error: el frontend solo se entera de si hay una guardada.
    """
    __tablename__ = "hikvision_dispositivos"
    __table_args__ = (
        # Dos filas apuntando al mismo equipo son siempre un error de captura y
        # provocan sincronizaciones duplicadas contra el mismo hardware.
        db.UniqueConstraint('host', 'puerto', name='uq_hikvision_host_puerto'),
    )

    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(120), nullable=False)

    # Conexión. `host` se valida en la capa de servicio contra rangos privados:
    # el lector vive en la LAN y el ERP no debe poder usarse como proxy a
    # internet (el inverso de app/utils/image_fetch.py, que exige IP pública).
    host = db.Column(db.String(120), nullable=False)
    puerto = db.Column(db.Integer, nullable=False, default=80, server_default='80')
    usuario = db.Column(db.String(64), nullable=False)
    password = db.Column(EncryptedString(500), nullable=False)

    activo = db.Column(db.Boolean, nullable=False, default=True, server_default='true', index=True)

    # Identidad que reportó el equipo la última vez que se probó la conexión
    # (GET /ISAPI/System/deviceInfo). Se cachea para mostrarla sin volver a
    # llamar al lector y para detectar que alguien cambió el equipo de sitio.
    modelo = db.Column(db.String(100), nullable=True)
    numero_serie = db.Column(db.String(120), nullable=True)
    firmware = db.Column(db.String(60), nullable=True)

    ultima_conexion = db.Column(db.DateTime, nullable=True)
    ultimo_estado = db.Column(db.String(20), nullable=True)   # OK | ERROR
    ultimo_error = db.Column(db.String(500), nullable=True)   # ya saneado, sin credenciales

    # Escucha de eventos en tiempo real (`flask hikvision escuchar`). La
    # escribe SOLO ese proceso; la API la lee para decir si la actividad que
    # muestra está al día. `escucha_latido` se renueva con cada señal de vida
    # del lector (~30 s): si queda viejo, el proceso murió aunque diga CONECTADO.
    escucha_estado = db.Column(db.String(20), nullable=True)   # CONECTADO | DESCONECTADO
    escucha_latido = db.Column(db.DateTime(timezone=True), nullable=True)
    escucha_error = db.Column(db.String(500), nullable=True)

    created_at = db.Column(db.DateTime, default=_now_utc)
    updated_at = db.Column(db.DateTime, default=_now_utc, onupdate=_now_utc)

    def to_dict(self) -> dict:
        """Serialización para la API. `password` no aparece por diseño."""
        return {
            'id': self.id,
            'nombre': self.nombre,
            'host': self.host,
            'puerto': self.puerto,
            'usuario': self.usuario,
            # El frontend necesita saber si ya hay contraseña guardada para
            # decidir entre "cambiar" y "capturar", nunca su valor.
            'tiene_password': bool(self.password),
            'activo': self.activo,
            'modelo': self.modelo or '',
            'numero_serie': self.numero_serie or '',
            'firmware': self.firmware or '',
            'ultima_conexion': self.ultima_conexion.isoformat() if self.ultima_conexion else None,
            'ultimo_estado': self.ultimo_estado or '',
            'ultimo_error': self.ultimo_error or '',
            'escucha': self.estado_escucha(),
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }

    def estado_escucha(self, ahora=None) -> dict:
        """¿La actividad de este lector llega en tiempo real ahora mismo?

        `en_vivo` exige las dos cosas: que el proceso de escucha diga estar
        conectado Y que su último latido sea reciente. Solo lo primero no basta:
        si el proceso muere de golpe no alcanza a escribir DESCONECTADO.
        """
        from datetime import datetime, timezone
        ahora = ahora or datetime.now(timezone.utc)
        latido = self.escucha_latido
        if latido is not None and latido.tzinfo is None:
            latido = latido.replace(tzinfo=timezone.utc)
        reciente = latido is not None and (ahora - latido).total_seconds() <= LATIDO_MAX_S
        return {
            'en_vivo': self.escucha_estado == 'CONECTADO' and reciente,
            'estado': self.escucha_estado or 'SIN_ESCUCHA',
            'latido': latido.isoformat() if latido else None,
            'error': self.escucha_error or '',
        }


class SyncEmpleadoHikvision(db.Model):
    """Estado de sincronización de un trabajador en un lector concreto.

    Los dos hashes son lo que permite responder "¿este empleado cambió desde la
    última vez que lo mandé?" sin volver a llamar al equipo: se comparan contra
    el hash de los datos y de la foto actuales, y si difieren la fila se muestra
    como pendiente de reenviar.
    """
    __tablename__ = "hikvision_sync_empleado"
    __table_args__ = (
        db.UniqueConstraint('dispositivo_id', 'trabajador_id', name='uq_hikvision_sync_disp_trab'),
        # Evita duplicados del lado del equipo: dos trabajadores del ERP no
        # pueden reclamar el mismo `employeeNo` en el mismo lector.
        db.UniqueConstraint('dispositivo_id', 'employee_no_remoto', name='uq_hikvision_sync_disp_empno'),
    )

    id = db.Column(db.Integer, primary_key=True)
    dispositivo_id = db.Column(
        db.Integer, db.ForeignKey('hikvision_dispositivos.id', ondelete='CASCADE'),
        nullable=False, index=True,
    )
    trabajador_id = db.Column(
        db.Integer, db.ForeignKey('trabajadores.id', ondelete='CASCADE'),
        nullable=False, index=True,
    )

    # El `employeeNo` que REALMENTE se envió al equipo (hoy `no_empleado`).
    # Se guarda en vez de recalcularlo para poder borrar del lector aunque el
    # número del ERP haya cambiado después.
    employee_no_remoto = db.Column(db.String(EMPLOYEE_NO_MAX), nullable=False)

    estado = db.Column(db.String(20), nullable=False, default='PENDIENTE',
                       server_default='PENDIENTE', index=True)

    hash_datos = db.Column(db.String(64), nullable=True)  # sha256 de nombre + vigencia
    hash_foto = db.Column(db.String(64), nullable=True)   # sha256 del JPEG enviado
    # Key de `Trabajador.foto_perfil` que se envió. Cada subida de foto genera
    # una key nueva, así que compararla contra la actual dice si la foto cambió
    # SIN leer el archivo de R2 por cada fila del listado (que es lo que
    # costaría comparar `hash_foto`).
    foto_key = db.Column(db.String(255), nullable=True)

    ultimo_error = db.Column(db.String(500), nullable=True)
    ultimo_intento = db.Column(db.DateTime, nullable=True)
    sincronizado_en = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=_now_utc)

    dispositivo = db.relationship(
        'DispositivoHikvision',
        backref=db.backref('sincronizaciones', lazy='select', cascade='all, delete-orphan'),
    )
    trabajador = db.relationship('Trabajador')

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'dispositivo_id': self.dispositivo_id,
            'trabajador_id': self.trabajador_id,
            'employee_no_remoto': self.employee_no_remoto,
            'estado': self.estado,
            'ultimo_error': self.ultimo_error or '',
            'ultimo_intento': self.ultimo_intento.isoformat() if self.ultimo_intento else None,
            'sincronizado_en': self.sincronizado_en.isoformat() if self.sincronizado_en else None,
        }


class EventoHikvision(db.Model):
    """Un evento de acceso del lector, guardado en el ERP.

    Lo escribe el proceso de escucha (`flask hikvision escuchar`) en cuanto el
    lector avisa. La pantalla de actividad y los indicadores leen de aquí: así
    no se le pregunta al equipo cada vez que alguien abre la página, y es la
    base de la fase de asistencia (checadas → nómina).

    `serial_no` es el número de evento del propio equipo: creciente y único por
    lector. La restricción única sobre (dispositivo, serial) hace idempotente
    la ingesta — el lector reenvía su historial al reconectar y eso no debe
    duplicar nada.
    """
    __tablename__ = "hikvision_eventos"
    __table_args__ = (
        db.UniqueConstraint('dispositivo_id', 'serial_no', name='uq_hikvision_evento_serial'),
        db.Index('ix_hikvision_eventos_disp_fecha', 'dispositivo_id', 'fecha_hora'),
    )

    id = db.Column(db.BigInteger().with_variant(db.Integer, 'sqlite'), primary_key=True)
    dispositivo_id = db.Column(
        db.Integer, db.ForeignKey('hikvision_dispositivos.id', ondelete='CASCADE'),
        nullable=False,
    )
    serial_no = db.Column(db.BigInteger, nullable=False)

    # Instante absoluto (para ordenar y filtrar) y la hora TAL COMO la reportó
    # el lector, con su desfase: es la hora de la oficina, la que se muestra.
    fecha_hora = db.Column(db.DateTime(timezone=True), nullable=False)
    hora_local = db.Column(db.String(32), nullable=False)

    major = db.Column(db.SmallInteger, nullable=False)
    minor = db.Column(db.Integer, nullable=False)
    tipo = db.Column(db.String(12), nullable=False, index=True)  # permitido|denegado|puerta|otro

    employee_no = db.Column(db.String(EMPLOYEE_NO_MAX), nullable=True, index=True)
    nombre_en_equipo = db.Column(db.String(NOMBRE_REMOTO_MAX), nullable=True)
    # Se resuelve al guardar, contra `hikvision_sync_empleado`. SET NULL: si el
    # trabajador se borra, el evento sigue siendo historia válida del lector.
    trabajador_id = db.Column(
        db.Integer, db.ForeignKey('trabajadores.id', ondelete='SET NULL'), nullable=True,
    )
    modo_verificacion = db.Column(db.String(40), nullable=True)
    cubrebocas = db.Column(db.Boolean, nullable=False, default=False, server_default='false')
    # Ruta de la foto dentro del equipo (bajo /LOCALS/pic/). La imagen NO se
    # copia al ERP: se pide al lector solo cuando alguien la abre.
    captura = db.Column(db.String(256), nullable=True)

    recibido_en = db.Column(db.DateTime(timezone=True), default=_now_utc, nullable=False)

    def to_dict(self) -> dict:
        from app.services.hikvision.eventos import TIPOS_EVENTO
        return {
            'serial': self.serial_no,
            'fecha_hora': self.hora_local,
            'minor': self.minor,
            'tipo': self.tipo,
            'descripcion': TIPOS_EVENTO.get(self.minor, (None, f'Evento {self.minor}'))[1],
            'employee_no': self.employee_no or '',
            'nombre_en_equipo': self.nombre_en_equipo or '',
            'trabajador_id': self.trabajador_id,
            'modo_verificacion': self.modo_verificacion or '',
            'cubrebocas': bool(self.cubrebocas),
            'captura': self.captura,
        }
