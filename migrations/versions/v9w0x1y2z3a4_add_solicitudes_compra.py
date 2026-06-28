"""Solicitudes de compra (procura): registro persistente de la lista de compra

Tablas:
  - `solicitudes_compra`: cabecera (quién pide, proveedor sugerido, proyecto,
    prioridad, estatus PENDIENTE/ORDENADA/RECIBIDA/CANCELADA).
  - `solicitudes_compra_detalle`: líneas (producto del catálogo o ítem de texto
    libre, cantidad solicitada/recibida, precio estimado).

Revision ID: v9w0x1y2z3a4
Revises: u8v9w0x1y2z3
Create Date: 2026-06-27 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'v9w0x1y2z3a4'
down_revision = 'u8v9w0x1y2z3'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'solicitudes_compra',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('solicitado_por_id', sa.Integer(), nullable=False),
        sa.Column('proveedor_sugerido', sa.String(length=150), nullable=True),
        sa.Column('proveedor_contacto', sa.String(length=150), nullable=True),
        sa.Column('proyecto_id', sa.Integer(), nullable=True),
        sa.Column('prioridad', sa.String(length=20), nullable=False, server_default='MEDIA'),
        sa.Column('estatus', sa.String(length=20), nullable=False, server_default='PENDIENTE'),
        sa.Column('notas', sa.Text(), nullable=True),
        sa.Column('fecha_creacion', sa.DateTime(), nullable=True),
        sa.Column('fecha_orden', sa.DateTime(), nullable=True),
        sa.Column('fecha_cierre', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['solicitado_por_id'], ['users.id']),
        sa.ForeignKeyConstraint(['proyecto_id'], ['proyectos.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_solicitudes_compra_solicitado_por_id'),
        'solicitudes_compra', ['solicitado_por_id'], unique=False,
    )
    op.create_index(
        op.f('ix_solicitudes_compra_proyecto_id'),
        'solicitudes_compra', ['proyecto_id'], unique=False,
    )
    op.create_index(
        op.f('ix_solicitudes_compra_estatus'),
        'solicitudes_compra', ['estatus'], unique=False,
    )
    op.create_index(
        op.f('ix_solicitudes_compra_fecha_creacion'),
        'solicitudes_compra', ['fecha_creacion'], unique=False,
    )

    op.create_table(
        'solicitudes_compra_detalle',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('solicitud_compra_id', sa.Integer(), nullable=False),
        sa.Column('producto_id', sa.Integer(), nullable=True),
        sa.Column('descripcion_libre', sa.String(length=250), nullable=True),
        sa.Column('unidad', sa.String(length=50), nullable=True),
        sa.Column('cantidad_solicitada', sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column('cantidad_recibida', sa.Numeric(precision=10, scale=2), nullable=False, server_default='0'),
        sa.Column('precio_estimado', sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column('notas', sa.String(length=500), nullable=True),
        sa.ForeignKeyConstraint(['solicitud_compra_id'], ['solicitudes_compra.id']),
        sa.ForeignKeyConstraint(['producto_id'], ['productos.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_solicitudes_compra_detalle_solicitud_compra_id'),
        'solicitudes_compra_detalle', ['solicitud_compra_id'], unique=False,
    )


def downgrade():
    op.drop_index(
        op.f('ix_solicitudes_compra_detalle_solicitud_compra_id'),
        table_name='solicitudes_compra_detalle',
    )
    op.drop_table('solicitudes_compra_detalle')
    op.drop_index(op.f('ix_solicitudes_compra_fecha_creacion'), table_name='solicitudes_compra')
    op.drop_index(op.f('ix_solicitudes_compra_estatus'), table_name='solicitudes_compra')
    op.drop_index(op.f('ix_solicitudes_compra_proyecto_id'), table_name='solicitudes_compra')
    op.drop_index(op.f('ix_solicitudes_compra_solicitado_por_id'), table_name='solicitudes_compra')
    op.drop_table('solicitudes_compra')
