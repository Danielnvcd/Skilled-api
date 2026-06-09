"""Listado paginado con agregados por proyecto."""
from flask import jsonify, request
from sqlalchemy.orm import joinedload, selectinload

from app.models import Proyecto
from app.routes._api_helpers import require_admin
from app.routes.api_auth import jwt_required

from ._core import _build_proyecto_data, bp


@bp.route('', methods=['GET'])
@jwt_required
def listar():
    err = require_admin()
    if err:
        return err

    page = request.args.get('page', 1, type=int)
    per_page = min(request.args.get('per_page', 20, type=int), 100)
    q = (request.args.get('q') or '').strip()

    query = Proyecto.query.options(
        joinedload(Proyecto.coordinador),
        selectinload(Proyecto.participantes),
    )
    if q:
        like = f'%{q}%'
        query = query.filter(Proyecto.nombre.ilike(like) | Proyecto.numero_proyecto.ilike(like))

    pagination = query.order_by(Proyecto.numero_proyecto).paginate(
        page=page, per_page=per_page, error_out=False,
    )

    proyectos_data = []
    for proyecto in pagination.items:
        agg = _build_proyecto_data(proyecto)
        if agg:
            proyectos_data.append(agg)

    return jsonify({
        'items': proyectos_data,
        'page': pagination.page,
        'per_page': pagination.per_page,
        'total': pagination.total,
        'pages': pagination.pages,
        'has_next': pagination.has_next,
        'has_prev': pagination.has_prev,
    })
