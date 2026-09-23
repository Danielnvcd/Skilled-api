"""Vigilancia de la conexión ERP ↔ lector: bitácora de sucesos y alertas.

Antes, un problema con el lector solo se notaba si alguien abría su pantalla
y veía «Sin tiempo real». Ahora la escucha deja constancia de cada suceso
(`SucesoEscuchaHikvision`) y avisa a los admins por notificación cuando algo
pide intervención humana:

    desconectado        la escucha lleva más de 5 min sin conexión
    reconectado         vuelve, SOLO si antes se avisó la desconexión
    credenciales        el lector rechazó la contraseña (y cuántos intentos quedan)
    equipo_cambiado     otro número de serie en la misma dirección
    seriales            el lector reinició su numeración de eventos
    firmware            se actualizó el firmware (puede cambiar el ISAPI)
    reloj               el reloj del lector se desfasó más de 1 min
    ip_dinamica         el lector dejó de tener IP fija

Cada alerta lleva una `referencia` (`hikvision:<id>:<clave>`) y no se repite
mientras haya una igual dentro de su ventana de silencio: un lector apagado
toda la noche genera UNA notificación, no una cada 30 s.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from app.extensions import db
from app.models import Notificacion, SucesoEscuchaHikvision

logger = logging.getLogger(__name__)

TIPO_NOTIFICACION = 'LECTOR'

# Horas durante las que una alerta con la misma referencia no se repite.
SILENCIO_H = {
    'desconectado': 6,
    'reconectado': 0,        # solo sale tras una desconexión avisada
    'credenciales': 1,
    'equipo_cambiado': 24,
    'seriales': 24,
    'firmware': 24,
    'reloj': 12,
    'ip_dinamica': 24,
}


def _ahora():
    return datetime.now(timezone.utc)


def desfase_segundos(hora_local: str, ahora_utc: datetime) -> int | None:
    """Segundos que el reloj del equipo va adelantado (+) o atrasado (−)."""
    try:
        equipo = datetime.fromisoformat(hora_local)
    except (TypeError, ValueError):
        return None
    if equipo.tzinfo is None:
        return None
    return round((equipo - ahora_utc).total_seconds())


def registrar(dispositivo_id: int, tipo: str, detalle: str | None = None) -> None:
    """Anota un suceso de la conexión. No hace commit."""
    db.session.add(SucesoEscuchaHikvision(
        dispositivo_id=dispositivo_id, tipo=tipo, detalle=(detalle or '')[:500] or None,
    ))


def referencia(dispositivo_id: int, clave: str) -> str:
    return f'hikvision:{dispositivo_id}:{clave}'


def alerta_reciente(dispositivo_id: int, clave: str, horas: float) -> bool:
    """¿Ya se avisó esto y sigue vigente?

    Vigente = alguien aún no la lee, o se creó dentro de la ventana. Lo primero
    es lo que realmente evita el spam: mientras la notificación siga sin leer
    no tiene sentido mandar otra igual. La ventana cubre el caso en que la
    leyeron pero el problema sigue (`created_at` se guarda sin zona, así que la
    ventana puede correrse unas horas; por eso no es el único criterio).
    """
    if horas <= 0:
        return False
    limite = (_ahora() - timedelta(hours=horas)).replace(tzinfo=None)
    return db.session.query(Notificacion.id).filter(
        Notificacion.referencia == referencia(dispositivo_id, clave),
        (Notificacion.leida.is_(False)) | (Notificacion.created_at >= limite),
    ).first() is not None


def alertar(dispositivo, clave: str, titulo: str, mensaje: str) -> bool:
    """Notifica a los admins salvo que ya se haya avisado hace poco.

    Devuelve True si se creó la notificación. No hace commit.
    """
    from app.models.notificaciones import crear_notif_admins

    if alerta_reciente(dispositivo.id, clave, SILENCIO_H.get(clave, 6)):
        return False
    crear_notif_admins(
        tipo=TIPO_NOTIFICACION,
        titulo=f'{dispositivo.nombre}: {titulo}'[:200],
        mensaje=mensaje[:500],
        url=f'/lectores/{dispositivo.id}?tab=equipo',
        referencia=referencia(dispositivo.id, clave),
    )
    logger.info('Hikvision vigilancia disp=%s: alerta %s', dispositivo.id, clave)
    return True


def hubo_alerta_desconexion(dispositivo_id: int) -> bool:
    """¿Se avisó una desconexión que todavía no tiene su «reconectado»?"""
    ultimo = db.session.query(Notificacion.referencia).filter(
        Notificacion.referencia.in_([
            referencia(dispositivo_id, 'desconectado'),
            referencia(dispositivo_id, 'reconectado'),
        ]),
    ).order_by(Notificacion.created_at.desc(), Notificacion.id.desc()).first()
    return bool(ultimo) and ultimo[0].endswith(':desconectado')
