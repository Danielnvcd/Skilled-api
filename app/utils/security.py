"""Validación de contraseñas y sanitización anti log-forging."""
import re


# Largo mínimo. Subido de 8 a 12: contra un atacante que ya tiene el hash, la
# longitud es lo que realmente encarece el crackeo — la complejidad de
# caracteres aporta mucho menos de lo que sugiere la intuición (NIST SP 800-63B
# recomienda priorizar largo sobre reglas de composición).
#
# Solo aplica a contraseñas NUEVAS: `is_strong_password` se llama al crear
# usuario, al cambiar la propia y al resetear desde admin. Las contraseñas ya
# existentes siguen sirviendo para iniciar sesión — nadie queda fuera.
PASSWORD_MIN_LEN = 12

# Contraseñas obvias que cumplen TODAS las reglas de composición y aun así son
# de las primeras que prueba cualquier ataque de diccionario. Bloquearlas cuesta
# nada y cierra el hueco de "cumple la política pero es adivinable".
# Se comparan en minúsculas y sin espacios.
_PASSWORDS_PROHIBIDAS = {
    'password1!', 'password123!', 'passw0rd123!', 'contraseña1!',
    'contrasena1!', 'contrasena123!', 'qwerty123!', 'qwerty12345!',
    'admin1234!', 'administrador1!', 'bienvenido1!', 'bienvenido123!',
    'abcd1234!', 'abc12345!', 'usuario123!', 'sistema123!', 'nomina123!',
    'skilled123!', 'skilled1234!', 'p@ssw0rd', 'p@ssw0rd123',
}


def is_strong_password(password):
    if password is None:
        return False
    if len(password) < PASSWORD_MIN_LEN:
        return False
    if not re.search(r"[A-Z]", password):
        return False
    if not re.search(r"[a-z]", password):
        return False
    if not re.search(r"[0-9]", password):
        return False
    if not re.search(r"[^A-Za-z0-9]", password):
        return False
    if password.strip().lower() in _PASSWORDS_PROHIBIDAS:
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
