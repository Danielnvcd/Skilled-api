"""Tomas físicas de inventario (Pausa 10).

Snapshot de stock por almacén → captura física → cierre que genera AJUSTEs
automáticos contra `_perform_movimiento`. PDF de acta para firmar.
"""
from decimal import Decimal

from flask import jsonify, request, send_file

from app.extensions import db
from app.models._base import _now_utc
from app.models import (
    Almacen, Producto, StockPorAlmacen,
    TomaInventario, TomaInventarioDetalle, ESTADOS_TOMA,
)

from app.realtime import emit_to_role

from ._core import (
    bp,
    _es_error_de_lock,
    _require_inventario_admin,
    _audit,
    renderizar_pdf,
    _INV_ROLES,
)
from .movimientos import _perform_movimiento


# ─── Serializers (privados al módulo de tomas) ───────────────────────────────

def _toma_to_dict(t: TomaInventario, *, include_detalles: bool = False) -> dict:
    total = len(t.detalles or [])
    capturados = sum(1 for d in (t.detalles or []) if d.cantidad_fisica is not None)
    out = {
        'id': t.id,
        'almacen_id': t.almacen_id,
        'almacen_nombre': t.almacen.nombre if t.almacen else None,
        'fecha_inicio': t.fecha_inicio.isoformat() if t.fecha_inicio else None,
        'fecha_cierre': t.fecha_cierre.isoformat() if t.fecha_cierre else None,
        'usuario_id': t.usuario_id,
        'usuario_nombre': (t.usuario.full_name or t.usuario.username) if t.usuario else None,
        'cerrada_por_id': t.cerrada_por_id,
        'cerrada_por_nombre': (t.cerrada_por.full_name or t.cerrada_por.username) if t.cerrada_por else None,
        'estatus': t.estatus,
        'notas': t.notas,
        'total_lineas': total,
        'lineas_capturadas': capturados,
        'progreso': (capturados / total) if total else 0,
    }
    if include_detalles:
        out['detalles'] = [_toma_detalle_to_dict(d) for d in (t.detalles or [])]
    return out


def _toma_detalle_to_dict(d: TomaInventarioDetalle) -> dict:
    p = d.producto
    cant_fis = None if d.cantidad_fisica is None else float(d.cantidad_fisica)
    cant_sis = float(d.cantidad_sistema or 0)
    diff = None if cant_fis is None else (cant_fis - cant_sis)
    return {
        'id': d.id,
        'toma_id': d.toma_id,
        'producto_id': d.producto_id,
        'producto_codigo': p.codigo if p else None,
        'producto_descripcion': p.descripcion if p else None,
        'producto_unidad': p.unidad if p else None,
        'cantidad_sistema': cant_sis,
        'cantidad_fisica': cant_fis,
        'diferencia': diff,
        'capturado_por_id': d.capturado_por_id,
        'capturado_en': d.capturado_en.isoformat() if d.capturado_en else None,
    }


# ─── Endpoints ────────────────────────────────────────────────────────────────

@bp.route('/tomas/', methods=['POST'])
@_require_inventario_admin
def create_toma():
    """Inicia una toma física para un almacén. Snapshotea StockPorAlmacen
    para todos los productos activos con stock cacheado en ese almacén.
    Si un producto activo no tiene fila en StockPorAlmacen para esa bodega,
    arranca con cantidad_sistema=0 (puede que aparezca al contar)."""
    body = request.get_json(silent=True) or {}
    almacen_id = body.get('almacen_id')
    notas = (body.get('notas') or '').strip()[:1000] or None

    if not almacen_id:
        return jsonify({'detail': 'almacen_id es requerido'}), 422
    alm = db.session.get(Almacen, almacen_id)
    if not alm or not alm.activo:
        return jsonify({'detail': 'Almacén no encontrado o inactivo'}), 404

    abierta = TomaInventario.query.filter_by(almacen_id=alm.id, estatus='ABIERTA').first()
    if abierta:
        return jsonify({
            'detail': f'Ya hay una toma ABIERTA (#{abierta.id}) para este almacén',
            'toma_id': abierta.id,
        }), 409

    user = request.current_user
    nueva = TomaInventario(
        almacen_id=alm.id,
        usuario_id=user.id,
        estatus='ABIERTA',
        notas=notas,
    )
    db.session.add(nueva)
    db.session.flush()

    # Snapshot: por cada producto activo que tenga fila StockPorAlmacen en
    # este almacén, copiamos la cantidad. También incluimos productos activos
    # sin fila para que el almacenista pueda registrarlos si los encuentra.
    stocks = {
        s.producto_id: s.cantidad
        for s in StockPorAlmacen.query.filter_by(almacen_id=alm.id).all()
    }
    productos = Producto.query.filter(Producto.activo == True).all()
    for p in productos:
        cant = stocks.get(p.id, Decimal('0'))
        db.session.add(TomaInventarioDetalle(
            toma_id=nueva.id,
            producto_id=p.id,
            cantidad_sistema=cant,
        ))

    _audit(user, f"Toma #{nueva.id} iniciada en almacén {alm.nombre} ({len(productos)} líneas)")
    db.session.commit()
    db.session.refresh(nueva)
    emit_to_role(_INV_ROLES, 'toma:changed', {'id': nueva.id, 'action': 'creada'})
    return jsonify(_toma_to_dict(nueva))


@bp.route('/tomas/', methods=['GET'])
@_require_inventario_admin
def list_tomas():
    estatus = request.args.get('estatus', '').strip().upper()
    almacen_id = request.args.get('almacen_id', type=int)
    q = TomaInventario.query
    if estatus and estatus in ESTADOS_TOMA:
        q = q.filter(TomaInventario.estatus == estatus)
    if almacen_id:
        q = q.filter(TomaInventario.almacen_id == almacen_id)
    tomas = q.order_by(TomaInventario.fecha_inicio.desc()).limit(200).all()
    return jsonify([_toma_to_dict(t) for t in tomas])


@bp.route('/tomas/<int:toma_id>', methods=['GET'])
@_require_inventario_admin
def get_toma(toma_id: int):
    t = db.get_or_404(TomaInventario, toma_id)
    return jsonify(_toma_to_dict(t, include_detalles=True))


@bp.route('/tomas/<int:toma_id>/detalles/<int:det_id>', methods=['PATCH'])
@_require_inventario_admin
def patch_toma_detalle(toma_id: int, det_id: int):
    """Captura `cantidad_fisica` en una línea. Body: `{cantidad_fisica}`.
    Acepta null para limpiar la captura."""
    t = db.get_or_404(TomaInventario, toma_id)
    if t.estatus != 'ABIERTA':
        return jsonify({'detail': f'Toma {t.estatus.lower()} — no se puede modificar'}), 409
    det = TomaInventarioDetalle.query.filter_by(id=det_id, toma_id=t.id).first_or_404()

    body = request.get_json(silent=True) or {}
    raw = body.get('cantidad_fisica')
    if raw is None or raw == '':
        det.cantidad_fisica = None
        det.capturado_por_id = None
        det.capturado_en = None
    else:
        try:
            cant = Decimal(str(raw))
        except Exception:
            return jsonify({'detail': 'cantidad_fisica inválida'}), 422
        if cant < 0:
            return jsonify({'detail': 'cantidad_fisica no puede ser negativa'}), 422
        det.cantidad_fisica = cant
        det.capturado_por_id = request.current_user.id
        det.capturado_en = _now_utc()

    db.session.commit()
    db.session.refresh(det)
    emit_to_role(_INV_ROLES, 'toma:changed', {
        'id': t.id, 'action': 'captura', 'detalle_id': det.id,
    })
    return jsonify(_toma_detalle_to_dict(det))


@bp.route('/tomas/<int:toma_id>/detalles/por-codigo', methods=['PATCH'])
@_require_inventario_admin
def patch_toma_detalle_por_codigo(toma_id: int):
    """Atajo para PWA scanner: captura por código de producto.
    Body: `{codigo, cantidad_fisica}`. Devuelve el detalle actualizado."""
    t = db.get_or_404(TomaInventario, toma_id)
    if t.estatus != 'ABIERTA':
        return jsonify({'detail': f'Toma {t.estatus.lower()} — no se puede modificar'}), 409

    body = request.get_json(silent=True) or {}
    codigo = (body.get('codigo') or '').strip()
    if not codigo:
        return jsonify({'detail': 'codigo es requerido'}), 422

    prod = Producto.query.filter_by(codigo=codigo, activo=True).first()
    if not prod:
        return jsonify({'detail': f'Producto {codigo} no encontrado'}), 404

    det = TomaInventarioDetalle.query.filter_by(toma_id=t.id, producto_id=prod.id).first()
    if not det:
        # producto que no estaba en el snapshot (activo nuevo) — lo agregamos
        det = TomaInventarioDetalle(toma_id=t.id, producto_id=prod.id, cantidad_sistema=Decimal('0'))
        db.session.add(det)
        db.session.flush()

    raw = body.get('cantidad_fisica')
    try:
        cant = Decimal(str(raw))
    except Exception:
        return jsonify({'detail': 'cantidad_fisica inválida'}), 422
    if cant < 0:
        return jsonify({'detail': 'cantidad_fisica no puede ser negativa'}), 422

    det.cantidad_fisica = cant
    det.capturado_por_id = request.current_user.id
    det.capturado_en = _now_utc()
    db.session.commit()
    db.session.refresh(det)
    emit_to_role(_INV_ROLES, 'toma:changed', {
        'id': t.id, 'action': 'captura', 'detalle_id': det.id,
    })
    return jsonify(_toma_detalle_to_dict(det))


@bp.route('/tomas/<int:toma_id>/cerrar', methods=['POST'])
@_require_inventario_admin
def cerrar_toma(toma_id: int):
    """Cierra la toma generando AJUSTES por cada línea con diferencia.
    Líneas sin captura (cantidad_fisica null) se asumen iguales al sistema
    — NO se ajustan, pero quedan registradas como "no contadas".

    El llamador puede pasar `{omitir_no_capturados: true}` (default) o false
    para forzar que las no capturadas se traten como cantidad_fisica=0 (riesgoso).
    """
    t = db.get_or_404(TomaInventario, toma_id)
    if t.estatus != 'ABIERTA':
        return jsonify({'detail': f'Toma ya está {t.estatus.lower()}'}), 409

    body = request.get_json(silent=True) or {}
    asumir_cero = bool(body.get('asumir_cero_no_capturados', False))

    user = request.current_user
    ajustes_creados = 0
    errores = []

    for det in t.detalles:
        cant_fis = det.cantidad_fisica
        if cant_fis is None:
            if not asumir_cero:
                continue
            cant_fis = Decimal('0')
        cant_sis = Decimal(str(det.cantidad_sistema or 0))
        diff = Decimal(str(cant_fis)) - cant_sis
        if diff == 0:
            continue
        # AJUSTE: positivo sube destino, negativo baja origen.
        # `reconciliar`: la toma es a nivel almacén; un faltante físico se
        # descuenta de cualquier bucket (general primero, luego proyectos) para
        # cuadrar el conteo sin fallar aunque el general esté vacío. Feature
        # stock por proyecto — el positivo (sobrante) entra al bucket general.
        data = {
            'tipo': 'AJUSTE',
            'producto_id': det.producto_id,
            'cantidad': diff,
            'motivo': f'Toma física #{t.id}',
            'reconciliar': True,
        }
        if diff > 0:
            data['almacen_destino_id'] = t.almacen_id
        else:
            data['almacen_origen_id'] = t.almacen_id
        # `autocommit=False`: el ajuste se queda en ESTA transacción. Cada línea
        # va en su savepoint para poder descartar solo la que falla y seguir
        # recolectando el resto de errores; si al final hubo alguno, el rollback
        # de abajo deshace TODO. Antes cada ajuste se commiteaba por separado:
        # un fallo a media lista dejaba la toma ABIERTA con stock ya movido, y
        # como `cantidad_sistema` es un snapshot, el reintento volvía a aplicar
        # los ajustes ya hechos (stock duplicado).
        punto = db.session.begin_nested()
        try:
            resp = _perform_movimiento(data, user, autocommit=False)
        except Exception as exc:
            punto.rollback()
            errores.append({
                'producto_id': det.producto_id,
                'detail': ('Stock bloqueado por otra operación, reintenta'
                           if _es_error_de_lock(exc) else str(exc)),
                'status': 409 if _es_error_de_lock(exc) else 500,
            })
            continue
        # _perform_movimiento puede devolver (response, status) o response.
        # Si es tuple con código de error, descartamos esa línea.
        if isinstance(resp, tuple):
            body_resp, status = resp
            if status >= 400:
                punto.rollback()
                errores.append({
                    'producto_id': det.producto_id,
                    'detail': body_resp.get_json().get('detail') if hasattr(body_resp, 'get_json') else str(body_resp),
                    'status': status,
                })
                continue
        punto.commit()  # libera el savepoint; sigue todo dentro de la transacción
        ajustes_creados += 1

    if errores:
        db.session.rollback()  # ahora SÍ deshace los ajustes: nada fue commiteado
        return jsonify({
            'detail': 'No se pudieron generar todos los ajustes — toma sigue ABIERTA',
            'errores': errores,
            'ajustes_intentados': ajustes_creados,
        }), 409

    t.estatus = 'CERRADA'
    t.fecha_cierre = _now_utc()
    t.cerrada_por_id = user.id
    _audit(user, f"Toma #{t.id} cerrada — {ajustes_creados} ajustes generados")
    db.session.commit()  # único commit: los N ajustes + el cierre, atómicos
    db.session.refresh(t)
    # Los ajustes no emitieron (autocommit=False), así que avisamos aquí: stock
    # cambió y la toma pasó a CERRADA.
    emit_to_role(_INV_ROLES, 'toma:changed', {'id': t.id, 'action': 'cerrada'})
    if ajustes_creados:
        emit_to_role(_INV_ROLES, 'movimiento:changed', {'origen': 'toma_cierre', 'toma_id': t.id})
        emit_to_role(_INV_ROLES, 'producto:changed', {'origen': 'toma_cierre'})
    return jsonify({**_toma_to_dict(t), 'ajustes_creados': ajustes_creados})


@bp.route('/tomas/<int:toma_id>/cancelar', methods=['POST'])
@_require_inventario_admin
def cancelar_toma(toma_id: int):
    """Cancela una toma sin aplicar ajustes."""
    t = db.get_or_404(TomaInventario, toma_id)
    if t.estatus != 'ABIERTA':
        return jsonify({'detail': f'Toma ya está {t.estatus.lower()}'}), 409
    t.estatus = 'CANCELADA'
    t.fecha_cierre = _now_utc()
    t.cerrada_por_id = request.current_user.id
    _audit(request.current_user, f"Toma #{t.id} cancelada")
    db.session.commit()
    emit_to_role(_INV_ROLES, 'toma:changed', {'id': t.id, 'action': 'cancelada'})
    return jsonify(_toma_to_dict(t))


def _conteo_para_acta(t: TomaInventario):
    """Líneas y resumen de la toma para el acta PDF, ordenadas por código.

    Una línea sin `cantidad_fisica` es "no capturada" (nadie la contó) y no
    cuenta como diferencia; con física, la diferencia es física − sistema.
    """
    detalles = []
    capturadas = con_diferencia = no_capturadas = 0
    for d in sorted(t.detalles, key=lambda x: (x.producto.codigo if x.producto else '')):
        cant_sistema = float(d.cantidad_sistema or 0)
        cant_fisica = None if d.cantidad_fisica is None else float(d.cantidad_fisica)
        if cant_fisica is None:
            no_capturadas += 1
            diferencia = None
        else:
            capturadas += 1
            diferencia = cant_fisica - cant_sistema
            if abs(diferencia) > 1e-9:
                con_diferencia += 1
        detalles.append({
            'codigo': d.producto.codigo if d.producto else '—',
            'descripcion': d.producto.descripcion if d.producto else '—',
            'unidad': d.producto.unidad if d.producto else None,
            'sistema': cant_sistema,
            'fisico': cant_fisica,
            'diff': diferencia if diferencia is not None else 0,
        })
    resumen = {
        'total': len(detalles),
        'capturadas': capturadas,
        'con_diferencia': con_diferencia,
        'no_capturadas': no_capturadas,
    }
    return detalles, resumen


@bp.route('/tomas/<int:toma_id>/pdf', methods=['GET'])
@_require_inventario_admin
def get_toma_pdf(toma_id: int):
    """PDF de acta de toma con diferencias y firmas."""
    t = db.get_or_404(TomaInventario, toma_id)
    detalles_out, resumen = _conteo_para_acta(t)

    pdf = renderizar_pdf(
        'toma_inventario_pdf.html',
        toma=t,
        almacen_nombre=t.almacen.nombre if t.almacen else None,
        iniciada_por=(t.usuario.full_name or t.usuario.username) if t.usuario else None,
        cerrada_por=(t.cerrada_por.full_name or t.cerrada_por.username) if t.cerrada_por else None,
        estatus=t.estatus,
        fecha_inicio=t.fecha_inicio.strftime('%Y-%m-%d %H:%M') if t.fecha_inicio else '',
        detalles=detalles_out,
        resumen=resumen,
    )
    if pdf is None:
        return jsonify({'detail': 'Error generando PDF'}), 500

    # El no-cache de PDFs lo aplica el after_request central (_security_headers).
    return send_file(
        pdf,
        mimetype='application/pdf',
        as_attachment=False,
        download_name=f'toma-{t.id}.pdf',
    )
