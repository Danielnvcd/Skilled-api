"""Núcleo compartido del paquete `herramientas_api`.

Era un solo archivo de ~430 líneas que mezclaba blueprint, constantes de upload,
14 schemas, 9 serializers y los helpers de permisos. Ahora cada responsabilidad
vive en su módulo y este `__init__` es la fachada: los módulos de rutas siguen
haciendo `from ._core import ...` sin enterarse del reparto.

  blueprint.py    `bp` y los roles que ven herramientas (_HERR_ROLES)
  permisos.py     guards reusados de Inventario + visibilidad/redacción por rol
  schemas.py      schemas Marshmallow de entrada
  serializers.py  modelo → dict JSON que consume el SPA
  uploads.py      validación y destino de las fotos de una unidad
  codigos.py      generación del codigo_interno (HRR-000123)

`permisos.py` es el ÚNICO punto que importa de `inventario_api`: los dos módulos
comparten roles y bitácora. Antes ese import estaba repetido en los 7 módulos
de rutas.

No registres rutas aquí. Las rutas viven en los submódulos por dominio:
catalogo.py, unidades.py, asignaciones.py, mantenimientos.py, incidencias.py,
bajas.py, multimedia.py.
"""
from .blueprint import _HERR_ROLES, bp, logger
from .codigos import _next_codigo_interno
from .permisos import (
    CODIGO_REGEX,
    _IMAGEN_URL_REGEX,
    _audit,
    _int_arg,
    _parse_or_422,
    _puede_ver_unidad,
    _redactar_para_rol,
    _require_inventario,
    _require_inventario_admin,
    _require_login,
)
from .schemas import (
    AsignacionCreateSchema,
    CategoriaUpsertSchema,
    DevolucionSchema,
    HerramientaCreateSchema,
    HerramientaUpdateSchema,
    IncidenciaAtenderSchema,
    IncidenciaCreateSchema,
    MantenimientoCierreSchema,
    MantenimientoCreateSchema,
    SolicitudBajaAutorizarSchema,
    SolicitudBajaCreateSchema,
    UnidadCreateSchema,
    UnidadUpdateSchema,
    _BaseSchema,
)
from .serializers import (
    _asignacion_to_dict,
    _categoria_to_dict,
    _evento_to_dict,
    _herramienta_to_dict,
    _incidencia_to_dict,
    _mantenimiento_to_dict,
    _media_to_dict,
    _solicitud_baja_to_dict,
    _unidad_to_dict,
)
from .uploads import (
    ALLOWED_IMAGE_EXTS,
    ALLOWED_IMAGE_MIMES,
    UPLOAD_MAX_BYTES,
    _upload_dir,
    _validar_imagen_archivo,
)

__all__ = [
    # blueprint
    'bp', 'logger', '_HERR_ROLES',
    # permisos (guards compartidos con inventario_api)
    '_require_login', '_require_inventario', '_require_inventario_admin',
    '_parse_or_422', '_int_arg', '_audit',
    'CODIGO_REGEX', '_IMAGEN_URL_REGEX',
    '_puede_ver_unidad', '_redactar_para_rol',
    # schemas
    '_BaseSchema',
    'HerramientaCreateSchema', 'HerramientaUpdateSchema', 'CategoriaUpsertSchema',
    'UnidadCreateSchema', 'UnidadUpdateSchema',
    'AsignacionCreateSchema', 'DevolucionSchema',
    'MantenimientoCreateSchema', 'MantenimientoCierreSchema',
    'IncidenciaCreateSchema', 'IncidenciaAtenderSchema',
    'SolicitudBajaCreateSchema', 'SolicitudBajaAutorizarSchema',
    # serializers
    '_herramienta_to_dict', '_unidad_to_dict', '_asignacion_to_dict',
    '_mantenimiento_to_dict', '_incidencia_to_dict', '_solicitud_baja_to_dict',
    '_evento_to_dict', '_media_to_dict', '_categoria_to_dict',
    # uploads
    'UPLOAD_MAX_BYTES', 'ALLOWED_IMAGE_MIMES', 'ALLOWED_IMAGE_EXTS',
    '_validar_imagen_archivo', '_upload_dir',
    # codigos
    '_next_codigo_interno',
]
