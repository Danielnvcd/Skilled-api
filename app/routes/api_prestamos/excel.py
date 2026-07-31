"""Exportación Excel de préstamos por trabajador."""
from datetime import datetime
from io import BytesIO

import pandas as pd
from flask import jsonify

from app.models import Prestamo, Trabajador
from app.routes._api_helpers import _aplicar_estilos_y_retornar, _sanitize_rows, require_admin
from app.routes.api_auth import jwt_required

from ._core import bp
from app.extensions import db


@bp.route('/trabajadores/<int:trabajador_id>/excel', methods=['GET'])
@jwt_required
def excel_prestamos_trabajador(trabajador_id):
    """Exporta a Excel todos los préstamos de un trabajador (activos y liquidados)
    con el mismo formato del blueprint clásico (header azul, zebra, fila TOTAL)."""
    denied = require_admin()
    if denied:
        return denied

    trabajador = db.get_or_404(Trabajador, trabajador_id)
    prestamos = (
        Prestamo.query
        .filter_by(trabajador_id=trabajador.id)
        .order_by(Prestamo.creado_en.desc())
        .all()
    )
    if not prestamos:
        return jsonify({'error': 'Este trabajador no tiene préstamos registrados.'}), 404

    output = BytesIO()
    writer = pd.ExcelWriter(output, engine='openpyxl')

    data = []
    for pr in prestamos:
        total_abonado = sum(float(a.monto or 0) for a in pr.abonos)
        saldo = float(pr.monto_total or 0) - total_abonado
        data.append({
            'ID Préstamo': pr.id,
            'Fecha Registro': pr.creado_en.strftime('%Y-%m-%d') if pr.creado_en else '',
            'Monto Original': float(pr.monto_total or 0),
            'Total Abonado': total_abonado,
            'Saldo Restante': saldo,
            'Descuento Semanal': float(pr.descuento_semanal or 0),
            'Estado': pr.estado,
            'Motivo': pr.motivo or '',
        })

    if data:
        data.append({
            'ID Préstamo': 'TOTAL',
            'Fecha Registro': '',
            'Monto Original': sum(d['Monto Original'] for d in data),
            'Total Abonado': sum(d['Total Abonado'] for d in data),
            'Saldo Restante': sum(d['Saldo Restante'] for d in data),
            'Descuento Semanal': sum(d['Descuento Semanal'] for d in data),
            'Estado': '',
            'Motivo': '',
        })

    df = pd.DataFrame(_sanitize_rows(data))
    df.to_excel(writer, sheet_name='Préstamos', index=False)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    nombre_file = f"Prestamos_{trabajador.no_empleado}_{timestamp}.xlsx"
    return _aplicar_estilos_y_retornar(writer, output, nombre_file)
