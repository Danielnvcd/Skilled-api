"""Utilidades HTTP del módulo: parseo de payloads/query params y el manejo
uniforme de errores dentro de transacciones de stock.

`ErrorDeNegocio` + `@transaccion_de_stock` sustituyen el bloque

    try:
        ...
        if algo_falla:
            db.session.rollback()
            return jsonify({'detail': ...}), 409
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        if _es_error_de_lock(exc):
            return jsonify({'detail': 'Stock bloqueado…'}), 409
        raise

que estaba copiado en una decena de vistas. Ahora la vista solo levanta
`ErrorDeNegocio(...)` y el decorador se encarga del rollback y de traducirlo a
respuesta JSON.
"""
from functools import wraps

from flask import jsonify, request
from marshmallow import Schema, ValidationError

from app.extensions import db

# Mensaje único para el 409 de "otra transacción tiene la fila bloqueada".
MENSAJE_LOCK = 'Stock bloqueado por otra operación, reintenta'


class ErrorDeNegocio(Exception):
    """Fallo esperado dentro de una operación de inventario (stock insuficiente,
    estado inválido, entidad inexistente…).

    Aborta el trabajo hecho hasta ese punto y se traduce a `(json, status)` por
    `@transaccion_de_stock`. `extra` agrega claves al cuerpo de la respuesta
    (p. ej. `errores=[...]` en las validaciones de reserva).
    """

    def __init__(self, detail, status: int = 409, **extra):
        super().__init__(detail if isinstance(detail, str) else repr(detail))
        self.detail = detail
        self.status = status
        self.extra = extra

    def como_respuesta(self):
        return jsonify({'detail': self.detail, **self.extra}), self.status


def _es_error_de_lock(exc: Exception) -> bool:
    """True si `exc` viene de un `SELECT ... FOR UPDATE NOWAIT` que no pudo tomar
    el lock (SQLSTATE 55P03 / lock_not_available).

    Se mira el SQLSTATE, NO el texto: PostgreSQL traduce sus mensajes según el
    `lc_messages` del servidor, así que comparar contra la frase en inglés
    ("could not obtain lock") falla en un servidor con locale español y el
    endpoint devuelve 500 en vez del 409 'reintenta'. El match por texto queda
    de respaldo para SQLite (tests) y para drivers que no expongan el código.
    """
    orig = getattr(exc, 'orig', None)
    sqlstate = getattr(orig, 'sqlstate', None) or getattr(orig, 'pgcode', None)
    if sqlstate == '55P03':
        return True
    texto = str(exc).lower()
    return 'could not obtain lock' in texto or 'lock_not_available' in texto


def respuesta_lock():
    """Respuesta 409 estándar cuando no se pudo tomar el lock de una fila."""
    return jsonify({'detail': MENSAJE_LOCK}), 409


def transaccion_de_stock(view):
    """Envuelve una vista que muta stock: ante cualquier fallo hace rollback y
    devuelve una respuesta JSON en vez de dejar la sesión sucia.

      - `ErrorDeNegocio` → su `(detail, status)`.
      - Lock no disponible (55P03) → 409 'reintenta'.
      - Cualquier otra excepción → rollback y se re-lanza (500 con traza).

    El commit sigue siendo explícito dentro de la vista: hay endpoints que
    commitean a medias y siguen trabajando, y los `emit_to_role` deben correr
    DESPUÉS del commit.
    """
    @wraps(view)
    def wrapper(*args, **kwargs):
        try:
            return view(*args, **kwargs)
        except ErrorDeNegocio as exc:
            db.session.rollback()
            return exc.como_respuesta()
        except Exception as exc:
            db.session.rollback()
            if _es_error_de_lock(exc):
                return respuesta_lock()
            raise
    return wrapper


def _parse_or_422(schema: Schema, data):
    """Valida `data` con `schema`. Si falla, devuelve tuple (None, response_422).
    Si pasa, devuelve (dict, None).
    """
    if not isinstance(data, dict):
        return None, (jsonify({'detail': 'Payload debe ser un objeto JSON'}), 422)
    try:
        return schema.load(data), None
    except ValidationError as err:
        return None, (jsonify({'detail': err.messages}), 422)


def _int_arg(name: str, default: int, minimum: int, maximum: int):
    """Lee un query param int, lo recorta al rango y devuelve (valor, error_response).
    Devuelve 422 si el valor no es numérico o queda fuera del rango.
    """
    raw = request.args.get(name)
    if raw is None:
        return default, None
    try:
        val = int(raw)
    except (TypeError, ValueError):
        return None, (jsonify({'detail': f"Parámetro '{name}' debe ser entero"}), 422)
    if val < minimum or val > maximum:
        return None, (jsonify({'detail': f"Parámetro '{name}' fuera de rango"}), 422)
    return val, None
