"""add índices a audit_log.created_at y audit_log.user

La bitácora y el dashboard ordenan por `created_at DESC` en cada carga y la
tabla `audit_log` crece sin límite (una fila por acción). Sin índice, cada
consulta hacía un full sort de toda la tabla; con cientos de miles de filas
eso degrada notablemente. Se indexa `created_at` (orden/rango por fecha) y
`user` (JOIN audit_log.user = users.username y filtros por usuario).

Revision ID: w0x1y2z3a4b5
Revises: v9w0x1y2z3a4
Create Date: 2026-06-29

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'w0x1y2z3a4b5'
down_revision = 'v9w0x1y2z3a4'
branch_labels = None
depends_on = None


def upgrade():
    op.create_index(
        op.f('ix_audit_log_created_at'),
        'audit_log',
        ['created_at'],
        unique=False,
    )
    op.create_index(
        op.f('ix_audit_log_user'),
        'audit_log',
        ['user'],
        unique=False,
    )


def downgrade():
    op.drop_index(op.f('ix_audit_log_user'), table_name='audit_log')
    op.drop_index(op.f('ix_audit_log_created_at'), table_name='audit_log')
