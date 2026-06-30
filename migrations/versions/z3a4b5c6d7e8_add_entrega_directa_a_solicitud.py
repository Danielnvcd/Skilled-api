"""add entrega directa (mostrador) a solicitudes_material

Apartado de Entrega directa: el de inventario surte material para un proyecto
en el acto, sin solicitud previa del trabajador. La fila de SolicitudMaterial
queda en ENTREGADA y el solicitante real (trabajador del sistema o nombre
libre) se guarda aparte del capturista.

`solicitudes_material` gana:
  - entrega_directa  (bool, NOT NULL, default 0): marca el flujo de mostrador.
  - solicitante_nombre (varchar 200, NULL): nombre libre de quien recoge.
  - solicitante_trabajador_id (FK trabajadores, NULL): trabajador, si se eligió.

Todas con default/NULL para que las filas existentes queden válidas sin backfill.

Revision ID: z3a4b5c6d7e8
Revises: y2z3a4b5c6d7
Create Date: 2026-06-30

"""
import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = 'z3a4b5c6d7e8'
down_revision = 'y2z3a4b5c6d7'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'solicitudes_material',
        sa.Column('entrega_directa', sa.Boolean(), nullable=False, server_default='0'),
    )
    op.add_column(
        'solicitudes_material',
        sa.Column('solicitante_nombre', sa.String(length=200), nullable=True),
    )
    op.add_column(
        'solicitudes_material',
        sa.Column('solicitante_trabajador_id', sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        'fk_solicitud_solicitante_trabajador',
        'solicitudes_material', 'trabajadores',
        ['solicitante_trabajador_id'], ['id'],
    )


def downgrade():
    op.drop_constraint('fk_solicitud_solicitante_trabajador', 'solicitudes_material', type_='foreignkey')
    op.drop_column('solicitudes_material', 'solicitante_trabajador_id')
    op.drop_column('solicitudes_material', 'solicitante_nombre')
    op.drop_column('solicitudes_material', 'entrega_directa')
