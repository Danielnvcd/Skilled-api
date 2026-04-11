# Constantes compartidas entre Flask y FastAPI.
# Deben mantenerse sincronizadas con la configuración de app/__init__.py.

# Tiempo de vida de la sesión en segundos (15 días).
# Debe coincidir con PERMANENT_SESSION_LIFETIME en app/__init__.py.
SESSION_LIFETIME_SECONDS = 15 * 86400
