"""Tests unitarios para la función _parse_date de trabajadores."""
from datetime import date


class TestParseDate:
    """Tests para el helper _parse_date."""

    def test_fecha_valida(self):
        from app.routes.trabajadores import _parse_date
        assert _parse_date('2025-03-15') == date(2025, 3, 15)

    def test_fecha_vacia(self):
        from app.routes.trabajadores import _parse_date
        assert _parse_date('') is None

    def test_fecha_none(self):
        from app.routes.trabajadores import _parse_date
        assert _parse_date(None) is None

    def test_formato_invalido(self):
        from app.routes.trabajadores import _parse_date
        assert _parse_date('15/03/2025') is None

    def test_texto_aleatorio(self):
        from app.routes.trabajadores import _parse_date
        assert _parse_date('no-es-fecha') is None

    def test_fecha_dia_31(self):
        from app.routes.trabajadores import _parse_date
        assert _parse_date('2025-01-31') == date(2025, 1, 31)

    def test_fecha_bisiesto(self):
        from app.routes.trabajadores import _parse_date
        assert _parse_date('2024-02-29') == date(2024, 2, 29)

    def test_fecha_no_bisiesto(self):
        """29 de feb en año no bisiesto → None."""
        from app.routes.trabajadores import _parse_date
        assert _parse_date('2025-02-29') is None
