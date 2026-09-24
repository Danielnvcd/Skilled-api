"""Núcleo del paquete `api_hikvision`.

Blueprint, gate de permisos y helpers compartidos. No registres rutas aquí:
viven en los submódulos (ver `__init__.py`).

Permisos: este módulo administra EMPLEADOS en un lector de control de acceso,
así que va al eje de RRHH (`is_admin()`), no al de sistemas. Un rol `sistemas`
administra la infraestructura, no quién puede abrir una puerta ni de quién es
la cara que se registra.
"""
from __future__ import annotations

import hashlib

from flask import Blueprint, jsonify

from app.models import DispositivoHikvision, SyncEmpleadoHikvision, Trabajador
from app.services.hikvision import fotos
from app.services.hikvision import usuarios as svc_usuarios
# Viven en el servicio porque también las usa el trabajador de tareas; se
# reexportan aquí para las rutas.
from app.services.hikvision.sincronizar import candidatos_query, sync_por_trabajador  # noqa: F401

bp = Blueprint('api_hikvision', __name__, url_prefix='/api/hikvision')


@bp.after_request
def _no_store(response):
    """Nada de este módulo debe quedar cacheado: lleva configuración de
    dispositivos y datos de personal."""
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    return response


def error(mensaje: str, codigo: int = 400):
    return jsonify({'error': mensaje}), codigo


def cambios_pendientes(t: Trabajador, sync: SyncEmpleadoHikvision | None) -> list[str]:
    """Qué cambió en el ERP desde la última sincronización exitosa.

    Lista vacía = el lector tiene lo mismo que el ERP. Se calcula sin llamar al
    equipo ni leer la foto: se comparan el hash de los datos y la key de la foto
    guardados al sincronizar contra los valores actuales.
    """
    if sync is None or sync.estado != 'SINCRONIZADO':
        return []
    cambios = []
    numero_actual = (t.no_empleado or '').strip()
    if numero_actual != sync.employee_no_remoto:
        cambios.append('número de empleado')
    elif sync.hash_datos and sync.hash_datos != svc_usuarios.huella_datos(t, numero_actual):
        cambios.append('nombre')
    # Sin `foto_key` (filas anteriores a la columna) no se puede saber: no se
    # marca, para no pintar a todos como desactualizados de golpe.
    if sync.foto_key and sync.foto_key != (t.foto_perfil or ''):
        cambios.append('fotografía')
    return cambios


def fila_candidato(t: Trabajador, sync: SyncEmpleadoHikvision | None) -> dict:
    """Un empleado tal como lo pinta la pantalla de sincronización."""
    tiene_foto = fotos.tiene_foto(t)
    cambios = cambios_pendientes(t, sync)
    return {
        'id': t.id,
        'no_empleado': t.no_empleado,
        'nombre_completo': t.nombre_completo,
        'area': t.area or '',
        'puesto': t.puesto or '',
        'tiene_foto': tiene_foto,
        # El frontend usa esto para deshabilitar la casilla; el backend vuelve a
        # comprobarlo al sincronizar, así que no es la única defensa.
        'sincronizable': tiene_foto,
        'motivo_no_sincronizable': '' if tiene_foto else 'Sin fotografía de perfil',
        'foto_url': f'/api/trabajadores/{t.id}/foto/thumb' if tiene_foto else None,
        # Cambia con cada foto nueva: el frontend lo usa para no mostrar la
        # miniatura vieja que ya tenía en memoria. No expone la key de R2.
        'foto_version': (
            hashlib.sha256(t.foto_perfil.encode('utf-8')).hexdigest()[:12] if tiene_foto else None
        ),
        # DESACTUALIZADO no se guarda: se deriva al listar, porque depende de
        # la ficha del empleado, que se edita en otro módulo.
        'estado': 'DESACTUALIZADO' if cambios else (sync.estado if sync else 'NO_AGREGADO'),
        'cambios_pendientes': cambios,
        'ultimo_error': (sync.ultimo_error or '') if sync else '',
        'sincronizado_en': (
            sync.sincronizado_en.isoformat() if sync and sync.sincronizado_en else None
        ),
    }


def obtener_dispositivo_o_404(dispositivo_id: int) -> DispositivoHikvision:
    from app.extensions import db
    return db.get_or_404(DispositivoHikvision, dispositivo_id)
