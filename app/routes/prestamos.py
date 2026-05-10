from flask import Blueprint, render_template, request, jsonify, current_app
from app.extensions import db, limiter
from app.models import Prestamo, Trabajador, Prenomina, DescuentoPrenomina, AbonoPrestamo
from app.utils import login_required, admin_required, log_action, to_dec, recalcular_totales_prenomina
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
import traceback

bp = Blueprint('prestamos', __name__, url_prefix='/prestamos')

@bp.route('/')
@login_required
@admin_required
def index():
    page = request.args.get('page', 1, type=int)
    q = request.args.get('q', '').strip()

    query = Prestamo.query
    if q:
        query = query.join(Trabajador, Prestamo.trabajador_id == Trabajador.id).filter(
            db.or_(
                Trabajador.nombre.ilike(f'%{q}%'),
                Trabajador.nombre_apellidos.ilike(f'%{q}%'),
                Trabajador.no_empleado.ilike(f'%{q}%'),
                db.cast(Prestamo.id, db.String).ilike(f'%{q}%')
            )
        )

    pagination = query.order_by(Prestamo.creado_en.desc()).paginate(page=page, per_page=20, error_out=False)
    prestamos = pagination.items

    trabajadores = Trabajador.query.filter_by(activo=True).order_by(Trabajador.nombre).all()
    return render_template('prestamos.html', prestamos=prestamos, trabajadores=trabajadores, pagination=pagination, q=q)

@bp.route('/crear', methods=['POST'])
@login_required
@admin_required
@limiter.limit("10 per minute")
def crear():
    try:
        data = request.get_json(silent=True)
        if not data:
            return jsonify({'success': False, 'message': 'Datos inválidos o vacíos.'}), 400
        
        if not data.get('trabajador_id') or not data.get('monto_total') or not data.get('plazo_semanas') or not data.get('descuento_semanal'):
            return jsonify({'success': False, 'message': 'Faltan campos obligatorios.'}), 400
        
        trabajador_id = int(data['trabajador_id'])
        monto_total = Decimal(str(data['monto_total']))
        plazo_semanas = int(data['plazo_semanas'])
        descuento_semanal = Decimal(str(data['descuento_semanal']))
        
        if monto_total <= 0 or descuento_semanal <= 0 or plazo_semanas <= 0:
            return jsonify({'success': False, 'message': 'El monto, plazo y descuento deben ser mayores a cero.'}), 400
        motivo = data.get('motivo', '')
        frecuencia = data.get('frecuencia', 'semanal')
        fecha_inicio_str = data.get('fecha_inicio')
        
        fecha_inicio = None
        if fecha_inicio_str:
            fecha_inicio = datetime.strptime(fecha_inicio_str, '%Y-%m-%d').date()
        
        prestamo = Prestamo(
            trabajador_id=trabajador_id,
            monto_total=monto_total,
            plazo_semanas=plazo_semanas,
            descuento_semanal=descuento_semanal,
            monto_restante=monto_total,
            motivo=motivo,
            frecuencia=frecuencia,
            fecha_inicio=fecha_inicio,
            estado='ACTIVO',
            activo=True
        )
        db.session.add(prestamo)
        db.session.commit()
        
        # Recalcular prenóminas abiertas del trabajador
        _recalcular_prenominas_abiertas(trabajador_id)
        
        log_action(f'crear_prestamo: Préstamo #{prestamo.id} creado para trabajador #{trabajador_id} por ${monto_total}')
        return jsonify({'success': True, 'message': 'Préstamo registrado correctamente.', 'id': prestamo.id})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error al crear préstamo: {traceback.format_exc()}")
        return jsonify({'success': False, 'message': 'Ocurrió un error al crear el préstamo.'}), 500

@bp.route('/editar/<int:id>', methods=['POST'])
@login_required
@admin_required
@limiter.limit("10 per minute")
def editar(id):
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'message': 'Datos inválidos o vacíos.'}), 400
        
        prestamo = Prestamo.query.get_or_404(id)
        
        if prestamo.estado == 'LIQUIDADO':
            return jsonify({'success': False, 'message': 'No se puede editar un préstamo liquidado.'}), 400
        
        nuevo_monto = to_dec(data.get('monto_total', prestamo.monto_total))
        nuevo_descuento = to_dec(data.get('descuento_semanal', prestamo.descuento_semanal))
        nuevo_plazo = int(data.get('plazo_semanas', prestamo.plazo_semanas))
        
        if nuevo_monto <= 0 or nuevo_descuento <= 0 or nuevo_plazo <= 0:
            return jsonify({'success': False, 'message': 'El monto, plazo y descuento deben ser mayores a cero.'}), 400
        
        # Calcular lo ya abonado para validar y recalcular
        total_abonado = sum(
            (to_dec(a.monto) for a in AbonoPrestamo.query.filter_by(prestamo_id=id).all()),
            Decimal('0')
        )
        
        # Impedir reducir el monto por debajo de lo ya pagado
        if nuevo_monto < total_abonado:
            return jsonify({
                'success': False, 
                'message': f'El monto total (${nuevo_monto:.2f}) no puede ser menor a lo ya abonado (${total_abonado:.2f}).'
            }), 400
        
        prestamo.monto_total = nuevo_monto
        prestamo.plazo_semanas = nuevo_plazo
        prestamo.descuento_semanal = nuevo_descuento
        prestamo.motivo = data.get('motivo', prestamo.motivo)
        prestamo.frecuencia = data.get('frecuencia', prestamo.frecuencia)
        
        # Recalcular monto restante con base en lo abonado
        prestamo.monto_restante = nuevo_monto - total_abonado
        
        if data.get('fecha_inicio'):
            prestamo.fecha_inicio = datetime.strptime(data['fecha_inicio'], '%Y-%m-%d').date()
        
        db.session.commit()
        _recalcular_prenominas_abiertas(prestamo.trabajador_id)
        
        log_action(f'editar_prestamo: Préstamo #{id} modificado. Nuevo total: ${nuevo_monto:.2f}, Restante: ${prestamo.monto_restante:.2f}')
        return jsonify({'success': True, 'message': 'Préstamo actualizado correctamente.'})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error al editar préstamo: {traceback.format_exc()}")
        return jsonify({'success': False, 'message': 'Ocurrió un error al editar el préstamo.'}), 500

@bp.route('/abonar/<int:id>', methods=['POST'])
@login_required
@admin_required
@limiter.limit("10 per minute")
def abonar(id):
    try:
        data = request.get_json()
        if not data or 'monto' not in data:
            return jsonify({'success': False, 'message': 'Datos inválidos o monto no proporcionado.'}), 400
        
        monto_abono = Decimal(str(data['monto']))
        
        if monto_abono <= 0:
            return jsonify({'success': False, 'message': 'El monto del abono debe ser mayor a cero.'}), 400
        
        prestamo = Prestamo.query.get_or_404(id)
        
        if prestamo.estado == 'LIQUIDADO':
            return jsonify({'success': False, 'message': 'Préstamo ya liquidado.'}), 400
        
        prestamo.monto_restante = max(Decimal('0'), to_dec(prestamo.monto_restante) - monto_abono)
        
        if prestamo.monto_restante <= 0:
            prestamo.monto_restante = 0
            prestamo.estado = 'LIQUIDADO'
            prestamo.activo = False
        
        from flask import session
        abono = AbonoPrestamo(
            prestamo_id=prestamo.id,
            monto=monto_abono,
            fecha_abono=datetime.now().date(),
            tipo='MANUAL',
            registrado_por_id=session.get('user_id'),
            notas='Abono extraordinario manual'
        )
        db.session.add(abono)
        
        db.session.commit()
        _recalcular_prenominas_abiertas(prestamo.trabajador_id)
        
        log_action(f'abonar_prestamo: Abono de ${monto_abono} al préstamo #{id}. Restante: ${prestamo.monto_restante}')
        return jsonify({
            'success': True, 
            'message': f'Abono de ${monto_abono:.2f} aplicado. Restante: ${float(prestamo.monto_restante):.2f}',
            'monto_restante': float(prestamo.monto_restante),
            'estado': prestamo.estado
        })
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error al abonar préstamo: {traceback.format_exc()}")
        return jsonify({'success': False, 'message': 'Ocurrió un error al registrar el abono.'}), 500

@bp.route('/liquidar/<int:id>', methods=['POST'])
@login_required
@admin_required
@limiter.limit("10 per minute")
def liquidar(id):
    try:
        prestamo = Prestamo.query.get_or_404(id)
        saldo_anterior = prestamo.monto_restante
        
        from flask import session
        if saldo_anterior > 0:
            abono = AbonoPrestamo(
                prestamo_id=prestamo.id,
                monto=saldo_anterior,
                fecha_abono=datetime.now().date(),
                tipo='MANUAL',
                registrado_por_id=session.get('user_id'),
                notas='Liquidación total manual'
            )
            db.session.add(abono)
            
        prestamo.monto_restante = 0
        prestamo.estado = 'LIQUIDADO'
        prestamo.activo = False
        db.session.commit()
        
        _recalcular_prenominas_abiertas(prestamo.trabajador_id)
        
        log_action(f'liquidar_prestamo: Préstamo #{id} liquidado manualmente (monto liquidado: ${saldo_anterior})')
        return jsonify({'success': True, 'message': 'Préstamo marcado como liquidado.'})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error al liquidar préstamo: {traceback.format_exc()}")
        return jsonify({'success': False, 'message': str(e)}), 500

@bp.route('/get/<int:id>', methods=['GET'])
@login_required
@admin_required
def get_detalles(id):
    prestamo = Prestamo.query.get_or_404(id)
    abonos_list = []
    
    for a in prestamo.abonos:
        abonos_list.append({
            'identificador': a.id,
            'fecha_abono': a.fecha_abono.strftime('%d/%m/%Y'),
            'monto': float(a.monto),
            'tipo': a.tipo,
            'notas': a.notas or ''
        })
        
    return jsonify({
        'success': True,
        'monto_total': float(prestamo.monto_total),
        'monto_restante': float(prestamo.monto_restante),
        'plazo': prestamo.plazo_semanas,
        'descuento': float(prestamo.descuento_semanal),
        'estado': prestamo.estado,
        'abonos': abonos_list
    })


def _recalcular_prenominas_abiertas(trabajador_id):
    """Recalcula todas las prenóminas ABIERTAS de un trabajador usando la lógica compartida."""
    prenominas_abiertas = Prenomina.query.filter_by(
        trabajador_id=trabajador_id, 
        estado='ABIERTA'
    ).all()
    
    for p in prenominas_abiertas:
        recalcular_totales_prenomina(p)
    if prenominas_abiertas:
        db.session.commit()
