"""Timeline cronológico de eventos por trabajador.

Registra:
  GET /<int:id>/timeline
"""
from flask import jsonify, request

from app.extensions import db
from app.models import Proyecto, Trabajador
from app.routes.api_auth import jwt_required

from ._core import _authorized, _parse_date, bp


@bp.route('/<int:id>/timeline', methods=['GET'])
@jwt_required
def timeline(id):
    """Eventos cronológicos consolidados de un trabajador.

    Une horas registradas, ausencias, ajustes (descuentos Inbursa),
    préstamos creados, abonos a préstamos y documentos subidos en un
    solo stream ordenado por fecha desc.

    Query params:
      desde   YYYY-MM-DD  default: hoy - 90 días
      hasta   YYYY-MM-DD  default: hoy
      limit   int         default: 200, máx 500

    Acceso: admin/super_admin siempre; coordinador solo si el trabajador
    pertenece a uno de sus proyectos (vía `_authorized`).
    """
    from datetime import date as _date, timedelta as _td

    from datetime import datetime

    from app.models import (
        AbonoPrestamo, AjusteDescuento, AjustePeriodo, Ausencia,
        DocumentoTrabajador, Prestamo, ReporteSemanal, RegistroDiarioHoras,
    )

    t = db.session.get(Trabajador, id)
    if not t:
        return jsonify({'error': 'No encontrado'}), 404
    if not _authorized(t):
        return jsonify({'error': 'Acceso denegado'}), 403

    hoy = _date.today()
    try:
        desde = _parse_date(request.args.get('desde')) or (hoy - _td(days=90))
        hasta = _parse_date(request.args.get('hasta')) or hoy
    except ValueError:
        return jsonify({'error': 'Fecha inválida (YYYY-MM-DD)'}), 400
    if desde > hasta:
        desde, hasta = hasta, desde

    try:
        limit = int(request.args.get('limit') or 200)
    except (TypeError, ValueError):
        limit = 200
    limit = max(1, min(limit, 500))

    eventos: list[dict] = []

    # ── Horas registradas ────────────────────────────────────────────────
    horas_rows = (
        db.session.query(RegistroDiarioHoras, ReporteSemanal, Proyecto)
        .join(ReporteSemanal, RegistroDiarioHoras.reporte_id == ReporteSemanal.id)
        .join(Proyecto, ReporteSemanal.proyecto_id == Proyecto.id)
        .filter(
            RegistroDiarioHoras.trabajador_id == t.id,
            RegistroDiarioHoras.fecha >= desde,
            RegistroDiarioHoras.fecha <= hasta,
        )
        .order_by(RegistroDiarioHoras.fecha.desc())
        .limit(limit)
        .all()
    )
    for reg, rep, proy in horas_rows:
        horas = float(reg.horas_productivas or 0)
        if reg.incidencia:
            titulo = reg.incidencia
        elif reg.hora_entrada and reg.hora_salida:
            titulo = f'{reg.hora_entrada.strftime("%H:%M")}–{reg.hora_salida.strftime("%H:%M")}'
        elif reg.hora_entrada:
            titulo = f'Entró {reg.hora_entrada.strftime("%H:%M")} (sin salida)'
        else:
            titulo = 'Registro'
        eventos.append({
            'tipo': 'horas',
            'fecha': reg.fecha.isoformat(),
            'titulo': titulo,
            'subtitle': f'{proy.nombre or proy.numero_proyecto}',
            'monto': None,
            'horas': horas,
            'url': f'/horas/{rep.id}',
        })

    # ── Ausencias ────────────────────────────────────────────────────────
    aus_rows = (
        Ausencia.query
        .filter(
            Ausencia.trabajador_id == t.id,
            Ausencia.fecha_inicio <= hasta,
            Ausencia.fecha_fin >= desde,
        )
        .order_by(Ausencia.fecha_inicio.desc())
        .limit(limit)
        .all()
    )
    for a in aus_rows:
        rango = a.fecha_inicio.isoformat()
        if a.fecha_fin and a.fecha_fin != a.fecha_inicio:
            rango = f'{a.fecha_inicio.isoformat()} → {a.fecha_fin.isoformat()}'
        dias = a.dias_solicitados or 1
        eventos.append({
            'tipo': 'ausencia',
            'fecha': a.fecha_inicio.isoformat(),
            'titulo': f'{a.tipo_ausencia} ({dias} día{"" if dias == 1 else "s"})',
            'subtitle': f'{a.estado} · {rango}',
            'monto': None,
            'url': None,
        })

    # ── Ajustes (descuentos Inbursa) ─────────────────────────────────────
    aj_rows = (
        db.session.query(AjusteDescuento, AjustePeriodo)
        .join(AjustePeriodo, AjusteDescuento.periodo_id == AjustePeriodo.id)
        .filter(
            AjusteDescuento.trabajador_id == t.id,
            AjusteDescuento.fecha_descuento >= desde,
            AjusteDescuento.fecha_descuento <= hasta,
        )
        .order_by(AjusteDescuento.fecha_descuento.desc())
        .limit(limit)
        .all()
    )
    for d, p in aj_rows:
        eventos.append({
            'tipo': 'ajuste',
            'fecha': d.fecha_descuento.isoformat(),
            'titulo': f'Descuento Inbursa — {p.nombre}',
            'subtitle': 'Cobrado en prenómina' if getattr(d, 'cobrado', False) else 'Pendiente de cobro',
            'monto': float(d.monto) if d.monto else 0.0,
            'url': f'/ajustes/{p.id}',
        })

    # ── Préstamos creados ────────────────────────────────────────────────
    prest_rows = (
        Prestamo.query
        .filter(
            Prestamo.trabajador_id == t.id,
            db.or_(
                db.and_(Prestamo.fecha_inicio != None, Prestamo.fecha_inicio >= desde, Prestamo.fecha_inicio <= hasta),  # noqa: E711
                db.and_(Prestamo.fecha_inicio == None, Prestamo.creado_en != None,  # noqa: E711
                        Prestamo.creado_en >= datetime.combine(desde, datetime.min.time()),
                        Prestamo.creado_en <= datetime.combine(hasta, datetime.max.time())),
            ),
        )
        .order_by(Prestamo.id.desc())
        .limit(limit)
        .all()
    )
    for p in prest_rows:
        fecha_evt = (p.fecha_inicio or (p.creado_en.date() if p.creado_en else hoy))
        eventos.append({
            'tipo': 'prestamo_creado',
            'fecha': fecha_evt.isoformat(),
            'titulo': f'Préstamo otorgado — {p.motivo or "Sin motivo"}',
            'subtitle': f'{p.plazo_semanas} {p.frecuencia or "semanas"} · resta {float(p.monto_restante or 0):.2f}',
            'monto': float(p.monto_total) if p.monto_total else 0.0,
            'url': f'/prestamos?trabajador_id={t.id}',
        })

    # ── Abonos a préstamos ───────────────────────────────────────────────
    abono_rows = (
        db.session.query(AbonoPrestamo, Prestamo)
        .join(Prestamo, AbonoPrestamo.prestamo_id == Prestamo.id)
        .filter(
            Prestamo.trabajador_id == t.id,
            AbonoPrestamo.fecha_abono >= desde,
            AbonoPrestamo.fecha_abono <= hasta,
        )
        .order_by(AbonoPrestamo.fecha_abono.desc())
        .limit(limit)
        .all()
    )
    for ab, pr in abono_rows:
        eventos.append({
            'tipo': 'abono',
            'fecha': ab.fecha_abono.isoformat(),
            'titulo': f'Abono — {pr.motivo or "Préstamo"}',
            'subtitle': f'{ab.tipo or "MANUAL"}{f" · {ab.notas}" if ab.notas else ""}',
            'monto': float(ab.monto) if ab.monto else 0.0,
            'url': f'/prestamos?trabajador_id={t.id}',
        })

    # ── Documentos subidos ───────────────────────────────────────────────
    doc_rows = (
        DocumentoTrabajador.query
        .filter(
            DocumentoTrabajador.trabajador_id == t.id,
            DocumentoTrabajador.fecha_subida != None,  # noqa: E711
            DocumentoTrabajador.fecha_subida >= datetime.combine(desde, datetime.min.time()),
            DocumentoTrabajador.fecha_subida <= datetime.combine(hasta, datetime.max.time()),
        )
        .order_by(DocumentoTrabajador.fecha_subida.desc())
        .limit(limit)
        .all()
    )
    for d in doc_rows:
        sub = d.tipo_documento or d.nombre_archivo
        venc = ''
        if d.fecha_fin:
            venc = f' · vence {d.fecha_fin.isoformat()}'
        eventos.append({
            'tipo': 'documento',
            'fecha': d.fecha_subida.date().isoformat(),
            'titulo': f'Documento: {sub}',
            'subtitle': f'{d.nombre_archivo}{venc}',
            'monto': None,
            'url': f'/empleados/{t.id}',
        })

    # Sort global desc por fecha y aplicar tope agregado.
    eventos.sort(key=lambda e: e['fecha'], reverse=True)
    eventos = eventos[:limit]

    return jsonify({
        'trabajador': {
            'id': t.id,
            'no_empleado': t.no_empleado,
            'nombre': f'{t.nombre or ""} {t.nombre_apellidos or ""}'.strip(),
        },
        'rango': {'desde': desde.isoformat(), 'hasta': hasta.isoformat()},
        'total': len(eventos),
        'eventos': eventos,
    })
