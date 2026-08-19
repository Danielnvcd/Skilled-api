"""Qué entrada del AuditLog cuenta como «evento de seguridad».

Una sola definición, usada por los dos consumidores:

  · `api_sistemas/endpoints.py` → filtra en SQL (ILIKE) el listado que pinta
    Sistemas → Eventos de seguridad.
  · `realtime.py` → decide si un AuditLog recién insertado merece empujar
    `seguridad:new` por WebSocket al rol `sistemas`.

Vive en un módulo propio y SIN imports para que `realtime.py` pueda usarlo sin
arrastrar el blueprint de sistemas (import circular).
"""

# Fragmentos que marcan una entrada del AuditLog como evento de seguridad.
# Se filtran en SQL (ILIKE) para no traer la tabla entera a memoria: crece sin
# límite y ya tiene índice por created_at.
PATRONES_SEGURIDAD = (
    'login fallido',
    'login rechazado',
    '2fa',
    'backup code',
    'refresh token',
    'revocó',
    'contraseña',
    'desactivó',
    'reactivó',
    'creó usuario',
    'antivirus',
)


def es_evento_de_seguridad(accion: str | None) -> bool:
    """Equivalente en Python del filtro SQL `ILIKE '%patrón%'`.

    Los patrones ya están en minúsculas, así que bajar la acción reproduce
    exactamente la semántica de ILIKE (subcadena, sin distinguir mayúsculas).
    Si las dos formas divergen, el push llegaría para entradas que el listado
    no muestra —o al revés— y la pantalla parpadearía sin cambiar nada.
    """
    if not accion:
        return False
    texto = accion.lower()
    return any(p in texto for p in PATRONES_SEGURIDAD)
