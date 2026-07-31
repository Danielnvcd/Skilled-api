"""Núcleo del paquete `api_sistemas`.

Blueprint del panel de TI/soporte. TODO endpoint de este paquete pasa por
`require_panel_sistemas()`, que exige rol `sistemas` (o `super_admin`) **y**
2FA activo — ver el docstring de ese helper para el porqué.
"""
from flask import Blueprint


bp = Blueprint('api_sistemas', __name__, url_prefix='/api/sistemas')


@bp.after_request
def _no_store(response):
    """Nada de este panel debe quedar cacheado.

    Las respuestas llevan sesiones activas, IPs, eventos de seguridad y estado
    de infraestructura. Igual que en `api_auth`, se marcan como no almacenables
    para que no queden en caché de navegador, proxies ni service worker.
    """
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response
