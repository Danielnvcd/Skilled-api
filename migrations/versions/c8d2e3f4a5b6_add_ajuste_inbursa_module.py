"""add ajuste_periodos and ajuste_descuentos tables

Revision ID: c8d2e3f4a5b6
Revises: b7e1a2c3d4f5
Create Date: 2026-03-06

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'c8d2e3f4a5b6'
down_revision = 'b7e1a2c3d4f5'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('ajuste_periodos',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('nombre', sa.String(100), nullable=False),
        sa.Column('fecha_inicio', sa.Date(), nullable=False),
        sa.Column('fecha_fin', sa.Date(), nullable=False),
        sa.Column('estado', sa.String(20), server_default='ABIERTO', nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_ajuste_periodos_estado'), 'ajuste_periodos', ['estado'])

    op.create_table('ajuste_descuentos',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('periodo_id', sa.Integer(), nullable=False),
        sa.Column('trabajador_id', sa.Integer(), nullable=False),
        sa.Column('monto', sa.Numeric(10, 2), nullable=False),
        sa.Column('fecha_descuento', sa.Date(), nullable=False),
        sa.Column('notas', sa.String(250), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['periodo_id'], ['ajuste_periodos.id']),
        sa.ForeignKeyConstraint(['trabajador_id'], ['trabajadores.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_ajuste_descuentos_periodo_id'), 'ajuste_descuentos', ['periodo_id'])
    op.create_index(op.f('ix_ajuste_descuentos_trabajador_id'), 'ajuste_descuentos', ['trabajador_id'])


def downgrade():
    op.drop_table('ajuste_descuentos')
    op.drop_table('ajuste_periodos')
