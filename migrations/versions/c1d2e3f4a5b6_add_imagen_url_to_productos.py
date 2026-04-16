"""Add imagen_url to productos

Revision ID: c1d2e3f4a5b6
Revises: 5838d810af16
Branch Labels: None
depends_on: None

"""
from alembic import op
import sqlalchemy as sa


revision = 'c1d2e3f4a5b6'
down_revision = ('5838d810af16', 'b3c4d5e6f7a8')
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('productos', schema=None) as batch_op:
        batch_op.add_column(sa.Column('imagen_url', sa.String(length=500), nullable=True))


def downgrade():
    with op.batch_alter_table('productos', schema=None) as batch_op:
        batch_op.drop_column('imagen_url')
