"""Add categorias_config table

Revision ID: a4b5c6d7e8f9
Revises: f3a4b5c6d7e8
Create Date: 2026-05-22 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'a4b5c6d7e8f9'
down_revision = 'f3a4b5c6d7e8'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'categorias_config',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('nombre', sa.String(length=100), nullable=False),
        sa.Column('imagen_url', sa.String(length=500), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('categorias_config', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_categorias_config_nombre'), ['nombre'], unique=True)


def downgrade():
    with op.batch_alter_table('categorias_config', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_categorias_config_nombre'))
    op.drop_table('categorias_config')
