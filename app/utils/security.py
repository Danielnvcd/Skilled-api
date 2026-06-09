"""Validación de contraseñas y sanitización anti log-forging."""
import re


def is_strong_password(password):
    if len(password) < 8:
        return False
    if not re.search(r"[A-Z]", password):
        return False
    if not re.search(r"[a-z]", password):
        return False
    if not re.search(r"[0-9]", password):
        return False
    if not re.search(r"[^A-Za-z0-9]", password):
        return False
    return True


# MED-03: sanitizador anti log-forging. Si una acción incluye \n / \r (porque
# vino de un campo controlado por usuario), un atacante podría escribir líneas
# falsas en el log que parezcan acciones de otros usuarios. Convertimos los
# saltos a literales y rechazamos chars de control no imprimibles.
def _safe_log_value(value, max_len: int = 200) -> str:
    s = str(value)
    s = s.replace('\r', '\\r').replace('\n', '\\n').replace('\t', '\\t')
    # Filtra otros control chars (excepto los visibles ya manejados)
    s = ''.join(ch if (ch >= ' ' or ch in ('\\',)) else '?' for ch in s)
    return s[:max_len]
