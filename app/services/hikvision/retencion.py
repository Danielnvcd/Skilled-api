"""Retención de lo que la integración Hikvision guarda en el ERP.

Sin esto las tablas crecen sin límite: un lector con 50 personas genera del
orden de 100 000 eventos al año (cada acceso son tres: el reconocimiento y el
desbloqueo/bloqueo de la puerta).

    hikvision_eventos           12 meses (HIKVISION_RETENCION_MESES)
    hikvision_escucha_sucesos   90 días
    hikvision_tareas            30 días, solo las ya terminadas

Lo que la nómina necesita NO depende de esto: las checadas ya se pasaron a
`registros_diarios_horas`, que no se purga. Y los accesos siguen también en el
propio lector (hasta 150 000 eventos).

Lo corre el supervisor de la escucha una vez al día, y a mano:
`flask hikvision purgar`.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from app.extensions import db
from app.models import EventoHikvision, SucesoEscuchaHikvision, TareaHikvision

DIAS_SUCESOS = 90
DIAS_TAREAS = 30


def meses_eventos() -> int:
    try:
        return max(1, int(os.environ.get('HIKVISION_RETENCION_MESES', '12')))
    except ValueError:
        return 12


def purgar(*, meses: int | None = None, ahora: datetime | None = None) -> dict:
    """Borra lo que excede la retención. Devuelve cuántas filas por tabla. No hace commit."""
    ahora = ahora or datetime.now(timezone.utc)
    meses = meses or meses_eventos()
    limite_eventos = ahora - timedelta(days=30 * meses)

    eventos = EventoHikvision.query.filter(
        EventoHikvision.fecha_hora < limite_eventos,
    ).delete(synchronize_session=False)
    sucesos = SucesoEscuchaHikvision.query.filter(
        SucesoEscuchaHikvision.creado_en < ahora - timedelta(days=DIAS_SUCESOS),
    ).delete(synchronize_session=False)
    tareas = TareaHikvision.query.filter(
        TareaHikvision.estado.in_(['TERMINADA', 'ERROR']),
        TareaHikvision.creada_en < ahora - timedelta(days=DIAS_TAREAS),
    ).delete(synchronize_session=False)
    db.session.flush()
    return {'eventos': eventos, 'sucesos': sucesos, 'tareas': tareas}
