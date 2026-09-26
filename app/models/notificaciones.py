"""Notificaciones in-app + helpers crear_notif_admins / crear_notif_inventario."""
from app.extensions import db
from app.models._base import _now_utc
from app.models.auth import User


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
