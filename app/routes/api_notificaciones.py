"""API JSON para Notificaciones (SPA React).

Espejo de `notificaciones.py` pero protegido por JWT. Reusa las constantes y
helpers (`CHANGELOG`, `_seed_updates_for_user`, `_purgar_notificaciones_viejas`,
`_tiempo_relativo`) del blueprint clásico para no duplicar el seeding del
changelog ni la política de expiración.
"""
from flask import Blueprint, g, jsonify

from app.extensions import db
from app.models import Notificacion
from app.routes.api_auth import jwt_required
from app.routes.notificaciones import (
    _purgar_notificaciones_viejas,
    _seed_updates_for_user,
    _tiempo_relativo,
)

bp = Blueprint('api_notificaciones', __name__, url_prefix='/api/notificaciones')


def _u():
    return g._jwt_user


def _is_admin() -> bool:
    return _u().role in ('admin', 'super_admin')


@bp.route('/resumen', methods=['GET'])
@jwt_required
def resumen():
    if not _is_admin():
        return jsonify({'no_leidas': 0, 'items': []})

    user_id = _u().id
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
    if not _is_admin():
        return jsonify({'error': 'Acceso denegado'}), 403
    n = Notificacion.query.filter_by(id=notif_id, usuario_id=_u().id).first_or_404()
    if not n.leida:
        n.leida = True
        db.session.commit()
    return jsonify({'success': True})


@bp.route('/marcar_todas', methods=['POST'])
@jwt_required
def marcar_todas():
    if not _is_admin():
        return jsonify({'error': 'Acceso denegado'}), 403
    Notificacion.query.filter_by(usuario_id=_u().id, leida=False).update({'leida': True})
    db.session.commit()
    return jsonify({'success': True})
