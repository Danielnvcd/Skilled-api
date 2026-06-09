"""Helpers de cálculo de horas: detección de traslape de turnos y horas productivas."""
from datetime import date, datetime, timedelta


def _time_to_minutes(t):
    """Convierte un time a minutos desde 00:00."""
    return t.hour * 60 + t.minute


def turnos_se_traslapan(entrada_a, salida_a, entrada_b, salida_b):
    """
    Detecta traslape entre dos turnos usando objetos time.
    Maneja correctamente turnos que cruzan medianoche (ej. 22:00 → 02:00).
    """
    a_start = _time_to_minutes(entrada_a)
    a_end = _time_to_minutes(salida_a)
    b_start = _time_to_minutes(entrada_b)
    b_end = _time_to_minutes(salida_b)

    # Si el turno cruza medianoche, extender fin +24h
    if a_end <= a_start:
        a_end += 1440
    if b_end <= b_start:
        b_end += 1440

    # Chequeo directo
    if a_start < b_end and a_end > b_start:
        return True

    # Chequeo con turno B desplazado +24h (cubre caso donde ambos cruzan medianoche
    # o están en "días" distintos dentro de la ventana de 48h)
    if a_start < (b_end + 1440) and a_end > (b_start + 1440):
        return True
    if (a_start + 1440) < b_end and (a_end + 1440) > b_start:
        return True

    return False


def calcular_horas_productivas(hora_entrada, hora_salida, tipo_nomina, tomo_comida):
    """
    Calcula las horas productivas basado en las reglas del diagrama de flujo.
    Args:
        hora_entrada (datetime.time): Hora de inicio de labores
        hora_salida (datetime.time): Hora de fin de labores
        tipo_nomina (str): 'Semanal', 'Por hora', o 'Cuadrado'
        tomo_comida (bool): Si el trabajador tomó la hora de comida o no.
    Returns:
        float: Total de horas productivas.
    """
    if not hora_entrada or not hora_salida:
        return 0.0

    # Crear datetimes auxiliares el mismo día para calcular la diferencia usando atributos time
    h_in = datetime.combine(date.today(), hora_entrada)
    h_out = datetime.combine(date.today(), hora_salida)

    # Si la hora de salida es menor, asumimos que cruzó la medianoche
    if h_out < h_in:
        h_out += timedelta(days=1)

    diff = h_out - h_in
    total_hours = diff.total_seconds() / 3600.0

    # Regla: Si es 'Por hora' y sí tomó comida (asumimos 1 hora por defecto, como en sistemas estándar)
    # según el diagrama: "MENOS la hora de la comida cuando el trabajador goce de ella"
    if tipo_nomina == 'Por hora' and tomo_comida:
        total_hours -= 1.0

    # 'Semanal' y 'Cuadrado' no descuentan comida según la descripción:
    # "Goce o no de la hora de la comida el total de las horas productivas es = a la Resta..."

    return max(0.0, round(total_hours, 2))
