"""API JSON para el Dashboard / página de inicio (consumido por el SPA React).

Replica la lógica de `main.home` pero responde JSON y autentica con JWT.
"""
from datetime import date, datetime, timedelta

from flask import Blueprint, g, jsonify
from sqlalchemy import case, extract, func
from sqlalchemy.orm import selectinload

from app.extensions import db
from app.models import (
    AjustePeriodo, AuditLog, CredencialPlanta, DocumentoTrabajador,
    Prestamo, Proyecto, Trabajador, User,
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

    # Oculta acciones operativas del rol 'inventario' (movimientos, ajustes, etc.)
    # PERO mantiene visibles login / logout / 2FA — esos sí interesan al admin.
    # LEFT JOIN: entradas sin usuario asociado (anon, usuarios borrados) tienen
    # role NULL y se mantienen visibles.
    from sqlalchemy import or_
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


# ── Banner de alertas en topbar ──────────────────────────────────────────────
# Endpoint agregador: una sola request → conteo global + items de cada
# categoría accionable. Pensado para el chip de alertas del SPA: lectura
# barata (top 5 items por categoría), cero mutación, sin emit.

_ALERTAS_PREVIEW_LIMIT = 5
_ALERTAS_VENC_DIAS = 30  # ventana "por vencer" para docs/credenciales


def _fmt_fecha_es(d) -> str:
    """Formato corto en español para subtitles de alertas."""
    if not d:
        return '—'
    meses = ['ene', 'feb', 'mar', 'abr', 'may', 'jun',
             'jul', 'ago', 'sep', 'oct', 'nov', 'dic']
    return f'{d.day:02d}/{meses[d.month - 1]}'


def _venc_subtitle(fecha_venc, hoy: date) -> str:
    """'vence en N días' / 'vencido hace N días' / 'vence hoy'."""
    if not fecha_venc:
        return ''
    delta = (fecha_venc - hoy).days
    if delta < 0:
        n = -delta
        return f'vencido hace {n} día{"" if n == 1 else "s"}'
    if delta == 0:
        return 'vence hoy'
    return f'vence en {delta} día{"" if delta == 1 else "s"}'


@bp.route('/alertas', methods=['GET'])
@jwt_required
def alertas():
    if not _is_admin():
        return jsonify({'error': 'Acceso denegado'}), 403

    hoy = date.today()
    limite_venc = hoy + timedelta(days=_ALERTAS_VENC_DIAS)
    categorias = []

    # 1) Documentos por vencer o vencidos (trabajador activo).
    docs_q = (
        db.session.query(DocumentoTrabajador, Trabajador)
        .join(Trabajador, DocumentoTrabajador.trabajador_id == Trabajador.id)
        .filter(
            Trabajador.activo == True,  # noqa: E712
            DocumentoTrabajador.fecha_fin != None,  # noqa: E711
            DocumentoTrabajador.fecha_fin <= limite_venc,
        )
        .order_by(DocumentoTrabajador.fecha_fin.asc())
    )
    docs_total = docs_q.count()
    docs_items = [
        {
            'title': f'{t.nombre or ""} {t.nombre_apellidos or ""}'.strip() or f'#{t.no_empleado}',
            'subtitle': f'{d.tipo_documento or "Documento"} · {_venc_subtitle(d.fecha_fin, hoy)}',
            'url': f'/empleados/{t.id}',
        }
        for d, t in docs_q.limit(_ALERTAS_PREVIEW_LIMIT).all()
    ]
    categorias.append({
        'key': 'docs_por_vencer',
        'label': 'Documentos por vencer',
        'tone': 'amber',
        'count': docs_total,
        'items': docs_items,
    })

    # 2) Credenciales por vencer o vencidas (trabajador activo).
    creds_q = (
        db.session.query(CredencialPlanta, Trabajador)
        .join(Trabajador, CredencialPlanta.trabajador_id == Trabajador.id)
        .filter(
            Trabajador.activo == True,  # noqa: E712
            CredencialPlanta.fecha_caducidad != None,  # noqa: E711
            CredencialPlanta.fecha_caducidad <= limite_venc,
        )
        .order_by(CredencialPlanta.fecha_caducidad.asc())
    )
    creds_total = creds_q.count()
    creds_items = [
        {
            'title': f'{t.nombre or ""} {t.nombre_apellidos or ""}'.strip() or f'#{t.no_empleado}',
            'subtitle': f'{c.planta or "Planta"} · {_venc_subtitle(c.fecha_caducidad, hoy)}',
            'url': f'/empleados/{t.id}',
        }
        for c, t in creds_q.limit(_ALERTAS_PREVIEW_LIMIT).all()
    ]
    categorias.append({
        'key': 'credenciales_por_vencer',
        'label': 'Credenciales por vencer',
        'tone': 'red',
        'count': creds_total,
        'items': creds_items,
    })

    # 3) Préstamos liquidados sin marcar como tales
    # (monto_restante <= 0 pero siguen activos en el sistema).
    prest_q = (
        db.session.query(Prestamo, Trabajador)
        .join(Trabajador, Prestamo.trabajador_id == Trabajador.id)
        .filter(
            Prestamo.activo == True,  # noqa: E712
            Prestamo.monto_restante <= 0,
        )
        .order_by(Prestamo.id.desc())
    )
    prest_total = prest_q.count()
    prest_items = [
        {
            'title': f'{t.nombre or ""} {t.nombre_apellidos or ""}'.strip() or f'#{t.no_empleado}',
            'subtitle': f'{p.motivo or "Préstamo"} · saldado',
            'url': f'/prestamos?trabajador_id={t.id}',
        }
        for p, t in prest_q.limit(_ALERTAS_PREVIEW_LIMIT).all()
    ]
    categorias.append({
        'key': 'prestamos_liquidados',
        'label': 'Préstamos liquidados sin cerrar',
        'tone': 'blue',
        'count': prest_total,
        'items': prest_items,
    })

    # 4) Periodos de Ajuste vencidos pero todavía ABIERTOS.
    aj_q = (
        AjustePeriodo.query
        .filter(
            AjustePeriodo.estado == 'ABIERTO',
            AjustePeriodo.fecha_fin < hoy,
        )
        .order_by(AjustePeriodo.fecha_fin.asc())
    )
    aj_total = aj_q.count()
    aj_items = [
        {
            'title': p.nombre,
            'subtitle': f'cerró el {_fmt_fecha_es(p.fecha_fin)} y sigue abierto',
            'url': f'/ajustes/{p.id}',
        }
        for p in aj_q.limit(_ALERTAS_PREVIEW_LIMIT).all()
    ]
    categorias.append({
        'key': 'ajustes_vencidos',
        'label': 'Periodos de ajuste vencidos',
        'tone': 'violet',
        'count': aj_total,
        'items': aj_items,
    })

    total = sum(c['count'] for c in categorias)
    return jsonify({'total': total, 'categorias': categorias})
