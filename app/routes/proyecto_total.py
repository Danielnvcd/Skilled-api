from flask import Blueprint, render_template
from app.utils import login_required
from app.extensions import db
from app.models import Proyecto, Prenomina, ReporteSemanal, RegistroDiarioHoras
from sqlalchemy import func

bp = Blueprint('proyecto_total', __name__, url_prefix='/proyecto-total')

@bp.route('/')
@login_required
def index():
    """
    Vista que muestra por cada proyecto:
    - Todas las semanas procesadas (cerradas/aprobadas)
    - El acumulado total de cada columna de nómina
    """
    proyectos = Proyecto.query.order_by(Proyecto.numero_proyecto).all()
    
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
        # Grand totals for the project
        grand = {
            'salario_base': 0, 'pago_viaticos': 0, 'depositos_otros': 0,
            'depositos_prestamos': 0, 'descuento_infonavit': 0, 'ajuste_inbursa': 0,
            'descuentos_otros': 0, 'descuento_prestamos': 0, 'descuento_incidencias': 0,
            'recuperacion_manual': 0, 'total_percepciones': 0, 'total_deducciones': 0,
            'total_a_pagar': 0, 'trabajadores_count': 0
        }
        
        for reporte in reportes:
            fecha_inicio = reporte.fecha_inicio_semana
            
            # Find workers that participated in this project this week
            trabajadores_in_project = db.session.query(
                RegistroDiarioHoras.trabajador_id
            ).filter(
                RegistroDiarioHoras.reporte_id == reporte.id,
                RegistroDiarioHoras.horas_productivas > 0
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
            
            # Weekly sums
            week = {
                'fecha_inicio': fecha_inicio,
                'fecha_fin': reporte.fecha_fin_semana,
                'num_trabajadores': len(prenominas),
                'salario_base': sum(float(p.salario_base or 0) for p in prenominas),
                'pago_viaticos': sum(float(p.pago_viaticos or 0) for p in prenominas),
                'depositos_otros': sum(float(p.depositos_otros or 0) for p in prenominas),
                'depositos_prestamos': sum(float(p.depositos_prestamos or 0) for p in prenominas),
                'descuento_infonavit': sum(float(p.descuento_infonavit or 0) for p in prenominas),
                'ajuste_inbursa': sum(float(p.ajuste_inbursa or 0) for p in prenominas),
                'descuentos_otros': sum(float(p.descuentos_otros or 0) for p in prenominas),
                'descuento_prestamos': sum(float(p.descuento_prestamos or 0) for p in prenominas),
                'descuento_incidencias': sum(float(p.descuento_incidencias or 0) for p in prenominas),
                'recuperacion_manual': sum(float(p.recuperacion_manual or 0) for p in prenominas),
                'total_percepciones': sum(float(p.total_percepciones or 0) for p in prenominas),
                'total_deducciones': sum(float(p.total_deducciones or 0) for p in prenominas),
                'total_a_pagar': sum(float(p.total_a_pagar or 0) for p in prenominas),
            }
            semanas.append(week)
            
            # Accumulate grand totals
            for key in grand:
                if key == 'trabajadores_count':
                    grand[key] += week['num_trabajadores']
                else:
                    grand[key] += week.get(key, 0)
        
        if not semanas:
            continue  # All weeks had zero prenominas, skip this project
        
        proyectos_data.append({
            'proyecto': proyecto,
            'semanas': semanas,
            'num_semanas': len(semanas),
            'grand': grand
        })
    
    return render_template('proyecto_total.html', proyectos_data=proyectos_data)
