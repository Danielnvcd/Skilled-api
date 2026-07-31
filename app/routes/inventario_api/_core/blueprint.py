"""Blueprint compartido del paquete `inventario_api` y audiencias de realtime.

Aislado en su propio módulo para que los demás módulos del núcleo (auth,
schemas, stock…) puedan importarlo sin arrastrar el paquete completo y sin
ciclos de importación.
"""
import logging

from flask import Blueprint, jsonify

logger = logging.getLogger(__name__)

bp = Blueprint('inventario_api', __name__, url_prefix='/api/v1')


# ─── Roles que reciben eventos en tiempo real ────────────────────────────────

# Roles que reciben eventos de inventario (productos, almacenes, movimientos,
# tomas). Coordinador/solicitante ven productos en sus solicitudes pero no
# editan; igual les incluimos para que sus listas (catalogo en SolicitudForm)
# refresquen al cambiar el stock.
_INV_ROLES = ['admin', 'super_admin', 'inventario', 'coordinador', 'solicitante_material']
# Las solicitudes involucran a todos: solicitante las crea, inventario aprueba,
# coordinador puede crearlas también.
_SOL_ROLES = ['admin', 'super_admin', 'inventario', 'coordinador', 'solicitante_material']


@bp.route('/health', methods=['GET'])
def health_check():
    return jsonify({'status': 'ok'})
