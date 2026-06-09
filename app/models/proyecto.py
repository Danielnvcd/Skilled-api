"""Modelo Proyecto + tabla M2M proyecto_trabajador."""
from app.extensions import db
from app.models._base import _now_utc


proyecto_trabajador = db.Table(
    'proyecto_trabajador',
    db.Column('proyecto_id', db.Integer, db.ForeignKey('proyectos.id'), primary_key=True),
    db.Column('trabajador_id', db.Integer, db.ForeignKey('trabajadores.id'), primary_key=True),
)


class Proyecto(db.Model):
    __tablename__ = "proyectos"
    id = db.Column(db.Integer, primary_key=True)
    numero_proyecto = db.Column(db.String(100), unique=True, nullable=False)
    nombre = db.Column(db.String(250), nullable=True)
    activo = db.Column(db.Boolean, default=True, index=True)  # Si es False, "ya no se le deberían cargar horas"

    coordinador_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    coordinador = db.relationship('User', foreign_keys=[coordinador_id])

    participantes = db.relationship('Trabajador', secondary=proyecto_trabajador, backref=db.backref('proyectos', lazy='dynamic'))

    created_at = db.Column(db.DateTime, default=_now_utc)
