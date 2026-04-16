from flask import Blueprint, render_template, session, request, redirect, url_for
from app.utils import login_required
from app.models import Trabajador, Proyecto, AuditLog, DocumentoTrabajador, CredencialPlanta
from app.extensions import db
from sqlalchemy import func, extract, case, or_
from sqlalchemy.orm import selectinload
from datetime import datetime, timedelta, date

bp = Blueprint('main', __name__)

@bp.route('/')
@login_required
def home():
    if session.get('role') == 'solicitante_material':
        return redirect(url_for('inventario_ui.solicitar'))
        
    current_month = datetime.now().month
    current_year = datetime.now().year

    # Consolidar conteos de trabajadores en una sola query
    stats = db.session.query(
        func.count(Trabajador.id),
        func.count(case((
            db.and_(
                extract('month', Trabajador.fecha_ingreso) == current_month,
                extract('year', Trabajador.fecha_ingreso) == current_year
            ), 1
        )))
    ).first()
    total_trabajadores = stats[0]
    nuevos_ingresos = stats[1]
    
    proj_stats = db.session.query(
        func.count(Proyecto.id),
        func.count(case((Proyecto.activo == True, 1)))
    ).first()
    total_proyectos = proj_stats[0]
    proyectos_activos = proj_stats[1]

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
    user_id = session.get('user_id')
    user_role = session.get('role', 'user')
    
    page = request.args.get('page', 1, type=int)
    q = request.args.get('q', '', type=str)

    query = Trabajador.query.options(selectinload(Trabajador.credenciales))

    # Apply role-based filtering
    if user_role == 'coordinador':
        # Find all workers involved in projects managed by this coordinator
        from app.models import proyecto_trabajador
        subquery = db.session.query(proyecto_trabajador.c.trabajador_id)\
            .join(Proyecto, Proyecto.id == proyecto_trabajador.c.proyecto_id)\
            .filter(Proyecto.coordinador_id == user_id).subquery()
                
        query = query.filter(Trabajador.id.in_(subquery))
    
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
    pagination = query.order_by(func.lower(Trabajador.nombre)).paginate(page=page, per_page=20, error_out=False)

    return render_template('credenciales.html', pagination=pagination, q=q)



@bp.route('/coordinadores')
@login_required
def coordinadores():
    return render_template('coordinadores.html')
