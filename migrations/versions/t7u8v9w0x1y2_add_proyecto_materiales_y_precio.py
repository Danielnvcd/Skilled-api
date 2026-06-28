"""Proyectos → materiales: precio de producto, proyecto_id en solicitudes y plan de materiales

Feature "Inventario → Proyectos":
  - productos.precio_unitario: precio del catálogo (base de los costos por proyecto).
  - solicitudes_material.proyecto_id: FK real al proyecto (antes solo texto).
  - proyecto_material_plan: presupuesto/plan de materiales por proyecto (lo
    captura Inventario). El consumo se deriva de las solicitudes entregadas.

Backfill: liga las solicitudes existentes a su proyecto cruzando el texto
`proyecto` contra `proyectos.numero_proyecto` (match exacto con trim). Las que
no crucen quedan en NULL.

Revision ID: t7u8v9w0x1y2
Revises: s6t7u8v9w0x1
Create Date: 2026-06-26 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 't7u8v9w0x1y2'
down_revision = 's6t7u8v9w0x1'
branch_labels = None
depends_on = None


def upgrade():
    # 1) Precio en el catálogo de productos.
    with op.batch_alter_table('productos', schema=None) as batch_op:
        batch_op.add_column(sa.Column(
            'precio_unitario', sa.Numeric(12, 2),
            nullable=False, server_default='0',
        ))

    # 2) FK real al proyecto en las solicitudes (nullable por compat histórica).
    with op.batch_alter_table('solicitudes_material', schema=None) as batch_op:
        batch_op.add_column(sa.Column('proyecto_id', sa.Integer(), nullable=True))
        batch_op.create_index(
            batch_op.f('ix_solicitudes_material_proyecto_id'),
            ['proyecto_id'], unique=False,
        )
        batch_op.create_foreign_key(
            'fk_solicitudes_material_proyecto_id',
            'proyectos', ['proyecto_id'], ['id'],
        )

    # 3) Plan de materiales por proyecto (presupuesto base).
    op.create_table(
        'proyecto_material_plan',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('proyecto_id', sa.Integer(), nullable=False),
        sa.Column('producto_id', sa.Integer(), nullable=False),
        sa.Column('cantidad_planeada', sa.Numeric(10, 2), nullable=False, server_default='0'),
        sa.Column('notas', sa.String(length=500), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['proyecto_id'], ['proyectos.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['producto_id'], ['productos.id']),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('proyecto_id', 'producto_id', name='uq_proyecto_producto_plan'),
    )
    op.create_index(
        op.f('ix_proyecto_material_plan_proyecto_id'),
        'proyecto_material_plan', ['proyecto_id'], unique=False,
    )
    op.create_index(
        op.f('ix_proyecto_material_plan_producto_id'),
        'proyecto_material_plan', ['producto_id'], unique=False,
    )

    # 4) Backfill best-effort: ligar solicitudes al proyecto por número.
    # Subconsulta correlacionada (portable Postgres + SQLite, a diferencia de
    # UPDATE ... FROM que es solo Postgres).
    op.execute("""
        UPDATE solicitudes_material
        SET proyecto_id = (
            SELECT p.id FROM proyectos p
            WHERE TRIM(p.numero_proyecto) = TRIM(solicitudes_material.proyecto)
            LIMIT 1
        )
        WHERE proyecto_id IS NULL
          AND proyecto IS NOT NULL
          AND EXISTS (
            SELECT 1 FROM proyectos p
            WHERE TRIM(p.numero_proyecto) = TRIM(solicitudes_material.proyecto)
          )
    """)


def downgrade():
    op.drop_index(op.f('ix_proyecto_material_plan_producto_id'), table_name='proyecto_material_plan')
    op.drop_index(op.f('ix_proyecto_material_plan_proyecto_id'), table_name='proyecto_material_plan')
    op.drop_table('proyecto_material_plan')

    with op.batch_alter_table('solicitudes_material', schema=None) as batch_op:
        batch_op.drop_constraint('fk_solicitudes_material_proyecto_id', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_solicitudes_material_proyecto_id'))
        batch_op.drop_column('proyecto_id')

    with op.batch_alter_table('productos', schema=None) as batch_op:
        batch_op.drop_column('precio_unitario')
