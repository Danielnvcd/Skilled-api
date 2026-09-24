"""hikvision_sync_empleado.foto_key: detectar foto de perfil cambiada

Guarda la key de `trabajadores.foto_perfil` que se envió al lector. Si la foto
de perfil cambia después de sincronizar, la key actual deja de coincidir y la
pantalla del lector marca al empleado como desactualizado.

Nullable y sin backfill: una fila sin `foto_key` (sincronizada antes de esta
columna) se trata como "no se sabe" y no se marca como desactualizada.

Revision ID: hikv2026b
Revises: hikv2026
Create Date: 2026-09-22

"""
import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = 'hikv2026b'
down_revision = 'hikv2026'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'hikvision_sync_empleado',
        sa.Column('foto_key', sa.String(length=255), nullable=True),
    )


def downgrade():
    op.drop_column('hikvision_sync_empleado', 'foto_key')
