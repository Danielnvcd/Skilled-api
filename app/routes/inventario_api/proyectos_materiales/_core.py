"""Piezas compartidas del plan de materiales por proyecto.

El coordinador es dueño de sus proyectos: los guards de alcance viven aquí para
que ninguna vista del módulo se salte el filtro por dueño.
"""
from decimal import Decimal

from flask import jsonify, request

from app.extensions import db
from app.models import (
    Proyecto, SolicitudMaterial, SolicitudMaterialDetalle,
)



def _f(v) -> float:
    return float(v or 0)


# Unidades "contables": no tiene sentido planear 2.5 piezas. El resto de
# unidades (kg, m, l, m2, m3, rollo…) sí admite decimales. Mantener en sintonía
# con `ENTERO_UNIDS` del frontend (ProyectoInventarioDetalle.jsx).
_UNIDADES_ENTERAS = {
    'pza', 'pz', 'pieza', 'piezas', 'pieza(s)',
    'unidad', 'unidades', 'u', 'und', 'uds', 'pieza',
    'caja', 'cajas',
}


def _es_unidad_entera(unidad: str | None) -> bool:
    return (unidad or '').strip().lower() in _UNIDADES_ENTERAS


def _consumo_por_producto(proyecto_id: int) -> dict[int, Decimal]:
    """Suma `cantidad_entregada` (consumo real) por producto para las
    solicitudes MATERIAL ligadas a este proyecto. Una sola query agregada."""
    rows = (
        db.session.query(
            SolicitudMaterialDetalle.producto_id,
            db.func.coalesce(db.func.sum(SolicitudMaterialDetalle.cantidad_entregada), 0),
        )
        .join(SolicitudMaterial, SolicitudMaterial.id == SolicitudMaterialDetalle.solicitud_id)
        .filter(
            SolicitudMaterial.proyecto_id == proyecto_id,
            SolicitudMaterialDetalle.producto_id != None,  # noqa: E711  solo MATERIAL
        )
        .group_by(SolicitudMaterialDetalle.producto_id)
        .all()
    )
    return {pid: Decimal(str(cant or 0)) for pid, cant in rows}


def _denegar_si_ajeno(proyecto: Proyecto):
    """Scoping por dueño: el coordinador solo puede tocar SUS proyectos
    (`Proyecto.coordinador_id`). Para inventario/admin/super_admin no aplica.

    Devuelve una respuesta 403 si un coordinador intenta un proyecto ajeno, o
    None si está permitido. 403 (no 404) es consistente con `api_proyectos`: el
    proyecto existe pero no es suyo."""
    user = request.current_user
    if user.role == 'coordinador' and proyecto.coordinador_id != user.id:
        return jsonify({'detail': 'No eres el coordinador de este proyecto'}), 403
    return None


def _scope_proyectos_query(query):
    """Restringe una query de `Proyecto` al alcance del usuario: el coordinador
    solo ve los suyos; inventario/admin/super_admin ven todos."""
    user = request.current_user
    if user.role == 'coordinador':
        query = query.filter(Proyecto.coordinador_id == user.id)
    return query
