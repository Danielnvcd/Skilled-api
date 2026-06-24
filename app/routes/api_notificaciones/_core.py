"""Núcleo del paquete `api_notificaciones`.

Blueprint, changelog del sistema y helpers de seeding/expiración/format.
`_tiempo_relativo` se re-exporta para `app/realtime.py`, que lo importa para
formatear mensajes en pushes Socket.IO.
"""
from datetime import datetime, timedelta, timezone

from flask import Blueprint

from app.extensions import db
from app.models import Notificacion


bp = Blueprint('api_notificaciones', __name__, url_prefix='/api/notificaciones')


DIAS_EXPIRACION = 30


# ─── Changelog de actualizaciones del sistema ─────────────────────────────────
# Agrega aquí nuevas entradas cuando liberes funcionalidades.
# La 'referencia' es una clave única que evita crear duplicados por usuario.
CHANGELOG = []


def _purgar_notificaciones_viejas(user_id):
    """Elimina notificaciones leídas con más de DIAS_EXPIRACION días para el usuario."""
    limite = datetime.now(timezone.utc) - timedelta(days=DIAS_EXPIRACION)
    Notificacion.query.filter(
        Notificacion.usuario_id == user_id,
        Notificacion.leida == True,  # noqa: E712
        Notificacion.created_at < limite,
    ).delete(synchronize_session=False)
    db.session.commit()


def _seed_updates_for_user(user_id):
    """Crea en BD las notificaciones de CHANGELOG que aún no existan para este usuario."""
    changed = False
    for entry in CHANGELOG:
        exists = Notificacion.query.filter_by(
            referencia=entry['referencia'],
            usuario_id=user_id,
        ).first()
        if not exists:
            db.session.add(Notificacion(
                usuario_id=user_id,
                tipo='ACTUALIZACION',
                titulo=entry['titulo'],
                mensaje=entry['mensaje'],
                url=entry.get('url'),
                referencia=entry['referencia'],
            ))
            changed = True
    if changed:
        db.session.commit()


def _tiempo_relativo(dt):
    if not dt:
        return ''
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    secs = int((now - dt).total_seconds())
    if secs < 60:
        return 'ahora mismo'
    if secs < 3600:
        return f'hace {secs // 60} min'
    if secs < 86400:
        return f'hace {secs // 3600}h'
    return f'hace {secs // 86400}d'
