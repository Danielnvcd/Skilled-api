"""Núcleo compartido del paquete `inventario_api`.

Era un solo archivo de ~1100 líneas que mezclaba blueprint, auth, schemas,
serializers, contabilidad de stock y reservas. Ahora cada responsabilidad vive
en su módulo y este `__init__` es la fachada: los módulos de rutas siguen
haciendo `from ._core import ...` sin enterarse del reparto.

  blueprint.py    `bp`, audiencias de realtime (_INV_ROLES/_SOL_ROLES), /health
  auth.py         guards por rol (@_require_*) y escritura de AuditLog
  schemas.py      schemas Marshmallow de entrada + regex de validación
  serializers.py  modelo → dict JSON que consume el SPA
  http.py         parseo de payload/query, ErrorDeNegocio, @transaccion_de_stock
  unidades.py     reglas de unidad de medida y categoría cable (sin DB)
  stock.py        buckets por proyecto, caches y consumo/depósito de existencias
  reservas.py     apartado por solicitudes APROBADAS (Producto.stock_reservado)
  resolvers.py    resolución de almacén / proyecto / trabajador desde el payload
  pdf.py          render de PDFs (xhtml2pdf) compartido por vales y reportes

Las rutas NO viven aquí (salvo /health, que es trivial): están en los submódulos
por dominio (productos, almacenes, movimientos, solicitudes, catalogo…).
"""
from .auth import (
    _audit,
    _require_inventario,
    _require_inventario_admin,
    _require_login,
    _require_plan_materiales,
)
from .blueprint import _INV_ROLES, _SOL_ROLES, bp, logger
from .http import (
    ErrorDeNegocio,
    MENSAJE_LOCK,
    _es_error_de_lock,
    _int_arg,
    _parse_or_422,
    respuesta_lock,
    transaccion_de_stock,
)
from .pdf import cantidad_legible, renderizar_pdf, ruta_logo
from .reservas import (
    _intentar_reservar,
    _liberar_reservas,
    _pendiente_de_linea,
    _reserva_derivada,
    _reservas_de_solicitud,
)
from .resolvers import (
    _resolver_partes,
    resolver_almacen_activo,
    resolver_proyecto,
    resolver_trabajador_activo,
)
from .schemas import (
    CODIGO_REGEX,
    AjusteBucketsSchema,
    AlmacenCreateSchema,
    AlmacenUpdateSchema,
    CategoriaConfigUpsertSchema,
    EntregaDirectaSchema,
    EntregarSolicitudSchema,
    EstanteCreateSchema,
    EstanteLayoutSchema,
    EstanteUpdateSchema,
    MovimientoCreateSchema,
    ProductoCreateSchema,
    ProductoUpdateSchema,
    ProyectoPlanUpsertSchema,
    SolicitudCreateSchema,
    SolicitudDetallePatchSchema,
    SolicitudUpdateEstadoSchema,
    _BaseSchema,
    _IMAGEN_URL_REGEX,
)
from .serializers import (
    _almacen_to_dict,
    _estante_to_dict,
    _movimiento_to_dict,
    _producto_to_dict,
    _solicitud_detalle_to_dict,
    _solicitud_to_dict,
)
from .stock import (
    _ajustar_bucket,
    _almacen_default_id,
    _cantidad_en_celdas_almacen,
    _consumir_bucket_exacto,
    _consumir_proyecto_luego_general,
    _consumir_reconciliando,
    _depositar,
    _lock_stock,
    _lock_stock_proyecto,
    _producto_almacen_stock,
    _recalcular_cache_stock,
    _recalcular_caches,
    _stock_proyecto_total,
)
from .unidades import (
    CABLE_UNIDAD,
    _es_categoria_cable,
    _es_entero,
    _unidad_permite_decimales,
    sin_acentos,
)

__all__ = [
    # blueprint
    'bp', 'logger', '_INV_ROLES', '_SOL_ROLES',
    # auth
    '_require_login', '_require_inventario', '_require_inventario_admin',
    '_require_plan_materiales', '_audit',
    # http
    '_parse_or_422', '_int_arg', '_es_error_de_lock', 'respuesta_lock',
    'ErrorDeNegocio', 'transaccion_de_stock', 'MENSAJE_LOCK',
    # schemas
    'CODIGO_REGEX', '_IMAGEN_URL_REGEX', '_BaseSchema',
    'ProductoCreateSchema', 'ProductoUpdateSchema',
    'AlmacenCreateSchema', 'AlmacenUpdateSchema',
    'EstanteCreateSchema', 'EstanteUpdateSchema', 'EstanteLayoutSchema',
    'MovimientoCreateSchema', 'AjusteBucketsSchema',
    'SolicitudCreateSchema', 'SolicitudUpdateEstadoSchema',
    'SolicitudDetallePatchSchema', 'EntregarSolicitudSchema',
    'EntregaDirectaSchema', 'CategoriaConfigUpsertSchema',
    'ProyectoPlanUpsertSchema',
    # serializers
    '_producto_to_dict', '_almacen_to_dict', '_estante_to_dict',
    '_movimiento_to_dict', '_solicitud_to_dict', '_solicitud_detalle_to_dict',
    # unidades
    '_unidad_permite_decimales', '_es_categoria_cable', '_es_entero',
    'sin_acentos', 'CABLE_UNIDAD',
    # stock
    '_almacen_default_id', '_lock_stock', '_lock_stock_proyecto',
    '_producto_almacen_stock', '_cantidad_en_celdas_almacen',
    '_recalcular_cache_stock', '_recalcular_caches',
    '_depositar', '_consumir_proyecto_luego_general', '_consumir_bucket_exacto',
    '_consumir_reconciliando', '_stock_proyecto_total', '_ajustar_bucket',
    # reservas
    '_reservas_de_solicitud', '_intentar_reservar', '_liberar_reservas',
    '_reserva_derivada', '_pendiente_de_linea',
    # resolvers
    '_resolver_partes', 'resolver_almacen_activo', 'resolver_proyecto',
    'resolver_trabajador_activo',
    # pdf
    'renderizar_pdf', 'cantidad_legible', 'ruta_logo',
]
