"""Preparación de la fotografía del empleado para el lector Hikvision.

El ERP guarda las fotos de perfil en WebP (ver `_save_profile_picture` en
`app/routes/api_trabajadores/_core.py`), pero el equipo solo acepta JPG y PNG
—confirmado en `GET /ISAPI/Intelligent/FDLib/capabilities`, campo
`facePicFormat: ["jpg","png"]`—. Así que hay que convertir.

Reglas que se siguen aquí:

  · La foto ORIGINAL del empleado NUNCA se modifica ni se duplica en disco.
    Se lee, se convierte en memoria y el JPEG se descarta al terminar.
  · Las fotos viejas del sistema son .jpg y .png (la conversión a WebP es
    reciente), así que el lector de entrada acepta los tres formatos.
  · Se reduce hasta caber en el tope de bytes del equipo, bajando calidad y
    luego resolución. Nunca por debajo del mínimo de píxeles útil para modelar
    un rostro.
"""
from __future__ import annotations

import hashlib
import io

from .errores import ErrorFoto

# Tope de bytes del JPEG que se manda al equipo. El firmware no publica este
# número en `capabilities`, así que se usa el valor conservador que documenta
# Hikvision para los terminales de rostro. Si el equipo aceptara más, lo único
# que pasa es que mandamos una foto algo más comprimida de lo necesario.
MAX_BYTES_FOTO = 200 * 1024

# Por debajo de esto el motor de rostro del equipo no tiene con qué trabajar.
MIN_LADO_PX = 80
# Cota superior: más resolución no mejora el modelado y solo gasta bytes.
MAX_LADO_PX = 960

_CALIDADES = (88, 80, 72, 64, 55)


def leer_foto_trabajador(trabajador) -> bytes:
    """Bytes de la foto de perfil del trabajador, desde R2 privado o disco.

    Usa el MISMO mecanismo que ya tiene el ERP (`app/utils/archivos`), donde la
    key guardada en `Trabajador.foto_perfil` resuelve indistintamente a un
    object de R2 o a un archivo local. No se duplica nada.

    El import va dentro de la función a propósito: `app.utils` arrastra el
    antivirus, Pillow y boto3, y esta capa debe poder probarse sin todo eso.
    """
    if not (trabajador.foto_perfil or '').strip():
        raise ErrorFoto(
            f'{trabajador.nombre_completo} no tiene fotografía de perfil. '
            'Súbela en su ficha antes de sincronizarlo con el lector.',
            detalle=f'trabajador {trabajador.id} sin foto_perfil',
        )

    from app.utils import archivos

    datos = archivos.leer(trabajador.foto_perfil)
    if not datos:
        raise ErrorFoto(
            f'No se encontró el archivo de la fotografía de {trabajador.nombre_completo}. '
            'Vuelve a subirla en su ficha.',
            detalle=f'archivos.leer() vacío para key {trabajador.foto_perfil!r}',
        )
    return datos


def preparar_jpeg(datos: bytes, *, max_bytes: int = MAX_BYTES_FOTO) -> bytes:
    """Convierte cualquier imagen soportada a un JPEG que el lector acepte.

    Función pura: recibe bytes y devuelve bytes. No toca la base ni el
    almacenamiento, lo que la hace trivial de probar.
    """
    from PIL import Image, ImageOps

    try:
        with Image.open(io.BytesIO(datos)) as img:
            # Los teléfonos rotan por metadata EXIF; sin esto la cara llega
            # acostada y el equipo no la modela.
            img = ImageOps.exif_transpose(img)
            # JPEG no tiene canal alfa: un PNG/WebP transparente se aplana
            # sobre blanco en vez de reventar al guardar.
            if img.mode in ('RGBA', 'LA', 'P'):
                img = img.convert('RGBA')
                fondo = Image.new('RGB', img.size, (255, 255, 255))
                fondo.paste(img, mask=img.split()[-1])
                img = fondo
            elif img.mode != 'RGB':
                img = img.convert('RGB')

            ancho, alto = img.size
            if min(ancho, alto) < MIN_LADO_PX:
                raise ErrorFoto(
                    f'La fotografía es demasiado pequeña ({ancho}×{alto} px). '
                    f'El lector necesita al menos {MIN_LADO_PX}×{MIN_LADO_PX}.',
                    detalle=f'imagen {ancho}x{alto} bajo el mínimo',
                )
            if max(ancho, alto) > MAX_LADO_PX:
                img.thumbnail((MAX_LADO_PX, MAX_LADO_PX), Image.LANCZOS)

            return _comprimir(img, max_bytes)
    except ErrorFoto:
        raise
    except Exception as e:
        raise ErrorFoto(
            'La fotografía no se pudo procesar. Puede estar dañada o en un formato no soportado.',
            detalle=f'Pillow falló al abrir/convertir: {type(e).__name__}: {e}',
        ) from None


def _comprimir(img, max_bytes: int) -> bytes:
    """Baja calidad y, si aún no cabe, resolución, hasta entrar en `max_bytes`."""
    from PIL import Image

    for calidad in _CALIDADES:
        buf = io.BytesIO()
        img.save(buf, 'JPEG', quality=calidad, optimize=True, progressive=False)
        if buf.tell() <= max_bytes:
            return buf.getvalue()

    # Agotadas las calidades, se reduce el tamaño a la mitad por vuelta.
    actual = img
    while min(actual.size) > MIN_LADO_PX * 2:
        actual = actual.resize(
            (max(actual.width // 2, MIN_LADO_PX), max(actual.height // 2, MIN_LADO_PX)),
            Image.LANCZOS,
        )
        buf = io.BytesIO()
        actual.save(buf, 'JPEG', quality=_CALIDADES[-1], optimize=True)
        if buf.tell() <= max_bytes:
            return buf.getvalue()

    raise ErrorFoto(
        'La fotografía no se pudo comprimir lo suficiente para el lector.',
        detalle=f'no se logró bajar de {max_bytes} bytes',
    )


def huella(datos: bytes) -> str:
    """SHA-256 de unos bytes. Es lo que se guarda en `hash_foto` para saber
    después si la foto del empleado cambió y hay que reenviarla."""
    return hashlib.sha256(datos).hexdigest()


def tiene_foto(trabajador) -> bool:
    """¿El trabajador tiene fotografía utilizable?

    Solo mira la columna, sin ir al almacenamiento: se usa para pintar el
    listado de candidatos, donde hacer una lectura de R2 por fila sería carísimo.
    La validación REAL (que el archivo exista y sea una imagen legible) ocurre al
    sincronizar, en `leer_foto_trabajador` + `preparar_jpeg`.
    """
    return bool((getattr(trabajador, 'foto_perfil', '') or '').strip())
