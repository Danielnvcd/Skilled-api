"""add monto_viaticos_manual to registros_diarios_horas

Revision ID: b7e1a2c3d4f5
Revises: 19f3576b5827
Create Date: 2026-03-06

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'b7e1a2c3d4f5'
down_revision = '60acfa1c0cad'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('registros_diarios_horas',
        sa.Column('monto_viaticos_manual', sa.Numeric(10, 2), nullable=True)
    )


def downgrade():
    op.drop_column('registros_diarios_horas', 'monto_viaticos_manual')
