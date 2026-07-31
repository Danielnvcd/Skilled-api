"""Notas internas por trabajador (el "chatter" de la ficha del SPA).

Registra:
  GET    /<int:id>/notas              listar (desc por fecha)
  POST   /<int:id>/notas              crear  {texto}
  DELETE /<int:id>/notas/<int:nid>    eliminar (autor o admin)

Acceso: misma regla que la ficha (`_authorized`): admin/super_admin siempre,
coordinador solo si el trabajador pertenece a uno de sus proyectos.

Tiempo real: cada mutación emite `nota:changed` {trabajador_id} a los roles
con acceso a fichas; el panel de notas del SPA lo usa en `invalidateOn` para
refrescarse en todas las pestañas/usuarios abiertos.
"""
from flask import jsonify, request

from app.extensions import db
from app.models import NotaTrabajador, Trabajador
from app.realtime import emit_to_role
from app.routes._api_helpers import current_user, is_admin
from app.routes.api_auth import jwt_required
from app.utils import log_action

from ._core import _authorized, bp

_NOTA_ROLES = ['admin', 'super_admin', 'coordinador']


def _get_trabajador_autorizado(id):
    t = db.session.get(Trabajador, id)
    if not t:
        return None, (jsonify({'error': 'No encontrado'}), 404)
    if not _authorized(t):
        return None, (jsonify({'error': 'Acceso denegado'}), 403)
    return t, None


@bp.route('/<int:id>/notas', methods=['GET'])
@jwt_required
def listar_notas(id):
    t, err = _get_trabajador_autorizado(id)
    if err:
        return err
    notas = (
        NotaTrabajador.query
        .filter_by(trabajador_id=t.id)
        .order_by(NotaTrabajador.created_at.desc(), NotaTrabajador.id.desc())
        .limit(200)
        .all()
    )
    return jsonify([n.to_dict() for n in notas])


@bp.route('/<int:id>/notas', methods=['POST'])
@jwt_required
def crear_nota(id):
    t, err = _get_trabajador_autorizado(id)
    if err:
        return err
    data = request.get_json(silent=True) or {}
    texto = (data.get('texto') or '').strip()
    if not texto:
        return jsonify({'error': 'La nota no puede estar vacía'}), 422
    if len(texto) > 2000:
        return jsonify({'error': 'Máximo 2000 caracteres'}), 422

    nota = NotaTrabajador(trabajador_id=t.id, user_id=current_user().id, texto=texto)
    db.session.add(nota)
    db.session.commit()
    log_action(f"Agregó nota al trabajador {t.nombre} (#{t.id})")
    emit_to_role(_NOTA_ROLES, 'nota:changed', {'trabajador_id': t.id, 'action': 'created'})
    return jsonify(nota.to_dict()), 201


@bp.route('/<int:id>/notas/<int:nota_id>', methods=['DELETE'])
@jwt_required
def eliminar_nota(id, nota_id):
    t, err = _get_trabajador_autorizado(id)
    if err:
        return err
    nota = NotaTrabajador.query.filter_by(id=nota_id, trabajador_id=t.id).first()
    if not nota:
        return jsonify({'error': 'Nota no encontrada'}), 404
    # Solo el autor borra su nota; admin/super_admin pueden borrar cualquiera.
    if nota.user_id != current_user().id and not is_admin():
        return jsonify({'error': 'Solo el autor o un admin pueden eliminarla'}), 403

    db.session.delete(nota)
    db.session.commit()
    log_action(f"Eliminó nota #{nota_id} del trabajador {t.nombre} (#{t.id})")
    emit_to_role(_NOTA_ROLES, 'nota:changed', {'trabajador_id': t.id, 'action': 'deleted'})
    return jsonify({'ok': True})
