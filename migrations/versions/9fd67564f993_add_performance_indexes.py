"""add_performance_indexes

Revision ID: 9fd67564f993
Revises: 4f5bae75a729
Create Date: 2026-03-31 20:24:07.280582

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '9fd67564f993'
down_revision = '4f5bae75a729'
branch_labels = None
depends_on = None


def upgrade():
    # Índice compuesto para queries de documentos por trabajador y tipo (dashboard, métricas DC3)
    op.create_index('ix_documentos_trabajador_tipo', 'documentos_trabajador', ['trabajador_id', 'tipo_documento'])
    
    # Índice en FK de credenciales (no se crea automáticamente por SQLAlchemy)
    op.create_index('ix_credenciales_plantas_trabajador', 'credenciales_plantas', ['trabajador_id'])
    
    # Índice compuesto para prenominas: consultas por fecha+estado (histórico, prenomina, reportes)
    op.create_index('ix_prenominas_fecha_estado', 'prenominas', ['fecha_inicio', 'estado'])
    
    # Índice en FK de descuentos_prenomina (mejora recalcular_totales y queries detail)
    op.create_index('ix_descuentos_prenomina_prenomina', 'descuentos_prenomina', ['prenomina_id'])
    
    # Índice en FK de depositos_extra (mejora recalcular_totales)
    op.create_index('ix_depositos_extra_prenomina', 'depositos_extra', ['prenomina_id'])


def downgrade():
    op.drop_index('ix_depositos_extra_prenomina', 'depositos_extra')
    op.drop_index('ix_descuentos_prenomina_prenomina', 'descuentos_prenomina')
    op.drop_index('ix_prenominas_fecha_estado', 'prenominas')
    op.drop_index('ix_credenciales_plantas_trabajador', 'credenciales_plantas')
    op.drop_index('ix_documentos_trabajador_tipo', 'documentos_trabajador')
