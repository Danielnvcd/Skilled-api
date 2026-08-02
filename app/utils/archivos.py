"""Almacenamiento de archivos PRIVADOS (fotos de perfil, documentos, evidencias).

Mismo espíritu que `app/utils/r2.py` — que sube las imágenes del catálogo a un
bucket PÚBLICO servido por dominio propio — pero aquí el bucket es PRIVADO, sin
dominio público: lo que vive acá son documentos de RRHH con PII (contratos, INE,
CURP) y fotos de personal. El acceso sigue pasando por los endpoints con JWT
exactamente como hoy; nadie lee un contrato adivinando una URL.

Contrato de key
---------------
La *object key* en R2 es EXACTAMENTE la ruta relativa que la BD ya guarda:

    User.profile_pic            'profile_7_ab12cd34.webp'
    Trabajador.foto_perfil      'perfiles/pp_1712345_foto.webp'
    DocumentoTrabajador.ruta_archivo  'trabajadores/5/1712345_contrato.pdf'
    MediaHerramienta.ruta_archivo     'herramientas/3/ab12.jpg'

Por eso migrar NO requiere tocar ni una columna ni una migración de Alembic: la
misma cadena que hoy resuelve un path en disco resuelve mañana un object en R2.

Comportamiento dual (el seguro contra romper algo)
--------------------------------------------------
  Escritura  R2 si el bucket privado está configurado; si falla, cae a disco
             para no perder el upload del usuario. Sin configurar → disco (hoy).
  Lectura    R2 primero; si el object no existe, disco. Así los archivos que
             todavía no se migraron siguen sirviéndose igual que siempre.
  Borrado    En ambos lados, best-effort.

Config (independiente de las R2_* de productos — las llaves cambian por entorno)
  R2_PRIVADO_BUCKET             obligatoria; si falta, TODO se queda en disco
  R2_PRIVADO_ACCOUNT_ID         opcional → cae a R2_ACCOUNT_ID
  R2_PRIVADO_ACCESS_KEY_ID      opcional → cae a R2_ACCESS_KEY_ID
  R2_PRIVADO_SECRET_ACCESS_KEY  opcional → cae a R2_SECRET_ACCESS_KEY

A diferencia de `r2.py`, este módulo NO exige `FLASK_ENV=production`: el gate es
sólo "hay bucket + llaves". Los archivos privados no se re-descargan de una URL
externa, así que activarlo en cualquier entorno es seguro — y evita que local y
prod diverjan en cómo guardan.
"""
import io
import os
import logging
import mimetypes
import threading

logger = logging.getLogger(__name__)

# Con gevent monkey-patch (prod) esto es un lock cooperativo; en dev, uno real.
_client = None
_client_lock = threading.Lock()

# Windows no siempre trae el mapping de webp en el registro; lo fijamos para que
# `_content_type` no devuelva application/octet-stream en fotos de perfil.
mimetypes.add_type('image/webp', '.webp')
mimetypes.add_type('image/heic', '.heic')


def _var(nombre: str, fallback: str = '') -> str:
    """Lee R2_PRIVADO_<nombre> y, si viene vacía, cae a la R2_<fallback> del
    pipeline público. Permite dos modos de operación sin cambiar código: token
    dedicado al bucket privado, o el mismo token si ya tiene acceso a ambos."""
    valor = os.environ.get(f'R2_PRIVADO_{nombre}', '').strip()
    if valor:
        return valor
    return os.environ.get(f'R2_{fallback or nombre}', '').strip()


def _bucket() -> str:
    # Sin fallback a propósito: si `R2_PRIVADO_BUCKET` no está, NO queremos
    # escribir documentos de RRHH en el bucket público del catálogo.
    return os.environ.get('R2_PRIVADO_BUCKET', '').strip()


def conflicto_de_bucket() -> bool:
    """True si `R2_PRIVADO_BUCKET` apunta al MISMO bucket público del catálogo.

    Es el único error de configuración que no podemos dejar pasar: ese bucket
    tiene un dominio conectado, así que un contrato ahí sería legible por
    cualquiera con la URL. Ante la duda se prefiere seguir guardando en disco
    —que funciona— antes que publicar PII sin querer."""
    privado = _bucket()
    return bool(privado) and privado == os.environ.get('R2_BUCKET', '').strip()


def habilitado() -> bool:
    """True si hay bucket privado + credenciales resolubles. Único interruptor:
    mientras sea False el sistema se comporta exactamente como antes (disco)."""
    if not _bucket():
        return False
    if conflicto_de_bucket():
        logger.error(
            'R2_PRIVADO_BUCKET apunta al bucket público del catálogo (%s). '
            'Almacenamiento privado DESACTIVADO: los archivos se guardan en '
            'disco para no exponer documentos con PII.', _bucket(),
        )
        return False
    return all((
        _var('ACCOUNT_ID'),
        _var('ACCESS_KEY_ID'),
        _var('SECRET_ACCESS_KEY'),
    ))


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
        _client = boto3.client(
            's3',
            endpoint_url=f"https://{_var('ACCOUNT_ID')}.r2.cloudflarestorage.com",
            aws_access_key_id=_var('ACCESS_KEY_ID'),
            aws_secret_access_key=_var('SECRET_ACCESS_KEY'),
            region_name='auto',
            config=Config(
                signature_version='s3v4',
                retries={'max_attempts': 3, 'mode': 'standard'},
            ),
        )
        return _client


def _reset_client_para_tests():
    """Olvida el singleton. Sólo para tests que cambian las env vars en vuelo."""
    global _client
    with _client_lock:
        _client = None


# ─── Rutas locales ────────────────────────────────────────────────────────────

class KeyInsegura(ValueError):
    """La key intenta salirse del área de archivos (traversal) o es inválida."""


def _norm(key: str) -> str:
    """Normaliza a separadores POSIX sin `/` inicial, y RECHAZA traversal.

    `send_from_directory` traía esta validación de fábrica; al servir desde R2
    hay que hacerla a mano. Hoy todas las keys se construyen en el servidor
    (`secure_filename`, uuid4, ids), así que ninguna ruta es explotable — esto
    es para que siga siendo verdad si mañana alguien conecta una key que venga
    del cliente. Sin esto, un `..` sería lectura Y escritura arbitraria de
    archivos del servidor."""
    limpia = (key or '').replace('\\', '/').strip().lstrip('/')
    if not limpia:
        raise KeyInsegura('key vacía')
    partes = limpia.split('/')
    if any(p in ('.', '..') for p in partes):
        raise KeyInsegura(f'key con traversal: {key!r}')
    # Rutas absolutas de Windows (`C:/...`) escaparían al os.path.join.
    if ':' in partes[0]:
        raise KeyInsegura(f'key con unidad absoluta: {key!r}')
    return limpia


def ruta_local(key: str) -> str:
    """Path absoluto en disco correspondiente a `key` (el layout de siempre).

    Además de `_norm`, comprueba la contención real del path resuelto: es la
    barrera que aguanta aunque la normalización sintáctica se quede corta
    (symlinks, formas raras de la misma ruta)."""
    from flask import current_app
    base = current_app.config.get('UPLOAD_FOLDER', 'uploads')
    destino = os.path.join(base, *_norm(key).split('/'))
    base_real = os.path.realpath(base)
    destino_real = os.path.realpath(destino)
    if destino_real != base_real and not destino_real.startswith(base_real + os.sep):
        raise KeyInsegura(f'key fuera del área de archivos: {key!r}')
    return destino


def _content_type(key: str, explicito: str | None = None) -> str:
    if explicito:
        return explicito
    tipo, _ = mimetypes.guess_type(_norm(key))
    return tipo or 'application/octet-stream'


# ─── Operaciones ──────────────────────────────────────────────────────────────

def guardar(key: str, data: bytes, content_type: str | None = None) -> bool:
    """Persiste `data` bajo `key`. Devuelve True si quedó en R2, False si en disco.

    Nunca lanza por culpa de R2: si la subida falla, escribe en disco y lo
    registra. Un incidente de red no debe costarle al usuario su documento —
    y el backfill lo recogerá después."""
    key = _norm(key)
    if habilitado():
        try:
            _get_client().put_object(
                Bucket=_bucket(),
                Key=key,
                Body=data,
                ContentType=_content_type(key, content_type),
            )
            return True
        except Exception as e:
            logger.warning('R2 privado: falló subir %s (%s) — se guarda en disco', key, e)

    destino = ruta_local(key)
    os.makedirs(os.path.dirname(destino), exist_ok=True)
    with open(destino, 'wb') as f:
        f.write(data)
    return False


def leer(key: str) -> bytes | None:
    """Bytes del object en R2, o None si no está (o R2 apagado). No toca disco."""
    if not habilitado():
        return None
    try:
        obj = _get_client().get_object(Bucket=_bucket(), Key=_norm(key))
        return obj['Body'].read()
    except Exception:
        return None


def existe(key: str) -> bool:
    """True si `key` está en R2 o en disco. Una key insegura no existe."""
    if existe_en_r2(key):
        return True
    try:
        return os.path.exists(ruta_local(key))
    except KeyInsegura:
        return False


def existe_en_r2(key: str) -> bool:
    if not habilitado():
        return False
    try:
        _get_client().head_object(Bucket=_bucket(), Key=_norm(key))
        return True
    except Exception:
        return False


def _mensaje_de_error(e: Exception) -> str:
    """Traduce un fallo de boto3 a algo accionable, SIN filtrar secretos.

    Importante: nunca devolver `str(e)`. El mensaje de botocore incluye el
    endpoint, y el endpoint lleva incrustado el valor de R2_PRIVADO_ACCOUNT_ID
    — que si está mal configurado puede ser justamente un token. Ese texto acaba
    en la respuesta HTTP y en la pantalla del navegador."""
    texto = str(e)
    nombre = type(e).__name__
    if 'Invalid endpoint' in texto:
        return ('R2_PRIVADO_ACCOUNT_ID no parece un Account ID válido. Debe ser el '
                'identificador de 32 caracteres hexadecimales de la cuenta '
                '(Cloudflare → R2 → Overview → Account ID), no un token ni una URL.')
    if 'NoSuchBucket' in texto:
        return 'El bucket indicado en R2_PRIVADO_BUCKET no existe en esa cuenta de Cloudflare.'
    if 'InvalidAccessKeyId' in texto or 'SignatureDoesNotMatch' in texto:
        return ('Las llaves de acceso no son válidas para esa cuenta. Revisa '
                'R2_PRIVADO_ACCESS_KEY_ID y R2_PRIVADO_SECRET_ACCESS_KEY.')
    if 'AccessDenied' in texto:
        return 'El token de R2 no tiene permiso de lectura/escritura sobre ese bucket.'
    return f'No se pudo contactar el bucket privado de R2 ({nombre}).'


def comprobar() -> str | None:
    """None si el bucket privado responde; si no, un mensaje explicando qué falla.

    Una credencial mal escrita no debe verse como un 500 ni —peor— como "todos
    los archivos están pendientes". El panel de sistemas usa esto para mostrar
    un aviso accionable."""
    if not habilitado():
        return None          # apagado a propósito: no es un error
    try:
        _get_client().list_objects_v2(Bucket=_bucket(), MaxKeys=1)
        return None
    except Exception as e:
        logger.warning('R2 privado no disponible: %s', e)
        return _mensaje_de_error(e)


def listar_keys(prefijo: str = '') -> set:
    """Todas las object keys del bucket privado (paginado, 1000 por llamada).

    Sirve para saber de un golpe qué está migrado y qué no: comparar contra las
    keys que referencia la BD cuesta N/1000 llamadas en vez de un `head_object`
    por archivo. Devuelve set vacío si R2 está apagado o no responde."""
    if not habilitado():
        return set()
    try:
        cliente = _get_client()
    except Exception as e:
        # Construir el cliente ya falla con credenciales mal formadas (p.ej. un
        # Account ID inválido hace que boto3 arme un endpoint imposible).
        logger.warning('R2 privado: no se pudo crear el cliente: %s', e)
        return set()
    keys, token = set(), None
    while True:
        kw = {'Bucket': _bucket(), 'MaxKeys': 1000}
        if prefijo:
            kw['Prefix'] = prefijo
        if token:
            kw['ContinuationToken'] = token
        try:
            resp = cliente.list_objects_v2(**kw)
        except Exception as e:
            logger.warning('R2 privado: falló listar objects: %s', e)
            return keys
        for obj in (resp.get('Contents') or []):
            keys.add(obj['Key'])
        if not resp.get('IsTruncated'):
            break
        token = resp.get('NextContinuationToken')
        if not token:
            break
    return keys


def eliminar(key: str) -> None:
    """Borra el archivo de R2 y de disco. Best-effort: nunca lanza."""
    try:
        key = _norm(key)
    except KeyInsegura as e:
        logger.warning('No se elimina nada: %s', e)
        return
    if habilitado():
        try:
            _get_client().delete_object(Bucket=_bucket(), Key=key)
        except Exception as e:
            logger.warning('R2 privado: falló borrar %s: %s', key, e)
    try:
        path = ruta_local(key)
        if os.path.exists(path):
            os.remove(path)
    except Exception as e:
        logger.warning('No se pudo eliminar %s de disco: %s', key, e)


def enviar(key: str, mimetype: str | None = None, as_attachment: bool = False,
           download_name: str | None = None):
    """Respuesta Flask con el contenido de `key`: R2 primero, disco de respaldo.

    Sustituye a `send_from_directory(UPLOAD_FOLDER, key, ...)` conservando la
    misma firma útil. Devuelve None si el archivo no está en ningún lado — el
    caller decide el 404 con su propio mensaje.

    Toda respuesta lleva `X-Almacenamiento: r2 | disco`, que dice de dónde
    salieron REALMENTE los bytes. Con lectura dual no hay forma de saberlo
    mirando la imagen; este header lo vuelve verificable desde la pestaña Red
    del navegador sin tener que leer logs."""
    from flask import send_file

    def _con_origen(resp, origen):
        resp.headers['X-Almacenamiento'] = origen
        return resp

    try:
        key = _norm(key)
        path = ruta_local(key)
    except KeyInsegura as e:
        # Se trata como "no existe": el caller responde 404 y no filtramos por
        # el mensaje de error si la ruta era un intento de sondeo.
        logger.warning('Petición de archivo rechazada: %s', e)
        return None
    ctype = _content_type(key, mimetype)

    data = leer(key)
    if data is not None:
        return _con_origen(send_file(
            io.BytesIO(data),
            mimetype=ctype,
            as_attachment=as_attachment,
            download_name=download_name or os.path.basename(key),
        ), 'r2')

    if not os.path.exists(path):
        return None
    return _con_origen(send_file(
        path,
        mimetype=ctype,
        as_attachment=as_attachment,
        download_name=download_name or os.path.basename(key),
    ), 'disco')
