"""add Producto.stock_reservado + backfill desde solicitudes APROBADAS

Pausa 2-bis del plan PLAN_INVENTARIO_MEJORAS_POR_PAUSAS.md.

Agrega la columna `stock_reservado` y la rellena con el "stock comprometido"
por solicitudes ya APROBADAS pero no entregadas. Esto previene el bug donde
dos solicitudes podían aprobarse aunque sumaran más de lo que había en bodega.

Fórmula del backfill:
    Por cada SolicitudMaterialDetalle de SolicitudMaterial.estatus='APROBADA',
    sumar al producto: MAX(0, cantidad_solicitada - cantidad_entregada).
    Solo cuenta detalles tipo MATERIAL (producto_id no nulo), no HERRAMIENTAS.

Revision ID: j7k8l9m0n1o2
Revises: i6j7k8l9m0n1
Create Date: 2026-05-25 16:30:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'j7k8l9m0n1o2'
down_revision = 'i6j7k8l9m0n1'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'productos',
        sa.Column('stock_reservado', sa.Numeric(precision=10, scale=2),
                  nullable=False, server_default='0'),
    )

    # Backfill desde solicitudes APROBADAS no entregadas.
    bind = op.get_bind()
    bind.execute(sa.text("""
        UPDATE productos
        SET stock_reservado = COALESCE(reservas.total, 0)
        FROM (
            SELECT d.producto_id,
                   SUM(GREATEST(
                       COALESCE(d.cantidad_solicitada, 0) - COALESCE(d.cantidad_entregada, 0),
                       0
                   )) AS total
            FROM solicitudes_material_detalle d
            JOIN solicitudes_material s ON s.id = d.solicitud_id
            WHERE s.estatus = 'APROBADA'
              AND d.producto_id IS NOT NULL
            GROUP BY d.producto_id
        ) AS reservas
        WHERE productos.id = reservas.producto_id
    """))


def downgrade():
    op.drop_column('productos', 'stock_reservado')
