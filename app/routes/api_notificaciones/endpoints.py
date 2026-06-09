"""Endpoints: resumen, marcar leída y marcar todas como leídas."""
from flask import jsonify

from app.extensions import db
from app.models import Notificacion
from app.realtime import emit_to_user
from app.routes._api_helpers import current_user, is_admin
from app.routes.api_auth import jwt_required

from ._core import (
    _purgar_notificaciones_viejas,
    _seed_updates_for_user,
    _tiempo_relativo,
    bp,
)


@bp.route('/resumen', methods=['GET'])
@jwt_required
def resumen():
    if not is_admin():
        return jsonify({'no_leidas': 0, 'items': []})

    user_id = current_user().id
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
            'created_at': n.created_at.isoformat() if n.created_at else None,
        } for n in recientes],
    })


@bp.route('/<int:notif_id>/leer', methods=['POST'])
@jwt_required
def marcar_leida(notif_id):
    # El gate real es `usuario_id == current_user().id` en el filter_by: un usuario solo
    # puede tocar sus propias notifs. No filtramos por rol aquí para que el
    # endpoint sobreviva si en el futuro se mandan notifs a coord u otros roles.
    n = Notificacion.query.filter_by(id=notif_id, usuario_id=current_user().id).first_or_404()
    if not n.leida:
        n.leida = True
        db.session.commit()
    unread = Notificacion.query.filter_by(usuario_id=current_user().id, leida=False).count()
    # Sincroniza otras pestañas/dispositivos del mismo usuario.
    emit_to_user(current_user().id, 'notif:read', {'id': notif_id, 'no_leidas': unread})
    return jsonify({'success': True})


@bp.route('/marcar_todas', methods=['POST'])
@jwt_required
def marcar_todas():
    # Mismo principio que arriba: el WHERE por usuario_id ya es el gate.
    Notificacion.query.filter_by(usuario_id=current_user().id, leida=False).update({'leida': True})
    db.session.commit()
    emit_to_user(current_user().id, 'notif:read_all', {'no_leidas': 0})
    return jsonify({'success': True})
