"""Tests unitarios para funciones de utilidad en app/utils.py"""
from datetime import time
from app.utils import turnos_se_traslapan, calcular_horas_productivas


class TestTurnosSeTraslapan:
    """Tests para la función de detección de traslape de horarios."""

    def test_traslape_simple(self):
        """Dos turnos diurnos que se solapan parcialmente."""
        assert turnos_se_traslapan(
            time(9, 0), time(17, 0),
            time(13, 0), time(21, 0)
        ) is True

    def test_sin_traslape_diurno(self):
        """Dos turnos diurnos sin contacto."""
        assert turnos_se_traslapan(
            time(8, 0), time(12, 0),
            time(14, 0), time(18, 0)
        ) is False

    def test_traslape_contenido(self):
        """Un turno contenido completamente dentro de otro."""
        assert turnos_se_traslapan(
            time(8, 0), time(20, 0),
            time(10, 0), time(15, 0)
        ) is True

    def test_traslape_medianoche(self):
        """Turno nocturno que cruza medianoche se solapa con otro nocturno."""
        assert turnos_se_traslapan(
            time(22, 0), time(2, 0),
            time(23, 0), time(6, 0)
        ) is True

    def test_sin_traslape_nocturno_vs_diurno(self):
        """Turno nocturno no se traslapa con turno diurno que no toca."""
        assert turnos_se_traslapan(
            time(22, 0), time(2, 0),
            time(8, 0), time(16, 0)
        ) is False

    def test_turnos_identicos(self):
        """Turnos idénticos deben traslaparse."""
        assert turnos_se_traslapan(
            time(9, 0), time(17, 0),
            time(9, 0), time(17, 0)
        ) is True

    def test_turnos_consecutivos_sin_traslape(self):
        """Turno A termina justo cuando empieza B → no hay traslape."""
        assert turnos_se_traslapan(
            time(8, 0), time(14, 0),
            time(14, 0), time(20, 0)
        ) is False

    def test_traslape_un_minuto(self):
        """Traslape de 1 minuto es real."""
        assert turnos_se_traslapan(
            time(8, 0), time(14, 1),
            time(14, 0), time(20, 0)
        ) is True

    def test_medianoche_completo(self):
        """Turno 20:00→04:00 vs 01:00→09:00 → se traslapan (01:00-04:00)."""
        assert turnos_se_traslapan(
            time(20, 0), time(4, 0),
            time(1, 0), time(9, 0)
        ) is True

    def test_dos_nocturnos_separados(self):
        """22:00→00:30 vs 02:00→06:00 → no se traslapan."""
        assert turnos_se_traslapan(
            time(22, 0), time(0, 30),
            time(2, 0), time(6, 0)
        ) is False


class TestCalcularHorasProductivas:
    """Tests para el cálculo de horas productivas."""

    def test_turno_diurno_normal(self):
        """8 horas de turno diurno estándar."""
        result = calcular_horas_productivas(
            time(9, 0), time(17, 0),
            tipo_nomina='Semanal', tomo_comida=False
        )
        assert result == 8.0

    def test_turno_nocturno_cruza_medianoche(self):
        """22:00 a 06:00 = 8 horas."""
        result = calcular_horas_productivas(
            time(22, 0), time(6, 0),
            tipo_nomina='Semanal', tomo_comida=False
        )
        assert result == 8.0

    def test_por_hora_con_comida(self):
        """Tipo 'Por hora' + comida → se descuenta 1 hora."""
        result = calcular_horas_productivas(
            time(8, 0), time(18, 0),
            tipo_nomina='Por hora', tomo_comida=True
        )
        assert result == 9.0  # 10 - 1 hora comida

    def test_por_hora_sin_comida(self):
        """Tipo 'Por hora' sin comida → se cuentan todas."""
        result = calcular_horas_productivas(
            time(8, 0), time(18, 0),
            tipo_nomina='Por hora', tomo_comida=False
        )
        assert result == 10.0

    def test_semanal_con_comida_no_descuenta(self):
        """Tipo 'Semanal' no descuenta comida aunque la tome."""
        result = calcular_horas_productivas(
            time(8, 0), time(18, 0),
            tipo_nomina='Semanal', tomo_comida=True
        )
        assert result == 10.0

    def test_cuadrado_con_comida_no_descuenta(self):
        """Tipo 'Cuadrado' no descuenta comida."""
        result = calcular_horas_productivas(
            time(8, 0), time(18, 0),
            tipo_nomina='Cuadrado', tomo_comida=True
        )
        assert result == 10.0

    def test_entrada_none_retorna_cero(self):
        """Si falta hora_entrada, retorna 0."""
        result = calcular_horas_productivas(
            None, time(17, 0),
            tipo_nomina='Semanal', tomo_comida=False
        )
        assert result == 0.0

    def test_media_hora(self):
        """Turno de 30 minutos."""
        result = calcular_horas_productivas(
            time(14, 0), time(14, 30),
            tipo_nomina='Semanal', tomo_comida=False
        )
        assert result == 0.5
