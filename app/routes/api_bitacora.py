"""API JSON para la Bitácora de auditoría (consumida por el SPA React).

Replica `bitacora.py` (vista clásica Jinja) pero responde JSON y autentica con
JWT. Reusa la misma cache de geo IP en memoria.
"""
import ipaddress
import json
from datetime import datetime

from flask import Blueprint, g, jsonify, request

from app.extensions import db
from app.models import AuditLog
from app.routes.api_auth import jwt_required
from app.routes.bitacora import IP_GEO_CACHE

bp = Blueprint('api_bitacora', __name__, url_prefix='/api/bitacora')


def _u():
    return g._jwt_user


def _is_admin() -> bool:
    return _u().role in ('admin', 'super_admin')


def _admin_only():
    if not _is_admin():
        return jsonify({'error': 'Acceso denegado'}), 403
    return None


def _log_to_dict(log: AuditLog) -> dict:
    return {
        'id': log.id,
        'user': log.user or 'Sistema',
        'action': log.action,
        'ip': log.ip,
        'created_at': log.created_at.isoformat() if log.created_at else None,
    }


@bp.route('', methods=['GET'])
@jwt_required
def listar():
    err = _admin_only()
    if err:
        return err

    page = request.args.get('page', 1, type=int)
    per_page = min(request.args.get('per_page', 50, type=int), 200)
    fecha_filtro = (request.args.get('fecha_filtro') or '').strip()

    query = AuditLog.query
    if fecha_filtro:
        try:
            filter_date = datetime.strptime(fecha_filtro, '%Y-%m-%d').date()
            query = query.filter(db.func.date(AuditLog.created_at) == filter_date)
        except ValueError:
            pass

    pagination = query.order_by(AuditLog.created_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False,
    )
    return jsonify({
        'items': [_log_to_dict(log) for log in pagination.items],
        'page': pagination.page,
        'per_page': pagination.per_page,
        'total': pagination.total,
        'pages': pagination.pages,
        'has_next': pagination.has_next,
        'has_prev': pagination.has_prev,
    })


@bp.route('/<int:log_id>', methods=['GET'])
@jwt_required
def detalle(log_id: int):
    err = _admin_only()
    if err:
        return err

    log = AuditLog.query.get(log_id)
    if not log:
        return jsonify({'error': 'Registro no encontrado'}), 404

    ip = log.ip or ''
    location_text = 'Ubicación no disponible'

    if ip.strip():
        if ip in IP_GEO_CACHE:
            location_text = IP_GEO_CACHE[ip]
        else:
            try:
                ip_obj = ipaddress.ip_address(ip)
                if ip_obj.is_private or ip_obj.is_loopback:
                    location_text = 'IP Local/Privada'
                    IP_GEO_CACHE[ip] = location_text
                else:
                    import urllib.request
                    # HIGH-07: HTTPS + IP canónica para evitar URL injection.
                    ip_for_url = str(ip_obj)
                    req = urllib.request.Request(f'https://ip-api.com/json/{ip_for_url}')
                    with urllib.request.urlopen(req, timeout=3) as response:
                        if response.status == 200:
                            data = json.loads(response.read().decode())
                            if data.get('status') == 'success':
                                country = data.get('country', '')
                                region = data.get('regionName', '')
                                city = data.get('city', '')
                                isp = data.get('isp', '')
                                parts = [p for p in [city, region, country] if p]
                                loc_str = ', '.join(parts)
                                if isp:
                                    loc_str += f' (ISP: {isp})'
                                location_text = loc_str
                                IP_GEO_CACHE[ip] = location_text
            except Exception:
                pass

    return jsonify({
        'id': log.id,
        'action': log.action,
        'user': log.user or 'Sistema',
        'date': log.created_at.isoformat() if log.created_at else None,
        'ip': ip or 'Sin IP',
        'location': location_text,
    })
