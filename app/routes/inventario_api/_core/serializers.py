"""Serializadores modelo → dict JSON del módulo de Inventario.

Único lugar donde se define la forma que ve el SPA. Si un campo cambia aquí,
cambia en todos los endpoints que lo devuelven.
"""
from app.models import (
    Almacen, Estante, MovimientoInventario, Producto,
    SolicitudMaterial, SolicitudMaterialDetalle,
)


def _iso(dt):
    """Fecha/hora en ISO 8601, o None. Evita repetir el ternario en cada campo."""
    return dt.isoformat() if dt else None


def _producto_to_dict(p: Producto) -> dict:
    actual = float(p.stock_actual or 0)
    reservado = float(p.stock_reservado or 0)
    return {
        'id': p.id,
        'codigo': p.codigo,
        'descripcion': p.descripcion,
        'categoria': p.categoria,
        # Marca / fabricante (None si no se capturó). Independiente del proveedor.
        'marca': p.marca,
        'unidad': p.unidad,
        # Atributos de cable (None en productos que no son cable).
        'cable_tipo': p.cable_tipo,
        'cable_calibre': p.cable_calibre,
        'stock_actual': actual,
        'stock_reservado': reservado,         # Pausa 2-bis: apartado por solicitudes APROBADAS
        'stock_disponible': actual - reservado,  # lo que sí se puede mover
        'stock_minimo': float(p.stock_minimo or 0),
        'precio_unitario': float(p.precio_unitario or 0),
        'imagen_url': p.imagen_url,
        # Estado del pipeline de imágenes → R2 (None si no aplica / R2 apagado).
        # El SPA lo usa para mostrar un badge "procesando/error" si quiere.
        'imagen_estado': p.imagen_estado,
        # Pausa 9: proveedor default para Compras express.
        'proveedor_default_nombre': p.proveedor_default_nombre,
        'proveedor_default_contacto': p.proveedor_default_contacto,
        'activo': bool(p.activo),
        'created_at': _iso(p.created_at),
        'updated_at': _iso(p.updated_at),
        'created_by_id': p.created_by_id,
    }


def _almacen_to_dict(a: Almacen) -> dict:
    return {
        'id': a.id,
        'nombre': a.nombre,
        'ubicacion': a.ubicacion,
        'activo': bool(a.activo),
        'qr_code': a.qr_code,
    }


def _estante_to_dict(e: Estante) -> dict:
    return {
        'id': e.id,
        'nombre': e.nombre,
        'descripcion': e.descripcion,
        'almacen_id': e.almacen_id,
        'qr_code': e.qr_code,
        'activo': bool(e.activo),
        'filas': e.filas or 1,
        'columnas': e.columnas or 1,
        'created_at': _iso(e.created_at),
    }


def _movimiento_to_dict(m: MovimientoInventario) -> dict:
    # Datos del producto embebidos: con miles de productos el SPA ya no puede
    # descargar el catálogo completo solo para resolver el nombre por id.
    prod = m.producto
    return {
        'id': m.id,
        'tipo': m.tipo,
        'producto_id': m.producto_id,
        'producto_codigo': prod.codigo if prod else None,
        'producto_descripcion': prod.descripcion if prod else None,
        'producto_unidad': prod.unidad if prod else None,
        'cantidad': float(m.cantidad or 0),
        'fecha': _iso(m.fecha),
        'almacen_origen_id': m.almacen_origen_id,
        'almacen_destino_id': m.almacen_destino_id,
        # Atribución de proyecto (None = general). Se incluye el número de
        # proyecto para pintarlo sin resolver por id en el cliente.
        'proyecto_origen_id': m.proyecto_origen_id,
        'proyecto_destino_id': m.proyecto_destino_id,
        'proyecto_origen': m.proyecto_origen.numero_proyecto if m.proyecto_origen else None,
        'proyecto_destino': m.proyecto_destino.numero_proyecto if m.proyecto_destino else None,
        'usuario_id': m.usuario_id,
        'motivo': m.motivo,
        # Partes del vale (None si no se capturaron / movimiento interno).
        'entrega_trabajador_id': m.entrega_trabajador_id,
        'recibe_trabajador_id': m.recibe_trabajador_id,
        'entrega_nombre': m.entrega_display,
        'recibe_nombre': m.recibe_display,
    }


def _item_de_detalle(d: SolicitudMaterialDetalle, tipo: str) -> dict:
    """Datos del ítem pedido (producto o herramienta) de una línea de solicitud.

    Devuelve las claves `item_*` (nombre neutro) más los atributos de cable, que
    solo existen en MATERIAL. Las claves `producto_*` que espera el SPA viejo
    las agrega `_solicitud_detalle_to_dict`.
    """
    if tipo == 'HERRAMIENTA':
        h = d.herramienta
        return {
            'herramienta_id': d.herramienta_id,
            'producto_id': None,
            'item_descripcion': h.descripcion if h else 'Herramienta eliminada',
            'item_codigo': h.sku if h else '---',
            'item_unidad': h.unidad if h else 'pza',
            'imagen_url': None,  # herramientas usan su propio sistema de media
            'cable_tipo': None,
            'cable_calibre': None,
        }
    p = d.producto
    return {
        'producto_id': d.producto_id,
        'herramienta_id': None,
        'item_descripcion': p.descripcion if p else 'Producto eliminado',
        'item_codigo': p.codigo if p else '---',
        'item_unidad': p.unidad if p else 'pza',
        'imagen_url': p.imagen_url if p else None,
        # Atributos de cable: solo aplican a MATERIAL de cable (None en el resto).
        'cable_tipo': p.cable_tipo if p else None,
        'cable_calibre': p.cable_calibre if p else None,
    }


def _solicitud_detalle_to_dict(d: SolicitudMaterialDetalle) -> dict:
    tipo = (d.tipo_item or 'MATERIAL').upper()
    item = _item_de_detalle(d, tipo)
    return {
        'id': d.id,
        'tipo_item': tipo,
        'cantidad_solicitada': float(d.cantidad_solicitada or 0),
        'cantidad_aprobada': float(d.cantidad_aprobada or 0),
        'cantidad_entregada': float(d.cantidad_entregada or 0),
        'fecha_uso_inicio': _iso(d.fecha_uso_inicio),
        'fecha_uso_fin': _iso(d.fecha_uso_fin),
        'justificacion': d.justificacion,
        'complementos': d.complementos,
        **item,
        # Compat: claves antiguas para que el SPA siga renderizando.
        'producto_descripcion': item['item_descripcion'],
        'producto_codigo': item['item_codigo'],
        'producto_unidad': item['item_unidad'],
    }


def _solicitud_to_dict(s: SolicitudMaterial) -> dict:
    return {
        'id': s.id,
        'solicitante_id': s.solicitante_id,
        'proyecto': s.proyecto,
        'proyecto_id': s.proyecto_id,
        'notas': s.notas,
        'estatus': s.estatus,
        'fecha_creacion': _iso(s.fecha_creacion),
        'fecha_cierre': _iso(s.fecha_cierre),
        # Nombre del solicitante REAL (trabajador / texto libre en entregas
        # directas; el capturista en solicitudes normales). Ver solicitante_display.
        'solicitante_nombre': s.solicitante_display,
        # Quién la capturó (útil para distinguir mostrador del solicitante).
        'capturado_por': (s.solicitante.full_name or s.solicitante.username) if s.solicitante else None,
        'entrega_directa': bool(s.entrega_directa),
        'solicitante_trabajador_id': s.solicitante_trabajador_id,
        # Trazabilidad de la resolución.
        'aprobada_por': s._user_display(s.aprobada_por),
        'entregada_por': s._user_display(s.entregada_por),
        'detalles': [_solicitud_detalle_to_dict(d) for d in s.detalles],
    }
