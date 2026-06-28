"""Proyectos → materiales: bitácora de cambios del plan (historial)

Tabla `proyecto_plan_historial`: una fila por cada guardado del plan de
materiales, con quién, cuándo y el desglose de cambios
(agregados / modificados / eliminados) para el panel de historial.

Revision ID: u8v9w0x1y2z3
Revises: t7u8v9w0x1y2
Create Date: 2026-06-27 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'u8v9w0x1y2z3'
down_revision = 't7u8v9w0x1y2'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'proyecto_plan_historial',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('proyecto_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('usuario', sa.String(length=80), nullable=True),
        sa.Column('resumen', sa.String(length=500), nullable=True),
        sa.Column('cambios', sa.JSON(), nullable=True),
        sa.Column('n_agregados', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('n_modificados', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('n_eliminados', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['proyecto_id'], ['proyectos.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_proyecto_plan_historial_proyecto_id'),
        'proyecto_plan_historial', ['proyecto_id'], unique=False,
    )
    op.create_index(
        op.f('ix_proyecto_plan_historial_created_at'),
        'proyecto_plan_historial', ['created_at'], unique=False,
    )


def downgrade():
    op.drop_index(op.f('ix_proyecto_plan_historial_created_at'), table_name='proyecto_plan_historial')
    op.drop_index(op.f('ix_proyecto_plan_historial_proyecto_id'), table_name='proyecto_plan_historial')
    op.drop_table('proyecto_plan_historial')
