"""Resolución y validación de las líneas a asignar.

Trabaja igual para captura manual y para importación de Excel: recibe líneas
crudas y devuelve un plan con el estado de cada una (ok, ajustada, error), que
la previsualización enseña antes de escribir nada.
"""
from flask import jsonify, request

from app.extensions import db
from app.models import Almacen, Producto, Proyecto

from .._core import _almacen_default_id
from ._comun import _dec, _f, _bucket


# ── Validación compartida ───────────────────────────────────────────────────
# La usan TANTO la previsualización como la aplicación. Es deliberado: si cada
# una validara por su cuenta, la vista previa podría prometer un resultado y la
# aplicación hacer otro — y entonces la previsualización deja de servir para
# lo único que sirve, que es confiar en ella.

def _resolver_lineas(proyecto_id: int, lineas: list, origen: str, modo: str) -> list:
    """Convierte las líneas crudas en un plan evaluado, sin escribir nada.

    Devuelve una lista de dicts con el estado de cada línea:
      estado='ok'      se aplicará tal cual
      estado='aviso'   se aplicará AJUSTADA (no había suficiente)
      estado='error'   se omitirá, con el motivo
    """
    almacen_default = _almacen_default_id()
    plan = []

    for cruda in lineas:
        sku = str(cruda.get('sku') or cruda.get('codigo') or '').strip()
        producto_id = cruda.get('producto_id')
        cantidad = _dec(cruda.get('cantidad'))
        almacen_nombre = str(cruda.get('almacen') or '').strip()
        almacen_id = cruda.get('almacen_id')

        fila = {
            'sku': sku, 'cantidad_pedida': _f(cantidad),
            'estado': 'error', 'motivo': None,
            'producto_id': None, 'descripcion': None, 'unidad': None,
            'almacen_id': None, 'almacen_nombre': None,
            'cantidad_aplicada': 0.0, 'actual': 0.0, 'resultado': 0.0,
            # Cuánto hay en General de este material en esa bodega. La interfaz
            # lo muestra ANTES de que se escriba la cantidad: sin ese dato se
            # captura a ciegas. En origen 'entrada' no aplica (el material llega
            # de fuera, no hay tope) y queda en null.
            'disponible': None,
        }

        # ── Producto ────────────────────────────────────────────────────────
        producto = None
        if producto_id:
            producto = Producto.query.filter(
                Producto.id == producto_id, Producto.activo == True,  # noqa: E712
            ).first()
        elif sku:
            producto = Producto.query.filter(
                Producto.codigo == sku, Producto.activo == True,  # noqa: E712
            ).first()
        if not producto:
            fila['motivo'] = f"El código «{sku or producto_id}» no existe en el catálogo"
            plan.append(fila)
            continue
        fila.update({
            'producto_id': producto.id, 'sku': producto.codigo,
            'descripcion': producto.descripcion, 'unidad': producto.unidad,
        })

        # ── Cantidad ────────────────────────────────────────────────────────
        if cantidad <= 0:
            fila['motivo'] = 'La cantidad debe ser mayor que cero'
            plan.append(fila)
            continue

        # ── Bodega ──────────────────────────────────────────────────────────
        almacen = None
        if almacen_id:
            almacen = Almacen.query.filter(
                Almacen.id == almacen_id, Almacen.activo == True,  # noqa: E712
            ).first()
        elif almacen_nombre:
            almacen = Almacen.query.filter(
                db.func.lower(Almacen.nombre) == almacen_nombre.lower(),
                Almacen.activo == True,  # noqa: E712
            ).first()
            if not almacen:
                # Sugerencia por parecido: un typo en el nombre de la bodega es
                # el error más común al llenar el Excel a mano.
                parecidas = [
                    a.nombre for a in Almacen.query.filter(Almacen.activo == True).all()  # noqa: E712
                    if a.nombre.lower().startswith(almacen_nombre.lower()[:3])
                ]
                sugerencia = f' ¿Quisiste decir «{parecidas[0]}»?' if parecidas else ''
                fila['motivo'] = f'La bodega «{almacen_nombre}» no existe.{sugerencia}'
                plan.append(fila)
                continue
        else:
            almacen = db.session.get(Almacen, almacen_default) if almacen_default else None
        if not almacen:
            fila['motivo'] = 'No se indicó bodega y no hay una bodega predeterminada'
            plan.append(fila)
            continue
        fila.update({'almacen_id': almacen.id, 'almacen_nombre': almacen.nombre})

        # ── Existencias actuales ────────────────────────────────────────────
        actual = _bucket(producto.id, almacen.id, proyecto_id)
        fila['actual'] = _f(actual)

        disponible = _bucket(producto.id, almacen.id, None) if origen == 'general' else None
        if disponible is not None:
            fila['disponible'] = _f(disponible)

        # En modo reemplazar, la cantidad de la línea es el OBJETIVO, no el
        # incremento: se calcula el delta necesario para llegar a él.
        objetivo = cantidad if modo == 'reemplazar' else actual + cantidad
        delta = objetivo - actual

        if delta == 0:
            fila.update({'estado': 'ok', 'cantidad_aplicada': 0.0,
                         'resultado': _f(actual),
                         'motivo': 'Ya tiene esa cantidad; no hay cambio'})
            plan.append(fila)
            continue

        if delta > 0:
            # Hay que meter material al proyecto.
            if origen == 'general':
                if disponible <= 0:
                    fila['motivo'] = (
                        f'No hay stock general de este material en {almacen.nombre}'
                    )
                    plan.append(fila)
                    continue
                if disponible < delta:
                    # Decisión: aplicar lo disponible y avisar.
                    fila.update({
                        'estado': 'aviso',
                        'motivo': (
                            f'Solo hay {_f(disponible)} en General ({almacen.nombre}); '
                            f'se asignará esa cantidad'
                        ),
                    })
                    delta = disponible
                else:
                    fila['estado'] = 'ok'
            else:
                # Entrada nueva: el material llega de fuera, no hay tope.
                fila['estado'] = 'ok'
        else:
            # Modo reemplazar con objetivo MENOR: sobra material y se devuelve
            # a General. Nunca puede dejar el bucket en negativo porque el
            # objetivo se valida como >= 0 más arriba (cantidad > 0).
            fila['estado'] = 'ok'
            fila['motivo'] = 'Sobra material; se devolverá a General'

        fila['cantidad_aplicada'] = _f(delta)
        fila['resultado'] = _f(actual + delta)
        plan.append(fila)

    return plan


def _leer_peticion(proyecto_id: int):
    """Valida proyecto, cuerpo y opciones comunes a los tres endpoints."""
    proyecto = db.session.get(Proyecto, proyecto_id)
    if not proyecto:
        return None, (jsonify({'detail': 'Proyecto no encontrado'}), 404)

    data = request.get_json(silent=True) or {}
    lineas = data.get('lineas')
    if not isinstance(lineas, list) or not lineas:
        return None, (jsonify({'detail': 'Se requiere al menos una línea'}), 422)
    if len(lineas) > 2000:
        return None, (jsonify({'detail': 'Máximo 2000 líneas por operación'}), 422)

    origen = (data.get('origen') or 'general').strip().lower()
    if origen not in ('general', 'entrada'):
        return None, (jsonify({'detail': "origen debe ser 'general' o 'entrada'"}), 422)

    modo = (data.get('modo') or 'sumar').strip().lower()
    if modo not in ('sumar', 'reemplazar'):
        return None, (jsonify({'detail': "modo debe ser 'sumar' o 'reemplazar'"}), 422)

    return (proyecto, lineas, origen, modo, data), None


def _resumen(plan: list) -> dict:
    """Conteo por estado del plan, para la barra de resumen del SPA."""
    return {
        'total': len(plan),
        'ok': sum(1 for f in plan if f['estado'] == 'ok'),
        'avisos': sum(1 for f in plan if f['estado'] == 'aviso'),
        'errores': sum(1 for f in plan if f['estado'] == 'error'),
        'unidades': _f(sum(_dec(f['cantidad_aplicada']) for f in plan
                           if f['estado'] != 'error')),
    }
