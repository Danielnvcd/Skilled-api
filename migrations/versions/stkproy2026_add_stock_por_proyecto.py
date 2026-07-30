"""add stock_almacen_proyecto (stock por proyecto) + proyecto en movimientos

Feature "stock por proyecto": agrega la dimensión PROYECTO al stock físico.

`stock_almacen_proyecto` es la nueva fuente de verdad, de grano
(producto, almacén, proyecto|NULL=general). Los caches previos se conservan:
    Σ proyecto  ⇒  stock_por_almacen.cantidad
    Σ almacén   ⇒  Producto.stock_actual
Backfill: todo el stock actual se deposita en el bucket GENERAL (proyecto_id
NULL) — una fila por cada `stock_por_almacen`, con la misma cantidad. Así el
sistema arranca idéntico a como estaba (todo "sin proyecto") y las nuevas
entradas pueden etiquetar existencias a un proyecto.

Además agrega `proyecto_origen_id`/`proyecto_destino_id` a
`movimientos_inventario` (nullable) para trazar la atribución de proyecto de
cada movimiento (ENTRADA→destino, SALIDA→origen, TRASPASO→ambos igual,
REASIGNACION→origen≠destino en el mismo almacén).

Revision ID: stkproy2026
Revises: r2imgcat2026
Create Date: 2026-07-12 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'stkproy2026'
down_revision = 'r2imgcat2026'
branch_labels = None
depends_on = None


def upgrade():
    # ─── 1. Tabla stock_almacen_proyecto ───────────────────────────────────
    op.create_table(
        'stock_almacen_proyecto',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('producto_id', sa.Integer(), nullable=False),
        sa.Column('almacen_id', sa.Integer(), nullable=False),
        sa.Column('proyecto_id', sa.Integer(), nullable=True),
        sa.Column('cantidad', sa.Numeric(precision=10, scale=2),
                  nullable=False, server_default='0'),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['producto_id'], ['productos.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['almacen_id'], ['almacenes.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['proyecto_id'], ['proyectos.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_stock_almacen_proyecto_producto_id',
                    'stock_almacen_proyecto', ['producto_id'])
    op.create_index('ix_stock_almacen_proyecto_almacen_id',
                    'stock_almacen_proyecto', ['almacen_id'])
    op.create_index('ix_stock_almacen_proyecto_proyecto_id',
                    'stock_almacen_proyecto', ['proyecto_id'])
    op.create_index('ix_sap_almacen_proyecto',
                    'stock_almacen_proyecto', ['almacen_id', 'proyecto_id'])
    # Unicidad por bucket: general (NULL) mapea a 0 vía COALESCE. Índice único
    # funcional — soportado por Postgres (prod).
    op.create_index(
        'uq_stock_almacen_proyecto',
        'stock_almacen_proyecto',
        ['producto_id', 'almacen_id', sa.text('coalesce(proyecto_id, 0)')],
        unique=True,
    )

    # ─── 2. Backfill: stock actual → bucket GENERAL (proyecto NULL) ─────────
    bind = op.get_bind()
    bind.execute(sa.text("""
        INSERT INTO stock_almacen_proyecto
            (producto_id, almacen_id, proyecto_id, cantidad, updated_at)
        SELECT producto_id, almacen_id, NULL, COALESCE(cantidad, 0), CURRENT_TIMESTAMP
        FROM stock_por_almacen
    """))

    # ─── 3. Columnas de proyecto en movimientos_inventario ─────────────────
    op.add_column('movimientos_inventario',
                  sa.Column('proyecto_origen_id', sa.Integer(), nullable=True))
    op.add_column('movimientos_inventario',
                  sa.Column('proyecto_destino_id', sa.Integer(), nullable=True))
    op.create_index('ix_movimientos_inventario_proyecto_origen_id',
                    'movimientos_inventario', ['proyecto_origen_id'])
    op.create_index('ix_movimientos_inventario_proyecto_destino_id',
                    'movimientos_inventario', ['proyecto_destino_id'])
    op.create_foreign_key('fk_mov_proyecto_origen',
                          'movimientos_inventario', 'proyectos',
                          ['proyecto_origen_id'], ['id'], ondelete='SET NULL')
    op.create_foreign_key('fk_mov_proyecto_destino',
                          'movimientos_inventario', 'proyectos',
                          ['proyecto_destino_id'], ['id'], ondelete='SET NULL')


def downgrade():
    op.drop_constraint('fk_mov_proyecto_destino', 'movimientos_inventario', type_='foreignkey')
    op.drop_constraint('fk_mov_proyecto_origen', 'movimientos_inventario', type_='foreignkey')
    op.drop_index('ix_movimientos_inventario_proyecto_destino_id', table_name='movimientos_inventario')
    op.drop_index('ix_movimientos_inventario_proyecto_origen_id', table_name='movimientos_inventario')
    op.drop_column('movimientos_inventario', 'proyecto_destino_id')
    op.drop_column('movimientos_inventario', 'proyecto_origen_id')

    op.drop_index('uq_stock_almacen_proyecto', table_name='stock_almacen_proyecto')
    op.drop_index('ix_sap_almacen_proyecto', table_name='stock_almacen_proyecto')
    op.drop_index('ix_stock_almacen_proyecto_proyecto_id', table_name='stock_almacen_proyecto')
    op.drop_index('ix_stock_almacen_proyecto_almacen_id', table_name='stock_almacen_proyecto')
    op.drop_index('ix_stock_almacen_proyecto_producto_id', table_name='stock_almacen_proyecto')
    op.drop_table('stock_almacen_proyecto')
