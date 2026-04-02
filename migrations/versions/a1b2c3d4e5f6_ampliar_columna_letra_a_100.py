"""ampliar columna letra a 100 caracteres

Revision ID: a1b2c3d4e5f6
Revises: 9fd67564f993
Create Date: 2026-04-02 14:42:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a1b2c3d4e5f6'
down_revision = '19c27b7ae6e6'
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column('trabajadores', 'letra',
                     existing_type=sa.String(length=10),
                     type_=sa.String(length=100),
                     existing_nullable=True)


def downgrade():
    op.alter_column('trabajadores', 'letra',
                     existing_type=sa.String(length=100),
                     type_=sa.String(length=10),
                     existing_nullable=True)
