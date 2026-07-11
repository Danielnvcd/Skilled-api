"""add imagen R2 pipeline fields to productos

Campos de estado del pipeline de imágenes → Cloudflare R2 (WebP). Todos
nullable a propósito: los productos existentes quedan con NULL y el catálogo
sigue funcionando igual (mostrando `imagen_url` tal cual). Solo se pueblan
cuando el pipeline de R2 está activo (producción) al importar/editar imágenes
o al correr la sincronización manual del catálogo.

  imagen_source_url  última URL externa de origen (lo que se importó/capturó)
  imagen_r2_key      object key en R2 una vez subida
  imagen_estado      None | PENDIENTE | PROCESANDO | OK | ERROR
  imagen_error       último error legible

Revision ID: d1e2f3a4b5c6
Revises: c9d0e1f2a3b4
Create Date: 2026-07-11 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'd1e2f3a4b5c6'
down_revision = 'c9d0e1f2a3b4'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('productos', sa.Column('imagen_source_url', sa.String(length=500), nullable=True))
    op.add_column('productos', sa.Column('imagen_r2_key', sa.String(length=300), nullable=True))
    op.add_column('productos', sa.Column('imagen_estado', sa.String(length=20), nullable=True))
    op.add_column('productos', sa.Column('imagen_error', sa.String(length=300), nullable=True))


def downgrade():
    op.drop_column('productos', 'imagen_error')
    op.drop_column('productos', 'imagen_estado')
    op.drop_column('productos', 'imagen_r2_key')
    op.drop_column('productos', 'imagen_source_url')
