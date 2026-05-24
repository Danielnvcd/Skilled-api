"""API JSON para la página de Métricas (consumido por el SPA React).

Replica los datos de `metricas.index` pero responde JSON y autentica con JWT.
Solo admin / super_admin pueden consultar.
"""
from datetime import date

from flask import Blueprint, g, jsonify
from sqlalchemy import func

from app.extensions import db
from app.models import CredencialPlanta, DocumentoTrabajador, Trabajador
from app.routes.api_auth import jwt_required

bp = Blueprint('api_metricas', __name__, url_prefix='/api/metricas')


def _u():
    return g._jwt_user


def _admin_only():
    if _u().role not in ('admin', 'super_admin'):
        return jsonify({'error': 'Solo admin puede consultar métricas'}), 403
    return None


@bp.route('', methods=['GET'])
@jwt_required
def metricas():
    err = _admin_only()
    if err:
        return err

    hoy = date.today()

    # ── Credenciales por planta (solo trabajadores activos) ─────────────────
    creds_por_planta = (
        db.session.query(
            CredencialPlanta.planta,
            func.count(func.distinct(CredencialPlanta.trabajador_id)).label('total'),
        )
        .join(Trabajador)
        .filter(Trabajador.activo == True)  # noqa: E712
        .group_by(CredencialPlanta.planta)
        .order_by(func.count(func.distinct(CredencialPlanta.trabajador_id)).desc())
        .all()
    )

    plantas_labels = [r.planta for r in creds_por_planta]
    plantas_datos = [r.total for r in creds_por_planta]

    total_con_credencial = (
        db.session.query(func.count(func.distinct(CredencialPlanta.trabajador_id)))
        .join(Trabajador)
        .filter(Trabajador.activo == True)  # noqa: E712
        .scalar()
    ) or 0

    # ── Detalle de trabajadores por planta (1 query consolidada) ────────────
    detalle_plantas = {p: [] for p in plantas_labels}
    if plantas_labels:
        rows = (
            db.session.query(
                CredencialPlanta.planta,
                Trabajador.no_empleado,
                Trabajador.nombre,
                Trabajador.nombre_apellidos,
                Trabajador.puesto,
                CredencialPlanta.credencial_id,
                CredencialPlanta.fecha_caducidad,
            )
            .join(Trabajador, CredencialPlanta.trabajador_id == Trabajador.id)
            .filter(
                CredencialPlanta.planta.in_(plantas_labels),
                Trabajador.activo == True,  # noqa: E712
            )
            .order_by(CredencialPlanta.planta, Trabajador.nombre_apellidos)
            .all()
        )
        for planta, no_emp, nombre, apellidos, puesto, cred_id, fcad in rows:
            estado = 'Vencida' if (fcad and fcad < hoy) else ('Vigente' if fcad else 'Sin fecha')
            detalle_plantas[planta].append({
                'no_empleado': no_emp,
                'nombre': f"{apellidos} {nombre}",
                'puesto': puesto or 'Sin puesto',
                'credencial_id': cred_id,
                'fecha_caducidad': fcad.isoformat() if fcad else None,
                'estado_cred': estado,
            })

    # ── Permisos DC3 ────────────────────────────────────────────────────────
    total_activos = Trabajador.query.filter_by(activo=True).count()

    trabajadores_con_dc3 = (
        db.session.query(func.count(func.distinct(DocumentoTrabajador.trabajador_id)))
        .join(Trabajador)
        .filter(
            Trabajador.activo == True,  # noqa: E712
            DocumentoTrabajador.tipo_documento == 'Permiso DC3',
        )
        .scalar()
    ) or 0
    total_con_dc3 = trabajadores_con_dc3
    total_sin_dc3 = max(0, total_activos - total_con_dc3)

    lista_dc3 = (
        db.session.query(
            Trabajador.no_empleado,
            Trabajador.nombre,
            Trabajador.nombre_apellidos,
            Trabajador.puesto,
            DocumentoTrabajador.fecha_fin,
        )
        .join(DocumentoTrabajador)
        .filter(
            Trabajador.activo == True,  # noqa: E712
            DocumentoTrabajador.tipo_documento == 'Permiso DC3',
        )
        .order_by(Trabajador.nombre_apellidos)
        .all()
    )

    detalle_dc3 = []
    for no_emp, nombre, apellidos, puesto, fecha_fin in lista_dc3:
        if fecha_fin and fecha_fin < hoy:
            estado = 'Vencido'
        elif fecha_fin:
            estado = 'Vigente'
        else:
            estado = 'Sin fecha'
        detalle_dc3.append({
            'no_empleado': no_emp,
            'nombre': f"{apellidos} {nombre}",
            'puesto': puesto or 'Sin puesto',
            'fecha_fin': fecha_fin.isoformat() if fecha_fin else None,
            'estado': estado,
        })

    return jsonify({
        'totales': {
            'empleados_activos': total_activos,
            'con_credencial': total_con_credencial,
            'total_plantas': len(plantas_labels),
            'con_dc3': total_con_dc3,
            'sin_dc3': total_sin_dc3,
        },
        'plantas': [
            {'planta': lbl, 'total': dato}
            for lbl, dato in zip(plantas_labels, plantas_datos)
        ],
        'detalle_plantas': detalle_plantas,
        'detalle_dc3': detalle_dc3,
    })
