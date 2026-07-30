"""add Producto.marca

Agrega una columna TEXTO `marca` al producto (marca / fabricante). Campo
independiente del proveedor default — una marca no es un proveedor. Nullable
para no romper los productos existentes (quedan con marca=NULL).

Revision ID: marca2026
Revises: movpartes2026
Create Date: 2026-07-14 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'marca2026'
down_revision = 'movpartes2026'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'productos',
        sa.Column('marca', sa.String(length=100), nullable=True),
    )


def downgrade():
    op.drop_column('productos', 'marca')
