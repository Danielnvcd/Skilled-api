"""Núcleo del paquete `api_bitacora`. Blueprint + cache LRU de geo IP."""
from collections import OrderedDict

from flask import Blueprint

from app.models import AuditLog


bp = Blueprint('api_bitacora', __name__, url_prefix='/api/bitacora')


# MED-07/HIGH-07: cache LRU acotada (antes era un dict global sin límite que
# crecía indefinidamente). 5000 IPs ≈ 1-2 MB de RAM, suficiente para horizontes
# de retención de meses sin presión de memoria.
_GEO_CACHE_MAX = 5000


class _LRUDict(OrderedDict):
    def __setitem__(self, key, value):
        if key in self:
            self.move_to_end(key)
        super().__setitem__(key, value)
        if len(self) > _GEO_CACHE_MAX:
            self.popitem(last=False)


IP_GEO_CACHE = _LRUDict()


def _log_to_dict(log: AuditLog) -> dict:
    return {
        'id': log.id,
        'user': log.user or 'Sistema',
        'action': log.action,
        'ip': log.ip,
        'created_at': log.created_at.isoformat() if log.created_at else None,
    }
