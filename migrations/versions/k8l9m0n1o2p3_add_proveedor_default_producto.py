"""add Producto.proveedor_default_nombre + proveedor_default_contacto

Pausa 9 del plan PLAN_INVENTARIO_MEJORAS_POR_PAUSAS.md.

Agrega dos columnas TEXTO al producto para el módulo de Compras express:
genera la OC sin construir aún un módulo completo de proveedores. Si más
adelante se necesita un catálogo formal, se migran a una tabla aparte.

Revision ID: k8l9m0n1o2p3
Revises: j7k8l9m0n1o2
Create Date: 2026-05-25 17:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'k8l9m0n1o2p3'
down_revision = 'j7k8l9m0n1o2'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'productos',
        sa.Column('proveedor_default_nombre', sa.String(length=150), nullable=True),
    )
    op.add_column(
        'productos',
        sa.Column('proveedor_default_contacto', sa.String(length=150), nullable=True),
    )


def downgrade():
    op.drop_column('productos', 'proveedor_default_contacto')
    op.drop_column('productos', 'proveedor_default_nombre')
