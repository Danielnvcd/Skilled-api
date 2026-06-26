"""Add notas (observaciones) to solicitudes_material

Persiste el campo "Observaciones" del pedido que el solicitante/coordinador
captura al armar la solicitud. Antes solo aparecía en la vista previa PDF
(vivía en memoria del navegador) y se perdía al enviar.

Revision ID: s6t7u8v9w0x1
Revises: r5s6t7u8v9w0
Create Date: 2026-06-26 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 's6t7u8v9w0x1'
down_revision = 'r5s6t7u8v9w0'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('solicitudes_material', schema=None) as batch_op:
        batch_op.add_column(sa.Column('notas', sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table('solicitudes_material', schema=None) as batch_op:
        batch_op.drop_column('notas')
