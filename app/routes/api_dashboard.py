"""API JSON para el Dashboard / página de inicio (consumido por el SPA React).

Replica la lógica de `main.home` pero responde JSON y autentica con JWT.
"""
from datetime import date, datetime, timedelta

from flask import Blueprint, g, jsonify
from sqlalchemy import case, extract, func
from sqlalchemy.orm import selectinload

from app.extensions import db
from app.models import (
    AuditLog, CredencialPlanta, DocumentoTrabajador, Proyecto, Trabajador, User,
)
from app.routes.api_auth import jwt_required

bp = Blueprint('api_dashboard', __name__, url_prefix='/api/dashboard')


def _u():
    return g._jwt_user


def _is_admin() -> bool:
    return _u().role in ('admin', 'super_admin')


@bp.route('', methods=['GET'])
@jwt_required
def dashboard():
    # SEGURIDAD: el dashboard agrega PII (cumpleaños, docs por vencer con nombres
    # completos), audit log y stats globales. No debe ser accesible para
    # coordinador / inventario / solicitante_material — esos roles tienen vistas
    # dedicadas con scope restringido.
    if not _is_admin():
        return jsonify({'error': 'Acceso denegado'}), 403

    current_month = datetime.now().month
    current_year = datetime.now().year

    stats = db.session.query(
        func.count(Trabajador.id),
        func.count(case((
            db.and_(
                extract('month', Trabajador.fecha_ingreso) == current_month,
                extract('year', Trabajador.fecha_ingreso) == current_year,
            ), 1,
        ))),
    ).first()
    total_trabajadores = stats[0]
    nuevos_ingresos = stats[1]

    proj_stats = db.session.query(
        func.count(Proyecto.id),
        func.count(case((Proyecto.activo == True, 1))),  # noqa: E712
    ).first()
    total_proyectos = proj_stats[0]
    proyectos_activos = proj_stats[1]

    # Empleados por proyecto (no_proyecto en Trabajador)
    empleados_por_proyecto = db.session.query(
        Trabajador.no_proyecto, func.count(Trabajador.id),
    ).filter(
        Trabajador.no_proyecto != None, Trabajador.no_proyecto != '',  # noqa: E711
    ).group_by(Trabajador.no_proyecto).all()

    empleados_por_puesto = db.session.query(
        Trabajador.puesto, func.count(Trabajador.id),
    ).filter(
        Trabajador.puesto != None, Trabajador.puesto != '',  # noqa: E711
    ).group_by(Trabajador.puesto).all()

    cumpleañeros = Trabajador.query.filter(
        Trabajador.activo == True,  # noqa: E712
        extract('month', Trabajador.fecha_nacimiento) == current_month,
    ).all()

    # Excluye acciones de usuarios con rol 'inventario' (no aportan a la auditoría
    # del admin de RH). LEFT JOIN sobre User.username — entradas sin usuario
    # asociado (anon, usuarios borrados) tienen role NULL y NO se excluyen.
    actividad_reciente = (
        AuditLog.query
        .outerjoin(User, User.username == AuditLog.user)
        .filter((User.role == None) | (User.role != 'inventario'))  # noqa: E711
        .order_by(AuditLog.created_at.desc())
        .limit(5)
        .all()
    )

    hoy = date.today()
    limite = hoy + timedelta(days=30)

    docs_vencidos = (
        db.session.query(DocumentoTrabajador, Trabajador)
        .join(Trabajador, DocumentoTrabajador.trabajador_id == Trabajador.id)
        .filter(
            Trabajador.activo == True,  # noqa: E712
            DocumentoTrabajador.fecha_fin != None,  # noqa: E711
            DocumentoTrabajador.fecha_fin <= limite,
        )
        .order_by(DocumentoTrabajador.fecha_fin.asc())
        .all()
    )

    creds_vencidas = (
        db.session.query(CredencialPlanta, Trabajador)
        .join(Trabajador, CredencialPlanta.trabajador_id == Trabajador.id)
        .filter(
            Trabajador.activo == True,  # noqa: E712
            CredencialPlanta.fecha_caducidad != None,  # noqa: E711
            CredencialPlanta.fecha_caducidad <= limite,
        )
        .order_by(CredencialPlanta.fecha_caducidad.asc())
        .all()
    )

    docs_por_vencer = []
    for doc, trab in docs_vencidos:
        docs_por_vencer.append({
            'tipo': 'documento',
            'trabajador_id': trab.id,
            'nombre_trabajador': f"{trab.nombre} {trab.nombre_apellidos}".strip(),
            'descripcion': doc.nombre_archivo,
            'fecha': doc.fecha_fin.isoformat(),
            'vencido': doc.fecha_fin < hoy,
        })
    for cred, trab in creds_vencidas:
        docs_por_vencer.append({
            'tipo': 'credencial',
            'trabajador_id': trab.id,
            'nombre_trabajador': f"{trab.nombre} {trab.nombre_apellidos}".strip(),
            'descripcion': f'Credencial {cred.planta}',
            'fecha': cred.fecha_caducidad.isoformat(),
            'vencido': cred.fecha_caducidad < hoy,
        })
    docs_por_vencer.sort(key=lambda x: (not x['vencido'], x['fecha']))

    return jsonify({
        'stats': {
            'total_trabajadores': total_trabajadores,
            'nuevos_ingresos': nuevos_ingresos,
            'total_proyectos': total_proyectos,
            'proyectos_activos': proyectos_activos,
        },
        'empleados_por_proyecto': [
            {'label': p[0], 'value': p[1]} for p in empleados_por_proyecto
        ],
        'empleados_por_puesto': [
            {'label': p[0], 'value': p[1]} for p in empleados_por_puesto
        ],
        'cumpleañeros': [
            {
                'id': e.id,
                'nombre': e.nombre,
                'nombre_apellidos': e.nombre_apellidos,
                'dia': e.fecha_nacimiento.day if e.fecha_nacimiento else None,
                'foto_perfil': e.foto_perfil,
            }
            for e in cumpleañeros
        ],
        'actividad_reciente': [
            {
                'id': log.id,
                'user': log.user or 'Sistema',
                'action': log.action,
                'created_at': log.created_at.isoformat() if log.created_at else None,
            }
            for log in actividad_reciente
        ],
        'docs_por_vencer': docs_por_vencer,
    })
