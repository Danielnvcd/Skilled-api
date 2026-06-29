"""add índice a producto_id en los detalles de solicitud (material y compra)

`solicitudes_material_detalle.producto_id` y `solicitudes_compra_detalle.producto_id`
eran FKs nullable sin índice. Se consultan por producto en:
  - disponibilidad de un producto (cuenta sus líneas de solicitud de material),
  - filtro "En compra" del catálogo y los indicadores de compra activa.
Sin índice, esos lookups escanean la tabla de detalles. Indexados.

Revision ID: x1y2z3a4b5c6
Revises: w0x1y2z3a4b5
Create Date: 2026-06-29

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'x1y2z3a4b5c6'
down_revision = 'w0x1y2z3a4b5'
branch_labels = None
depends_on = None


def upgrade():
    op.create_index(
        op.f('ix_solicitudes_material_detalle_producto_id'),
        'solicitudes_material_detalle',
        ['producto_id'],
        unique=False,
    )
    op.create_index(
        op.f('ix_solicitudes_compra_detalle_producto_id'),
        'solicitudes_compra_detalle',
        ['producto_id'],
        unique=False,
    )


def downgrade():
    op.drop_index(op.f('ix_solicitudes_compra_detalle_producto_id'), table_name='solicitudes_compra_detalle')
    op.drop_index(op.f('ix_solicitudes_material_detalle_producto_id'), table_name='solicitudes_material_detalle')
