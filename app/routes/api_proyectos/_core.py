"""Núcleo del paquete `api_proyectos`.

Define el blueprint, serializers de proyecto/coordinador/trabajador-pickable y
los helpers de validación que comparten lectura y escritura.
"""
from flask import Blueprint, jsonify
from sqlalchemy.orm import joinedload

from app.models import Proyecto, Trabajador, User


bp = Blueprint('api_proyectos', __name__, url_prefix='/api/proyectos')


# HIGH-05: solo estos roles pueden ser asignados como coordinador de un proyecto.
# Asignar un solicitante_material o un inventario abre vías de bypass de
# autorización en endpoints que checan `proyecto.coordinador_id == user.id`.
_VALID_COORD_ROLES = {'coordinador', 'admin', 'super_admin'}


def _coord_initials(nombre: str) -> str:
    return (nombre or '')[:2].upper()


def _proyecto_row(p: Proyecto, participantes_count: int | None = None) -> dict:
    """Resumen para la tabla principal.

    `participantes_count` viene de la subquery de conteo del listado (evita
    cargar las filas de participantes solo para contarlas); si no se pasa,
    cae al len() de la relación.
    """
    coord = p.coordinador
    display = (coord.full_name or coord.username) if coord else None
    return {
        'id': p.id,
        'numero_proyecto': p.numero_proyecto,
        'nombre': p.nombre or '',
        'activo': bool(p.activo),
        'coordinador': {
            'id': coord.id,
            'username': coord.username,
            'full_name': display,
            'initials': _coord_initials(display),
            # Para <UserAvatar/> del SPA: filename actual (sirve de cache
            # buster) — la imagen se descarga de /api/auth/users/<id>/foto.
            'profile_pic': coord.profile_pic,
        } if coord else None,
        'participantes_count': (
            participantes_count if participantes_count is not None
            else len(p.participantes)
        ),
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


def recalcular_campos_proyecto(t: Trabajador) -> None:
    """Deriva `no_proyecto` / `ubicacion_actual` / `coord_a_cargo` del trabajador
    a partir de sus proyectos ACTIVOS (relación M:N `proyecto_trabajador`).

    Regla de negocio: un trabajador puede estar en varios proyectos a la vez
    (y un coordinador llevar varios). En el expediente/credenciales solo deben
    figurar los proyectos activos donde sigue asignado — si el proyecto se
    desactiva o lo sacan de la lista, la relación desaparece de estos campos.

    Los tres son columnas legacy de ancho fijo (ver TRABAJADOR_LENGTHS); con
    varios proyectos se unen con ', ' y se truncan al ancho de la columna.
    Llamar SIEMPRE después de un flush (la query lee la tabla asociativa).
    """
    activos = (
        t.proyectos
        .options(joinedload(Proyecto.coordinador))
        .filter_by(activo=True)
        .order_by(Proyecto.numero_proyecto)
        .all()
    )
    numeros = [p.numero_proyecto for p in activos]
    nombres = [p.nombre for p in activos if p.nombre]
    coords = []
    for p in activos:
        if p.coordinador:
            n = p.coordinador.full_name or p.coordinador.username
            if n and n not in coords:
                coords.append(n)

    t.no_proyecto = (', '.join(numeros)[:100] or None)
    t.ubicacion_actual = (', '.join(nombres)[:150] or None)
    t.coord_a_cargo = (', '.join(coords)[:150] or None)
