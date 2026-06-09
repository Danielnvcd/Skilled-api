"""Núcleo del paquete `api_proyectos`.

Define el blueprint, serializers de proyecto/coordinador/trabajador-pickable y
los helpers de validación que comparten lectura y escritura.
"""
from flask import Blueprint, jsonify

from app.models import Proyecto, Trabajador, User


bp = Blueprint('api_proyectos', __name__, url_prefix='/api/proyectos')


# HIGH-05: solo estos roles pueden ser asignados como coordinador de un proyecto.
# Asignar un solicitante_material o un inventario abre vías de bypass de
# autorización en endpoints que checan `proyecto.coordinador_id == user.id`.
_VALID_COORD_ROLES = {'coordinador', 'admin', 'super_admin'}


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
