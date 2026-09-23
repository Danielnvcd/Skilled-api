"""Integración con lectores biométricos Hikvision vía ISAPI.

Capa de servicio: las rutas Flask llaman aquí y nunca hablan HTTP con el equipo.

    client.py      ClienteHikvision — digest, timeouts, errores, paginación
    usuarios.py    alta/consulta/baja de usuarios y rostros
    fotos.py       foto del perfil del ERP → JPEG que el lector acepta
    eventos.py     consulta de eventos por serial y lectura del alertStream
    ingesta.py     guardar eventos en el ERP sin duplicar (toca la base)
    escucha.py     proceso de escucha en tiempo real (`flask hikvision escuchar`)
    asistencia.py  lectura de checadas (esqueleto, fase 2)
    errores.py     jerarquía de errores con mensaje para el usuario y detalle
                   para el log — ninguno contiene credenciales

Todo lo de aquí se validó contra un DS-K1T342MFWX-E1 con firmware V3.16.1;
los límites del equipo (`employeeNo` ≤ 32, páginas de 30) se releen de sus
capabilities en vez de darse por supuestos.
"""
from .client import ClienteHikvision, validar_host, validar_puerto
from .errores import (
    ErrorAutenticacion,
    ErrorConexion,
    ErrorConfiguracion,
    ErrorDispositivo,
    ErrorFoto,
    ErrorHikvision,
)

__all__ = [
    'ClienteHikvision',
    'validar_host',
    'validar_puerto',
    'ErrorHikvision',
    'ErrorConfiguracion',
    'ErrorConexion',
    'ErrorAutenticacion',
    'ErrorDispositivo',
    'ErrorFoto',
]
