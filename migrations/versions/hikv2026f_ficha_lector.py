"""hikvision_dispositivos: foto, ubicación y notas del lector

Para reconocer cada equipo en la pantalla de lectores: una foto de cómo se ve
instalado, dónde está y notas libres. Todo nullable y sin backfill.

Revision ID: hikv2026f
Revises: hikv2026e
Create Date: 2026-09-23

"""
import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = 'hikv2026f'
down_revision = 'hikv2026e'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('hikvision_dispositivos', sa.Column('ubicacion', sa.String(length=120), nullable=True))
    op.add_column('hikvision_dispositivos', sa.Column('notas', sa.String(length=500), nullable=True))
    op.add_column('hikvision_dispositivos', sa.Column('foto', sa.String(length=255), nullable=True))


def downgrade():
    op.drop_column('hikvision_dispositivos', 'foto')
    op.drop_column('hikvision_dispositivos', 'notas')
    op.drop_column('hikvision_dispositivos', 'ubicacion')
