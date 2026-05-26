"""add herramientas module (catalogo, unidades, asignaciones, mantenimientos,
incidencias, bajas, eventos, media) + user.trabajador_id + solicitudes_detalle
extensions for herramientas

Revision ID: g4h5i6j7k8l9
Revises: e1f2a3b4c5d6, f3a4b5c6d7e8
Create Date: 2026-05-24 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'g4h5i6j7k8l9'
# Merge de las dos heads vivas: la rama de estantes (e1f2a3b4c5d6) y la rama
# de índices de documentos (f3a4b5c6d7e8). Patrón ya usado en c1d2e3f4a5b6.
down_revision = ('e1f2a3b4c5d6', 'f3a4b5c6d7e8')
branch_labels = None
depends_on = None


def upgrade():
    # ─── 1. Catálogo de configuración visual por categoría ──────────────────
    op.create_table(
        'herramienta_categorias',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('nombre', sa.String(length=100), nullable=False),
        sa.Column('imagen_url', sa.String(length=500), nullable=True),
        sa.Column('icono', sa.String(length=50), nullable=True),
        sa.Column('color', sa.String(length=20), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('herramienta_categorias', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_herramienta_categorias_nombre'),
                              ['nombre'], unique=True)

    # ─── 2. Catálogo de herramientas (tipo) ────────────────────────────────
    op.create_table(
        'herramientas',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('sku', sa.String(length=50), nullable=False),
        sa.Column('descripcion', sa.String(length=250), nullable=False),
        sa.Column('clasificacion', sa.String(length=100), nullable=False),
        sa.Column('categoria_id', sa.Integer(), nullable=True),
        sa.Column('marca', sa.String(length=100), nullable=True),
        sa.Column('modelo', sa.String(length=100), nullable=True),
        sa.Column('uso', sa.String(length=50), nullable=True),
        sa.Column('unidad', sa.String(length=50), nullable=False),
        sa.Column('piezas', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('serializada', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('imagen_url', sa.String(length=500), nullable=True),
        sa.Column('activo', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['categoria_id'], ['herramienta_categorias.id']),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('herramientas', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_herramientas_sku'), ['sku'], unique=True)
        batch_op.create_index(batch_op.f('ix_herramientas_clasificacion'),
                              ['clasificacion'], unique=False)
        batch_op.create_index(batch_op.f('ix_herramientas_activo'),
                              ['activo'], unique=False)

    # ─── 3. Unidades físicas ───────────────────────────────────────────────
    op.create_table(
        'herramienta_unidades',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('herramienta_id', sa.Integer(), nullable=False),
        sa.Column('no_serie', sa.String(length=100), nullable=True),
        sa.Column('codigo_interno', sa.String(length=50), nullable=False),
        sa.Column('qr_code', sa.String(length=100), nullable=False),
        sa.Column('estado', sa.String(length=20), nullable=False,
                  server_default='DISPONIBLE'),
        sa.Column('almacen_id', sa.Integer(), nullable=True),
        sa.Column('estante_id', sa.Integer(), nullable=True),
        sa.Column('asignado_trabajador_id', sa.Integer(), nullable=True),
        sa.Column('cantidad', sa.Numeric(10, 2), nullable=False, server_default='1'),
        sa.Column('complementos', sa.String(length=500), nullable=True),
        sa.Column('fecha_adquisicion', sa.Date(), nullable=True),
        sa.Column('costo_adquisicion', sa.Numeric(10, 2), nullable=True),
        sa.Column('vida_util_meses', sa.Integer(), nullable=True),
        sa.Column('observaciones', sa.Text(), nullable=True),
        sa.Column('fecha_baja', sa.DateTime(), nullable=True),
        sa.Column('motivo_baja', sa.String(length=250), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['herramienta_id'], ['herramientas.id']),
        sa.ForeignKeyConstraint(['almacen_id'], ['almacenes.id']),
        sa.ForeignKeyConstraint(['estante_id'], ['estantes.id']),
        sa.ForeignKeyConstraint(['asignado_trabajador_id'], ['trabajadores.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('herramienta_unidades', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_h_unid_herramienta_id'),
                              ['herramienta_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_h_unid_no_serie'),
                              ['no_serie'], unique=True)
        batch_op.create_index(batch_op.f('ix_h_unid_codigo_interno'),
                              ['codigo_interno'], unique=True)
        batch_op.create_index(batch_op.f('ix_h_unid_qr_code'),
                              ['qr_code'], unique=True)
        batch_op.create_index(batch_op.f('ix_h_unid_estado'),
                              ['estado'], unique=False)
        batch_op.create_index(batch_op.f('ix_h_unid_asig_trab'),
                              ['asignado_trabajador_id'], unique=False)
        batch_op.create_index('ix_h_unid_herr_estado',
                              ['herramienta_id', 'estado'], unique=False)

    # ─── 4. Asignaciones / préstamos ───────────────────────────────────────
    op.create_table(
        'asignaciones_herramienta',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('unidad_id', sa.Integer(), nullable=False),
        sa.Column('trabajador_id', sa.Integer(), nullable=False),
        sa.Column('solicitud_id', sa.Integer(), nullable=True),
        sa.Column('proyecto', sa.String(length=200), nullable=True),
        sa.Column('fecha_entrega', sa.DateTime(), nullable=False),
        sa.Column('fecha_devolucion_prevista', sa.DateTime(), nullable=True),
        sa.Column('fecha_devolucion_real', sa.DateTime(), nullable=True),
        sa.Column('estado', sa.String(length=20), nullable=False,
                  server_default='ACTIVA'),
        sa.Column('condicion_entrega', sa.String(length=20), nullable=True),
        sa.Column('condicion_devolucion', sa.String(length=20), nullable=True),
        sa.Column('observaciones_entrega', sa.Text(), nullable=True),
        sa.Column('observaciones_devolucion', sa.Text(), nullable=True),
        sa.Column('entregado_por_id', sa.Integer(), nullable=False),
        sa.Column('recibido_por_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['unidad_id'], ['herramienta_unidades.id']),
        sa.ForeignKeyConstraint(['trabajador_id'], ['trabajadores.id']),
        sa.ForeignKeyConstraint(['solicitud_id'], ['solicitudes_material.id']),
        sa.ForeignKeyConstraint(['entregado_por_id'], ['users.id']),
        sa.ForeignKeyConstraint(['recibido_por_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('asignaciones_herramienta', schema=None) as batch_op:
        batch_op.create_index('ix_asig_unidad_id', ['unidad_id'], unique=False)
        batch_op.create_index('ix_asig_trabajador_id', ['trabajador_id'], unique=False)
        batch_op.create_index('ix_asig_estado', ['estado'], unique=False)
        batch_op.create_index('ix_asig_unidad_estado', ['unidad_id', 'estado'], unique=False)

    # ─── 5. Mantenimientos ─────────────────────────────────────────────────
    op.create_table(
        'mantenimientos_herramienta',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('unidad_id', sa.Integer(), nullable=False),
        sa.Column('tipo', sa.String(length=20), nullable=False),
        sa.Column('motivo', sa.String(length=250), nullable=False),
        sa.Column('proveedor', sa.String(length=150), nullable=True),
        sa.Column('fecha_inicio', sa.DateTime(), nullable=False),
        sa.Column('fecha_fin', sa.DateTime(), nullable=True),
        sa.Column('costo', sa.Numeric(10, 2), nullable=True),
        sa.Column('observaciones', sa.Text(), nullable=True),
        sa.Column('estado_final_unidad', sa.String(length=20), nullable=True),
        sa.Column('estado', sa.String(length=20), nullable=False, server_default='ABIERTO'),
        sa.Column('abierto_por_id', sa.Integer(), nullable=False),
        sa.Column('cerrado_por_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['unidad_id'], ['herramienta_unidades.id']),
        sa.ForeignKeyConstraint(['abierto_por_id'], ['users.id']),
        sa.ForeignKeyConstraint(['cerrado_por_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('mantenimientos_herramienta', schema=None) as batch_op:
        batch_op.create_index('ix_mant_unidad_id', ['unidad_id'], unique=False)
        batch_op.create_index('ix_mant_estado', ['estado'], unique=False)

    # ─── 6. Incidencias ────────────────────────────────────────────────────
    op.create_table(
        'incidencias_herramienta',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('unidad_id', sa.Integer(), nullable=False),
        sa.Column('reportado_por_id', sa.Integer(), nullable=False),
        sa.Column('tipo', sa.String(length=30), nullable=False),
        sa.Column('descripcion', sa.Text(), nullable=False),
        sa.Column('estado', sa.String(length=20), nullable=False, server_default='ABIERTA'),
        sa.Column('fecha_reporte', sa.DateTime(), nullable=False),
        sa.Column('atendido_por_id', sa.Integer(), nullable=True),
        sa.Column('resolucion', sa.Text(), nullable=True),
        sa.Column('fecha_cierre', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['unidad_id'], ['herramienta_unidades.id']),
        sa.ForeignKeyConstraint(['reportado_por_id'], ['users.id']),
        sa.ForeignKeyConstraint(['atendido_por_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('incidencias_herramienta', schema=None) as batch_op:
        batch_op.create_index('ix_inc_unidad_id', ['unidad_id'], unique=False)
        batch_op.create_index('ix_inc_estado', ['estado'], unique=False)
        batch_op.create_index('ix_inc_reportado', ['reportado_por_id'], unique=False)

    # ─── 7. Solicitudes de baja ────────────────────────────────────────────
    op.create_table(
        'solicitudes_baja_herramienta',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('unidad_id', sa.Integer(), nullable=False),
        sa.Column('solicitante_id', sa.Integer(), nullable=False),
        sa.Column('motivo', sa.Text(), nullable=False),
        sa.Column('estado', sa.String(length=20), nullable=False, server_default='PENDIENTE'),
        sa.Column('autorizado_por_id', sa.Integer(), nullable=True),
        sa.Column('ejecutado_por_id', sa.Integer(), nullable=True),
        sa.Column('fecha_solicitud', sa.DateTime(), nullable=False),
        sa.Column('fecha_autorizacion', sa.DateTime(), nullable=True),
        sa.Column('fecha_ejecucion', sa.DateTime(), nullable=True),
        sa.Column('observaciones', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['unidad_id'], ['herramienta_unidades.id']),
        sa.ForeignKeyConstraint(['solicitante_id'], ['users.id']),
        sa.ForeignKeyConstraint(['autorizado_por_id'], ['users.id']),
        sa.ForeignKeyConstraint(['ejecutado_por_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('solicitudes_baja_herramienta', schema=None) as batch_op:
        batch_op.create_index('ix_sbh_unidad_id', ['unidad_id'], unique=False)
        batch_op.create_index('ix_sbh_estado', ['estado'], unique=False)
        batch_op.create_index('ix_sbh_solicitante', ['solicitante_id'], unique=False)

    # ─── 8. Eventos (bitácora dedicada) ────────────────────────────────────
    op.create_table(
        'eventos_herramienta',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('unidad_id', sa.Integer(), nullable=False),
        sa.Column('tipo_evento', sa.String(length=40), nullable=False),
        sa.Column('estado_anterior', sa.String(length=20), nullable=True),
        sa.Column('estado_nuevo', sa.String(length=20), nullable=True),
        sa.Column('usuario_id', sa.Integer(), nullable=False),
        sa.Column('observaciones', sa.Text(), nullable=True),
        sa.Column('referencia_id', sa.Integer(), nullable=True),
        sa.Column('referencia_tipo', sa.String(length=40), nullable=True),
        sa.Column('fecha', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['unidad_id'], ['herramienta_unidades.id']),
        sa.ForeignKeyConstraint(['usuario_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('eventos_herramienta', schema=None) as batch_op:
        batch_op.create_index('ix_evt_unidad_fecha', ['unidad_id', 'fecha'], unique=False)
        batch_op.create_index('ix_evt_tipo', ['tipo_evento'], unique=False)

    # ─── 9. Media (fotos / evidencia) ──────────────────────────────────────
    op.create_table(
        'media_herramienta',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('unidad_id', sa.Integer(), nullable=False),
        sa.Column('evento_id', sa.Integer(), nullable=True),
        sa.Column('tipo', sa.String(length=30), nullable=False),
        sa.Column('ruta_archivo', sa.String(length=500), nullable=False),
        sa.Column('nombre_original', sa.String(length=250), nullable=True),
        sa.Column('mime', sa.String(length=50), nullable=True),
        sa.Column('tamano_bytes', sa.Integer(), nullable=True),
        sa.Column('subido_por_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['unidad_id'], ['herramienta_unidades.id']),
        sa.ForeignKeyConstraint(['evento_id'], ['eventos_herramienta.id']),
        sa.ForeignKeyConstraint(['subido_por_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('media_herramienta', schema=None) as batch_op:
        batch_op.create_index('ix_media_unidad_id', ['unidad_id'], unique=False)
        batch_op.create_index('ix_media_tipo', ['tipo'], unique=False)

    # ─── 10. User.trabajador_id (FK opcional) ──────────────────────────────
    # Permite que el rol solicitante_material vea SUS herramientas asignadas.
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('trabajador_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            'fk_users_trabajador_id', 'trabajadores', ['trabajador_id'], ['id']
        )
        batch_op.create_index('ix_users_trabajador_id', ['trabajador_id'], unique=False)

    # ─── 11. Extensión de solicitudes_material_detalle para herramientas ──
    # Estrategia: agregar columnas con default y luego ajustar producto_id a
    # nullable. Con batch_alter_table SQLite también soporta el cambio.
    with op.batch_alter_table('solicitudes_material_detalle', schema=None) as batch_op:
        batch_op.add_column(sa.Column('tipo_item', sa.String(length=20),
                                      nullable=False, server_default='MATERIAL'))
        batch_op.add_column(sa.Column('herramienta_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('fecha_uso_inicio', sa.Date(), nullable=True))
        batch_op.add_column(sa.Column('fecha_uso_fin', sa.Date(), nullable=True))
        batch_op.add_column(sa.Column('justificacion', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('complementos', sa.String(length=500), nullable=True))
        batch_op.alter_column('producto_id', existing_type=sa.Integer(), nullable=True)
        batch_op.create_foreign_key(
            'fk_smd_herramienta_id', 'herramientas', ['herramienta_id'], ['id']
        )
        batch_op.create_index('ix_smd_tipo_item', ['tipo_item'], unique=False)


def downgrade():
    with op.batch_alter_table('solicitudes_material_detalle', schema=None) as batch_op:
        batch_op.drop_index('ix_smd_tipo_item')
        batch_op.drop_constraint('fk_smd_herramienta_id', type_='foreignkey')
        batch_op.alter_column('producto_id', existing_type=sa.Integer(), nullable=False)
        batch_op.drop_column('complementos')
        batch_op.drop_column('justificacion')
        batch_op.drop_column('fecha_uso_fin')
        batch_op.drop_column('fecha_uso_inicio')
        batch_op.drop_column('herramienta_id')
        batch_op.drop_column('tipo_item')

    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_index('ix_users_trabajador_id')
        batch_op.drop_constraint('fk_users_trabajador_id', type_='foreignkey')
        batch_op.drop_column('trabajador_id')

    op.drop_table('media_herramienta')
    op.drop_table('eventos_herramienta')
    op.drop_table('solicitudes_baja_herramienta')
    op.drop_table('incidencias_herramienta')
    op.drop_table('mantenimientos_herramienta')
    op.drop_table('asignaciones_herramienta')
    op.drop_table('herramienta_unidades')
    op.drop_table('herramientas')
    op.drop_table('herramienta_categorias')
