"""add estantes table

Revision ID: e1f2a3b4c5d6
Revises: a4b5c6d7e8f9
Create Date: 2026-05-24 02:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e1f2a3b4c5d6'
down_revision = 'a4b5c6d7e8f9'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'estantes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('nombre', sa.String(length=100), nullable=False),
        sa.Column('descripcion', sa.String(length=250), nullable=True),
        sa.Column('almacen_id', sa.Integer(), nullable=False),
        sa.Column('qr_code', sa.String(length=100), nullable=False),
        sa.Column('activo', sa.Boolean(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['almacen_id'], ['almacenes.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('estantes', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_estantes_almacen_id'), ['almacen_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_estantes_qr_code'), ['qr_code'], unique=True)


def downgrade():
    with op.batch_alter_table('estantes', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_estantes_qr_code'))
        batch_op.drop_index(batch_op.f('ix_estantes_almacen_id'))
    op.drop_table('estantes')
