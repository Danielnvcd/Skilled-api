"""add stock_por_almacen + backfill desde Producto.stock_actual

Pausa 2 del plan PLAN_INVENTARIO_MEJORAS_POR_PAUSAS.md.

Crea la tabla `stock_por_almacen` y reparte el stock actual de cada producto
hacia una bodega "principal". La elección de bodega principal es:
  1. Si existe alguna almacén con activo=True → la de menor id.
  2. Si no hay ninguna → se crea una llamada 'Bodega Principal' con un
     qr_code generado y se usa esa.

`Producto.stock_actual` SE CONSERVA como columna (cache denormalizado) — no se
borra. La fuente de verdad pasa a ser `stock_por_almacen`, pero el cache se
mantiene en sync dentro de cada movimiento.

Revision ID: h5i6j7k8l9m0
Revises: g4h5i6j7k8l9
Create Date: 2026-05-25 14:00:00.000000
"""
import uuid

from alembic import op
import sqlalchemy as sa


revision = 'h5i6j7k8l9m0'
down_revision = 'g4h5i6j7k8l9'
branch_labels = None
depends_on = None


def upgrade():
    # ─── 1. Crear la tabla ─────────────────────────────────────────────────
    op.create_table(
        'stock_por_almacen',
        sa.Column('producto_id', sa.Integer(), nullable=False),
        sa.Column('almacen_id', sa.Integer(), nullable=False),
        sa.Column('cantidad', sa.Numeric(precision=10, scale=2),
                  nullable=False, server_default='0'),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['producto_id'], ['productos.id'],
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['almacen_id'], ['almacenes.id'],
                                ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('producto_id', 'almacen_id'),
    )

    # ─── 2. Backfill: elegir/crear bodega principal y depositar stock ──────
    bind = op.get_bind()

    # 2.a) Buscar bodega activa con menor id.
    res = bind.execute(sa.text(
        "SELECT id FROM almacenes WHERE activo = true ORDER BY id ASC LIMIT 1"
    )).fetchone()

    if res is not None:
        almacen_id = res[0]
    else:
        # No hay bodega activa: la creamos con un qr_code uuid.
        nuevo_qr = str(uuid.uuid4())
        bind.execute(sa.text("""
            INSERT INTO almacenes (nombre, ubicacion, qr_code, activo)
            VALUES ('Bodega Principal', 'Migración automática', :qr, true)
        """), {'qr': nuevo_qr})
        res = bind.execute(sa.text(
            "SELECT id FROM almacenes WHERE qr_code = :qr"
        ), {'qr': nuevo_qr}).fetchone()
        almacen_id = res[0]

    # 2.b) Insertar una fila stock_por_almacen por cada producto con
    # stock_actual > 0, usando la bodega elegida. Productos con stock 0
    # también se insertan para que el cliente vea explícitamente que existen
    # en esa bodega aunque sea con cantidad 0.
    bind.execute(sa.text("""
        INSERT INTO stock_por_almacen (producto_id, almacen_id, cantidad, updated_at)
        SELECT id, :alm, COALESCE(stock_actual, 0), CURRENT_TIMESTAMP
        FROM productos
    """), {'alm': almacen_id})


def downgrade():
    # No revertimos la creación de 'Bodega Principal' (puede ya tener datos).
    # Solo eliminamos la tabla; el Producto.stock_actual se conservó intacto.
    op.drop_table('stock_por_almacen')
