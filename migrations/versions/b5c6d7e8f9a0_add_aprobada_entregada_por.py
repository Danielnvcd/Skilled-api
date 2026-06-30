"""add aprobada_por / entregada_por a solicitudes_material

Trazabilidad de quién resolvió cada solicitud: `aprobada_por_id` (quién la
aprobó) y `entregada_por_id` (quién la surtió). Ambas NULL para filas
existentes; se llenan en adelante al cambiar de estado.

Revision ID: b5c6d7e8f9a0
Revises: z3a4b5c6d7e8
Create Date: 2026-06-30

"""
import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = 'b5c6d7e8f9a0'
down_revision = 'z3a4b5c6d7e8'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('solicitudes_material', sa.Column('aprobada_por_id', sa.Integer(), nullable=True))
    op.add_column('solicitudes_material', sa.Column('entregada_por_id', sa.Integer(), nullable=True))
    op.create_foreign_key(
        'fk_solicitud_aprobada_por', 'solicitudes_material', 'users',
        ['aprobada_por_id'], ['id'],
    )
    op.create_foreign_key(
        'fk_solicitud_entregada_por', 'solicitudes_material', 'users',
        ['entregada_por_id'], ['id'],
    )


def downgrade():
    op.drop_constraint('fk_solicitud_entregada_por', 'solicitudes_material', type_='foreignkey')
    op.drop_constraint('fk_solicitud_aprobada_por', 'solicitudes_material', type_='foreignkey')
    op.drop_column('solicitudes_material', 'entregada_por_id')
    op.drop_column('solicitudes_material', 'aprobada_por_id')
