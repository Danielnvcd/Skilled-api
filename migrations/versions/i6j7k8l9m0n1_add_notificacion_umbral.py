"""add notificacion_umbral para idempotencia de STOCK_BAJO (Pausa 5)

Revision ID: i6j7k8l9m0n1
Revises: h5i6j7k8l9m0
Create Date: 2026-05-25 15:30:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'i6j7k8l9m0n1'
down_revision = 'h5i6j7k8l9m0'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'notificacion_umbral',
        sa.Column('producto_id', sa.Integer(), nullable=False),
        sa.Column('fecha', sa.Date(), nullable=False),
        sa.Column('creada_en', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['producto_id'], ['productos.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('producto_id', 'fecha'),
    )


def downgrade():
    op.drop_table('notificacion_umbral')
