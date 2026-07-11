"""add Producto.cable_tipo + cable_calibre

Atributos específicos de CABLE. Cuando la categoría del producto contiene
"cable", la plantilla pide dos datos extra: Tipo (THHN, THW…) y Tamaño
(mm²/AWG). Se guardan como TEXTO nullable en `productos`; la obligatoriedad
(solo para cable) se valida en la capa de aplicación, no en la DB, para no
romper los productos ya existentes.

Revision ID: c9d0e1f2a3b4
Revises: b5c6d7e8f9a0
Create Date: 2026-07-10 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'c9d0e1f2a3b4'
down_revision = 'b5c6d7e8f9a0'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('productos', sa.Column('cable_tipo', sa.String(length=60), nullable=True))
    op.add_column('productos', sa.Column('cable_calibre', sa.String(length=40), nullable=True))


def downgrade():
    op.drop_column('productos', 'cable_calibre')
    op.drop_column('productos', 'cable_tipo')
