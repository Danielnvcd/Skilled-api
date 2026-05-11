"""add index on documentos_trabajador tipo_documento

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
Create Date: 2026-05-11 00:01:00.000000

"""
from alembic import op

revision = 'f3a4b5c6d7e8'
down_revision = 'e2f3a4b5c6d7'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('documentos_trabajador', schema=None) as batch_op:
        # Métricas filtra por tipo_documento='Permiso DC3' sobre todos los trabajadores activos
        batch_op.create_index('ix_doctra_tipo_documento', ['trabajador_id', 'tipo_documento'], unique=False)


def downgrade():
    with op.batch_alter_table('documentos_trabajador', schema=None) as batch_op:
        batch_op.drop_index('ix_doctra_tipo_documento')
