from flask import Blueprint, render_template
from app.utils import login_required
from app.models import Trabajador, Proyecto, AuditLog
from app.extensions import db
from sqlalchemy import func, extract
from datetime import datetime

bp = Blueprint('main', __name__)

@bp.route('/')
@login_required
def home():
    # Tarjetas de resumen
    total_trabajadores = Trabajador.query.count()
    total_proyectos = Proyecto.query.count()
    proyectos_activos = Proyecto.query.filter_by(activo=True).count()
    
    # Trabajadores agregados este mes
    current_month = datetime.now().month
    current_year = datetime.now().year
    # Asuminos 'fecha_ingreso' para nuevos ingresos. Contamos cuantos entraron este mes.
    nuevos_ingresos = Trabajador.query.filter(
        extract('month', Trabajador.fecha_ingreso) == current_month,
        extract('year', Trabajador.fecha_ingreso) == current_year
    ).count()

    # Gráfica: Empleados por Proyecto (agrupados por 'no_proyecto')
    # Filter out Nulls or empty strings
    empleados_por_proyecto = db.session.query(
        Trabajador.no_proyecto, func.count(Trabajador.id)
    ).filter(Trabajador.no_proyecto != None, Trabajador.no_proyecto != '').group_by(Trabajador.no_proyecto).all()
    
    labels_proyectos = [p[0] for p in empleados_por_proyecto]
    data_proyectos = [p[1] for p in empleados_por_proyecto]
    
    # Gráfica: Empleados por Puesto
    empleados_por_puesto = db.session.query(
        Trabajador.puesto, func.count(Trabajador.id)
    ).filter(Trabajador.puesto != None, Trabajador.puesto != '').group_by(Trabajador.puesto).all()
    
    labels_puestos = [p[0] for p in empleados_por_puesto]
    data_puestos = [p[1] for p in empleados_por_puesto]
    
    # Alertas: Cumpleaños del Mes
    cumpleañeros = Trabajador.query.filter(
        extract('month', Trabajador.fecha_nacimiento) == current_month
    ).all()
    
    # Bitácora (Últimos 5 movimientos)
    actividad_reciente = AuditLog.query.order_by(AuditLog.created_at.desc()).limit(5).all()

    return render_template(
        'index.html',
        total_trabajadores=total_trabajadores,
        total_proyectos=total_proyectos,
        proyectos_activos=proyectos_activos,
        nuevos_ingresos=nuevos_ingresos,
        labels_proyectos=labels_proyectos,
        data_proyectos=data_proyectos,
        labels_puestos=labels_puestos,
        data_puestos=data_puestos,
        cumpleañeros=cumpleañeros,
        actividad_reciente=actividad_reciente
    )

@bp.route('/credenciales')
@login_required
def credenciales():
    trabajadores = Trabajador.query.filter_by(activo=True).order_by(Trabajador.id).all()
    return render_template('credenciales.html', trabajadores=trabajadores)



@bp.route('/coordinadores')
@login_required
def coordinadores():
    return render_template('coordinadores.html')
