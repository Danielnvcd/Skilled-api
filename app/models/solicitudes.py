"""Modelos del dominio Solicitudes de Material/Herramienta."""
from app.extensions import db
from app.models._base import _now_utc


class SolicitudMaterial(db.Model):
    __tablename__ = "solicitudes_material"
    id = db.Column(db.Integer, primary_key=True)
    solicitante_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    proyecto = db.Column(db.String(200), nullable=True)
    # FK real al proyecto (nullable por compat con solicitudes históricas que
    # solo tienen el texto). Es la base para atribuir consumo de material por
    # proyecto en el panel de Inventario → Proyectos. Se conserva `proyecto`
    # (texto) para el PDF y para no romper datos viejos.
    proyecto_id = db.Column(db.Integer, db.ForeignKey('proyectos.id'), nullable=True, index=True)
    # Observaciones generales del solicitante para el almacén (campo libre del pedido).
    notas = db.Column(db.Text, nullable=True)
    estatus = db.Column(db.String(50), default='PENDIENTE', nullable=False, index=True)  # PENDIENTE, APROBADA, RECHAZADA, ENTREGADA
    fecha_creacion = db.Column(db.DateTime, default=_now_utc, index=True)
    fecha_cierre = db.Column(db.DateTime, nullable=True)

    # Entrega directa de mostrador (creada por inventario que surte en el acto,
    # sin solicitud previa del trabajador). `solicitante_id` es quien la captura
    # (el de inventario); el "pedido por" real va en los campos de abajo:
    #   - solicitante_trabajador_id: trabajador del sistema (si se eligió uno).
    #   - solicitante_nombre: nombre libre (si quien recoge no está dado de alta).
    # El PDF y los listados muestran el nombre del solicitante real, no el del
    # capturista. Las solicitudes normales dejan estos campos en NULL/False.
    entrega_directa = db.Column(db.Boolean, default=False, nullable=False, server_default='0')
    solicitante_nombre = db.Column(db.String(200), nullable=True)
    solicitante_trabajador_id = db.Column(db.Integer, db.ForeignKey('trabajadores.id'), nullable=True)

    # Trazabilidad de quién resolvió la solicitud (se llenan en las transiciones
    # de estado). `aprobada_por` = quién la aprobó; `entregada_por` = quién la
    # surtió (en entregas directas es el mismo capturista). Se limpian al
    # reabrir a PENDIENTE.
    aprobada_por_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    entregada_por_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)

    solicitante = db.relationship('User', foreign_keys=[solicitante_id])
    solicitante_trabajador = db.relationship('Trabajador', foreign_keys=[solicitante_trabajador_id])
    aprobada_por = db.relationship('User', foreign_keys=[aprobada_por_id])
    entregada_por = db.relationship('User', foreign_keys=[entregada_por_id])
    proyecto_ref = db.relationship('Proyecto', foreign_keys=[proyecto_id])

    @staticmethod
    def _user_display(u):
        if u is None:
            return None
        return u.full_name or u.username

    @property
    def solicitante_display(self):
        """Nombre a mostrar como solicitante: trabajador o texto libre en
        entregas directas; el usuario capturista en solicitudes normales."""
        if self.solicitante_trabajador is not None:
            return self.solicitante_trabajador.nombre_completo
        if self.solicitante_nombre:
            return self.solicitante_nombre
        if self.solicitante is not None:
            return self.solicitante.full_name or self.solicitante.username
        return 'Desconocido'


class SolicitudMaterialDetalle(db.Model):
    __tablename__ = "solicitudes_material_detalle"
    # Índices con los nombres y la forma que YA tienen en la base. Declararlos
    # aquí evita que `flask db migrate` proponga borrarlos y recrearlos.
    __table_args__ = (
        db.Index('ix_smd_tipo_item', 'tipo_item'),
    )

    id = db.Column(db.Integer, primary_key=True)
    solicitud_id = db.Column(db.Integer, db.ForeignKey('solicitudes_material.id'), nullable=False, index=True)
    # producto_id es NULLABLE: una línea es MATERIAL (producto_id) o HERRAMIENTA (herramienta_id), XOR.
    # Indexado: la disponibilidad de un producto cuenta sus líneas de solicitud.
    producto_id = db.Column(db.Integer, db.ForeignKey('productos.id'), nullable=True, index=True)
    cantidad_solicitada = db.Column(db.Numeric(10, 2), nullable=False)
    cantidad_aprobada = db.Column(db.Numeric(10, 2), default=0)
    cantidad_entregada = db.Column(db.Numeric(10, 2), default=0)

    # Extensión para soportar solicitudes de herramientas
    tipo_item = db.Column(db.String(20), nullable=False, default='MATERIAL', server_default='MATERIAL')
    herramienta_id = db.Column(db.Integer, db.ForeignKey('herramientas.id'), nullable=True)
    fecha_uso_inicio = db.Column(db.Date, nullable=True)
    fecha_uso_fin = db.Column(db.Date, nullable=True)
    justificacion = db.Column(db.Text, nullable=True)
    complementos = db.Column(db.String(500), nullable=True)

    solicitud = db.relationship('SolicitudMaterial', backref=db.backref('detalles', lazy='selectin', cascade='all, delete-orphan'))
    producto = db.relationship('Producto')
    herramienta = db.relationship('Herramienta', foreign_keys=[herramienta_id])
