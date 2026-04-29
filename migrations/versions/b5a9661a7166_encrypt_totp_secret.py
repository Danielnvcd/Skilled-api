"""encrypt_totp_secret

Revision ID: b5a9661a7166
Revises: c1d2e3f4a5b6
Create Date: 2026-04-29 16:44:16.252670

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b5a9661a7166'
down_revision = 'c1d2e3f4a5b6'
branch_labels = None
depends_on = None


def upgrade():
    # Ampliar columna totp_secret de VARCHAR(32) a VARCHAR(500) para almacenar el
    # ciphertext Fernet. El cifrado/descifrado ocurre en la capa Python (EncryptedString),
    # la BD solo ve el texto cifrado como VARCHAR.
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.alter_column('totp_secret',
               existing_type=sa.VARCHAR(length=32),
               type_=sa.VARCHAR(length=500),
               existing_nullable=True)


def downgrade():
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.alter_column('totp_secret',
               existing_type=sa.VARCHAR(length=500),
               type_=sa.VARCHAR(length=32),
               existing_nullable=True)
