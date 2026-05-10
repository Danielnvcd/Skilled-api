from flask import Blueprint, render_template, request, jsonify
from app.utils import login_required, admin_required
from app.models import AuditLog
from app.extensions import db
from datetime import datetime
import ipaddress

bp = Blueprint('bitacora', __name__, url_prefix='/bitacora')

# Cache en memoria para evitar llamadas repetitivas a la API externa
IP_GEO_CACHE = {}

@bp.route('/', methods=['GET'])
@login_required
@admin_required
def index():
    page = request.args.get('page', 1, type=int)
    fecha_filtro = request.args.get('fecha_filtro', '')
    
    query = AuditLog.query

    if fecha_filtro:
        try:
            # Parse the incoming date and filter logs from that single day
            filter_date = datetime.strptime(fecha_filtro, '%Y-%m-%d').date()
            query = query.filter(db.func.date(AuditLog.created_at) == filter_date)
        except ValueError:
            pass
            
    # Order by newest first
    query = query.order_by(AuditLog.created_at.desc())
    
    # Paginate (50 items per page)
    pagination = query.paginate(page=page, per_page=50, error_out=False)
    logs = pagination.items
    
    return render_template(
        'bitacora.html',
        logs=logs,
        pagination=pagination,
        fecha_filtro=fecha_filtro
    )

@bp.route('/api/log/<int:log_id>', methods=['GET'])
@login_required
@admin_required
def get_log_detail(log_id):
    """
    Obtiene el detalle de un log específico por ID y trata de geolocalizar la IP
    si es pública. No interrumpe la carga en caso de fallo de API externa.
    """
    log = AuditLog.query.get_or_404(log_id)
    
    ip = log.ip
    location_text = "Ubicación no disponible"
    
    if ip and ip.strip():
        # Revisar en cache primario
        if ip in IP_GEO_CACHE:
            location_text = IP_GEO_CACHE[ip]
        else:
            try:
                # Validar IP
                ip_obj = ipaddress.ip_address(ip)
                if ip_obj.is_private or ip_obj.is_loopback:
                    location_text = "IP Local/Privada"
                    IP_GEO_CACHE[ip] = location_text
                else:
                    # Llamar a ip-api gratuita usando built-in urllib
                    import urllib.request
                    import json
                    req = urllib.request.Request(f"http://ip-api.com/json/{ip}")
                    with urllib.request.urlopen(req, timeout=3) as response:
                        if response.status == 200:
                            data = json.loads(response.read().decode())
                            if data.get("status") == "success":
                                country = data.get("country", "")
                                region = data.get("regionName", "")
                                city = data.get("city", "")
                                isp = data.get("isp", "")
                                location_parts = [p for p in [city, region, country] if p]
                                loc_str = ", ".join(location_parts)
                                
                                if isp:
                                    loc_str += f" (ISP: {isp})"
                                    
                                location_text = loc_str
                                IP_GEO_CACHE[ip] = location_text
            except Exception:
                # Si falla timeout, parseo invaido o algo más, silencioso
                pass

    return jsonify({
        "id": log.id,
        "action": log.action,
        "user": log.user or "Sistema",
        "date": log.created_at.strftime('%d/%m/%Y %I:%M %p') if log.created_at else "Desconocida",
        "ip": ip or "Sin IP",
        "location": location_text
    })
