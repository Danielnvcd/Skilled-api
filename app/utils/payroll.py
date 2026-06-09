"""Helpers financieros compartidos: Decimal seguro y recálculo de prenómina."""
from decimal import Decimal


def to_dec(value):
    """Convierte un valor a Decimal de forma segura."""
    if value is None:
        return Decimal('0')
    return Decimal(str(value))


def recalcular_totales_prenomina(prenomina, prestamos_activos=None):
    """Recalcula los totales de una prenómina individual basándose en sus descuentos, depósitos y préstamos activos.

    Acepta `prestamos_activos` pre-cargado para evitar una query por prenómina cuando se
    recalculan varias del mismo trabajador en un loop (ver _recalcular_prenominas_abiertas).
    """
    from app.models import Prestamo

    # Descuentos granulares
    total_desc_detalle = sum((to_dec(d.monto) for d in prenomina.descuentos_detalle), Decimal('0')) if prenomina.descuentos_detalle else Decimal('0')
    # Depósitos extras
    total_dep_extra = sum((to_dec(d.monto) for d in prenomina.depositos_detalle), Decimal('0')) if prenomina.depositos_detalle else Decimal('0')

    # Cuotas de préstamos activos — usa el listado pre-cargado si se pasó como argumento
    if prestamos_activos is None:
        prestamos_activos = Prestamo.query.filter_by(trabajador_id=prenomina.trabajador_id, estado='ACTIVO').all()
    total_prestamos = sum((to_dec(pr.descuento_semanal) for pr in prestamos_activos), Decimal('0'))

    prenomina.descuento_prestamos = total_prestamos
    prenomina.depositos_otros = total_dep_extra
    prenomina.descuentos_otros = total_desc_detalle

    prenomina.total_percepciones = to_dec(prenomina.salario_base) + to_dec(prenomina.pago_horas_extras) + to_dec(prenomina.pago_viaticos) + to_dec(prenomina.pago_festivos) + to_dec(prenomina.depositos_otros)
    prenomina.total_deducciones = to_dec(prenomina.descuento_infonavit) + to_dec(prenomina.ajuste_inbursa) + to_dec(prenomina.descuento_incidencias) + to_dec(prenomina.descuento_prestamos) + to_dec(prenomina.descuentos_otros)
    prenomina.total_a_pagar = prenomina.total_percepciones - prenomina.total_deducciones
    # No hace commit — el caller controla la transacción
