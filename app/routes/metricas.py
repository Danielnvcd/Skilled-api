from flask import Blueprint, render_template, jsonify
from app.utils import login_required
from sqlalchemy import func
from app.extensions import db
from app.models import Trabajador, CredencialPlanta, DocumentoTrabajador

bp = Blueprint('metricas', __name__, url_prefix='/metricas')


@bp.route('/')
@login_required
def index():
    # --- Credenciales por Planta ---
    creds_por_planta = (
        db.session.query(
            CredencialPlanta.planta,
            func.count(func.distinct(CredencialPlanta.trabajador_id)).label('total')
        )
        .join(Trabajador)
        .filter(Trabajador.activo == True)
        .group_by(CredencialPlanta.planta)
        .order_by(func.count(func.distinct(CredencialPlanta.trabajador_id)).desc())
        .all()
    )

    plantas_labels = [r.planta for r in creds_por_planta]
    plantas_datos = [r.total for r in creds_por_planta]
    total_con_credencial = len(set(
        r[0] for r in db.session.query(CredencialPlanta.trabajador_id)
        .join(Trabajador).filter(Trabajador.activo == True).all()
    ))

    from datetime import date
    hoy = date.today()

    # Detalle: trabajadores por planta
    detalle_plantas = {}
    for planta_name in plantas_labels:
        trabajadores = (
            db.session.query(
                Trabajador.no_empleado,
                Trabajador.nombre,
                Trabajador.nombre_apellidos,
                Trabajador.puesto,
                CredencialPlanta.credencial_id,
                CredencialPlanta.fecha_caducidad
            )
            .join(CredencialPlanta)
            .filter(CredencialPlanta.planta == planta_name, Trabajador.activo == True)
            .order_by(Trabajador.nombre_apellidos)
            .all()
        )
        detalle_plantas[planta_name] = [
            {
                'no_empleado': t.no_empleado,
                'nombre': f"{t.nombre_apellidos} {t.nombre}",
                'puesto': t.puesto or 'Sin puesto',
                'credencial_id': t.credencial_id,
                'fecha_caducidad': t.fecha_caducidad.isoformat() if t.fecha_caducidad else None,
                'estado_cred': 'Vencida' if (t.fecha_caducidad and t.fecha_caducidad < hoy) else ('Vigente' if t.fecha_caducidad else 'Sin fecha')
            }
            for t in trabajadores
        ]

    # --- Permisos DC3 ---
    total_activos = Trabajador.query.filter_by(activo=True).count()

    trabajadores_con_dc3_ids = set(
        r[0] for r in db.session.query(DocumentoTrabajador.trabajador_id)
        .join(Trabajador)
        .filter(
            Trabajador.activo == True,
            DocumentoTrabajador.tipo_documento == 'Permiso DC3'
        )
        .all()
    )
    total_con_dc3 = len(trabajadores_con_dc3_ids)
    total_sin_dc3 = total_activos - total_con_dc3

    # Lista de trabajadores con DC3
    lista_dc3 = (
        db.session.query(
            Trabajador.no_empleado,
            Trabajador.nombre,
            Trabajador.nombre_apellidos,
            Trabajador.puesto,
            DocumentoTrabajador.fecha_fin
        )
        .join(DocumentoTrabajador)
        .filter(
            Trabajador.activo == True,
            DocumentoTrabajador.tipo_documento == 'Permiso DC3'
        )
        .order_by(Trabajador.nombre_apellidos)
        .all()
    )


    detalle_dc3 = []
    for t in lista_dc3:
        estado = 'Vigente'
        if t.fecha_fin and t.fecha_fin < hoy:
            estado = 'Vencido'
        elif t.fecha_fin:
            estado = 'Vigente'
        else:
            estado = 'Sin fecha'

        detalle_dc3.append({
            'no_empleado': t.no_empleado,
            'nombre': f"{t.nombre_apellidos} {t.nombre}",
            'puesto': t.puesto or 'Sin puesto',
            'fecha_fin': t.fecha_fin.isoformat() if t.fecha_fin else None,
            'estado': estado
        })

    return render_template('metricas.html',
        plantas_labels=plantas_labels,
        plantas_datos=plantas_datos,
        total_con_credencial=total_con_credencial,
        total_plantas=len(plantas_labels),
        total_activos=total_activos,
        total_con_dc3=total_con_dc3,
        total_sin_dc3=total_sin_dc3,
        detalle_plantas=detalle_plantas,
        detalle_dc3=detalle_dc3
    )
