"""registros_diarios_horas.origen: checadas del lector biométrico

Fase 2 de la integración Hikvision: los accesos del lector se convierten en
entrada/salida en `registros_diarios_horas`, igual que el kiosko RFID, y la
prenómina los toma sin cambios.

`origen` distingue esos registros:
  NULL             captura manual, QR o kiosko (todo lo que ya existe)
  'LECTOR'         lo escribe el lector y lo sigue actualizando
  'LECTOR_EDITADO' alguien cambió a mano la hora del lector: ya no se toca

Nullable y sin backfill: los registros existentes quedan como siempre.

Revision ID: hikv2026d
Revises: hikv2026c
Create Date: 2026-09-22

"""
import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = 'hikv2026d'
down_revision = 'hikv2026c'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('registros_diarios_horas',
                  sa.Column('origen', sa.String(length=20), nullable=True))


def downgrade():
    op.drop_column('registros_diarios_horas', 'origen')
