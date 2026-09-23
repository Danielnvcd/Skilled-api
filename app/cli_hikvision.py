"""Comandos de consola de la integración Hikvision.

    flask hikvision escuchar      escucha en tiempo real de todos los lectores
    flask hikvision al-dia        trae lo pendiente de cada lector una vez y sale
    flask hikvision checadas      reprocesa eventos guardados → registros de horas

`escuchar` es un proceso de larga vida: en Docker corre como su propio
servicio (`hikvision-escucha`), UNO solo. Dos a la vez no duplican eventos (la
tabla es única por serial) pero sí duplican conexiones al lector.
"""
from __future__ import annotations

import logging

import click
from flask import current_app
from flask.cli import AppGroup

hikvision_cli = AppGroup('hikvision', help='Integración con lectores Hikvision.')


@hikvision_cli.command('escuchar')
def escuchar():
    """Mantiene abierta la conexión de eventos de cada lector activo."""
    from app.services.hikvision.escucha import Supervisor

    logging.basicConfig(level=logging.INFO)
    Supervisor(current_app._get_current_object()).correr()


@hikvision_cli.command('al-dia')
def al_dia():
    """Una sola pasada: guarda lo que cada lector tenga pendiente."""
    from app.extensions import db
    from app.models import DispositivoHikvision
    from app.services.hikvision import ClienteHikvision, ErrorHikvision, asistencia, ingesta

    for d in DispositivoHikvision.query.filter_by(activo=True):
        try:
            with ClienteHikvision.desde_dispositivo(d) as cli:
                nuevos = ingesta.ponerse_al_dia(cli, d.id)
            checadas = asistencia.aplicar_eventos(nuevos)
            db.session.commit()
            asistencia.avisar_cambios(checadas)
            click.echo(f'{d.nombre}: {len(nuevos)} evento(s) nuevo(s)')
        except ErrorHikvision as e:
            db.session.rollback()
            click.echo(f'{d.nombre}: {e.mensaje}', err=True)


@hikvision_cli.command('checadas')
@click.option('--dias', default=7, show_default=True,
              help='Cuántos días hacia atrás reprocesar (hoy incluido).')
def checadas(dias):
    """Pasa a registros de horas los accesos ya guardados. Idempotente.

    Sirve para arrancar la fase 2 con los eventos que ya estaban en la base, o
    para reprocesar tras corregir algo. Respeta las mismas reglas que el tiempo
    real: no toca registros editados a mano ni semanas con prenómina guardada.
    """
    from datetime import date, timedelta

    from app.extensions import db
    from app.services.hikvision import asistencia

    hasta = date.today()
    desde = hasta - timedelta(days=max(dias, 1) - 1)
    resultados = asistencia.recalcular(desde, hasta)
    db.session.commit()
    asistencia.avisar_cambios(resultados)
    for r in resultados:
        linea = f"{r['fecha']} trab={r['trabajador_id']}: {r['accion']}"
        click.echo(linea + (f" ({r['motivo']})" if r['motivo'] else ''))
    click.echo(f'{len(resultados)} día(s) procesado(s) entre {desde} y {hasta}.')
