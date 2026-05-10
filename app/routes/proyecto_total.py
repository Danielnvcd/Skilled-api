from flask import Blueprint, render_template, request
from decimal import Decimal
from app.utils import login_required, admin_required, to_dec
from app.extensions import db
from app.models import Proyecto, Prenomina, ReporteSemanal, RegistroDiarioHoras
from sqlalchemy import func
from sqlalchemy.orm import selectinload

bp = Blueprint('proyecto_total', __name__, url_prefix='/proyecto-total')

_MONEY_KEYS = [
    'salario_base', 'pago_viaticos', 'pago_festivos', 'depositos_otros', 'depositos_prestamos',
    'descuento_infonavit', 'ajuste_inbursa', 'descuentos_otros', 'descuento_prestamos',
    'descuento_incidencias', 'recuperacion_manual', 'total_percepciones',
    'total_deducciones', 'total_a_pagar'
]

@bp.route('/')
@login_required
@admin_required
def index():
    """
    Vista que muestra por cada proyecto:
    - Todas las semanas procesadas (cerradas/aprobadas)
    - El acumulado total de cada columna de nómina
    """
    page = request.args.get('page', 1, type=int)
    q = request.args.get('q', '').strip()

    query = Proyecto.query.options(selectinload(Proyecto.participantes))
    if q:
        query = query.filter(Proyecto.nombre.ilike(f'%{q}%') | Proyecto.numero_proyecto.ilike(f'%{q}%'))
        
    pagination = query.order_by(Proyecto.numero_proyecto).paginate(page=page, per_page=20, error_out=False)
    proyectos = pagination.items
    
    proyectos_data = []
    
    for proyecto in proyectos:
        # Find all closed weekly reports for this project
        reportes = ReporteSemanal.query.filter_by(
            proyecto_id=proyecto.id, 
            estado='PRENOMINA_CERRADA'
        ).order_by(ReporteSemanal.fecha_inicio_semana).all()
        
        if not reportes:
            continue  # Skip projects with no closed payrolls
        
        semanas = []
        # Grand totals for the project (Decimal precision)
        grand = {k: Decimal('0') for k in _MONEY_KEYS}
        grand['trabajadores_count'] = 0
        
        for reporte in reportes:
            fecha_inicio = reporte.fecha_inicio_semana
            
            # Find workers that participated in this project this week
            trabajadores_in_project = db.session.query(
                RegistroDiarioHoras.trabajador_id
            ).filter(
                RegistroDiarioHoras.reporte_id == reporte.id
            ).distinct().all()
            t_ids = [t[0] for t in trabajadores_in_project]
            
            # Get their prenominas for that week
            prenominas = Prenomina.query.filter(
                Prenomina.fecha_inicio == fecha_inicio,
                Prenomina.estado == 'APROBADO',
                Prenomina.trabajador_id.in_(t_ids)
            ).all() if t_ids else []
            
            if not prenominas:
                continue  # Skip weeks where no workers had approved prenominas
            
            # Weekly sums using Decimal
            week_dec = {}
            for key in _MONEY_KEYS:
                week_dec[key] = sum((to_dec(getattr(p, key)) for p in prenominas), Decimal('0'))
            
            # Convert to float for Jinja template rendering
            week = {
                'fecha_inicio': fecha_inicio,
                'fecha_fin': reporte.fecha_fin_semana,
                'num_trabajadores': len(prenominas),
            }
            for key in _MONEY_KEYS:
                week[key] = float(week_dec[key])
            
            semanas.append(week)
            
            # Accumulate grand totals (in Decimal)
            for key in _MONEY_KEYS:
                grand[key] += week_dec[key]
            grand['trabajadores_count'] += week['num_trabajadores']
        
        if not semanas:
            continue  # All weeks had zero prenominas, skip this project
        
        # Convert grand totals to float for template
        grand_float = {k: float(v) if isinstance(v, Decimal) else v for k, v in grand.items()}
        
        proyectos_data.append({
            'proyecto': proyecto,
            'semanas': semanas,
            'num_semanas': len(semanas),
            'grand': grand_float
        })
    
    return render_template('proyecto_total.html', proyectos_data=proyectos_data, pagination=pagination, q=q)
