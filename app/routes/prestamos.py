from flask import Blueprint, render_template, request, jsonify, current_app
from app.extensions import db
from app.models import Prestamo, Trabajador, Prenomina, DescuentoPrenomina, AbonoPrestamo
from app.utils import login_required, log_action
from datetime import datetime
import traceback

bp = Blueprint('prestamos', __name__, url_prefix='/prestamos')

@bp.route('/')
@login_required
def index():
    prestamos = Prestamo.query.order_by(Prestamo.creado_en.desc()).all()
    trabajadores = Trabajador.query.filter_by(activo=True).order_by(Trabajador.nombre).all()
    return render_template('prestamos.html', prestamos=prestamos, trabajadores=trabajadores)

@bp.route('/crear', methods=['POST'])
@login_required
def crear():
    try:
        data = request.get_json()
        trabajador_id = int(data['trabajador_id'])
        monto_total = float(data['monto_total'])
        plazo_semanas = int(data['plazo_semanas'])
        descuento_semanal = float(data['descuento_semanal'])
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
        return jsonify({'success': False, 'message': str(e)}), 500

@bp.route('/editar/<int:id>', methods=['POST'])
@login_required
def editar(id):
    try:
        data = request.get_json()
        prestamo = Prestamo.query.get_or_404(id)
        
        if prestamo.estado == 'LIQUIDADO':
            return jsonify({'success': False, 'message': 'No se puede editar un préstamo liquidado.'}), 400
        
        prestamo.monto_total = float(data.get('monto_total', prestamo.monto_total))
        prestamo.plazo_semanas = int(data.get('plazo_semanas', prestamo.plazo_semanas))
        prestamo.descuento_semanal = float(data.get('descuento_semanal', prestamo.descuento_semanal))
        prestamo.motivo = data.get('motivo', prestamo.motivo)
        prestamo.frecuencia = data.get('frecuencia', prestamo.frecuencia)
        
        if data.get('fecha_inicio'):
            prestamo.fecha_inicio = datetime.strptime(data['fecha_inicio'], '%Y-%m-%d').date()
        
        db.session.commit()
        _recalcular_prenominas_abiertas(prestamo.trabajador_id)
        
        log_action(f'editar_prestamo: Préstamo #{id} modificado')
        return jsonify({'success': True, 'message': 'Préstamo actualizado correctamente.'})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error al editar préstamo: {traceback.format_exc()}")
        return jsonify({'success': False, 'message': str(e)}), 500

@bp.route('/abonar/<int:id>', methods=['POST'])
@login_required
def abonar(id):
    try:
        data = request.get_json()
        monto_abono = float(data['monto'])
        prestamo = Prestamo.query.get_or_404(id)
        
        if prestamo.estado == 'LIQUIDADO':
            return jsonify({'success': False, 'message': 'Préstamo ya liquidado.'}), 400
        
        prestamo.monto_restante = max(0, float(prestamo.monto_restante) - monto_abono)
        
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
        return jsonify({'success': False, 'message': str(e)}), 500

@bp.route('/liquidar/<int:id>', methods=['POST'])
@login_required
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
    """Recalcula todas las prenóminas ABIERTAS de un trabajador."""
    prenominas_abiertas = Prenomina.query.filter_by(
        trabajador_id=trabajador_id, 
        estado='ABIERTA'
    ).all()
    
    for p in prenominas_abiertas:
        # Sumar descuentos granulares
        total_desc_detalle = sum(float(d.monto) for d in p.descuentos_detalle) if p.descuentos_detalle else 0
        # Sumar depósitos extras
        total_dep_extra = sum(float(d.monto) for d in p.depositos_detalle) if p.depositos_detalle else 0
        
        # Cuotas de préstamos activos
        prestamos_activos = Prestamo.query.filter_by(trabajador_id=trabajador_id, estado='ACTIVO').all()
        total_prestamos = sum(float(pr.descuento_semanal) for pr in prestamos_activos)
        
        p.descuento_prestamos = total_prestamos
        p.depositos_otros = total_dep_extra
        p.descuentos_otros = total_desc_detalle
        
        p.total_percepciones = float(p.salario_base or 0) + float(p.pago_horas_extras or 0) + float(p.pago_viaticos or 0) + float(p.pago_festivos or 0) + float(p.depositos_otros or 0)
        p.total_deducciones = float(p.descuento_infonavit or 0) + float(p.ajuste_inbursa or 0) + float(p.descuento_incidencias or 0) + float(p.descuento_prestamos or 0) + float(p.descuentos_otros or 0)
        p.total_a_pagar = p.total_percepciones - p.total_deducciones
    
    db.session.commit()
