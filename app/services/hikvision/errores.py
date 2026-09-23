"""Errores tipados de la integración Hikvision.

Todos llevan DOS mensajes a propósito:

  `mensaje`  lo que ve el usuario en la UI, en español y accionable.
  `detalle`  lo que va al log del servidor, con el código del fabricante.

Ninguno de los dos contiene jamás credenciales: los constructores reciben texto
ya saneado y `ClienteHikvision` nunca pasa la contraseña a un mensaje. Esa es la
razón de que exista esta jerarquía en vez de reventar con la excepción cruda de
httpx, que sí incluiría la URL completa con usuario embebido.
"""
from __future__ import annotations


class ErrorHikvision(Exception):
    """Raíz de la jerarquía. Capturar esto atrapa cualquier fallo del lector."""

    # Se usa cuando el error no cae en ninguna subclase más específica.
    mensaje_por_defecto = 'El lector no pudo completar la operación.'

    def __init__(self, mensaje: str = '', detalle: str = ''):
        self.mensaje = mensaje or self.mensaje_por_defecto
        self.detalle = detalle or self.mensaje
        super().__init__(self.detalle)


class ErrorConfiguracion(ErrorHikvision):
    """La configuración del dispositivo es inválida (host, puerto, credenciales vacías)."""
    mensaje_por_defecto = 'La configuración del lector no es válida.'


class ErrorConexion(ErrorHikvision):
    """No se pudo llegar al equipo: timeout, DNS, conexión rechazada, red caída."""
    mensaje_por_defecto = (
        'No se pudo conectar con el lector. Revisa que esté encendido y en la misma red.'
    )


class ErrorAutenticacion(ErrorHikvision):
    """El equipo respondió 401. Usuario o contraseña incorrectos.

    OJO: Hikvision bloquea la cuenta ~30 min tras varios intentos fallidos, así
    que ante este error NUNCA se reintenta automáticamente.
    """
    mensaje_por_defecto = (
        'El lector rechazó las credenciales. Verifica el usuario y la contraseña.'
    )


class ErrorDispositivo(ErrorHikvision):
    """El equipo contestó pero con un `statusCode` de fallo."""
    mensaje_por_defecto = 'El lector rechazó la operación.'

    def __init__(self, mensaje: str = '', detalle: str = '', sub_status: str = ''):
        super().__init__(mensaje, detalle)
        self.sub_status = sub_status


class ErrorFoto(ErrorHikvision):
    """La fotografía no sirve para reconocimiento facial.

    No es un fallo del sistema sino del contenido de la imagen: el equipo la
    recibió y su motor de rostro no pudo modelarla.
    """
    mensaje_por_defecto = (
        'La fotografía no permite reconocimiento facial. Se necesita una foto '
        'frontal, bien iluminada y con el rostro completo y despejado.'
    )


# ── Traducción de los códigos del fabricante ────────────────────────────────
# `subStatusCode` que el equipo devuelve y su significado en español. Los que
# vienen marcados como VERIFICADO se observaron en un DS-K1T342MFWX-E1 con
# firmware V3.16.1; el resto son del manual de ISAPI y pueden no dispararse
# nunca en este modelo.
MENSAJES_SUB_STATUS = {
    # VERIFICADO: el rostro de la foto no se pudo modelar.
    'SubpicAnalysisModelingError': ErrorFoto.mensaje_por_defecto,
    'lowFacePicQuality': 'La calidad de la fotografía es demasiado baja para el lector.',
    'faceLibNotExist': 'La biblioteca de rostros del lector no existe.',
    'deviceMemoryLimited': 'El lector se quedó sin memoria para más rostros.',
    'exceedMaxFaceNumber': 'El lector alcanzó su límite de rostros registrados.',
    'employeeNoAlreadyExist': 'Ese número de empleado ya existe en el lector.',
    'invalidEmployeeNo': 'El número de empleado no es válido para el lector.',
    'notSupport': 'El lector no soporta esta operación.',
    'methodNotAllowed': 'El lector no acepta ese método para esta operación.',
}


def traducir_sub_status(sub_status: str) -> str:
    """Mensaje en español para un `subStatusCode`, o uno genérico si no se conoce."""
    return MENSAJES_SUB_STATUS.get(sub_status, '')
