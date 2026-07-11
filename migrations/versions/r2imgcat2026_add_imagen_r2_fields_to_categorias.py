"""add imagen R2 pipeline fields to categorias_config

Mismos campos de estado del pipeline de imágenes → R2 que en `productos`, ahora
para las imágenes de categoría. Todos nullable (los registros existentes quedan
en NULL y siguen mostrando `imagen_url` tal cual).

Revision ID: r2imgcat2026
Revises: d1e2f3a4b5c6
Create Date: 2026-07-11 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'r2imgcat2026'
down_revision = 'd1e2f3a4b5c6'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('categorias_config', sa.Column('imagen_source_url', sa.String(length=500), nullable=True))
    op.add_column('categorias_config', sa.Column('imagen_r2_key', sa.String(length=300), nullable=True))
    op.add_column('categorias_config', sa.Column('imagen_estado', sa.String(length=20), nullable=True))
    op.add_column('categorias_config', sa.Column('imagen_error', sa.String(length=300), nullable=True))


def downgrade():
    op.drop_column('categorias_config', 'imagen_error')
    op.drop_column('categorias_config', 'imagen_estado')
    op.drop_column('categorias_config', 'imagen_r2_key')
    op.drop_column('categorias_config', 'imagen_source_url')
