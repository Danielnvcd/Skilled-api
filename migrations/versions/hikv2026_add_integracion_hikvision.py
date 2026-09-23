"""integración Hikvision: es_oficina + dispositivos + estado de sincronización

Agrega lo mínimo para administrar lectores biométricos Hikvision desde el ERP.
NO toca nómina, horas, prenómina ni ninguna tabla existente salvo una columna
nueva en `trabajadores`.

`trabajadores` gana:
  - es_oficina (bool, NOT NULL, server_default 'false'): marca al personal de
    oficina, que es el único que puede darse de alta en un lector. Con el
    default, las filas existentes quedan válidas sin backfill y nadie aparece
    en el lector hasta que se le marque a propósito.

Tablas nuevas:
  - hikvision_dispositivos    un lector configurado (host/puerto/credenciales).
                              La contraseña va cifrada con Fernet desde el
                              modelo (EncryptedString), aquí es un varchar.
  - hikvision_sync_empleado   estado por (dispositivo, trabajador), con los
                              hashes que detectan si hay que resincronizar.

Revision ID: hikv2026
Revises: impundo2026
Create Date: 2026-09-22

"""
import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = 'hikv2026'
down_revision = 'impundo2026'
branch_labels = None
depends_on = None


def upgrade():
    # ── trabajadores.es_oficina ──────────────────────────────────────────────
    op.add_column(
        'trabajadores',
        sa.Column('es_oficina', sa.Boolean(), nullable=False, server_default='false'),
    )
    op.create_index('ix_trabajadores_es_oficina', 'trabajadores', ['es_oficina'])

    # ── hikvision_dispositivos ───────────────────────────────────────────────
    op.create_table(
        'hikvision_dispositivos',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('nombre', sa.String(length=120), nullable=False),
        sa.Column('host', sa.String(length=120), nullable=False),
        sa.Column('puerto', sa.Integer(), nullable=False, server_default='80'),
        sa.Column('usuario', sa.String(length=64), nullable=False),
        # EncryptedString persiste el token Fernet como texto; 500 sobra.
        sa.Column('password', sa.String(length=500), nullable=False),
        sa.Column('activo', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('modelo', sa.String(length=100), nullable=True),
        sa.Column('numero_serie', sa.String(length=120), nullable=True),
        sa.Column('firmware', sa.String(length=60), nullable=True),
        sa.Column('ultima_conexion', sa.DateTime(), nullable=True),
        sa.Column('ultimo_estado', sa.String(length=20), nullable=True),
        sa.Column('ultimo_error', sa.String(length=500), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('host', 'puerto', name='uq_hikvision_host_puerto'),
    )
    op.create_index('ix_hikvision_dispositivos_activo', 'hikvision_dispositivos', ['activo'])

    # ── hikvision_sync_empleado ──────────────────────────────────────────────
    op.create_table(
        'hikvision_sync_empleado',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('dispositivo_id', sa.Integer(), nullable=False),
        sa.Column('trabajador_id', sa.Integer(), nullable=False),
        # 32 = largo máximo real de employeeNo en el equipo (leído de
        # /ISAPI/AccessControl/UserInfo/capabilities).
        sa.Column('employee_no_remoto', sa.String(length=32), nullable=False),
        sa.Column('estado', sa.String(length=20), nullable=False, server_default='PENDIENTE'),
        sa.Column('hash_datos', sa.String(length=64), nullable=True),
        sa.Column('hash_foto', sa.String(length=64), nullable=True),
        sa.Column('ultimo_error', sa.String(length=500), nullable=True),
        sa.Column('ultimo_intento', sa.DateTime(), nullable=True),
        sa.Column('sincronizado_en', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        # Borrar el lector (o el trabajador) se lleva su historial de sync:
        # sin el dispositivo la fila no significa nada.
        sa.ForeignKeyConstraint(['dispositivo_id'], ['hikvision_dispositivos.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['trabajador_id'], ['trabajadores.id'], ondelete='CASCADE'),
        sa.UniqueConstraint('dispositivo_id', 'trabajador_id', name='uq_hikvision_sync_disp_trab'),
        sa.UniqueConstraint('dispositivo_id', 'employee_no_remoto', name='uq_hikvision_sync_disp_empno'),
    )
    op.create_index('ix_hikvision_sync_dispositivo', 'hikvision_sync_empleado', ['dispositivo_id'])
    op.create_index('ix_hikvision_sync_trabajador', 'hikvision_sync_empleado', ['trabajador_id'])
    op.create_index('ix_hikvision_sync_estado', 'hikvision_sync_empleado', ['estado'])


def downgrade():
    op.drop_index('ix_hikvision_sync_estado', table_name='hikvision_sync_empleado')
    op.drop_index('ix_hikvision_sync_trabajador', table_name='hikvision_sync_empleado')
    op.drop_index('ix_hikvision_sync_dispositivo', table_name='hikvision_sync_empleado')
    op.drop_table('hikvision_sync_empleado')

    op.drop_index('ix_hikvision_dispositivos_activo', table_name='hikvision_dispositivos')
    op.drop_table('hikvision_dispositivos')

    op.drop_index('ix_trabajadores_es_oficina', table_name='trabajadores')
    op.drop_column('trabajadores', 'es_oficina')
