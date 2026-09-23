"""Sincronizar empleados del ERP con un lector: la lógica, sin HTTP.

La usan dos caminos que deben dar exactamente el mismo resultado:

  · la API, para tandas chicas (responde al instante con el detalle);
  · el trabajador de tareas del proceso de escucha (`tareas.py`), para
    tandas grandes que no caben en el límite de 120 s de gunicorn.

Dos reglas que se aplican AQUÍ, no solo en la interfaz:

  1. Solo empleados con `es_oficina = true`, activos y sin fecha de baja.
  2. Solo empleados con fotografía utilizable.

Una petición hecha a mano con curl salta el frontend pero no esto. La segunda
regla se comprueba dos veces: al filtrar la lista y, ya dentro del bucle, al
leer y convertir la imagen de verdad — porque la columna puede tener una ruta
cuyo archivo ya no exista.
"""
from __future__ import annotations

import logging

from app.extensions import db
from app.models import SyncEmpleadoHikvision, Trabajador, _now_utc

from . import fotos as svc_fotos
from . import usuarios as svc_usuarios
from .client import ClienteHikvision
from .errores import ErrorHikvision

logger = logging.getLogger(__name__)


def candidatos_query():
    """Empleados que PUEDEN ir a un lector: de oficina, activos y sin baja.

    Es la misma condición que aplica la sincronización. Tenerla en un solo
    lugar evita que el listado ofrezca a alguien que luego se rechaza.
    """
    return (
        Trabajador.query
        .filter(
            Trabajador.es_oficina.is_(True),
            Trabajador.activo.is_(True),
            Trabajador.fecha_baja.is_(None),
        )
        .order_by(Trabajador.nombre, Trabajador.nombre_apellidos)
    )


def sync_por_trabajador(dispositivo_id: int) -> dict[int, SyncEmpleadoHikvision]:
    """Filas de sincronización de un dispositivo, indexadas por trabajador.

    Una sola consulta para todo el listado: hacer una por empleado convertiría
    la pantalla en N+1 consultas.
    """
    filas = SyncEmpleadoHikvision.query.filter_by(dispositivo_id=dispositivo_id).all()
    return {f.trabajador_id: f for f in filas}


def sincronizar_uno(cli, dispositivo, trabajador, estados, caps, *,
                     foto: tuple[bytes, str] | None = None,
                     marcar_error: bool = True) -> dict:
    """Sincroniza a UN empleado. Nunca lanza: devuelve el resultado de su intento.

    Que no lance es la razón de que una foto mala de una persona no cancele la
    tanda entera de las demás.

    `foto` = (jpeg, hash) ya preparados, para enviar una foto que todavía no es
    la de perfil. `marcar_error=False` no toca la fila si falla: al probar una
    foto nueva, que el lector la rechace no significa que el registro que ya
    tenía haya dejado de funcionar.
    """
    resultado = {
        'trabajador_id': trabajador.id,
        'no_empleado': trabajador.no_empleado,
        'nombre_completo': trabajador.nombre_completo,
        'ok': False,
        'estado': 'ERROR',
        'accion': '',
        'error': '',
    }

    fila = estados.get(trabajador.id)
    try:
        employee_no = svc_usuarios.employee_no_de(trabajador, maximo=caps['employee_no_max'])

        # Se prepara la foto ANTES de tocar el equipo: si no se puede leer ni
        # convertir, no se le crea el usuario.
        jpeg, hash_foto = foto or svc_usuarios.preparar_foto(trabajador)

        accion = svc_usuarios.crear_o_actualizar(
            cli, trabajador, employee_no, nombre_max=caps['nombre_max'],
        )

        # Desde aquí el usuario YA existe en el equipo. Que la foto se haya
        # convertido bien no garantiza que el lector pueda modelar el rostro:
        # eso lo decide su motor facial, y lo hace DESPUÉS del alta. Si falla,
        # quedaría un usuario sin cara —inútil en un lector facial y confuso al
        # auditar el equipo—, así que se revierte.
        #
        # Solo se revierte lo que esta llamada creó. Si el usuario ya existía,
        # borrarlo destruiría un registro que hasta hace un momento funcionaba,
        # y una foto nueva mala no es razón para dejar a alguien fuera.
        try:
            svc_usuarios.subir_rostro(cli, employee_no, jpeg)

            # Verificación explícita contra el equipo: sin esto daríamos por
            # buena una sincronización que el lector pudo no haber completado.
            if not svc_usuarios.tiene_rostro(cli, employee_no):
                raise ErrorHikvision(
                    'El lector aceptó la fotografía pero no la registró. Inténtalo de nuevo.',
                    detalle=f'FDSearch sin coincidencias tras subir el rostro de {employee_no}',
                )
        except ErrorHikvision:
            if accion == 'creado':
                try:
                    svc_usuarios.eliminar(cli, employee_no)
                except ErrorHikvision as e_limpieza:
                    # La reversión es best-effort: si tampoco se puede borrar,
                    # se deja constancia en el log y gana el error original,
                    # que es el que explica qué hay que arreglar.
                    logger.warning(
                        'Hikvision: no se pudo revertir el alta de %s tras fallar el '
                        'rostro: %s', employee_no, e_limpieza.detalle,
                    )
            raise

        # Si el número de empleado cambió en el ERP, el lector acaba de recibir
        # un usuario NUEVO con el número nuevo y todavía tiene el viejo. Sin
        # esto quedaría una segunda identidad de la misma persona en el equipo
        # que el ERP ya no puede ver ni quitar.
        if (fila is not None and fila.estado == 'SINCRONIZADO'
                and fila.employee_no_remoto != employee_no):
            try:
                svc_usuarios.eliminar(cli, fila.employee_no_remoto)
            except ErrorHikvision as e_viejo:
                logger.warning(
                    'Hikvision: no se pudo quitar el número anterior %s de trab=%s: %s',
                    fila.employee_no_remoto, trabajador.id, e_viejo.detalle,
                )

        if fila is None:
            fila = SyncEmpleadoHikvision(
                dispositivo_id=dispositivo.id,
                trabajador_id=trabajador.id,
                employee_no_remoto=employee_no,
            )
            db.session.add(fila)
            estados[trabajador.id] = fila

        fila.employee_no_remoto = employee_no
        fila.estado = 'SINCRONIZADO'
        fila.hash_datos = svc_usuarios.huella_datos(trabajador, employee_no)
        fila.hash_foto = hash_foto
        # Con `foto` explícita la key aún no existe: la fija quien la guarda.
        fila.foto_key = None if foto else trabajador.foto_perfil
        fila.ultimo_error = None
        fila.ultimo_intento = _now_utc()
        fila.sincronizado_en = _now_utc()

        resultado.update(ok=True, estado='SINCRONIZADO', accion=accion)

    except ErrorHikvision as e:
        logger.warning(
            'Hikvision sync disp=%s trab=%s: %s', dispositivo.id, trabajador.id, e.detalle,
        )
        resultado['error'] = e.mensaje
        if not marcar_error:
            return resultado
        if fila is None:
            fila = SyncEmpleadoHikvision(
                dispositivo_id=dispositivo.id,
                trabajador_id=trabajador.id,
                employee_no_remoto=(trabajador.no_empleado or '')[:32] or '?',
            )
            db.session.add(fila)
            estados[trabajador.id] = fila
        fila.estado = 'ERROR'
        fila.ultimo_error = e.mensaje[:500]
        fila.ultimo_intento = _now_utc()

    return resultado


def sincronizar_lote(dispositivo, ids, *, al_avanzar=None) -> dict:
    """Sincroniza a los trabajadores `ids` con el lector. No hace commit.

    Devuelve {'ok', 'resultados', 'resumen'} con el detalle por persona: que
    uno falle no cancela a los demás. `al_avanzar(procesados, total)` se llama
    tras cada empleado (el trabajador de tareas lo usa para el progreso).

    Si falla la CONEXIÓN con el lector (no un empleado), lanza ErrorHikvision:
    no tiene sentido seguir intentando con los demás.
    """
    ids = {int(i) for i in ids}

    # Se re-filtra contra la MISMA condición del listado. Un id que no salga de
    # aquí es alguien que no es de oficina, está dado de baja, o no existe —
    # da igual lo que haya mandado el cliente.
    permitidos = candidatos_query().filter(Trabajador.id.in_(ids)).all()
    encontrados = {t.id for t in permitidos}
    rechazados = [
        {
            'trabajador_id': i, 'ok': False, 'estado': 'RECHAZADO',
            'error': 'No es personal de oficina activo, o no existe.',
        }
        for i in sorted(ids - encontrados)
    ]

    # Sin fotografía no se intenta siquiera: se reporta y se sigue.
    con_foto = [t for t in permitidos if svc_fotos.tiene_foto(t)]
    sin_foto = [
        {
            'trabajador_id': t.id, 'no_empleado': t.no_empleado,
            'nombre_completo': t.nombre_completo, 'ok': False, 'estado': 'SIN_FOTO',
            'error': 'No tiene fotografía de perfil. Súbela en su ficha.',
        }
        for t in permitidos if not svc_fotos.tiene_foto(t)
    ]

    total = len(ids)
    procesados = len(rechazados) + len(sin_foto)
    resultados = []
    estados = sync_por_trabajador(dispositivo.id)

    if con_foto:
        with ClienteHikvision.desde_dispositivo(dispositivo) as cli:
            caps = cli.capacidades_usuario()
            for t in con_foto:
                resultados.append(sincronizar_uno(cli, dispositivo, t, estados, caps))
                procesados += 1
                if al_avanzar:
                    al_avanzar(procesados, total)

    resultados.extend(sin_foto)
    resultados.extend(rechazados)

    ok = sum(1 for r in resultados if r['ok'])
    fallos = len(resultados) - ok
    return {
        'ok': fallos == 0,
        'resultados': resultados,
        'resumen': {'sincronizados': ok, 'fallidos': fallos, 'total': len(resultados)},
    }
