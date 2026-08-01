"""add importaciones_catalogo (+ cambios) para deshacer una importación

Guarda cada carga masiva de Excel como un lote, con el detalle por producto y
los valores que tenía cada campo ANTES. Sin ese registro no hay forma segura de
revertir una importación: una carga toca cientos de productos y corregirlos a
mano es inviable.

`antes` / `despues` van como TEXT con JSON serializado en la aplicación: no se
consultan por contenido, y así el esquema es idéntico en Postgres (producción) y
SQLite (tests) sin depender del tipo JSON de cada motor.

Revision ID: impundo2026
Revises: marca2026
Create Date: 2026-07-31 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'impundo2026'
down_revision = 'marca2026'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'importaciones_catalogo',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('usuario_id', sa.Integer(), nullable=True),
        sa.Column('fecha', sa.DateTime(), nullable=True),
        sa.Column('archivo', sa.String(length=250), nullable=True),
        sa.Column('creados', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('actualizados', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('sin_cambios', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('errores', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('estado', sa.String(length=20), nullable=False, server_default='APLICADA'),
        sa.Column('revertida_at', sa.DateTime(), nullable=True),
        sa.Column('revertida_por_id', sa.Integer(), nullable=True),
        sa.Column('revertida_notas', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['usuario_id'], ['users.id'], name='fk_impcat_usuario'),
        sa.ForeignKeyConstraint(['revertida_por_id'], ['users.id'], name='fk_impcat_revertida_por'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_importaciones_catalogo_fecha', 'importaciones_catalogo', ['fecha'])
    op.create_index('ix_importaciones_catalogo_estado', 'importaciones_catalogo', ['estado'])
    op.create_index('ix_importaciones_catalogo_usuario_id', 'importaciones_catalogo', ['usuario_id'])

    op.create_table(
        'importaciones_catalogo_cambios',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('importacion_id', sa.Integer(), nullable=False),
        sa.Column('producto_id', sa.Integer(), nullable=True),
        sa.Column('codigo', sa.String(length=100), nullable=False),
        sa.Column('accion', sa.String(length=12), nullable=False),
        sa.Column('antes', sa.Text(), nullable=True),
        sa.Column('despues', sa.Text(), nullable=True),
        sa.Column('stock_inicial', sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column('almacen_id', sa.Integer(), nullable=True),
        sa.Column('proyecto_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['importacion_id'], ['importaciones_catalogo.id'],
                                name='fk_impcambio_importacion', ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['producto_id'], ['productos.id'], name='fk_impcambio_producto'),
        sa.ForeignKeyConstraint(['almacen_id'], ['almacenes.id'], name='fk_impcambio_almacen'),
        sa.ForeignKeyConstraint(['proyecto_id'], ['proyectos.id'], name='fk_impcambio_proyecto'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_impcambios_importacion_id', 'importaciones_catalogo_cambios',
                    ['importacion_id'])
    op.create_index('ix_impcambios_producto_id', 'importaciones_catalogo_cambios', ['producto_id'])


def downgrade():
    op.drop_index('ix_impcambios_producto_id', table_name='importaciones_catalogo_cambios')
    op.drop_index('ix_impcambios_importacion_id', table_name='importaciones_catalogo_cambios')
    op.drop_table('importaciones_catalogo_cambios')
    op.drop_index('ix_importaciones_catalogo_usuario_id', table_name='importaciones_catalogo')
    op.drop_index('ix_importaciones_catalogo_estado', table_name='importaciones_catalogo')
    op.drop_index('ix_importaciones_catalogo_fecha', table_name='importaciones_catalogo')
    op.drop_table('importaciones_catalogo')
