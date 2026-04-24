from flask import Blueprint, jsonify, session
from app.extensions import db
from app.models import Notificacion, User
from app.utils import login_required
from datetime import datetime, timezone, timedelta

DIAS_EXPIRACION = 30

bp = Blueprint('notificaciones', __name__, url_prefix='/notificaciones')

# ─── Changelog de actualizaciones del sistema ─────────────────────────────────
# Agrega aquí nuevas entradas cuando liberes funcionalidades.
# La 'referencia' es una clave única que evita crear duplicados por usuario.
CHANGELOG = [
    {
        'referencia': 'update_2026-04-24_importar_materiales',
        'titulo': 'Importación masiva de materiales',
        'mensaje': 'El catálogo ahora permite cargar materiales en lote desde Excel con corrección automática de errores en categorías.',
        'url': '/inventario/importar',
    },
    {
        'referencia': 'update_2026-04-24_notificaciones',
        'titulo': 'Sistema de notificaciones',
        'mensaje': 'Nuevo panel de notificaciones en tiempo real: avisos de reportes de horas cerrados y prenóminas aprobadas.',
        'url': None,
    },
]


def _purgar_notificaciones_viejas(user_id):
    """Elimina notificaciones leídas con más de DIAS_EXPIRACION días para el usuario."""
    limite = datetime.now(timezone.utc) - timedelta(days=DIAS_EXPIRACION)
    Notificacion.query.filter(
        Notificacion.usuario_id == user_id,
        Notificacion.leida == True,
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


@bp.route('/api/resumen')
@login_required
def api_resumen():
    user_id = session.get('user_id')
    role = session.get('role')
    if role not in ['admin', 'super_admin']:
        return jsonify({'no_leidas': 0, 'items': []})

    try:
        _seed_updates_for_user(user_id)
    except Exception:
        pass

    try:
        _purgar_notificaciones_viejas(user_id)
    except Exception:
        pass

    no_leidas = Notificacion.query.filter_by(usuario_id=user_id, leida=False).count()
    recientes = (
        Notificacion.query
        .filter_by(usuario_id=user_id)
        .order_by(Notificacion.leida.asc(), Notificacion.created_at.desc())
        .limit(15)
        .all()
    )

    return jsonify({
        'no_leidas': no_leidas,
        'items': [{
            'id': n.id,
            'tipo': n.tipo,
            'titulo': n.titulo,
            'mensaje': n.mensaje,
            'url': n.url or '',
            'leida': n.leida,
            'tiempo': _tiempo_relativo(n.created_at),
        } for n in recientes],
    })


@bp.route('/api/marcar_leida/<int:notif_id>', methods=['POST'])
@login_required
def marcar_leida(notif_id):
    if session.get('role') not in ('admin', 'super_admin'):
        return jsonify({'success': False, 'message': 'Acceso denegado.'}), 403
    n = Notificacion.query.filter_by(
        id=notif_id,
        usuario_id=session.get('user_id'),
    ).first_or_404()
    n.leida = True
    db.session.commit()
    return jsonify({'success': True})


@bp.route('/api/marcar_todas', methods=['POST'])
@login_required
def marcar_todas():
    if session.get('role') not in ('admin', 'super_admin'):
        return jsonify({'success': False, 'message': 'Acceso denegado.'}), 403
    Notificacion.query.filter_by(
        usuario_id=session.get('user_id'),
        leida=False,
    ).update({'leida': True})
    db.session.commit()
    return jsonify({'success': True})
