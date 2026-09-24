"""resiliencia de la conexión con los lectores Hikvision

  - Época de seriales: `hikvision_dispositivos.epoca_eventos` y
    `hikvision_eventos.epoca`. La restricción única pasa de
    (dispositivo, serial) a (dispositivo, época, serial): tras un reset del
    lector sus seriales vuelven a empezar y no deben chocar con los viejos.
  - hikvision_escucha_sucesos: bitácora de la conexión (conectado,
    desconectado, credenciales, equipo cambiado…) para el panel de salud.
  - hikvision_tareas: sincronizaciones de empleados en segundo plano.

Todo con defaults: las filas existentes quedan en la época 1.

Revision ID: hikv2026e
Revises: hikv2026d
Create Date: 2026-09-23

"""
import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = 'hikv2026e'
down_revision = 'hikv2026d'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('hikvision_dispositivos', sa.Column(
        'epoca_eventos', sa.Integer(), nullable=False, server_default='1'))
    op.add_column('hikvision_eventos', sa.Column(
        'epoca', sa.Integer(), nullable=False, server_default='1'))
    op.drop_constraint('uq_hikvision_evento_serial', 'hikvision_eventos', type_='unique')
    op.create_unique_constraint('uq_hikvision_evento_epoca_serial', 'hikvision_eventos',
                                ['dispositivo_id', 'epoca', 'serial_no'])

    op.create_table(
        'hikvision_escucha_sucesos',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('dispositivo_id', sa.Integer(),
                  sa.ForeignKey('hikvision_dispositivos.id', ondelete='CASCADE'), nullable=False),
        sa.Column('tipo', sa.String(length=30), nullable=False),
        sa.Column('detalle', sa.String(length=500), nullable=True),
        sa.Column('creado_en', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_index('ix_hikvision_sucesos_disp_fecha', 'hikvision_escucha_sucesos',
                    ['dispositivo_id', 'creado_en'])

    op.create_table(
        'hikvision_tareas',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('dispositivo_id', sa.Integer(),
                  sa.ForeignKey('hikvision_dispositivos.id', ondelete='CASCADE'), nullable=False),
        sa.Column('tipo', sa.String(length=20), nullable=False),
        sa.Column('trabajador_ids', sa.JSON(), nullable=False),
        sa.Column('estado', sa.String(length=15), nullable=False, server_default='PENDIENTE'),
        sa.Column('total', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('procesados', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('resultados', sa.JSON(), nullable=True),
        sa.Column('error', sa.String(length=500), nullable=True),
        sa.Column('creado_por_id', sa.Integer(),
                  sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('creada_en', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column('iniciada_en', sa.DateTime(timezone=True), nullable=True),
        sa.Column('terminada_en', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_hikvision_tareas_dispositivo_id', 'hikvision_tareas', ['dispositivo_id'])
    op.create_index('ix_hikvision_tareas_estado', 'hikvision_tareas', ['estado'])


def downgrade():
    op.drop_index('ix_hikvision_tareas_estado', table_name='hikvision_tareas')
    op.drop_index('ix_hikvision_tareas_dispositivo_id', table_name='hikvision_tareas')
    op.drop_table('hikvision_tareas')
    op.drop_index('ix_hikvision_sucesos_disp_fecha', table_name='hikvision_escucha_sucesos')
    op.drop_table('hikvision_escucha_sucesos')
    op.drop_constraint('uq_hikvision_evento_epoca_serial', 'hikvision_eventos', type_='unique')
    op.create_unique_constraint('uq_hikvision_evento_serial', 'hikvision_eventos',
                                ['dispositivo_id', 'serial_no'])
    op.drop_column('hikvision_eventos', 'epoca')
    op.drop_column('hikvision_dispositivos', 'epoca_eventos')
