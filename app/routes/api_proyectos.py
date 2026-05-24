"""API JSON para el módulo de Proyectos (consumida por el SPA React).

Replica `proyectos.py` (vista clásica con Jinja) pero responde JSON y autentica con
JWT. Mantiene la sincronización de campos del trabajador (`no_proyecto`,
`ubicacion_actual`, `coord_a_cargo`) cuando se altera la lista de participantes,
para que el dato visible en el módulo de empleados quede coherente.
"""
import traceback

from flask import Blueprint, current_app, g, jsonify, request
from sqlalchemy.orm import selectinload

from app.extensions import db
from app.models import Proyecto, Trabajador, User
from app.routes.api_auth import jwt_required
from app.utils import log_action

bp = Blueprint('api_proyectos', __name__, url_prefix='/api/proyectos')


# ── Helpers ────────────────────────────────────────────────────────────────────

def _u():
    return g._jwt_user


def _is_admin() -> bool:
    return _u().role in ('admin', 'super_admin')


def _admin_only():
    if not _is_admin():
        return jsonify({'error': 'Acceso denegado'}), 403
    return None


def _coord_initials(username: str) -> str:
    return (username or '')[:2].upper()


def _proyecto_row(p: Proyecto) -> dict:
    """Resumen para la tabla principal."""
    coord = p.coordinador
    return {
        'id': p.id,
        'numero_proyecto': p.numero_proyecto,
        'nombre': p.nombre or '',
        'activo': bool(p.activo),
        'coordinador': {
            'id': coord.id,
            'username': coord.username,
            'initials': _coord_initials(coord.username),
        } if coord else None,
        'participantes_count': len(p.participantes),
        'created_at': p.created_at.isoformat() if p.created_at else None,
    }


def _proyecto_detail(p: Proyecto) -> dict:
    """Detalle completo (para edición)."""
    return {
        'id': p.id,
        'numero_proyecto': p.numero_proyecto,
        'nombre': p.nombre or '',
        'activo': bool(p.activo),
        'coordinador_id': p.coordinador_id,
        'participantes_ids': [t.id for t in p.participantes],
    }


def _trabajador_pickable(t: Trabajador) -> dict:
    """Subset usado por el selector de participantes del modal."""
    sin_salario = not t.salario_real_pactado_x_sem or t.salario_real_pactado_x_sem <= 0
    sin_nomina = not t.tipo_nomina or not t.tipo_nomina.strip()
    motivos = []
    if sin_salario:
        motivos.append('Sin salario')
    if sin_nomina:
        motivos.append('Sin tipo nómina')
    return {
        'id': t.id,
        'no_empleado': t.no_empleado,
        'nombre': t.nombre,
        'puesto': t.puesto or '',
        'disponible': not (sin_salario or sin_nomina),
        'motivos_no_disponible': motivos,
    }


def _parse_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        return False
    return str(v).strip().lower() in ('1', 'true', 'on', 'yes', 'si', 'sí')


# HIGH-05: solo estos roles pueden ser asignados como coordinador de un proyecto.
# Asignar un solicitante_material o un inventario abre vías de bypass de
# autorización en endpoints que checan `proyecto.coordinador_id == user.id`.
_VALID_COORD_ROLES = {'coordinador', 'admin', 'super_admin'}


def _validar_coordinador(coord_id):
    """Verifica que coord_id apunta a un User existente con rol válido para
    coordinar un proyecto. Devuelve (User|None, error_response|None)."""
    if coord_id is None:
        return None, None
    sup = User.query.get(coord_id)
    if not sup:
        return None, (jsonify({'error': 'El coordinador indicado no existe'}), 400)
    if sup.role not in _VALID_COORD_ROLES:
        return None, (jsonify({
            'error': f"El usuario '{sup.username}' no tiene rol válido para coordinar un proyecto"
        }), 400)
    return sup, None


def _sync_trabajador_from_proyecto(t: Trabajador, p: Proyecto, coord_name: str | None):
    t.no_proyecto = p.numero_proyecto
    t.ubicacion_actual = p.nombre
    if coord_name:
        t.coord_a_cargo = coord_name


# ── Endpoints ──────────────────────────────────────────────────────────────────

@bp.route('/mios', methods=['GET'])
@jwt_required
def mios():
    """Lista los proyectos del usuario. Para coordinador, solo los proyectos
    donde está asignado como coordinador; para admin, todos los activos.
    Devuelve información enriquecida (participantes con nombre + número de
    empleado) para la vista 'Mis Proyectos' del coordinador en móvil."""
    user = _u()
    query = Proyecto.query.options(selectinload(Proyecto.participantes)).filter_by(activo=True)
    if user.role == 'coordinador':
        query = query.filter_by(coordinador_id=user.id)
    elif not _is_admin():
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
    """Listado con filtros opcionales `q` (texto) y `estado` (activos|inactivos|todos)."""
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
    """Datos auxiliares para el modal de alta/edición."""
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
    p = Proyecto.query.options(selectinload(Proyecto.participantes)).get_or_404(id)
    # Misma regla que el blueprint clásico: coordinador solo accede a los propios.
    if _u().role == 'coordinador' and p.coordinador_id != _u().id:
        return jsonify({'error': 'Acceso denegado'}), 403
    return jsonify(_proyecto_detail(p))


@bp.route('', methods=['POST'])
@jwt_required
def crear():
    denied = _admin_only()
    if denied:
        return denied

    data = request.get_json(silent=True) or {}
    numero_proyecto = (data.get('numero_proyecto') or '').strip()
    nombre = (data.get('nombre') or '').strip()

    if not numero_proyecto or not nombre:
        return jsonify({'error': 'Número de Proyecto y Nombre son obligatorios'}), 400

    if Proyecto.query.filter_by(numero_proyecto=numero_proyecto).first():
        return jsonify({'error': 'El Número de Proyecto ya existe'}), 409

    coord_id = data.get('coordinador_id') or None
    try:
        coord_id = int(coord_id) if coord_id else None
    except (TypeError, ValueError):
        return jsonify({'error': 'coordinador_id inválido'}), 400

    # HIGH-05: validar que el coord existe y tiene rol apropiado ANTES de crear
    sup, err = _validar_coordinador(coord_id)
    if err:
        return err

    try:
        nuevo = Proyecto(
            numero_proyecto=numero_proyecto,
            nombre=nombre,
            activo=_parse_bool(data.get('activo', True)),
            coordinador_id=coord_id,
        )

        coord_name = sup.username if sup else None

        participantes_ids = data.get('participantes_ids') or []
        for t_id in participantes_ids:
            t = Trabajador.query.get(t_id)
            if t:
                nuevo.participantes.append(t)
                _sync_trabajador_from_proyecto(t, nuevo, coord_name)

        db.session.add(nuevo)
        db.session.commit()
        log_action(f"Creó el proyecto {nuevo.numero_proyecto} - {nuevo.nombre}")
        return jsonify(_proyecto_detail(nuevo)), 201

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error creando proyecto: {e}\n{traceback.format_exc()}")
        return jsonify({'error': 'Error al crear el proyecto'}), 500


@bp.route('/<int:id>', methods=['PUT'])
@jwt_required
def actualizar(id):
    denied = _admin_only()
    if denied:
        return denied

    p = Proyecto.query.get_or_404(id)
    data = request.get_json(silent=True) or {}

    nuevo_numero = (data.get('numero_proyecto') or '').strip()
    nuevo_nombre = (data.get('nombre') or '').strip()
    if not nuevo_numero or not nuevo_nombre:
        return jsonify({'error': 'Número de Proyecto y Nombre son obligatorios'}), 400

    if nuevo_numero != p.numero_proyecto and Proyecto.query.filter_by(numero_proyecto=nuevo_numero).first():
        return jsonify({'error': 'El Número de Proyecto ya existe'}), 409

    coord_id = data.get('coordinador_id') or None
    try:
        coord_id = int(coord_id) if coord_id else None
    except (TypeError, ValueError):
        return jsonify({'error': 'coordinador_id inválido'}), 400

    # HIGH-05: validar que el coord existe y tiene rol apropiado
    sup, err = _validar_coordinador(coord_id)
    if err:
        return err

    try:
        old_numero = p.numero_proyecto

        p.numero_proyecto = nuevo_numero
        p.nombre = nuevo_nombre
        p.activo = _parse_bool(data.get('activo', p.activo))
        p.coordinador_id = coord_id

        coord_name = sup.username if sup else None

        nuevos_ids = set(int(x) for x in (data.get('participantes_ids') or []))
        actuales_ids = set(t.id for t in p.participantes)

        # Limpiar campos del trabajador removido del proyecto (solo si el dato
        # del trabajador todavía apunta al número viejo de este proyecto).
        for past in list(p.participantes):
            if past.id not in nuevos_ids and past.no_proyecto == old_numero:
                past.no_proyecto = None
                past.ubicacion_actual = None
                past.coord_a_cargo = None

        p.participantes = []
        for t_id in nuevos_ids:
            t = Trabajador.query.get(t_id)
            if t:
                p.participantes.append(t)
                _sync_trabajador_from_proyecto(t, p, coord_name)

        db.session.commit()
        log_action(f"Actualizó el proyecto {p.numero_proyecto} - {p.nombre}")
        return jsonify(_proyecto_detail(p))

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error actualizando proyecto {id}: {e}\n{traceback.format_exc()}")
        return jsonify({'error': 'Error al actualizar el proyecto'}), 500
