"""hikvision_eventos + estado de la escucha en tiempo real

La actividad del lector deja de consultarse al equipo cada vez que alguien abre
la pantalla: un proceso aparte (`flask hikvision escuchar`) mantiene abierta la
conexión `alertStream` del lector y guarda aquí cada evento en cuanto ocurre.

Tabla nueva:
  - hikvision_eventos   un evento de acceso por fila. Única por
                        (dispositivo_id, serial_no): el lector reenvía su
                        historial al reconectar y la ingesta debe ser
                        idempotente.

`hikvision_dispositivos` gana, todas nullable y sin backfill (un lector sin
escucha simplemente aparece como «sin tiempo real»):
  - escucha_estado   CONECTADO | DESCONECTADO
  - escucha_latido   última señal de vida recibida del lector
  - escucha_error    motivo de la última desconexión, ya saneado

Revision ID: hikv2026c
Revises: hikv2026b
Create Date: 2026-09-22

"""
import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = 'hikv2026c'
down_revision = 'hikv2026b'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('hikvision_dispositivos',
                  sa.Column('escucha_estado', sa.String(length=20), nullable=True))
    op.add_column('hikvision_dispositivos',
                  sa.Column('escucha_latido', sa.DateTime(timezone=True), nullable=True))
    op.add_column('hikvision_dispositivos',
                  sa.Column('escucha_error', sa.String(length=500), nullable=True))

    op.create_table(
        'hikvision_eventos',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('dispositivo_id', sa.Integer(),
                  sa.ForeignKey('hikvision_dispositivos.id', ondelete='CASCADE'),
                  nullable=False),
        sa.Column('serial_no', sa.BigInteger(), nullable=False),
        sa.Column('fecha_hora', sa.DateTime(timezone=True), nullable=False),
        sa.Column('hora_local', sa.String(length=32), nullable=False),
        sa.Column('major', sa.SmallInteger(), nullable=False),
        sa.Column('minor', sa.Integer(), nullable=False),
        sa.Column('tipo', sa.String(length=12), nullable=False),
        sa.Column('employee_no', sa.String(length=32), nullable=True),
        sa.Column('nombre_en_equipo', sa.String(length=128), nullable=True),
        sa.Column('trabajador_id', sa.Integer(),
                  sa.ForeignKey('trabajadores.id', ondelete='SET NULL'), nullable=True),
        sa.Column('modo_verificacion', sa.String(length=40), nullable=True),
        sa.Column('cubrebocas', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('captura', sa.String(length=256), nullable=True),
        sa.Column('recibido_en', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.UniqueConstraint('dispositivo_id', 'serial_no', name='uq_hikvision_evento_serial'),
    )
    op.create_index('ix_hikvision_eventos_disp_fecha', 'hikvision_eventos',
                    ['dispositivo_id', 'fecha_hora'])
    op.create_index('ix_hikvision_eventos_tipo', 'hikvision_eventos', ['tipo'])
    op.create_index('ix_hikvision_eventos_employee_no', 'hikvision_eventos', ['employee_no'])


def downgrade():
    op.drop_index('ix_hikvision_eventos_employee_no', table_name='hikvision_eventos')
    op.drop_index('ix_hikvision_eventos_tipo', table_name='hikvision_eventos')
    op.drop_index('ix_hikvision_eventos_disp_fecha', table_name='hikvision_eventos')
    op.drop_table('hikvision_eventos')
    op.drop_column('hikvision_dispositivos', 'escucha_error')
    op.drop_column('hikvision_dispositivos', 'escucha_latido')
    op.drop_column('hikvision_dispositivos', 'escucha_estado')
