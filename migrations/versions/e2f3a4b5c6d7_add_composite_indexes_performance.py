"""add composite indexes for performance

Revision ID: e2f3a4b5c6d7
Revises: d1b22089662b
Create Date: 2026-05-11 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'e2f3a4b5c6d7'
down_revision = 'd1b22089662b'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('registros_diarios_horas', schema=None) as batch_op:
        # Consultas frecuentes filtran por reporte + trabajador juntos
        batch_op.create_index('ix_rdh_reporte_trabajador', ['reporte_id', 'trabajador_id'], unique=False)
        # Consultas de prenómina filtran por reporte + fecha
        batch_op.create_index('ix_rdh_reporte_fecha', ['reporte_id', 'fecha'], unique=False)

    with op.batch_alter_table('credenciales_plantas', schema=None) as batch_op:
        # GROUP BY planta en métricas hace table scan sin este índice
        batch_op.create_index('ix_credenciales_plantas_planta', ['planta'], unique=False)

    with op.batch_alter_table('reportes_semanales', schema=None) as batch_op:
        # Filtro (fecha_inicio_semana, estado) usado en prenómina y horas
        batch_op.create_index('ix_reportes_fecha_estado', ['fecha_inicio_semana', 'estado'], unique=False)


def downgrade():
    with op.batch_alter_table('reportes_semanales', schema=None) as batch_op:
        batch_op.drop_index('ix_reportes_fecha_estado')

    with op.batch_alter_table('credenciales_plantas', schema=None) as batch_op:
        batch_op.drop_index('ix_credenciales_plantas_planta')

    with op.batch_alter_table('registros_diarios_horas', schema=None) as batch_op:
        batch_op.drop_index('ix_rdh_reporte_fecha')
        batch_op.drop_index('ix_rdh_reporte_trabajador')
