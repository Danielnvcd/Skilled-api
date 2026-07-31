"""Serializadores modelo → dict JSON del módulo de Herramientas.

Único lugar donde se define la forma que ve el SPA. `_unidad_to_dict` además
implementa la redacción de campos sensibles: la decisión de SI redactar es de
`permisos._redactar_para_rol`; QUÉ se redacta se define aquí, junto a la forma.
"""
from app.models import (
    AsignacionHerramienta, EventoHerramienta, Herramienta, HerramientaCategoria,
    HerramientaUnidad, IncidenciaHerramienta, MantenimientoHerramienta,
    MediaHerramienta, SolicitudBajaHerramienta, ESTADOS_UNIDAD,
)


def _iso(dt):
    """Fecha/hora en ISO 8601, o None."""
    return dt.isoformat() if dt else None


def _num(v):
    """Decimal → float, conservando None (no lo convierte en 0)."""
    return float(v) if v is not None else None


# Campos administrativos/financieros/logísticos que NO deben salir hacia roles
# "solicitantes" (coordinador): solo necesitan identificar la herramienta y pedir
# baja. Redactamos en el backend para que no se obtengan ni llamando la API directo.
_CAMPOS_SENSIBLES_UNIDAD = (
    'costo_adquisicion', 'vida_util_meses', 'observaciones', 'fecha_adquisicion',
    'complementos', 'cantidad', 'almacen_id', 'estante_id',
    'almacen_nombre', 'estante_nombre',
)


def _herramienta_to_dict(h: Herramienta, *, incluir_stats=False) -> dict:
    base = {
        'id': h.id,
        'sku': h.sku,
        'descripcion': h.descripcion,
        'clasificacion': h.clasificacion,
        'categoria_id': h.categoria_id,
        'marca': h.marca,
        'modelo': h.modelo,
        'uso': h.uso,
        'unidad': h.unidad,
        'piezas': h.piezas,
        'serializada': bool(h.serializada),
        'imagen_url': h.imagen_url,
        'activo': bool(h.activo),
        'created_at': _iso(h.created_at),
        'updated_at': _iso(h.updated_at),
    }
    if incluir_stats:
        stats = {e: 0 for e in ESTADOS_UNIDAD}
        for u in h.unidades:
            stats[u.estado] = stats.get(u.estado, 0) + 1
        base['stats_estados'] = stats
        base['total_unidades'] = sum(stats.values())
    return base


def _unidad_to_dict(u: HerramientaUnidad, *, incluir_relacion=False, redactar=False) -> dict:
    base = {
        'id': u.id,
        'herramienta_id': u.herramienta_id,
        'no_serie': u.no_serie,
        'codigo_interno': u.codigo_interno,
        'qr_code': u.qr_code,
        'estado': u.estado,
        'almacen_id': u.almacen_id,
        'estante_id': u.estante_id,
        'asignado_trabajador_id': u.asignado_trabajador_id,
        'cantidad': float(u.cantidad or 1),
        'complementos': u.complementos,
        'fecha_adquisicion': _iso(u.fecha_adquisicion),
        'costo_adquisicion': _num(u.costo_adquisicion),
        'vida_util_meses': u.vida_util_meses,
        'observaciones': u.observaciones,
        'fecha_baja': _iso(u.fecha_baja),
        'motivo_baja': u.motivo_baja,
        'created_at': _iso(u.created_at),
    }
    if incluir_relacion:
        base['herramienta'] = {
            'id': u.herramienta.id,
            'sku': u.herramienta.sku,
            'descripcion': u.herramienta.descripcion,
            'clasificacion': u.herramienta.clasificacion,
            'marca': u.herramienta.marca,
            'modelo': u.herramienta.modelo,
            'imagen_url': u.herramienta.imagen_url,
        } if u.herramienta else None
        base['almacen_nombre'] = u.almacen.nombre if u.almacen else None
        base['estante_nombre'] = u.estante.nombre if u.estante else None
        base['trabajador_nombre'] = u.asignado_trabajador.nombre_completo if u.asignado_trabajador else None
        foto_principal = next((m for m in u.media if m.tipo == 'FOTO_HERRAMIENTA'), None)
        base['foto_principal_id'] = foto_principal.id if foto_principal else None
    if redactar:
        for campo in _CAMPOS_SENSIBLES_UNIDAD:
            base.pop(campo, None)
    return base


def _asignacion_to_dict(a: AsignacionHerramienta) -> dict:
    return {
        'id': a.id,
        'unidad_id': a.unidad_id,
        'trabajador_id': a.trabajador_id,
        'trabajador_nombre': a.trabajador.nombre_completo if a.trabajador else None,
        'unidad_codigo': a.unidad.codigo_interno if a.unidad else None,
        'unidad_no_serie': a.unidad.no_serie if a.unidad else None,
        'unidad_descripcion': (a.unidad.herramienta.descripcion if (a.unidad and a.unidad.herramienta) else None),
        'solicitud_id': a.solicitud_id,
        'proyecto': a.proyecto,
        'fecha_entrega': _iso(a.fecha_entrega),
        'fecha_devolucion_prevista': _iso(a.fecha_devolucion_prevista),
        'fecha_devolucion_real': _iso(a.fecha_devolucion_real),
        'estado': a.estado,
        'condicion_entrega': a.condicion_entrega,
        'condicion_devolucion': a.condicion_devolucion,
        'observaciones_entrega': a.observaciones_entrega,
        'observaciones_devolucion': a.observaciones_devolucion,
        'entregado_por_id': a.entregado_por_id,
        'entregado_por_username': a.entregado_por.username if a.entregado_por else None,
        'recibido_por_id': a.recibido_por_id,
    }


def _mantenimiento_to_dict(m: MantenimientoHerramienta) -> dict:
    return {
        'id': m.id,
        'unidad_id': m.unidad_id,
        'tipo': m.tipo,
        'motivo': m.motivo,
        'proveedor': m.proveedor,
        'fecha_inicio': _iso(m.fecha_inicio),
        'fecha_fin': _iso(m.fecha_fin),
        'costo': _num(m.costo),
        'observaciones': m.observaciones,
        'estado_final_unidad': m.estado_final_unidad,
        'estado': m.estado,
        'abierto_por_id': m.abierto_por_id,
        'cerrado_por_id': m.cerrado_por_id,
    }


def _incidencia_to_dict(i: IncidenciaHerramienta) -> dict:
    return {
        'id': i.id,
        'unidad_id': i.unidad_id,
        'reportado_por_id': i.reportado_por_id,
        'reportado_por_username': i.reportado_por.username if i.reportado_por else None,
        'tipo': i.tipo,
        'descripcion': i.descripcion,
        'estado': i.estado,
        'fecha_reporte': _iso(i.fecha_reporte),
        'atendido_por_id': i.atendido_por_id,
        'resolucion': i.resolucion,
        'fecha_cierre': _iso(i.fecha_cierre),
    }


def _solicitud_baja_to_dict(s: SolicitudBajaHerramienta) -> dict:
    return {
        'id': s.id,
        'unidad_id': s.unidad_id,
        'solicitante_id': s.solicitante_id,
        'solicitante_username': s.solicitante.username if s.solicitante else None,
        'motivo': s.motivo,
        'estado': s.estado,
        'autorizado_por_id': s.autorizado_por_id,
        'ejecutado_por_id': s.ejecutado_por_id,
        'fecha_solicitud': _iso(s.fecha_solicitud),
        'fecha_autorizacion': _iso(s.fecha_autorizacion),
        'fecha_ejecucion': _iso(s.fecha_ejecucion),
        'observaciones': s.observaciones,
    }


def _evento_to_dict(e: EventoHerramienta) -> dict:
    return {
        'id': e.id,
        'tipo_evento': e.tipo_evento,
        'estado_anterior': e.estado_anterior,
        'estado_nuevo': e.estado_nuevo,
        'usuario_id': e.usuario_id,
        'usuario_username': e.usuario.username if e.usuario else None,
        'observaciones': e.observaciones,
        'referencia_id': e.referencia_id,
        'referencia_tipo': e.referencia_tipo,
        'fecha': _iso(e.fecha),
    }


def _media_to_dict(m: MediaHerramienta) -> dict:
    return {
        'id': m.id,
        'unidad_id': m.unidad_id,
        'evento_id': m.evento_id,
        'tipo': m.tipo,
        'ruta_archivo': m.ruta_archivo,
        'url': f"/api/v1/herramientas-unidades/{m.unidad_id}/media/{m.id}",
        'nombre_original': m.nombre_original,
        'mime': m.mime,
        'tamano_bytes': m.tamano_bytes,
        'subido_por_id': m.subido_por_id,
        'created_at': _iso(m.created_at),
    }


def _categoria_to_dict(c: HerramientaCategoria) -> dict:
    return {
        'id': c.id,
        'nombre': c.nombre,
        'imagen_url': c.imagen_url,
        'icono': c.icono,
        'color': c.color,
    }
