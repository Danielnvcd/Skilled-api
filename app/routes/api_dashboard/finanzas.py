"""Panel financiero (rol `finanzas`, también visible para admin/super_admin).

Agregados de solo lectura sobre dinero que ya salió o está por salir:
dispersión semanal/anual de nómina, préstamos por recuperar y ajustes
Inbursa pendientes de cobro. No expone PII de trabajadores — solo montos
y conteos agregados por semana.
"""
from datetime import date

from flask import jsonify
from sqlalchemy import func

from app.extensions import db
from app.models import AjusteDescuento, Prenomina, Prestamo, ReporteSemanal
from app.routes._api_helpers import require_roles
from app.routes.api_auth import jwt_required

from ._core import bp

_ROLES_FINANZAS = ('finanzas', 'admin', 'super_admin')


@bp.route('/finanzas', methods=['GET'])
@jwt_required
def panel_finanzas():
    err = require_roles(_ROLES_FINANZAS)
    if err:
        return err

    hoy = date.today()

    # Semanas APROBADAS agregadas por fecha — lo único que ya se pagó.
    semanas = (
        db.session.query(
            Prenomina.fecha_inicio,
            func.count(Prenomina.id),
            func.coalesce(func.sum(Prenomina.total_a_pagar), 0),
        )
        .filter(Prenomina.estado == 'APROBADO')
        .group_by(Prenomina.fecha_inicio)
        .order_by(Prenomina.fecha_inicio.desc())
        .all()
    )

    ultimas_semanas = [
        {'fecha_str': f.isoformat(), 'trabajadores': n, 'total': float(t)}
        for f, n, t in semanas[:12]
    ]
    dispersado_anual = {
        'total': sum(float(t) for f, _, t in semanas if f.year == hoy.year),
        'semanas': sum(1 for f, _, _ in semanas if f.year == hoy.year),
    }

    # Semana en proceso: la más antigua con reportes cerrados aún sin aprobar
    # (misma regla que el deep-link de prenómina del dashboard admin). Para
    # finanzas es el próximo egreso: si ya está ABIERTA incluimos el monto
    # calculado hasta ahora como estimado.
    fechas_reportes = [
        f for (f,) in db.session.query(ReporteSemanal.fecha_inicio_semana)
        .filter(ReporteSemanal.estado.in_(['TERMINADO', 'PRENOMINA_CERRADA']))
        .distinct().all()
    ]
    fechas_aprobadas = {f for f, _, _ in semanas}
    semana_en_proceso = None
    for fecha in sorted(fechas_reportes):
        if fecha in fechas_aprobadas:
            continue
        abierta = (
            db.session.query(func.coalesce(func.sum(Prenomina.total_a_pagar), 0))
            .filter(Prenomina.fecha_inicio == fecha, Prenomina.estado == 'ABIERTA')
            .scalar()
        )
        semana_en_proceso = {
            'fecha_str': fecha.isoformat(),
            'estado': 'ABIERTA' if float(abierta) > 0 else 'PENDIENTE',
            'total_estimado': float(abierta) if float(abierta) > 0 else None,
        }
        break

    prestamos_q = (
        db.session.query(
            func.count(Prestamo.id),
            func.coalesce(func.sum(Prestamo.monto_restante), 0),
        )
        .filter(Prestamo.estado == 'ACTIVO')
        .first()
    )
    ajustes_q = (
        db.session.query(
            func.count(AjusteDescuento.id),
            func.coalesce(func.sum(AjusteDescuento.monto), 0),
        )
        .filter(AjusteDescuento.cobrado == False)  # noqa: E712
        .first()
    )

    return jsonify({
        'dispersado_anual': dispersado_anual,
        'ultima_semana': ultimas_semanas[0] if ultimas_semanas else None,
        'ultimas_semanas': ultimas_semanas,
        'semana_en_proceso': semana_en_proceso,
        'prestamos': {'activos': prestamos_q[0], 'por_recuperar': float(prestamos_q[1])},
        'ajustes_pendientes': {'registros': ajustes_q[0], 'monto': float(ajustes_q[1])},
    })
