"""add ajuste_trabajadores_periodo table

Revision ID: d9e3f4a5b6c7
Revises: c8d2e3f4a5b6
Create Date: 2026-03-06

"""
from alembic import op
import sqlalchemy as sa

revision = 'd9e3f4a5b6c7'
down_revision = 'c8d2e3f4a5b6'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('ajuste_trabajadores_periodo',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('periodo_id', sa.Integer(), nullable=False),
        sa.Column('trabajador_id', sa.Integer(), nullable=False),
        sa.Column('monto_meta', sa.Numeric(10, 2), nullable=False),
        sa.ForeignKeyConstraint(['periodo_id'], ['ajuste_periodos.id']),
        sa.ForeignKeyConstraint(['trabajador_id'], ['trabajadores.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_ajuste_tp_periodo_id'), 'ajuste_trabajadores_periodo', ['periodo_id'])
    op.create_index(op.f('ix_ajuste_tp_trabajador_id'), 'ajuste_trabajadores_periodo', ['trabajador_id'])


def downgrade():
    op.drop_table('ajuste_trabajadores_periodo')
