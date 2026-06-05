"""add totp_backup_codes

Crea la tabla para los códigos de respaldo de 2FA TOTP. Cada usuario tiene N
códigos one-shot que puede usar si pierde el dispositivo autenticador.

Revision ID: q4r5s6t7u8v9
Revises: p3q4r5s6t7u8
Create Date: 2026-06-05

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'q4r5s6t7u8v9'
down_revision = 'p3q4r5s6t7u8'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'totp_backup_codes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('code_hash', sa.String(length=64), nullable=False),
        sa.Column('consumed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_totp_backup_codes_user_id',
        'totp_backup_codes',
        ['user_id'],
        unique=False,
    )
    op.create_index(
        'ix_totp_backup_codes_code_hash',
        'totp_backup_codes',
        ['code_hash'],
        unique=False,
    )


def downgrade():
    op.drop_index('ix_totp_backup_codes_code_hash', table_name='totp_backup_codes')
    op.drop_index('ix_totp_backup_codes_user_id', table_name='totp_backup_codes')
    op.drop_table('totp_backup_codes')
