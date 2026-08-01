"""Generación de PDFs del módulo de Inventario (comprobantes, solicitudes, tomas, OC).

Los cuatro generadores del módulo repetían el mismo preámbulo: importar pisa,
resolver el logo contra BASE_DIR, renderizar el template, correr `CreatePDF` y
rebobinar el buffer. Aquí queda una sola vez.
"""
import io
import os

from flask import current_app, render_template

# Logo corporativo usado por los PDFs del módulo, relativo a BASE_DIR.
_LOGO_RELATIVO = ('static', 'imagenes', 'skilled (1).png')


def ruta_logo() -> str | None:
    """Ruta absoluta del logo, o None si no está en disco.

    La app es API-only (`static_folder=None`), así que los assets se resuelven
    contra BASE_DIR; si falta el archivo el template se renderiza sin logo
    (degradación benigna, no rompe el PDF).
    """
    base_dir = current_app.config.get('BASE_DIR') or os.path.dirname(current_app.root_path)
    ruta = os.path.join(base_dir, *_LOGO_RELATIVO)
    return ruta if os.path.exists(ruta) else None


def renderizar_pdf(template: str, **contexto) -> io.BytesIO | None:
    """Renderiza `template` (Jinja) y lo convierte a PDF con xhtml2pdf.

    Inyecta `logo_path` en el contexto si el template no lo trae. Devuelve el
    buffer listo para `send_file`, o None si xhtml2pdf no está instalado o falló
    la conversión — el llamador decide qué error HTTP devolver.
    """
    try:
        from xhtml2pdf import pisa
    except ImportError:
        return None
    contexto.setdefault('logo_path', ruta_logo())
    html = render_template(template, **contexto)
    buf = io.BytesIO()
    status = pisa.CreatePDF(io.BytesIO(html.encode('utf-8')), dest=buf)
    if status.err:
        return None
    buf.seek(0)
    return buf


def cantidad_legible(v):
    """Cantidad para impresión: entera si no tiene decimales, si no a 2 dígitos.
    Evita imprimir '3.0 pza' en los comprobantes."""
    v = float(v or 0)
    return int(v) if v % 1 == 0 else round(v, 2)
