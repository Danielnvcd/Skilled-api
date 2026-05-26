"""add producto_estante mapping table

Pausa 4 del plan PLAN_INVENTARIO_MEJORAS_POR_PAUSAS.md.

Mapping puro producto↔estante. Permite que al escanear el QR de un estante
desde el móvil veamos qué productos viven en él, sin duplicar cantidades
(StockPorAlmacen sigue siendo la fuente de verdad para stock).

NOTA OPERATIVA: si la BD tiene aplicadas las migraciones de la Pausa 7
revertida (l9m0n1o2p3q4 y m0n1o2p3q4r5), antes de upgradear a este nodo hay
que limpiar manualmente la tabla alembic_version:
  DELETE FROM alembic_version WHERE version_num IN ('l9m0n1o2p3q4', 'm0n1o2p3q4r5');
  INSERT INTO alembic_version (version_num) VALUES ('k8l9m0n1o2p3');

Revision ID: n1o2p3q4r5s6
Revises: k8l9m0n1o2p3
Create Date: 2026-05-26 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'n1o2p3q4r5s6'
down_revision = 'k8l9m0n1o2p3'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'producto_estante',
        sa.Column('producto_id', sa.Integer,
                  sa.ForeignKey('productos.id', ondelete='CASCADE'),
                  primary_key=True),
        sa.Column('estante_id', sa.Integer,
                  sa.ForeignKey('estantes.id', ondelete='CASCADE'),
                  primary_key=True),
        sa.Column('updated_at', sa.DateTime, server_default=sa.func.now()),
    )


def downgrade():
    op.drop_table('producto_estante')
