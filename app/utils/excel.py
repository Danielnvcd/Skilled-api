"""Helpers compartidos para importación tolerante de Excel.

Centralizan la lógica "a prueba de tontos" usada por los endpoints
`/api/v1/productos/importar` (inventario) y `/api/trabajadores/importar`:

  norm_header(s)         normaliza un header (lower, sin acentos, sin dobles
                          espacios) para comparar de forma robusta.
  cell_str(v, maxlen)    convierte celda de pandas a str limpio (NaN → '',
                          strip, truncado si maxlen). Sanitiza prefijos
                          peligrosos de Excel para anti-fórmula injection.
  cell_number(v, default) parsea celda a float aceptando '$1,234.56'/'$50'/etc.
                          Devuelve (valor, error_str_o_None).
  cell_date(v)           parsea celda a `date` aceptando objetos datetime y
                          strings en múltiples formatos comunes (MX y ISO).
                          Devuelve (date_o_None, error_str_o_None).
  find_header_row(df, expected, required, max_scan=10)
                          escanea las primeras `max_scan` filas buscando
                          aquella cuya intersección de columnas cubre todos
                          los headers `required`. Devuelve
                          (row_idx_o_None, {col_idx: header_oficial}).
  read_cell(row, column_map, header)
                          lee la celda de `row` cuyo header oficial es
                          `header`, usando el mapping armado por
                          `find_header_row`. Devuelve `None` si no aplica.

Todos los helpers funcionan con cualquier DataFrame leído por pandas con
`header=None` (la forma recomendada para que la fila de encabezados se
detecte por contenido y no por posición).
"""
from __future__ import annotations

import math
import re
import unicodedata
from datetime import date, datetime
from typing import Iterable

# Caracteres que Excel interpreta como inicio de fórmula. Anteponer ' al
# guardar neutraliza la fórmula sin perder el valor visible.
_EXCEL_FORMULA_PREFIXES = ('=', '+', '-', '@', '\t', '\r')


def norm_header(s) -> str:
    """Normaliza un header para comparación tolerante.

    Aplica NFKD + strip de acentos, baja a minúsculas y colapsa espacios
    repetidos. Idempotente. Acepta cualquier tipo (lo convierte a str).
    """
    s = unicodedata.normalize('NFKD', str(s or '')).encode('ascii', 'ignore').decode('ascii')
    return ' '.join(s.strip().lower().split())


def _is_nan(value) -> bool:
    """True si value es float NaN. Robusto contra tipos no-float."""
    try:
        return isinstance(value, float) and math.isnan(value)
    except Exception:
        return False


def cell_str(value, maxlen: int | None = None) -> str:
    """Convierte una celda de pandas a string limpio.

    - None / NaN / 'nan' (la cadena literal que pandas produce) → ''.
    - strip + colapso de espacios internos repetidos.
    - Si maxlen está, trunca al límite.
    - Anti formula-injection: si el primer char es =, +, -, @, TAB o CR,
      antepone ' para que Excel lo trate como texto literal.
    """
    if value is None or _is_nan(value):
        return ''
    s = str(value).strip()
    if s.lower() == 'nan':
        return ''
    if s and s[0] in _EXCEL_FORMULA_PREFIXES:
        s = "'" + s
    if maxlen and len(s) > maxlen:
        s = s[:maxlen]
    return s


def cell_number(value, default: float = 0.0) -> tuple[float, str | None]:
    """Parsea celda a float aceptando formato MX ('$1,234.56', '50 ', etc.).

    Devuelve `(valor, None)` si OK, `(default, "razón")` si no se pudo.
    Strings vacíos / NaN devuelven `(default, None)` sin error.
    """
    if value is None or value == '' or _is_nan(value):
        return default, None
    if isinstance(value, (int, float)):
        return float(value), None
    s = str(value).strip().replace(',', '').replace('$', '').replace(' ', '')
    if not s:
        return default, None
    try:
        return float(s), None
    except ValueError:
        return default, f'no es un número válido ({value!r})'


# Formatos aceptados por cell_date (en orden de prueba)
_DATE_FORMATS = (
    '%Y-%m-%d',     # ISO oficial — el recomendado en la plantilla
    '%d/%m/%Y',     # MX común
    '%d-%m-%Y',
    '%d.%m.%Y',
    '%Y/%m/%d',
    '%m/%d/%Y',     # US (último para evitar ambigüedad con MX)
)


def cell_date(value) -> tuple[date | None, str | None]:
    """Parsea celda a `datetime.date` aceptando múltiples formatos.

    - Si pandas ya entregó un Timestamp/datetime/date, lo devuelve directo.
    - Si es string, prueba los formatos comunes (ISO primero, después MX, US).
    - NaN / '' / None devuelven `(None, None)`.
    - Si no matchea ninguno, devuelve `(None, "razón")`.
    """
    if value is None or value == '' or _is_nan(value):
        return None, None
    # pandas.Timestamp tiene .date(); datetime también; date se queda igual.
    if hasattr(value, 'date') and callable(getattr(value, 'date', None)):
        try:
            return value.date(), None
        except Exception:
            pass
    if isinstance(value, date) and not isinstance(value, datetime):
        return value, None
    s = str(value).strip()
    if not s or s.lower() == 'nan':
        return None, None
    # Quitar parte de hora si vino "YYYY-MM-DD HH:MM:SS"
    s_date = s.split(' ')[0]
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s_date, fmt).date(), None
        except ValueError:
            continue
    return None, f'fecha inválida ({value!r}) — usa YYYY-MM-DD'


def find_header_row(
    raw_df,
    expected_headers: Iterable[str],
    required_headers: Iterable[str],
    max_scan: int = 10,
) -> tuple[int | None, dict[int, str]]:
    """Busca la fila de encabezados dentro de un DataFrame leído con header=None.

    Args:
        raw_df: DataFrame de pandas leído con `header=None` (filas crudas).
        expected_headers: lista de TODOS los headers oficiales esperados (los que
                          la plantilla genera).
        required_headers: subset de `expected_headers` que DEBE estar presente
                          para considerar la fila como header válido. Los demás
                          son opcionales.
        max_scan: cuántas filas escanear desde el inicio. Default 10 (cubre
                  plantillas con título + instrucciones + separador antes del
                  header en fila 4).

    Returns:
        (row_idx, column_map) donde:
          row_idx = índice 0-based de la fila de headers (o None si no se halló).
          column_map = {idx_columna_excel_0based: header_oficial} de los
                       headers que sí matchearon. Los headers no presentes
                       quedan fuera del map (caller usa `read_cell` y obtiene
                       None automáticamente).
    """
    expected_norm = {norm_header(h): h for h in expected_headers}
    required_set = set(required_headers)
    upper = min(max_scan, len(raw_df))
    for ridx in range(upper):
        row_vals = [norm_header(v) for v in raw_df.iloc[ridx].tolist()]
        matches = {expected_norm[h]: i for i, h in enumerate(row_vals) if h in expected_norm}
        if required_set.issubset(matches.keys()):
            column_map = {matches[h]: h for h in matches}
            return ridx, column_map
    return None, {}


def read_cell(row, column_map: dict[int, str], header_oficial: str):
    """Lee la celda de `row` cuyo header oficial es `header_oficial`.

    `column_map` viene de `find_header_row`. Si el header no estaba en el
    Excel del usuario, devuelve None (las columnas opcionales caen aquí).
    """
    for col_idx, oficial in column_map.items():
        if oficial == header_oficial:
            return row.iloc[col_idx] if col_idx < len(row) else None
    return None


# ─── Normalización de valores con vocabularios cerrados ─────────────────────

def _strip_accents_upper(s: str) -> str:
    s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode('ascii')
    return s.strip().upper()


def normalize_choice(value, choices_map: dict[str, str]) -> str:
    """Normaliza un valor contra un mapeo de aliases → valor canónico.

    Ejemplo:
        sexo = normalize_choice('masculino', {
            'M': 'M', 'MASCULINO': 'M', 'HOMBRE': 'M',
            'F': 'F', 'FEMENINO': 'F', 'MUJER': 'F',
        })
        # → 'M'

    Comparación case-insensitive, sin acentos, strip. Si no matchea,
    devuelve el valor original (stripped) — el caller decide si validar.
    """
    if value is None or value == '':
        return ''
    raw = str(value).strip()
    if not raw:
        return ''
    key = _strip_accents_upper(raw)
    return choices_map.get(key, raw)


# Mapeos de uso común — exportables para que los importadores reusen el
# vocabulario sin reinventarlo.
SEXO_ALIASES = {
    'M': 'M', 'MASCULINO': 'M', 'HOMBRE': 'M', 'H': 'M', 'MALE': 'M',
    'F': 'F', 'FEMENINO': 'F', 'MUJER': 'F', 'FEMENINA': 'F', 'FEMALE': 'F',
}

TIPO_NOMINA_ALIASES = {
    'SEMANAL': 'Semanal', 'SEM': 'Semanal',
    'POR HORA': 'Por hora', 'HORA': 'Por hora', 'HRS': 'Por hora', 'PORHORA': 'Por hora',
    'CUADRADO': 'Cuadrado', 'CUAD': 'Cuadrado',
}

TIPO_PAGO_ALIASES = {
    'EFECTIVO': 'EFECTIVO', 'CASH': 'EFECTIVO',
    'TRANSFERENCIA': 'TRANSFERENCIA', 'TRANSF': 'TRANSFERENCIA',
    'SPEI': 'TRANSFERENCIA', 'DEPOSITO': 'TRANSFERENCIA', 'BANCO': 'TRANSFERENCIA',
}

LENTES_ALIASES = {
    'SI': 'Sí', 'SÍ': 'Sí', 'S': 'Sí', 'YES': 'Sí', 'Y': 'Sí', '1': 'Sí', 'TRUE': 'Sí',
    'NO': 'No', 'N': 'No', 'FALSE': 'No', '0': 'No',
}

ESTADO_CIVIL_ALIASES = {
    'SOLTERO': 'Soltero(a)', 'SOLTERA': 'Soltero(a)', 'SOLTERO(A)': 'Soltero(a)',
    'CASADO': 'Casado(a)', 'CASADA': 'Casado(a)', 'CASADO(A)': 'Casado(a)',
    'UNION LIBRE': 'Unión Libre', 'UNIÓN LIBRE': 'Unión Libre', 'UL': 'Unión Libre',
    'DIVORCIADO': 'Divorciado(a)', 'DIVORCIADA': 'Divorciado(a)', 'DIVORCIADO(A)': 'Divorciado(a)',
    'VIUDO': 'Viudo(a)', 'VIUDA': 'Viudo(a)', 'VIUDO(A)': 'Viudo(a)',
}


def normalize_curp(value) -> str:
    """Limpia CURP: uppercase, sin espacios, quita caracteres no válidos.
    NO valida formato (eso lo hace _CURP_RE) — solo deja el string en la forma
    que el regex espera. CURPs con 'ñ' en posición se mantienen como 'N'."""
    s = re.sub(r'\s+', '', str(value or '').upper())
    return s


def normalize_rfc(value) -> str:
    """Limpia RFC: uppercase, sin espacios. Acepta personas físicas (13) y morales (12)."""
    s = re.sub(r'\s+', '', str(value or '').upper())
    return s
