"""Endpoints: listado paginado y detalle con geolocalización de IP."""
import ipaddress
import json
from datetime import datetime, timedelta

from flask import jsonify, request
from sqlalchemy import or_

from app.models import AuditLog, User
from app.routes._api_helpers import require_admin
from app.routes.api_auth import jwt_required

from ._core import IP_GEO_CACHE, _log_to_dict, bp


@bp.route('', methods=['GET'])
@jwt_required
def listar():
    err = require_admin()
    if err:
        return err

    page = request.args.get('page', 1, type=int)
    per_page = min(request.args.get('per_page', 50, type=int), 200)
    fecha_filtro = (request.args.get('fecha_filtro') or '').strip()

    # Oculta acciones operativas del rol 'inventario' (movimientos, ajustes, etc.)
    # PERO mantiene visibles login / logout / 2FA — esos sí interesan al admin.
    # LEFT JOIN: entradas sin usuario asociado (anon, usuarios borrados) tienen
    # role NULL y se mantienen visibles.
    query = (
        AuditLog.query
        .outerjoin(User, User.username == AuditLog.user)
        .filter(
            or_(
                User.role.is_(None),
                User.role != 'inventario',
                AuditLog.action.like('API login%'),
                AuditLog.action.like('API logout%'),
                AuditLog.action.like('API 2FA%'),
            )
        )
    )
    if fecha_filtro:
        try:
            filter_date = datetime.strptime(fecha_filtro, '%Y-%m-%d').date()
            # Comparación por rango [día, día+1) en vez de func.date(created_at):
            # envolver la columna en una función la vuelve no-sargable y anula el
            # índice de created_at. Con el rango el planner sí usa el índice.
            inicio = datetime.combine(filter_date, datetime.min.time())
            fin = inicio + timedelta(days=1)
            query = query.filter(
                AuditLog.created_at >= inicio,
                AuditLog.created_at < fin,
            )
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
    err = require_admin()
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
