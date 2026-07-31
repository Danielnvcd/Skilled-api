"""Blueprint compartido del paquete `herramientas_api` y audiencia de realtime.

Aislado en su propio módulo para que los demás módulos del núcleo (schemas,
serializers, permisos…) puedan importarlo sin ciclos.
"""
import logging

from flask import Blueprint

logger = logging.getLogger(__name__)

bp = Blueprint('herramientas_api', __name__, url_prefix='/api/v1')

# Roles que ven herramientas (catalogo + unidades + asignaciones + incidencias).
# coordinador es relevante porque puede recibir asignaciones; solicitante_material
# las pide vía solicitudes. Mantengo simétrico con _INV_ROLES.
_HERR_ROLES = ['admin', 'super_admin', 'inventario', 'coordinador']
