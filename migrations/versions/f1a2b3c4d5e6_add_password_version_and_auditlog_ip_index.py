"""add_password_version_and_auditlog_ip_index

Revision ID: f1a2b3c4d5e6
Revises: 5838d810af16
Create Date: 2026-04-11 00:00:00.000000

Seguridad:
  - User.password_version: permite invalidar todas las sesiones activas al cambiar contraseña.
  - AuditLog.ip: índice para acelerar búsquedas por IP en auditorías de seguridad.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f1a2b3c4d5e6'
down_revision = '5838d810af16'
branch_labels = None
depends_on = None


def upgrade():
    # Agregar columna password_version a users.
    # server_default='1' asegura que las filas existentes tengan valor 1 sin necesidad de UPDATE.
    op.add_column(
        'users',
        sa.Column(
            'password_version',
            sa.Integer(),
            nullable=False,
            server_default='1'
        )
    )

    # Índice en AuditLog.ip para búsquedas rápidas por IP
    op.create_index(
        op.f('ix_audit_log_ip'),
        'audit_log',
        ['ip'],
        unique=False
    )


def downgrade():
    op.drop_index(op.f('ix_audit_log_ip'), table_name='audit_log')
    op.drop_column('users', 'password_version')
