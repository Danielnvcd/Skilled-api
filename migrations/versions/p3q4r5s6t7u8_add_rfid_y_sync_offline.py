"""add rfid_uid a trabajadores y client_record_id + modificado_en a registros_diarios_horas

Soporta el kiosko de asistencias RFID offline-first:
  - rfid_uid: identificador de tarjeta RFID (Wiegand/USB-HID) asociada al trabajador.
  - client_record_id: UUID generado por el cliente para idempotencia al subir/reintentar.
  - modificado_en: timestamp del último cambio, usado para LWW (last write wins)
    cuando un registro se editó tanto offline como en la web.

Revision ID: p3q4r5s6t7u8
Revises: o2p3q4r5s6t7
Create Date: 2026-05-29 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'p3q4r5s6t7u8'
down_revision = 'o2p3q4r5s6t7'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('trabajadores', schema=None) as batch_op:
        batch_op.add_column(sa.Column('rfid_uid', sa.String(length=64), nullable=True))
        batch_op.create_index('ix_trabajadores_rfid_uid', ['rfid_uid'], unique=True)

    with op.batch_alter_table('registros_diarios_horas', schema=None) as batch_op:
        batch_op.add_column(sa.Column('client_record_id', sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column(
            'modificado_en',
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
        ))
        batch_op.create_index('ix_registros_diarios_horas_client_record_id', ['client_record_id'], unique=True)

    # Backfill: para registros existentes, modificado_en queda con NOW() (vía server_default).
    # Esto los hace "más viejos" que cualquier edición offline futura → la edición offline gana
    # en el primer sync, que es el comportamiento esperado al estrenar el kiosko.


def downgrade():
    with op.batch_alter_table('registros_diarios_horas', schema=None) as batch_op:
        batch_op.drop_index('ix_registros_diarios_horas_client_record_id')
        batch_op.drop_column('modificado_en')
        batch_op.drop_column('client_record_id')

    with op.batch_alter_table('trabajadores', schema=None) as batch_op:
        batch_op.drop_index('ix_trabajadores_rfid_uid')
        batch_op.drop_column('rfid_uid')
