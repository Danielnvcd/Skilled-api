"""Vista principal del dashboard: stats globales, cumpleañeros, actividad
reciente, docs/credenciales por vencer."""
from datetime import date, datetime, timedelta

from flask import jsonify
from sqlalchemy import case, extract, func, or_

from app.extensions import db
from app.models import (
    AuditLog, CredencialPlanta, DocumentoTrabajador, Prenomina,
    Proyecto, ReporteSemanal, Trabajador, User, proyecto_trabajador,
)
from app.routes._api_helpers import require_admin
from app.routes.api_auth import jwt_required

from ._core import bp


@bp.route('', methods=['GET'])
@jwt_required
def dashboard():
    # SEGURIDAD: el dashboard agrega PII (cumpleaños, docs por vencer con nombres
    # completos), audit log y stats globales. No debe ser accesible para
    # coordinador / inventario / solicitante_material — esos roles tienen vistas
    # dedicadas con scope restringido.
    err = require_admin()
    if err:
        return err

    current_month = datetime.now().month
    current_year = datetime.now().year

    # Solo plantilla vigente: la tarjeta dice "Trab. activos", así que las
    # bajas (bandera o fecha_baja legacy) no deben contar.
    stats = db.session.query(
        func.count(Trabajador.id),
        func.count(case((
            db.and_(
                extract('month', Trabajador.fecha_ingreso) == current_month,
                extract('year', Trabajador.fecha_ingreso) == current_year,
            ), 1,
        ))),
    ).filter(
        Trabajador.activo == True,  # noqa: E712
        Trabajador.fecha_baja == None,  # noqa: E711
    ).first()
    total_trabajadores = stats[0]
    nuevos_ingresos = stats[1]

    proj_stats = db.session.query(
        func.count(Proyecto.id),
        func.count(case((Proyecto.activo == True, 1))),  # noqa: E712
    ).first()
    total_proyectos = proj_stats[0]
    proyectos_activos = proj_stats[1]

    # Empleados por proyecto via la relación M:N (un trabajador puede estar en
    # varios proyectos; el string Trabajador.no_proyecto une varios con coma y
    # agrupar por él creaba buckets falsos tipo "PRY-1, PRY-2").
    empleados_por_proyecto = (
        db.session.query(
            Proyecto.numero_proyecto, func.count(proyecto_trabajador.c.trabajador_id),
        )
        .join(proyecto_trabajador, proyecto_trabajador.c.proyecto_id == Proyecto.id)
        .join(Trabajador, Trabajador.id == proyecto_trabajador.c.trabajador_id)
        .filter(
            Proyecto.activo == True, Trabajador.activo == True,  # noqa: E712
            Trabajador.fecha_baja == None,  # noqa: E711
        )
        .group_by(Proyecto.numero_proyecto)
        .all()
    )

    empleados_por_puesto = db.session.query(
        Trabajador.puesto, func.count(Trabajador.id),
    ).filter(
        Trabajador.puesto != None, Trabajador.puesto != '',  # noqa: E711
        Trabajador.activo == True,  # noqa: E712
        Trabajador.fecha_baja == None,  # noqa: E711
    ).group_by(Trabajador.puesto).all()

    # Doble filtro activo+fecha_baja (mismo patrón que credenciales-lista):
    # hay registros legacy/importados con fecha_baja capturada y la bandera
    # `activo` aún encendida — sin esto, los dados de baja salían en el panel.
    cumpleañeros = Trabajador.query.filter(
        Trabajador.activo == True,  # noqa: E712
        Trabajador.fecha_baja == None,  # noqa: E711
        extract('month', Trabajador.fecha_nacimiento) == current_month,
    ).all()

    # Oculta acciones operativas del rol 'inventario' (movimientos, ajustes, etc.)
    # PERO mantiene visibles login / logout / 2FA — esos sí interesan al admin.
    # LEFT JOIN: entradas sin usuario asociado (anon, usuarios borrados) tienen
    # role NULL y se mantienen visibles.
    actividad_reciente = (
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
            Trabajador.fecha_baja == None,  # noqa: E711
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
            Trabajador.fecha_baja == None,  # noqa: E711
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

    # Semana de prenómina accionable: la más antigua con reportes cerrados cuya
    # prenómina aún no fue APROBADA. La nómina se procesa cronológicamente, así
    # que el deep-link del Dashboard apunta a la semana más vieja sin cerrar.
    # Misma regla de prioridad de estados que /prenomina/semanas
    # (APROBADO > ABIERTA > PENDIENTE) — una semana puede tener varias filas
    # Prenomina y el estado "de la semana" es el dominante.
    semanas_fechas = [
        f for (f,) in db.session.query(ReporteSemanal.fecha_inicio_semana)
        .filter(ReporteSemanal.estado.in_(['TERMINADO', 'PRENOMINA_CERRADA']))
        .distinct().all()
    ]
    estados_por_fecha = {}
    for fecha, estado in db.session.query(Prenomina.fecha_inicio, Prenomina.estado).distinct().all():
        prev = estados_por_fecha.get(fecha)
        if prev == 'APROBADO':
            continue
        if estado == 'APROBADO' or prev is None or (prev == 'PENDIENTE' and estado == 'ABIERTA'):
            estados_por_fecha[fecha] = estado
    prenomina_pendiente = None
    for fecha in sorted(semanas_fechas):
        estado = estados_por_fecha.get(fecha, 'PENDIENTE')
        if estado != 'APROBADO':
            prenomina_pendiente = {'fecha_str': fecha.isoformat(), 'estado': estado}
            break

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
        'prenomina_pendiente': prenomina_pendiente,
    })
