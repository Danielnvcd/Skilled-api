"""Núcleo compartido del paquete `api_ajustes`.

Define el blueprint `bp` y los serializers comunes (_num, _periodo_row).

No registres rutas en este archivo. Las rutas viven en periodos.py y
descuentos.py.
"""
from decimal import Decimal

from flask import Blueprint

from app.models import AjustePeriodo
from app.utils import to_dec

bp = Blueprint('api_ajustes', __name__, url_prefix='/api/ajustes')


# ── Helpers ────────────────────────────────────────────────────────────────────


def _num(v) -> float:
    return float(to_dec(v)) if v is not None else 0.0


def _periodo_row(p: AjustePeriodo) -> dict:
    total_meta = sum((tp.monto_meta or Decimal('0') for tp in p.trabajadores_periodo), Decimal('0'))
    total_desc = sum((d.monto or Decimal('0') for d in p.descuentos), Decimal('0'))
    return {
        'id': p.id,
        'nombre': p.nombre,
        'fecha_inicio': p.fecha_inicio.isoformat(),
        'fecha_fin': p.fecha_fin.isoformat(),
        'estado': p.estado,
        'created_at': p.created_at.isoformat() if p.created_at else None,
        'num_trabajadores': len(p.trabajadores_periodo),
        'total_meta': float(total_meta),
        'total_descontado': float(total_desc),
    }
