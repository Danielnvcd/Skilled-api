"""Servicios de dominio: lógica que habla con sistemas externos.

Se separa de `app/routes/` (que traduce HTTP) y de `app/utils/` (helpers sin
estado). Un servicio encapsula un sistema de afuera —hoy solo los lectores
Hikvision— para que las rutas Flask no sepan de protocolos, timeouts ni
reintentos.
"""
