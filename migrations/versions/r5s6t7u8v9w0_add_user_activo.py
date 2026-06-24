"""add users.activo (borrado lógico de usuarios)

Agrega la columna `activo` a `users` para soportar desactivación en vez de
borrado físico. Un usuario con relaciones en otras tablas (movimientos,
solicitudes, asignaciones…) no puede borrarse sin violar las FKs; en su lugar
se marca `activo=False`: no puede iniciar sesión ni aparece en listas activas,
pero su historial queda intacto.

server_default='1' deja a TODOS los usuarios existentes como activos.

Revision ID: r5s6t7u8v9w0
Revises: q4r5s6t7u8v9
Create Date: 2026-06-24

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'r5s6t7u8v9w0'
down_revision = 'q4r5s6t7u8v9'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'users',
        sa.Column('activo', sa.Boolean(), nullable=False, server_default='1'),
    )
    op.create_index('ix_users_activo', 'users', ['activo'], unique=False)


def downgrade():
    op.drop_index('ix_users_activo', table_name='users')
    op.drop_column('users', 'activo')
