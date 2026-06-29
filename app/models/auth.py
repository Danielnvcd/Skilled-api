"""Modelos de autenticación y auditoría: User, RefreshToken, TwoFactorBackupCode, AuditLog."""
from app.extensions import db, EncryptedString
from app.models._base import _now_utc


class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), default='user')
    totp_secret = db.Column(EncryptedString(500), nullable=True)
    # Incrementado cada vez que se cambia la contraseña; invalida todas las sesiones anteriores.
    password_version = db.Column(db.Integer, nullable=False, default=1, server_default='1')
    # Borrado lógico: un usuario con relaciones en otras tablas (movimientos,
    # solicitudes, asignaciones…) no se puede borrar físicamente sin violar las
    # FKs. En su lugar se desactiva: no puede iniciar sesión ni aparece en las
    # listas activas, pero todo su historial queda intacto.
    activo = db.Column(db.Boolean, nullable=False, default=True, server_default='1', index=True)

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
    # `user` se usa en el JOIN con User.username (dashboard / bitácora) y en
    # filtros — indexarlo evita escanear toda la tabla al cruzar por usuario.
    user = db.Column(db.String(80), index=True)
    action = db.Column(db.String(200))
    ip = db.Column(db.String(45), index=True)
    # created_at es la columna de orden por defecto (ORDER BY created_at DESC)
    # de la bitácora y del dashboard. La tabla crece sin límite (una fila por
    # acción), así que sin índice cada carga hacía un full sort. Indexado.
    created_at = db.Column(db.DateTime, default=_now_utc, index=True)
