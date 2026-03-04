from flask import Blueprint, render_template, request
from app.utils import login_required
from app.models import AuditLog
from app.extensions import db
from datetime import datetime

bp = Blueprint('bitacora', __name__, url_prefix='/bitacora')

@bp.route('/', methods=['GET'])
@login_required
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
