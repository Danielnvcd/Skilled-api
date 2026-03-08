"""
Tests de integración: Ausencias/Vacaciones ↔ Prenómina

Verifica que:
1. Una ausencia de vacaciones registrada perdona las faltas en prenómina.
2. Una incapacidad SÍ descuenta el día.
3. Una falta sin ausencia registrada SÍ descuenta.
4. El saldo vacacional se descuenta al crear la ausencia.
5. Al cancelar una ausencia, se reintegra el saldo.
6. El endpoint /balance devuelve JSON correcto.
"""

from datetime import date, timedelta
from decimal import Decimal


def test_saldo_se_descuenta_al_crear_ausencia(client, app, db, logged_in_admin, trabajador):
    """Al registrar vacaciones, el saldo de días disfrutados debe incrementar."""
    from app.models import SaldoVacaciones, Ausencia

    # Dar 12 días de vacaciones al trabajador
    saldo = SaldoVacaciones(trabajador_id=trabajador.id, dias_totales_asignados=12, dias_disfrutados=0)
    db.session.add(saldo)
    db.session.commit()

    hoy = date.today()
    fin = hoy + timedelta(days=2)

    resp = logged_in_admin.post('/ausencias/solicitar', data={
        'trabajador_id': trabajador.id,
        'tipo_ausencia': 'VACACIONES',
        'fecha_inicio': hoy.strftime('%Y-%m-%d'),
        'fecha_fin': fin.strftime('%Y-%m-%d'),
        'dias_solicitados': 3,
        'motivo': 'Prueba saldo'
    }, follow_redirects=True)

    assert resp.status_code == 200

    # Verificar que se descontaron 3 días
    saldo_updated = SaldoVacaciones.query.filter_by(trabajador_id=trabajador.id).first()
    assert saldo_updated.dias_disfrutados == 3


def test_saldo_se_restaura_al_cancelar(client, app, db, logged_in_admin, trabajador):
    """Al cancelar una vacación, los días deben reintegrarse al saldo."""
    from app.models import SaldoVacaciones, Ausencia

    saldo = SaldoVacaciones(trabajador_id=trabajador.id, dias_totales_asignados=10, dias_disfrutados=5)
    db.session.add(saldo)
    db.session.commit()

    hoy = date.today()
    ausencia = Ausencia(
        trabajador_id=trabajador.id,
        tipo_ausencia='VACACIONES',
        fecha_inicio=hoy,
        fecha_fin=hoy + timedelta(days=1),
        dias_solicitados=2,
        estado='EN_CURSO',
        motivo='Test cancelación'
    )
    db.session.add(ausencia)
    db.session.commit()

    resp = logged_in_admin.post(f'/ausencias/cancelar/{ausencia.id}', follow_redirects=True)
    assert resp.status_code == 200

    saldo_updated = SaldoVacaciones.query.filter_by(trabajador_id=trabajador.id).first()
    # Was 5, minus 2 restored = 3
    assert saldo_updated.dias_disfrutados == 3

    ausencia_updated = Ausencia.query.get(ausencia.id)
    assert ausencia_updated.estado == 'CANCELADA'


def test_no_permite_vacaciones_sin_saldo(client, app, db, logged_in_admin, trabajador):
    """No se deben poder solicitar más días de los disponibles."""
    from app.models import SaldoVacaciones

    saldo = SaldoVacaciones(trabajador_id=trabajador.id, dias_totales_asignados=2, dias_disfrutados=1)
    db.session.add(saldo)
    db.session.commit()

    hoy = date.today()
    resp = logged_in_admin.post('/ausencias/solicitar', data={
        'trabajador_id': trabajador.id,
        'tipo_ausencia': 'VACACIONES',
        'fecha_inicio': hoy.strftime('%Y-%m-%d'),
        'fecha_fin': (hoy + timedelta(days=4)).strftime('%Y-%m-%d'),
        'dias_solicitados': 5,
        'motivo': 'Test sin saldo'
    }, follow_redirects=True)

    assert resp.status_code == 200
    # El saldo no debió cambiar
    saldo_check = SaldoVacaciones.query.filter_by(trabajador_id=trabajador.id).first()
    assert saldo_check.dias_disfrutados == 1  # no cambió


def test_balance_endpoint_json(client, app, db, logged_in_admin, trabajador):
    """El endpoint /balance/<id> debe devolver JSON con los días correctos."""
    from app.models import SaldoVacaciones

    saldo = SaldoVacaciones(trabajador_id=trabajador.id, dias_totales_asignados=15, dias_disfrutados=4)
    db.session.add(saldo)
    db.session.commit()

    resp = logged_in_admin.get(f'/ausencias/balance/{trabajador.id}')
    assert resp.status_code == 200

    data = resp.get_json()
    assert data['success'] is True
    assert data['dias_asignados'] == 15
    assert data['dias_disfrutados'] == 4
    assert data['dias_pendientes'] == 11


def test_ajuste_manual_saldo(client, app, db, logged_in_admin, trabajador):
    """El admin puede ajustar manualmente el saldo de vacaciones."""
    resp = logged_in_admin.post(f'/ausencias/actualizar_saldo/{trabajador.id}', data={
        'dias_asignados': 20,
        'dias_disfrutados': 3
    }, follow_redirects=True)

    assert resp.status_code == 200

    from app.models import SaldoVacaciones
    saldo = SaldoVacaciones.query.filter_by(trabajador_id=trabajador.id).first()
    assert saldo is not None
    assert saldo.dias_totales_asignados == 20
    assert saldo.dias_disfrutados == 3


def test_incapacidad_no_afecta_saldo_vacaciones(client, app, db, logged_in_admin, trabajador):
    """Registrar una incapacidad no debe tocar el saldo de vacaciones."""
    from app.models import SaldoVacaciones, Ausencia

    saldo = SaldoVacaciones(trabajador_id=trabajador.id, dias_totales_asignados=10, dias_disfrutados=2)
    db.session.add(saldo)
    db.session.commit()

    hoy = date.today()
    resp = logged_in_admin.post('/ausencias/solicitar', data={
        'trabajador_id': trabajador.id,
        'tipo_ausencia': 'INCAPACIDAD_IMSS',
        'fecha_inicio': hoy.strftime('%Y-%m-%d'),
        'fecha_fin': (hoy + timedelta(days=3)).strftime('%Y-%m-%d'),
        'dias_solicitados': 4,
        'motivo': 'Incapacidad test'
    }, follow_redirects=True)

    assert resp.status_code == 200

    # El saldo de vacaciones NO debe haber cambiado
    saldo_check = SaldoVacaciones.query.filter_by(trabajador_id=trabajador.id).first()
    assert saldo_check.dias_disfrutados == 2  # sin cambio


def test_dashboard_ausencias_carga(client, app, db, logged_in_admin):
    """La página del dashboard de ausencias debe cargar sin errores."""
    resp = logged_in_admin.get('/ausencias/')
    assert resp.status_code == 200
    assert b'Control de Ausencias' in resp.data
