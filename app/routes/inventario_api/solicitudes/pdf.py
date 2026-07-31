"""PDFs de solicitudes: la guardada (con columnas de entrega) y el preview
del carrito antes de enviarla al almacén."""
import datetime

from flask import jsonify, request, send_file
from sqlalchemy.orm import joinedload, selectinload

from app.extensions import db, limiter, get_real_client_ip_flask
from app.models import SolicitudMaterial, SolicitudMaterialDetalle

from .._core import (
    bp,
    _require_login,
    _audit,
    renderizar_pdf, cantidad_legible,
)


# ─── PDF de solicitudes ──────────────────────────────────────────────────────

def _render_solicitud_pdf(*, folio, fecha_str, solicitante, proyecto, notas, materiales, herramientas,
                          estatus=None, estado_label=None, mostrar_entrega=False):
    """Helper común: ya recibe los dicts normalizados y devuelve BytesIO con el PDF.

    `mostrar_entrega`/`estado_label` solo se pasan al imprimir una solicitud ya
    guardada (muestra columnas Aprobada/Entregada/Pendiente y el estado, p. ej.
    entrega parcial). El preview del carrito los omite (default off)."""
    return renderizar_pdf(
        'solicitud_pedido_pdf.html',
        folio=folio,
        fecha=fecha_str,
        solicitante=solicitante,
        proyecto=proyecto,
        notas=notas,
        materiales=materiales,
        herramientas=herramientas,
        estatus=estatus,
        estado_label=estado_label,
        mostrar_entrega=mostrar_entrega,
    )


def _columnas_de_entrega(d: SolicitudMaterialDetalle) -> dict:
    """Columnas Aprobada / Entregada / Pendiente de una línea en el PDF."""
    solicitada = float(d.cantidad_solicitada or 0)
    aprobada = float(d.cantidad_aprobada or 0)
    entregada = float(d.cantidad_entregada or 0)
    baseline = aprobada if aprobada > 0 else solicitada
    return {
        'aprobada': cantidad_legible(aprobada),
        'entregada': cantidad_legible(entregada),
        'pendiente': cantidad_legible(max(0.0, baseline - entregada)),
    }


def _fila_pdf_de_detalle(d: SolicitudMaterialDetalle) -> dict:
    """Datos del ítem para la tabla del PDF, recortados a lo que cabe en la
    plantilla. Materiales y herramientas tienen columnas distintas."""
    cantidad = cantidad_legible(d.cantidad_solicitada)
    justificacion = (d.justificacion or '')[:2000]
    if (d.tipo_item or 'MATERIAL').upper() == 'HERRAMIENTA':
        h = d.herramienta
        return {
            'descripcion': (h.descripcion if h else 'Herramienta eliminada')[:250],
            'sku': (h.sku if h else '---')[:50],
            'cantidad': cantidad,
            'fecha_uso_inicio': d.fecha_uso_inicio.isoformat() if d.fecha_uso_inicio else '',
            'fecha_uso_fin': d.fecha_uso_fin.isoformat() if d.fecha_uso_fin else '',
            'justificacion': justificacion,
            'complementos': (d.complementos or '')[:500],
        }
    p = d.producto
    return {
        'descripcion': (p.descripcion if p else 'Producto eliminado')[:250],
        'codigo': (p.codigo if p else '---')[:50],
        'categoria': (p.categoria if p else '')[:100],
        'unidad': (p.unidad if p else '')[:50],
        'cable_tipo': (p.cable_tipo if p else '') or '',
        'cable_calibre': (p.cable_calibre if p else '') or '',
        'cantidad': cantidad,
        'justificacion': justificacion,
    }


def _etiqueta_estado(sol: SolicitudMaterial, hay_entregas: bool, hay_pendiente: bool) -> str:
    """Estado legible para el encabezado del PDF, incluido el caso de entrega
    parcial (que el PDF no reflejaba en absoluto)."""
    if sol.estatus == 'ENTREGADA':
        return ('ENTREGA DIRECTA — surtido en mostrador' if sol.entrega_directa
                else 'ENTREGADA — surtido completo')
    if sol.estatus == 'APROBADA':
        if hay_entregas and hay_pendiente:
            return 'ENTREGA PARCIAL — faltan piezas por surtir'
        if hay_entregas:
            return 'APROBADA — entrega parcial registrada'
        return 'APROBADA — pendiente de entrega'
    if sol.estatus == 'PENDIENTE':
        return 'PENDIENTE de aprobación'
    if sol.estatus == 'RECHAZADA':
        return 'RECHAZADA'
    return sol.estatus


@bp.route('/solicitudes/<int:sol_id>/pdf', methods=['GET'])
@_require_login
def imprimir_solicitud(sol_id: int):
    """Genera el PDF de una solicitud ya guardada. Solicitante solo puede
    imprimir las suyas; inventario/admin/super_admin pueden imprimir todas."""
    user = request.current_user
    sol = (
        SolicitudMaterial.query
        .options(
            joinedload(SolicitudMaterial.solicitante),
            selectinload(SolicitudMaterial.detalles).joinedload(SolicitudMaterialDetalle.producto),
            selectinload(SolicitudMaterial.detalles).joinedload(SolicitudMaterialDetalle.herramienta),
        )
        .filter(SolicitudMaterial.id == sol_id).first()
    )
    if not sol:
        return jsonify({'detail': 'Solicitud no encontrada'}), 404

    # AuthZ: solicitante y coordinador solo pueden imprimir las suyas.
    if user.role in ('solicitante_material', 'coordinador') and sol.solicitante_id != user.id:
        return jsonify({'detail': 'Forbidden'}), 403
    if user.role not in ('solicitante_material', 'coordinador', 'inventario', 'admin', 'super_admin'):
        return jsonify({'detail': 'Forbidden'}), 403

    # Las columnas Aprobada/Entregada/Pendiente solo tienen sentido una vez que la
    # solicitud fue aprobada o entregada (en PENDIENTE/RECHAZADA aún no hay nada).
    mostrar_entrega = sol.estatus in ('APROBADA', 'ENTREGADA')

    hay_entregas = False
    hay_pendiente = False
    materiales, herramientas = [], []
    for d in sol.detalles:
        entrega = _columnas_de_entrega(d)
        if entrega['entregada'] > 0:
            hay_entregas = True
        if mostrar_entrega and entrega['pendiente'] > 0:
            hay_pendiente = True
        destino = herramientas if (d.tipo_item or 'MATERIAL').upper() == 'HERRAMIENTA' else materiales
        destino.append({**_fila_pdf_de_detalle(d), **entrega})

    estado_label = _etiqueta_estado(sol, hay_entregas, hay_pendiente)

    folio = f'SOL-{sol.id:06d}'
    fecha_str = sol.fecha_creacion.strftime('%d/%m/%Y %H:%M') if sol.fecha_creacion else ''
    # Nombre del solicitante REAL (trabajador / texto libre en entregas directas;
    # el capturista en solicitudes normales).
    solicitante = sol.solicitante_display

    pdf = _render_solicitud_pdf(
        folio=folio, fecha_str=fecha_str, solicitante=solicitante,
        proyecto=sol.proyecto or '', notas=sol.notas or '',
        materiales=materiales, herramientas=herramientas,
        estatus=sol.estatus, estado_label=estado_label, mostrar_entrega=mostrar_entrega,
    )
    if pdf is None:
        return jsonify({'detail': 'Error al generar el PDF'}), 500

    return send_file(pdf, mimetype='application/pdf', as_attachment=False, download_name=f'{folio}.pdf')


# ─── PDF preview de solicitud (sin persistir) ────────────────────────────────

@bp.route('/solicitudes/preview-pdf', methods=['POST'])
@limiter.limit('10/minute', key_func=lambda: f"ip:{get_real_client_ip_flask()}")
@_require_login
def preview_solicitud_pdf():
    """Genera un PDF a partir del carrito actual del usuario, SIN guardar la
    solicitud. Sirve para que el solicitante pueda imprimir/firmar antes de
    enviar al almacén. Mismo mecanismo que prenómina: xhtml2pdf → send_file.
    """
    user = request.current_user
    if user.role not in ('solicitante_material', 'coordinador', 'inventario', 'admin', 'super_admin'):
        return jsonify({'detail': 'Forbidden'}), 403

    payload = request.get_json(silent=True) or {}
    materiales_raw = payload.get('materiales') or []
    herramientas_raw = payload.get('herramientas') or []

    if not materiales_raw and not herramientas_raw:
        return jsonify({'detail': 'Agrega al menos un material o herramienta'}), 422
    if len(materiales_raw) > 500 or len(herramientas_raw) > 500:
        return jsonify({'detail': 'Demasiados ítems en una sola solicitud'}), 422

    # Normalizar / sanitizar (xhtml2pdf escapa automáticamente vía Jinja autoescape)
    def _clean(s, maxlen=500):
        return (str(s or '')[:maxlen]).strip()

    materiales = []
    for m in materiales_raw:
        try:
            cantidad = float(m.get('cantidad') or 0)
        except (TypeError, ValueError):
            cantidad = 0
        materiales.append({
            'descripcion': _clean(m.get('descripcion'), 250),
            'codigo': _clean(m.get('codigo'), 50),
            'categoria': _clean(m.get('categoria'), 100),
            'unidad': _clean(m.get('unidad'), 50),
            'cable_tipo': _clean(m.get('cable_tipo'), 60),
            'cable_calibre': _clean(m.get('cable_calibre'), 40),
            'cantidad': cantidad if cantidad % 1 else int(cantidad),
            'justificacion': _clean(m.get('justificacion'), 2000),
        })

    herramientas = []
    for h in herramientas_raw:
        try:
            cantidad = int(float(h.get('cantidad') or 0))
        except (TypeError, ValueError):
            cantidad = 0
        herramientas.append({
            'descripcion': _clean(h.get('descripcion'), 250),
            'sku': _clean(h.get('sku'), 50),
            'cantidad': cantidad,
            'fecha_uso_inicio': _clean(h.get('fecha_uso_inicio'), 20),
            'fecha_uso_fin': _clean(h.get('fecha_uso_fin'), 20),
            'justificacion': _clean(h.get('justificacion'), 2000),
            'complementos': _clean(h.get('complementos'), 500),
        })

    proyecto = _clean(payload.get('proyecto'), 200)
    notas = _clean(payload.get('notas'), 2000)
    folio = 'SOL-' + datetime.datetime.now().strftime('%y%m%d%H%M%S')
    fecha_str = datetime.datetime.now().strftime('%d/%m/%Y %H:%M')
    solicitante = user.full_name or user.username

    pdf = _render_solicitud_pdf(
        folio=folio, fecha_str=fecha_str, solicitante=solicitante,
        proyecto=proyecto, notas=notas,
        materiales=materiales, herramientas=herramientas,
    )
    if pdf is None:
        return jsonify({'detail': 'Error al generar el PDF (xhtml2pdf no disponible)'}), 500

    _audit(user, f"Vista previa PDF solicitud ({len(materiales)} mat, {len(herramientas)} herr)")
    db.session.commit()

    return send_file(pdf, mimetype='application/pdf', as_attachment=False, download_name=f'{folio}.pdf')
