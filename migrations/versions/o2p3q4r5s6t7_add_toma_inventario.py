"""add tomas_inventario + tomas_inventario_detalle

Pausa 10 del plan PLAN_INVENTARIO_MEJORAS_POR_PAUSAS.md.

Conteo físico de almacenes. Una toma snapshotea StockPorAlmacen al iniciar
(cantidad_sistema), el usuario captura cantidad_fisica, al cerrar genera
AJUSTES por cada diferencia.

Regla: una sola toma ABIERTA por almacén (partial unique index en PG).

Revision ID: o2p3q4r5s6t7
Revises: n1o2p3q4r5s6
Create Date: 2026-05-26 12:30:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'o2p3q4r5s6t7'
down_revision = 'n1o2p3q4r5s6'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'tomas_inventario',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('almacen_id', sa.Integer,
                  sa.ForeignKey('almacenes.id'), nullable=False, index=True),
        sa.Column('fecha_inicio', sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column('fecha_cierre', sa.DateTime, nullable=True),
        sa.Column('usuario_id', sa.Integer, sa.ForeignKey('users.id'), nullable=False),
        sa.Column('cerrada_por_id', sa.Integer, sa.ForeignKey('users.id'), nullable=True),
        sa.Column('estatus', sa.String(length=20), nullable=False, server_default='ABIERTA', index=True),
        sa.Column('notas', sa.Text, nullable=True),
    )
    # Solo una toma ABIERTA por almacén (PG partial unique).
    op.create_index(
        'one_open_toma_per_almacen',
        'tomas_inventario',
        ['almacen_id'],
        unique=True,
        postgresql_where=sa.text("estatus = 'ABIERTA'"),
    )

    op.create_table(
        'tomas_inventario_detalle',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('toma_id', sa.Integer,
                  sa.ForeignKey('tomas_inventario.id', ondelete='CASCADE'),
                  nullable=False, index=True),
        sa.Column('producto_id', sa.Integer,
                  sa.ForeignKey('productos.id'), nullable=False, index=True),
        sa.Column('cantidad_sistema', sa.Numeric(10, 2), nullable=False, server_default='0'),
        sa.Column('cantidad_fisica', sa.Numeric(10, 2), nullable=True),
        sa.Column('capturado_por_id', sa.Integer, sa.ForeignKey('users.id'), nullable=True),
        sa.Column('capturado_en', sa.DateTime, nullable=True),
        sa.UniqueConstraint('toma_id', 'producto_id', name='uq_toma_producto'),
    )


def downgrade():
    op.drop_table('tomas_inventario_detalle')
    op.drop_index('one_open_toma_per_almacen', table_name='tomas_inventario')
    op.drop_table('tomas_inventario')
