from flask import Blueprint, render_template
from app.utils import login_required
from app.models import Trabajador, Proyecto, AuditLog, DocumentoTrabajador, CredencialPlanta
from app.extensions import db
from sqlalchemy import func, extract, case
from datetime import datetime, timedelta, date

bp = Blueprint('main', __name__)

@bp.route('/')
@login_required
def home():
    current_month = datetime.now().month
    current_year = datetime.now().year

    # Consolidar 4 counts en 1 sola query
    stats = db.session.query(
        func.count(Trabajador.id)
    ).first()
    total_trabajadores = stats[0]
    
    proj_stats = db.session.query(
        func.count(Proyecto.id),
        func.count(case((Proyecto.activo == True, 1)))
    ).first()
    total_proyectos = proj_stats[0]
    proyectos_activos = proj_stats[1]
    
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
        Trabajador.activo == True,
        extract('month', Trabajador.fecha_nacimiento) == current_month
    ).all()
    
    # Bitácora (Últimos 5 movimientos)
    actividad_reciente = AuditLog.query.order_by(AuditLog.created_at.desc()).limit(5).all()

    # Documentos y credenciales por vencer (próximos 30 días) o ya vencidos
    hoy = date.today()
    limite = hoy + timedelta(days=30)

    docs_vencidos = (
        db.session.query(DocumentoTrabajador, Trabajador)
        .join(Trabajador, DocumentoTrabajador.trabajador_id == Trabajador.id)
        .filter(
            Trabajador.activo == True,
            DocumentoTrabajador.fecha_fin != None,
            DocumentoTrabajador.fecha_fin <= limite
        )
        .order_by(DocumentoTrabajador.fecha_fin.asc())
        .all()
    )

    creds_vencidas = (
        db.session.query(CredencialPlanta, Trabajador)
        .join(Trabajador, CredencialPlanta.trabajador_id == Trabajador.id)
        .filter(
            Trabajador.activo == True,
            CredencialPlanta.fecha_caducidad != None,
            CredencialPlanta.fecha_caducidad <= limite
        )
        .order_by(CredencialPlanta.fecha_caducidad.asc())
        .all()
    )

    # Combinar en lista unificada
    docs_por_vencer = []
    for doc, trab in docs_vencidos:
        docs_por_vencer.append({
            'tipo': 'documento',
            'nombre_trabajador': trab.nombre_completo,
            'trabajador_id': trab.id,
            'descripcion': doc.nombre_archivo,
            'fecha': doc.fecha_fin,
            'vencido': doc.fecha_fin < hoy
        })
    for cred, trab in creds_vencidas:
        docs_por_vencer.append({
            'tipo': 'credencial',
            'nombre_trabajador': trab.nombre_completo,
            'trabajador_id': trab.id,
            'descripcion': f'Credencial {cred.planta}',
            'fecha': cred.fecha_caducidad,
            'vencido': cred.fecha_caducidad < hoy
        })

    # Ordenar: vencidos primero, luego por fecha más próxima
    docs_por_vencer.sort(key=lambda x: (not x['vencido'], x['fecha']))

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
        actividad_reciente=actividad_reciente,
        docs_por_vencer=docs_por_vencer
    )

@bp.route('/credenciales')
@login_required
def credenciales():
    from flask import session, request
    from sqlalchemy import or_
    import math
    
    user_id = session.get('user_id')
    user_role = session.get('role', 'user')
    
    page = request.args.get('page', 1, type=int)
    q = request.args.get('q', '', type=str)

    query = Trabajador.query

    # Apply role-based filtering
    if user_role == 'coordinador':
        # Find all workers involved in projects managed by this coordinator
        proyectos = Proyecto.query.filter_by(coordinador_id=user_id).all()
        trabajador_ids = []
        for p in proyectos:
            for t in p.participantes:
                trabajador_ids.append(t.id)
                
        query = query.filter(Trabajador.id.in_(trabajador_ids))
    
    # Base filter: only active workers
    query = query.filter_by(activo=True)

    # Apply search filter
    if q:
        query = query.filter(or_(
            Trabajador.nombre.ilike(f'%{q}%'),
            Trabajador.nombre_apellidos.ilike(f'%{q}%'),
            Trabajador.no_empleado.ilike(f'%{q}%'),
            Trabajador.rfc.ilike(f'%{q}%')
        ))

    # Paginate results
    pagination = query.order_by(Trabajador.id).paginate(page=page, per_page=20, error_out=False)

    return render_template('credenciales.html', pagination=pagination, q=q)



@bp.route('/coordinadores')
@login_required
def coordinadores():
    return render_template('coordinadores.html')
