"""Cliente Cloudflare R2 (S3-compatible) para imágenes de productos.

Se AUTO-DESACTIVA si faltan credenciales o si no estamos en producción (salvo
`R2_FORCE_ENABLE=true`). Ninguna función lanza en el camino "feliz" del request:
`r2_enabled()` es el gate — mientras sea False, nadie llama al resto y el
catálogo funciona igual que hoy (guardando la URL de imagen tal cual).

Config por env vars (ver .env.example):
  R2_ACCOUNT_ID, R2_BUCKET, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY,
  R2_PUBLIC_BASE_URL, R2_FORCE_ENABLE
"""
import os
import logging
import threading

logger = logging.getLogger(__name__)

# Con gevent monkey-patch (prod) esto es un lock cooperativo; en dev, uno real.
_client = None
_client_lock = threading.Lock()

_REQUIRED_VARS = (
    'R2_ACCOUNT_ID', 'R2_BUCKET', 'R2_ACCESS_KEY_ID',
    'R2_SECRET_ACCESS_KEY', 'R2_PUBLIC_BASE_URL',
)


def _is_prod() -> bool:
    return os.environ.get('FLASK_ENV', '').strip().lower() == 'production'


def r2_enabled() -> bool:
    """True solo si (producción o R2_FORCE_ENABLE) y TODAS las credenciales están.

    Este es el único interruptor: en local (sin credenciales) o en cualquier
    entorno sin las 5 variables, el pipeline de imágenes no hace nada.
    """
    forced = os.environ.get('R2_FORCE_ENABLE', '').strip().lower() == 'true'
    if not (_is_prod() or forced):
        return False
    return all(os.environ.get(v, '').strip() for v in _REQUIRED_VARS)


def public_base_url() -> str:
    return os.environ.get('R2_PUBLIC_BASE_URL', '').strip().rstrip('/')


def public_url(key: str) -> str:
    """URL pública de un object key (para pintar en el catálogo)."""
    return f"{public_base_url()}/{key.lstrip('/')}"


def _bucket() -> str:
    return os.environ['R2_BUCKET'].strip()


def _get_client():
    """boto3 S3 client apuntando al endpoint de R2 (lazy singleton)."""
    global _client
    if _client is not None:
        return _client
    with _client_lock:
        if _client is not None:
            return _client
        import boto3
        from botocore.config import Config
        account_id = os.environ['R2_ACCOUNT_ID'].strip()
        _client = boto3.client(
            's3',
            endpoint_url=f'https://{account_id}.r2.cloudflarestorage.com',
            aws_access_key_id=os.environ['R2_ACCESS_KEY_ID'].strip(),
            aws_secret_access_key=os.environ['R2_SECRET_ACCESS_KEY'].strip(),
            region_name='auto',
            config=Config(
                signature_version='s3v4',
                retries={'max_attempts': 3, 'mode': 'standard'},
            ),
        )
        return _client


def object_exists(key: str) -> bool:
    """True si el object ya existe en el bucket (idempotencia: no re-subir)."""
    try:
        _get_client().head_object(Bucket=_bucket(), Key=key)
        return True
    except Exception:
        return False


def subir_webp(key: str, data: bytes) -> str:
    """Sube `data` (bytes WebP) a R2 con Content-Type image/webp y caché larga.

    La clave suele ser `productos/<sha256(data)>.webp` (inmutable por contenido)
    → se puede cachear para siempre y una edición genera una key nueva sin tener
    que invalidar la CDN. Devuelve la URL pública. Lanza si falla (el caller lo
    captura y marca el producto en ERROR)."""
    _get_client().put_object(
        Bucket=_bucket(),
        Key=key,
        Body=data,
        ContentType='image/webp',
        CacheControl='public, max-age=31536000, immutable',
    )
    return public_url(key)
