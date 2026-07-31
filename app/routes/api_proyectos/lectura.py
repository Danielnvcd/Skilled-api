"""Endpoints de lectura: /mios, /, /meta, /<id>."""
from flask import jsonify, request
from sqlalchemy import func, or_
from sqlalchemy.orm import selectinload

from app.extensions import db
from app.models import Proyecto, Trabajador, User, proyecto_trabajador
from app.routes._api_helpers import current_user, is_admin, require_admin
from app.routes.api_auth import jwt_required

from ._core import _proyecto_detail, _proyecto_row, _trabajador_pickable, bp


@bp.route('/mios', methods=['GET'])
@jwt_required
def mios():
    """Lista los proyectos del usuario. Para coordinador, solo los proyectos
    donde está asignado como coordinador; para admin, todos los activos.
    Devuelve información enriquecida (participantes con nombre + número de
    empleado) para la vista 'Mis Proyectos' del coordinador en móvil."""
    user = current_user()
    query = Proyecto.query.options(selectinload(Proyecto.participantes)).filter_by(activo=True)
    if user.role == 'coordinador':
        query = query.filter_by(coordinador_id=user.id)
    elif not is_admin():
        return jsonify({'error': 'Acceso denegado'}), 403

    proyectos = query.order_by(Proyecto.numero_proyecto).all()
    out = []
    for p in proyectos:
        out.append({
            'id': p.id,
            'numero_proyecto': p.numero_proyecto,
            'nombre': p.nombre or '',
            'activo': bool(p.activo),
            'participantes': [
                {
                    'id': t.id,
                    'no_empleado': t.no_empleado,
                    'nombre': t.nombre,
                    'nombre_completo': t.nombre_completo,
                    'puesto': t.puesto or '',
                    'tipo_nomina': t.tipo_nomina or '',
                }
                for t in p.participantes
            ],
            'participantes_count': len(p.participantes),
        })
    return jsonify(out)


@bp.route('', methods=['GET'])
@jwt_required
def listar():
    """Listado paginado con filtros `q` (texto), `estado` (activos|inactivos|todos)
    y orden `sort`/`dir` (whitelist; un sort desconocido cae al default).

    AuthZ: admin/super_admin ven todos los proyectos. Coordinador ve solo los
    suyos (sin distinción de estado: si está asignado, lo ve). Otros roles 403.
    """
    user = current_user()
    if not (is_admin() or user.role == 'coordinador'):
        return jsonify({'error': 'Acceso denegado'}), 403

    page = max(1, request.args.get('page', 1, type=int))
    per_page = min(100, max(1, request.args.get('per_page', 20, type=int)))
    q = (request.args.get('q') or '').strip()
    estado = (request.args.get('estado') or 'todos').lower()
    sort = (request.args.get('sort') or '').lower()
    direccion = (request.args.get('dir') or 'asc').lower()

    # Conteo de participantes por subquery: evita cargar las filas de la M:N
    # solo para hacer len() — era el costo dominante del listado sin paginar.
    cnt_sq = (
        db.session.query(
            proyecto_trabajador.c.proyecto_id.label('pid'),
            func.count(proyecto_trabajador.c.trabajador_id).label('cnt'),
        )
        .group_by(proyecto_trabajador.c.proyecto_id)
        .subquery()
    )
    cnt_col = func.coalesce(cnt_sq.c.cnt, 0)

    query = (
        Proyecto.query
        .outerjoin(cnt_sq, cnt_sq.c.pid == Proyecto.id)
        .add_columns(cnt_col.label('participantes_count'))
        .options(selectinload(Proyecto.coordinador))
    )
    if estado == 'activos':
        query = query.filter(Proyecto.activo == True)  # noqa: E712
    elif estado == 'inactivos':
        query = query.filter(Proyecto.activo == False)  # noqa: E712

    # Coordinador solo ve sus propios proyectos (mismo gate que en `/mios`).
    if user.role == 'coordinador':
        query = query.filter(Proyecto.coordinador_id == user.id)

    if q:
        like = f'%{q}%'
        query = query.filter(or_(
            Proyecto.numero_proyecto.ilike(like),
            Proyecto.nombre.ilike(like),
        ))

    # Orden por columna con whitelist; desempate estable por numero+id para
    # que la paginación no duplique/omita filas con valores repetidos.
    sortables = {
        'numero': Proyecto.numero_proyecto,
        'nombre': func.lower(Proyecto.nombre),
        'estado': Proyecto.activo,
        'participantes': cnt_col,
        'creado': Proyecto.created_at,
        'coordinador': func.lower(User.username),
    }
    if sort in sortables:
        if sort == 'coordinador':
            query = query.outerjoin(User, Proyecto.coordinador_id == User.id)
        col = sortables[sort]
        orden = col.desc() if direccion == 'desc' else col.asc()
        query = query.order_by(orden.nullslast(), Proyecto.numero_proyecto, Proyecto.id)
    else:
        query = query.order_by(Proyecto.numero_proyecto)

    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    return jsonify({
        'items': [_proyecto_row(p, cnt) for p, cnt in pagination.items],
        'page': pagination.page,
        'per_page': pagination.per_page,
        'total': pagination.total,
        'pages': pagination.pages,
        'has_next': pagination.has_next,
        'has_prev': pagination.has_prev,
    })


@bp.route('/meta', methods=['GET'])
@jwt_required
def meta():
    """Datos auxiliares para el modal de alta/edición de proyectos.

    AuthZ: solo admin/super_admin. Expone la lista completa de coordinadores
    (usernames, full_names) y todos los trabajadores activos (no_empleado,
    nombre) — eso es PII que NO debe estar accesible a roles operativos.
    """
    denied = require_admin()
    if denied:
        return denied
    # Mismos roles que _VALID_COORD_ROLES — antes faltaba super_admin y un
    # proyecto coordinado por uno no mostraba a su coordinador en el selector.
    coordinadores = (
        User.query.filter(User.role.in_(['coordinador', 'admin', 'super_admin']))
        .order_by(User.username)
        .all()
    )
    trabajadores = (
        Trabajador.query.filter_by(activo=True)
        .order_by(Trabajador.nombre)
        .all()
    )
    return jsonify({
        'coordinadores': [
            {'id': u.id, 'username': u.username, 'full_name': u.full_name or u.username}
            for u in coordinadores
        ],
        'trabajadores': [_trabajador_pickable(t) for t in trabajadores],
    })


@bp.route('/<int:id>', methods=['GET'])
@jwt_required
def obtener(id):
    """Detalle de un proyecto.

    AuthZ: admin/super_admin ven todo. Coordinador solo el propio. Otros roles
    no tienen razón de ver detalles de proyectos (incluye participantes, fechas,
    costo) — devolvemos 403 antes de leer la BD para no leakear existencia del
    proyecto vía 404 vs 403.
    """
    user = current_user()
    if not (is_admin() or user.role == 'coordinador'):
        return jsonify({'error': 'Acceso denegado'}), 403
    p = db.get_or_404(Proyecto, id, options=[selectinload(Proyecto.participantes)])
    # Coordinador solo accede a los propios.
    if user.role == 'coordinador' and p.coordinador_id != user.id:
        return jsonify({'error': 'Acceso denegado'}), 403
    return jsonify(_proyecto_detail(p))
