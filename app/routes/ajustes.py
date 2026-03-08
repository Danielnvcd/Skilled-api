from flask import Blueprint, render_template, request, flash, redirect, url_for, jsonify, current_app
from app.extensions import db
from app.models import AjustePeriodo, AjusteTrabajadorPeriodo, AjusteDescuento, Trabajador
from app.utils import login_required, log_action
from datetime import datetime
from decimal import Decimal
import traceback

bp = Blueprint('ajustes', __name__, url_prefix='/ajustes')


@bp.route('/')
@login_required
def index():
    page = request.args.get('page', 1, type=int)
    q = request.args.get('q', '').strip()

    query = AjustePeriodo.query
    if q:
        query = query.filter(AjustePeriodo.nombre.ilike(f'%{q}%'))
        
    pagination = query.order_by(AjustePeriodo.created_at.desc()).paginate(page=page, per_page=20, error_out=False)
    periodos = pagination.items

    # Calcular totales para cada periodo
    for p in periodos:
        p.total_meta = sum(tp.monto_meta or Decimal('0') for tp in p.trabajadores_periodo)
        p.total_descontado = sum(d.monto or Decimal('0') for d in p.descuentos)
        p.num_trabajadores = len(p.trabajadores_periodo)

    return render_template('ajustes.html', periodos=periodos,
                           trabajadores=Trabajador.query.filter_by(activo=True).order_by(Trabajador.nombre).all(),
                           pagination=pagination, q=q)


@bp.route('/crear_periodo', methods=['POST'])
@login_required
def crear_periodo():
    try:
        nombre = request.form.get('nombre', '').strip()
        fecha_inicio_str = request.form.get('fecha_inicio')
        fecha_fin_str = request.form.get('fecha_fin')
        trabajador_ids = request.form.getlist('trabajador_ids')
        montos_meta = request.form.getlist('montos_meta')

        if not nombre or not fecha_inicio_str or not fecha_fin_str:
            flash("Nombre y fechas son obligatorios.", "danger")
            return redirect(url_for('ajustes.index'))

        fecha_inicio = datetime.strptime(fecha_inicio_str, '%Y-%m-%d').date()
        fecha_fin = datetime.strptime(fecha_fin_str, '%Y-%m-%d').date()

        if fecha_inicio >= fecha_fin:
            flash("La fecha de inicio debe ser anterior a la fecha fin.", "danger")
            return redirect(url_for('ajustes.index'))

        # Bloquear si ya hay un periodo (abierto O cerrado) con fechas que se traslapan
        periodo_existente = AjustePeriodo.query.filter(
            AjustePeriodo.fecha_inicio <= fecha_fin,
            AjustePeriodo.fecha_fin >= fecha_inicio
        ).first()
        if periodo_existente:
            flash(f"Ya existe un periodo en esas fechas: '{periodo_existente.nombre}' ({periodo_existente.fecha_inicio.strftime('%d/%m/%Y')} - {periodo_existente.fecha_fin.strftime('%d/%m/%Y')}).", "danger")
            return redirect(url_for('ajustes.index'))

        if not trabajador_ids:
            flash("Debes seleccionar al menos un trabajador.", "danger")
            return redirect(url_for('ajustes.index'))

        periodo = AjustePeriodo(nombre=nombre, fecha_inicio=fecha_inicio, fecha_fin=fecha_fin)
        db.session.add(periodo)
        db.session.flush()

        # Vincular trabajadores con sus metas
        for i, t_id in enumerate(trabajador_ids):
            monto = Decimal(montos_meta[i]) if i < len(montos_meta) and montos_meta[i] else Decimal('0')
            if monto > 0:
                tp = AjusteTrabajadorPeriodo(
                    periodo_id=periodo.id,
                    trabajador_id=int(t_id),
                    monto_meta=monto
                )
                db.session.add(tp)

        db.session.commit()
        log_action(f"Creó periodo de ajuste: {nombre}")
        flash(f"Periodo '{nombre}' creado exitosamente.", "success")

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error creando periodo: {traceback.format_exc()}")
        flash("Error al crear el periodo.", "danger")

    return redirect(url_for('ajustes.index'))


@bp.route('/periodo/<int:periodo_id>')
@login_required
def detalle(periodo_id):
    periodo = AjustePeriodo.query.get_or_404(periodo_id)
    trabajadores_all = Trabajador.query.filter_by(activo=True).order_by(Trabajador.nombre).all()

    # Metas por trabajador
    metas = {tp.trabajador_id: tp for tp in periodo.trabajadores_periodo}

    # Descuentos agrupados
    descuentos_por_trabajador = {}
    for d in periodo.descuentos:
        descuentos_por_trabajador.setdefault(d.trabajador_id, []).append(d)

    # Calcular progreso por trabajador
    progreso = {}
    for tp in periodo.trabajadores_periodo:
        t_id = tp.trabajador_id
        total_desc = sum(d.monto or Decimal('0') for d in descuentos_por_trabajador.get(t_id, []))
        progreso[t_id] = {
            'meta': tp.monto_meta,
            'descontado': total_desc,
            'restante': tp.monto_meta - total_desc,
            'porcentaje': min(100, int((total_desc / tp.monto_meta * 100))) if tp.monto_meta > 0 else 0
        }

    # Fechas únicas para columnas
    fechas_unicas = sorted(set(d.fecha_descuento for d in periodo.descuentos))

    total_meta_global = sum(tp.monto_meta for tp in periodo.trabajadores_periodo)
    total_descontado_global = sum(p['descontado'] for p in progreso.values())

    return render_template('ajuste_detalle.html',
        periodo=periodo,
        trabajadores_all=trabajadores_all,
        metas=metas,
        descuentos_por_trabajador=descuentos_por_trabajador,
        progreso=progreso,
        fechas_unicas=fechas_unicas,
        total_meta_global=total_meta_global,
        total_descontado_global=total_descontado_global
    )


@bp.route('/agregar_descuento/<int:periodo_id>', methods=['POST'])
@login_required
def agregar_descuento(periodo_id):
    periodo = AjustePeriodo.query.get_or_404(periodo_id)

    if periodo.estado != 'ABIERTO':
        flash("Este periodo ya está cerrado.", "warning")
        return redirect(url_for('ajustes.detalle', periodo_id=periodo.id))

    try:
        trabajador_id = request.form.get('trabajador_id')
        monto_str = request.form.get('monto', '').strip()
        fecha_str = request.form.get('fecha_descuento')
        notas = request.form.get('notas', '').strip()

        if not trabajador_id or not monto_str or not fecha_str:
            flash("Trabajador, monto y fecha son obligatorios.", "danger")
            return redirect(url_for('ajustes.detalle', periodo_id=periodo.id))

        monto = Decimal(monto_str)
        if monto <= 0:
            flash("El monto debe ser mayor a $0.00.", "danger")
            return redirect(url_for('ajustes.detalle', periodo_id=periodo.id))

        fecha = datetime.strptime(fecha_str, '%Y-%m-%d').date()

        trabajador = Trabajador.query.get(trabajador_id)
        if not trabajador:
            flash("Trabajador no encontrado.", "danger")
            return redirect(url_for('ajustes.detalle', periodo_id=periodo.id))

        # Verificar que el trabajador está en el periodo
        tp = AjusteTrabajadorPeriodo.query.filter_by(periodo_id=periodo.id, trabajador_id=trabajador.id).first()
        if not tp:
            flash("Este trabajador no está asignado a este periodo.", "danger")
            return redirect(url_for('ajustes.detalle', periodo_id=periodo.id))

        descuento = AjusteDescuento(
            periodo_id=periodo.id,
            trabajador_id=trabajador.id,
            monto=monto,
            fecha_descuento=fecha,
            notas=notas if notas else None
        )
        db.session.add(descuento)
        db.session.commit()
        flash(f"Descuento de ${monto:.2f} agregado a {trabajador.nombre_apellidos}.", "success")

    except (ValueError, TypeError):
        flash("Monto inválido.", "danger")
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error agregando descuento: {traceback.format_exc()}")
        flash("Error al agregar el descuento.", "danger")

    return redirect(url_for('ajustes.detalle', periodo_id=periodo.id))


@bp.route('/eliminar_descuento/<int:descuento_id>', methods=['POST'])
@login_required
def eliminar_descuento(descuento_id):
    descuento = AjusteDescuento.query.get_or_404(descuento_id)
    periodo_id = descuento.periodo_id

    if descuento.periodo.estado != 'ABIERTO':
        flash("No se pueden eliminar descuentos de un periodo cerrado.", "warning")
        return redirect(url_for('ajustes.detalle', periodo_id=periodo_id))
        
    if getattr(descuento, 'cobrado', False):
        flash("Este descuento ya fue cobrado en la prenómina y no puede eliminarse.", "warning")
        return redirect(url_for('ajustes.detalle', periodo_id=periodo_id))

    try:
        db.session.delete(descuento)
        db.session.commit()
        flash("Descuento eliminado.", "info")
    except Exception:
        db.session.rollback()
        flash("Error al eliminar el descuento.", "danger")

    return redirect(url_for('ajustes.detalle', periodo_id=periodo_id))


@bp.route('/cerrar_periodo/<int:periodo_id>', methods=['POST'])
@login_required
def cerrar_periodo(periodo_id):
    periodo = AjustePeriodo.query.get_or_404(periodo_id)

    if periodo.estado != 'ABIERTO':
        flash("Este periodo ya está cerrado.", "warning")
        return redirect(url_for('ajustes.detalle', periodo_id=periodo.id))

    try:
        periodo.estado = 'CERRADO'
        db.session.commit()
        log_action(f"Cerró periodo de ajuste: {periodo.nombre}")
        flash(f"Periodo '{periodo.nombre}' cerrado exitosamente.", "success")
    except Exception:
        db.session.rollback()
        flash("Error al cerrar el periodo.", "danger")

    return redirect(url_for('ajustes.detalle', periodo_id=periodo.id))
