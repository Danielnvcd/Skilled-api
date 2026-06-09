"""Listado e inspección del histórico de nóminas aprobadas."""
from datetime import datetime

from flask import jsonify, request
from sqlalchemy.orm import joinedload, selectinload

from app.extensions import db
from app.models import Prenomina, Proyecto, RegistroDiarioHoras, ReporteSemanal
from app.routes._api_helpers import require_admin
from app.routes.api_auth import jwt_required

from ._core import _coord_dict, _prenomina_to_dict, bp


@bp.route('', methods=['GET'])
@jwt_required
def listar_semanas():
    err = require_admin()
    if err:
        return err

    page = request.args.get('page', 1, type=int)
    per_page = min(request.args.get('per_page', 20, type=int), 100)
    search_date_str = (request.args.get('search_date') or '').strip()

    query = db.session.query(Prenomina.fecha_inicio).filter_by(estado='APROBADO')
    if search_date_str:
        try:
            search_date_obj = datetime.strptime(search_date_str, '%Y-%m-%d').date()
            query = query.filter_by(fecha_inicio=search_date_obj)
        except ValueError:
            pass

    pagination = query.distinct().order_by(Prenomina.fecha_inicio.desc()).paginate(
        page=page, per_page=per_page, error_out=False,
    )
    fechas = [f[0] for f in pagination.items]

    semanas = []
    for fecha in fechas:
        reportes = ReporteSemanal.query.options(
            joinedload(ReporteSemanal.proyecto).joinedload(Proyecto.coordinador),
        ).filter_by(fecha_inicio_semana=fecha, estado='PRENOMINA_CERRADA').all()

        semanas.append({
            'fecha_inicio': fecha.isoformat(),
            'proyectos': [
                {
                    'id': r.proyecto.id,
                    'numero_proyecto': r.proyecto.numero_proyecto,
                    'nombre': r.proyecto.nombre or '',
                    'coordinador': _coord_dict(r.proyecto.coordinador),
                }
                for r in reportes if r.proyecto
            ],
        })

    return jsonify({
        'items': semanas,
        'page': pagination.page,
        'per_page': pagination.per_page,
        'total': pagination.total,
        'pages': pagination.pages,
        'has_next': pagination.has_next,
        'has_prev': pagination.has_prev,
    })


@bp.route('/<string:fecha_str>', methods=['GET'])
@jwt_required
def detalle_semana(fecha_str):
    err = require_admin()
    if err:
        return err

    try:
        fecha_obj = datetime.strptime(fecha_str, '%Y-%m-%d').date()
    except ValueError:
        return jsonify({'error': 'Fecha inválida'}), 400

    reportes = ReporteSemanal.query.options(
        joinedload(ReporteSemanal.proyecto),
    ).filter_by(fecha_inicio_semana=fecha_obj, estado='PRENOMINA_CERRADA').all()
    if not reportes:
        return jsonify({'fecha': fecha_str, 'proyectos': []})

    prenominas_semana = Prenomina.query.options(
        selectinload(Prenomina.trabajador),
    ).filter_by(fecha_inicio=fecha_obj, estado='APROBADO').all()
    prenominas_dict = {p.trabajador_id: p for p in prenominas_semana}

    proyectos_out = []
    for r in reportes:
        trabajadores_in_project = db.session.query(
            RegistroDiarioHoras.trabajador_id,
        ).filter(RegistroDiarioHoras.reporte_id == r.id).distinct().all()
        t_ids = [t[0] for t in trabajadores_in_project]
        prens = [prenominas_dict[tid] for tid in t_ids if tid in prenominas_dict]

        proyectos_out.append({
            'proyecto': {
                'id': r.proyecto.id,
                'numero_proyecto': r.proyecto.numero_proyecto,
                'nombre': r.proyecto.nombre or '',
            },
            'fecha_fin': r.fecha_fin_semana.isoformat() if r.fecha_fin_semana else None,
            'prenominas': [_prenomina_to_dict(p) for p in prens],
            'total_deposit': sum(float(p.total_a_pagar or 0) for p in prens),
        })

    return jsonify({
        'fecha': fecha_str,
        'proyectos': proyectos_out,
    })
