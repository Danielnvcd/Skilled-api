"""add partes (entrega/recibe) a movimientos_inventario

Feature "vale de movimiento": cada ENTRADA/SALIDA/TRASPASO puede registrar quién
ENTREGA y quién RECIBE la mercancía, para el comprobante PDF y la trazabilidad.
Cada parte es un trabajador del sistema (FK trabajadores, SET NULL) o un nombre
libre. Todas las columnas son nullable → los movimientos previos y los internos
(AJUSTE/REASIGNACION) quedan intactos.

Revision ID: movpartes2026
Revises: stkproy2026
Create Date: 2026-07-13 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'movpartes2026'
down_revision = 'stkproy2026'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('movimientos_inventario',
                  sa.Column('entrega_trabajador_id', sa.Integer(), nullable=True))
    op.add_column('movimientos_inventario',
                  sa.Column('entrega_nombre', sa.String(length=200), nullable=True))
    op.add_column('movimientos_inventario',
                  sa.Column('recibe_trabajador_id', sa.Integer(), nullable=True))
    op.add_column('movimientos_inventario',
                  sa.Column('recibe_nombre', sa.String(length=200), nullable=True))
    op.create_foreign_key('fk_mov_entrega_trabajador',
                          'movimientos_inventario', 'trabajadores',
                          ['entrega_trabajador_id'], ['id'], ondelete='SET NULL')
    op.create_foreign_key('fk_mov_recibe_trabajador',
                          'movimientos_inventario', 'trabajadores',
                          ['recibe_trabajador_id'], ['id'], ondelete='SET NULL')


def downgrade():
    op.drop_constraint('fk_mov_recibe_trabajador', 'movimientos_inventario', type_='foreignkey')
    op.drop_constraint('fk_mov_entrega_trabajador', 'movimientos_inventario', type_='foreignkey')
    op.drop_column('movimientos_inventario', 'recibe_nombre')
    op.drop_column('movimientos_inventario', 'recibe_trabajador_id')
    op.drop_column('movimientos_inventario', 'entrega_nombre')
    op.drop_column('movimientos_inventario', 'entrega_trabajador_id')
