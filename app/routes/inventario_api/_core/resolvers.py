"""Resolución de entidades referenciadas en los payloads (almacén, proyecto,
partes de un vale).

Reglas de resolución que estaban copiadas entre `solicitudes`, `movimientos`,
`compras` y `productos`. Cada helper levanta `ErrorDeNegocio` con el mismo
mensaje/status que antes devolvía cada vista a mano.
"""
from app.models import Almacen, Proyecto, Trabajador

from .http import ErrorDeNegocio
from .stock import _almacen_default_id


_SIN_BODEGAS = 'No hay bodegas registradas para descontar stock'


def resolver_almacen_activo(almacen_id: int | None, *, usar_default: bool = True,
                            mensaje_sin_bodegas: str = _SIN_BODEGAS) -> Almacen:
    """Devuelve el `Almacen` activo indicado, cayendo a la bodega default cuando
    no se especifica ninguna (compat con clientes que tratan al stock como
    global). Levanta `ErrorDeNegocio` 400 si no hay bodegas y 404 si la pedida
    no existe o está inactiva."""
    if not almacen_id and usar_default:
        almacen_id = _almacen_default_id()
    if not almacen_id:
        raise ErrorDeNegocio(mensaje_sin_bodegas, 400)
    almacen = Almacen.query.filter(
        Almacen.id == almacen_id,
        Almacen.activo == True,  # noqa: E712
    ).first()
    if not almacen:
        raise ErrorDeNegocio(f'Almacén #{almacen_id} no existe o está inactivo', 404)
    return almacen


def resolver_proyecto(proyecto_id: int | None, numero_proyecto: str) -> Proyecto | None:
    """Resuelve el proyecto de una solicitud/entrega: por id explícito (el SPA lo
    manda desde el dropdown) o, si no viene, por su número — así las solicitudes
    de clientes viejos quedan ligadas igual y atribuyen consumo.

    Devuelve el `Proyecto` o None (texto libre sin proyecto en el sistema).
    Levanta `ErrorDeNegocio` 422 si el id explícito no existe.
    """
    if proyecto_id is not None:
        proy = Proyecto.query.filter(Proyecto.id == proyecto_id).first()
        if not proy:
            raise ErrorDeNegocio(f'Proyecto #{proyecto_id} no existe', 422)
        return proy
    return Proyecto.query.filter(Proyecto.numero_proyecto == numero_proyecto).first()


def resolver_trabajador_activo(trabajador_id: int, etiqueta: str = '') -> Trabajador:
    """Trabajador activo por id. Levanta `ErrorDeNegocio` 422 si no existe o está
    dado de baja. `etiqueta` prefija el mensaje cuando la parte tiene un rol en
    el vale ('Entrega', 'Recibe'); sin etiqueta el mensaje va suelto."""
    trab = Trabajador.query.filter(
        Trabajador.id == trabajador_id,
        Trabajador.activo == True,  # noqa: E712
    ).first()
    if not trab:
        detalle = (
            f'{etiqueta}: trabajador #{trabajador_id} no existe o está inactivo'
            if etiqueta else
            f'Trabajador #{trabajador_id} no existe o está inactivo'
        )
        raise ErrorDeNegocio(detalle, 422)
    return trab


def _resolver_partes(data: dict):
    """Valida y resuelve las partes (entrega/recibe) de un movimiento a partir del
    payload validado por `MovimientoCreateSchema`.

    Cada parte puede venir como trabajador del sistema (`*_trabajador_id`) o como
    nombre libre (`*_nombre`). Si viene un `*_trabajador_id` debe existir y estar
    activo (mismo criterio que la entrega directa). Devuelve una tupla
    `(campos, error)` donde `campos` es el dict listo para asignar al modelo
    (`entrega_trabajador_id`, `entrega_nombre`, `recibe_trabajador_id`,
    `recibe_nombre`) y `error` es un `Response` 422 (o None si todo ok). Ambas
    partes son OPCIONALES: si no se manda nada, `campos` queda en None."""
    campos = {
        'entrega_trabajador_id': None, 'entrega_nombre': None,
        'recibe_trabajador_id': None, 'recibe_nombre': None,
    }
    for parte, key_id, key_nombre in (
        ('Entrega', 'entrega_trabajador_id', 'entrega_nombre'),
        ('Recibe', 'recibe_trabajador_id', 'recibe_nombre'),
    ):
        trab_id = data.get(key_id)
        nombre = (data.get(key_nombre) or '').strip() or None
        if trab_id is not None:
            try:
                trab = resolver_trabajador_activo(trab_id, parte)
            except ErrorDeNegocio as exc:
                return None, exc.como_respuesta()
            # El trabajador manda: ignoramos un nombre libre redundante.
            campos[key_id] = trab.id
            campos[key_nombre] = None
        else:
            campos[key_nombre] = nombre
    return campos, None
