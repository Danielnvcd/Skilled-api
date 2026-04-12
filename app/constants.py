# Constantes compartidas entre Flask y FastAPI.
# Deben mantenerse sincronizadas con la configuración de app/__init__.py.

# Access token (cookie de sesión Flask): 20 minutos.
# Debe coincidir con PERMANENT_SESSION_LIFETIME en app/__init__.py.
SESSION_LIFETIME_SECONDS = 20 * 60

# Refresh token: 7 días. Solo se emite si el usuario marca "recordarme".
REFRESH_TOKEN_LIFETIME_DAYS = 7
