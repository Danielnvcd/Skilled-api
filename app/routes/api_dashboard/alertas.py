"""Banner de alertas en topbar.

Endpoint agregador: una sola request → conteo global + items de cada
categoría accionable. Pensado para el chip de alertas del SPA: lectura
barata (top 5 items por categoría), cero mutación, sin emit.
"""
from datetime import date, timedelta

from flask import jsonify

from app.extensions import db
from app.models import (
    AjustePeriodo, CredencialPlanta, DocumentoTrabajador, Prestamo, Trabajador,
)
from app.routes._api_helpers import require_admin
from app.routes.api_auth import jwt_required

from ._core import bp


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
    err = require_admin()
    if err:
        return err

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
