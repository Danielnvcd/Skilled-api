"""Endpoints de lectura: /mios, /, /meta, /<id>."""
from flask import jsonify, request
from sqlalchemy.orm import selectinload

from app.models import Proyecto, Trabajador, User
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
    """Listado con filtros opcionales `q` (texto) y `estado` (activos|inactivos|todos).

    AuthZ: admin/super_admin ven todos los proyectos. Coordinador ve solo los
    suyos (sin distinción de estado: si está asignado, lo ve). Otros roles 403.
    """
    user = current_user()
    if not (is_admin() or user.role == 'coordinador'):
        return jsonify({'error': 'Acceso denegado'}), 403

    q = (request.args.get('q') or '').strip().lower()
    estado = (request.args.get('estado') or 'todos').lower()

    query = Proyecto.query.options(
        selectinload(Proyecto.coordinador),
        selectinload(Proyecto.participantes),
    )
    if estado == 'activos':
        query = query.filter_by(activo=True)
    elif estado == 'inactivos':
        query = query.filter_by(activo=False)

    # Coordinador solo ve sus propios proyectos (mismo gate que en `/mios`).
    if user.role == 'coordinador':
        query = query.filter_by(coordinador_id=user.id)

    proyectos = query.all()
    if q:
        proyectos = [
            p for p in proyectos
            if q in (p.numero_proyecto or '').lower() or q in (p.nombre or '').lower()
        ]

    return jsonify({
        'items': [_proyecto_row(p) for p in proyectos],
        'total': len(proyectos),
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
    coordinadores = (
        User.query.filter(User.role.in_(['coordinador', 'admin']))
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
    p = Proyecto.query.options(selectinload(Proyecto.participantes)).get_or_404(id)
    # Coordinador solo accede a los propios.
    if user.role == 'coordinador' and p.coordinador_id != user.id:
        return jsonify({'error': 'Acceso denegado'}), 403
    return jsonify(_proyecto_detail(p))
