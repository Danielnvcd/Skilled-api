"""add rejilla (filas/columnas) a estantes y posición+cantidad a producto_estante

Pausa 11 — vista visual del estante + stock por celda.

`estantes` gana `filas`/`columnas` (rejilla física; default 1 = comportamiento
plano previo). `producto_estante` gana `fila`/`columna` (posición 1-based,
NULL = sin ubicar) y `cantidad` (sub-libro reconciliado de StockPorAlmacen).

Todas las columnas NOT NULL llevan server_default para que las filas
existentes queden válidas sin backfill manual.

Revision ID: y2z3a4b5c6d7
Revises: x1y2z3a4b5c6
Create Date: 2026-06-29

"""
import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = 'y2z3a4b5c6d7'
down_revision = 'x1y2z3a4b5c6'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('estantes', sa.Column('filas', sa.Integer(), nullable=False, server_default='1'))
    op.add_column('estantes', sa.Column('columnas', sa.Integer(), nullable=False, server_default='1'))
    op.add_column('producto_estante', sa.Column('fila', sa.Integer(), nullable=True))
    op.add_column('producto_estante', sa.Column('columna', sa.Integer(), nullable=True))
    op.add_column(
        'producto_estante',
        sa.Column('cantidad', sa.Numeric(10, 2), nullable=False, server_default='0'),
    )


def downgrade():
    op.drop_column('producto_estante', 'cantidad')
    op.drop_column('producto_estante', 'columna')
    op.drop_column('producto_estante', 'fila')
    op.drop_column('estantes', 'columnas')
    op.drop_column('estantes', 'filas')
