"""Tests de Pausa 8b — entrega parcial y edición de cantidad aprobada.

Cubre los endpoints nuevos:
  - PATCH /api/v1/solicitudes/<id>/detalles/<det_id>
  - POST  /api/v1/solicitudes/<id>/entregar

Patrón de auth: JWT real (Bearer), no session — los endpoints usan @jwt_required
estricto. Ver memoria `tests-session-vs-jwt`.
"""
import uuid
from decimal import Decimal

import pytest
from werkzeug.security import generate_password_hash

from app.models import (
    User, Almacen, Producto, StockPorAlmacen, StockAlmacenProyecto,
    MovimientoInventario,
    SolicitudMaterial, SolicitudMaterialDetalle,
    Trabajador, Herramienta, HerramientaUnidad, AsignacionHerramienta,
    Estante, ProductoEstante,
)
from app.routes.api_auth import _encode_access_token


def _hdr(user):
    return {'Authorization': f'Bearer {_encode_access_token(user)}'}


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def inv_admin(db):
    u = User(username='ep_admin', password_hash=generate_password_hash('Pass123!'), role='admin')
    db.session.add(u); db.session.commit()
    return u


@pytest.fixture
def inv_user(db):
    u = User(username='ep_inv', password_hash=generate_password_hash('Pass123!'), role='inventario')
    db.session.add(u); db.session.commit()
    return u


@pytest.fixture
def solicitante(db):
    u = User(username='ep_sol', password_hash=generate_password_hash('Pass123!'),
             role='solicitante_material')
    db.session.add(u); db.session.commit()
    return u


@pytest.fixture
def almacen(db):
    a = Almacen(nombre='Bodega EP', qr_code=str(uuid.uuid4()), activo=True)
    db.session.add(a); db.session.commit()
    return a


@pytest.fixture
def producto(db, almacen):
    p = Producto(
        codigo='EP-001', descripcion='Cinta de aislar', categoria='Suministros',
        unidad='pza', stock_actual=Decimal('100'), stock_reservado=Decimal('0'),
        stock_minimo=0, activo=True,
    )
    db.session.add(p); db.session.flush()
    db.session.add(StockAlmacenProyecto(producto_id=p.id, almacen_id=almacen.id,
                                        proyecto_id=None, cantidad=Decimal('100')))
    db.session.add(StockPorAlmacen(producto_id=p.id, almacen_id=almacen.id,
                                    cantidad=Decimal('100')))
    db.session.commit()
    return p


@pytest.fixture
def producto2(db, almacen):
    p = Producto(
        codigo='EP-002', descripcion='Tornillo 1/4"', categoria='Suministros',
        unidad='pza', stock_actual=Decimal('50'), stock_reservado=Decimal('0'),
        stock_minimo=0, activo=True,
    )
    db.session.add(p); db.session.flush()
    db.session.add(StockAlmacenProyecto(producto_id=p.id, almacen_id=almacen.id,
                                        proyecto_id=None, cantidad=Decimal('50')))
    db.session.add(StockPorAlmacen(producto_id=p.id, almacen_id=almacen.id,
                                    cantidad=Decimal('50')))
    db.session.commit()
    return p


@pytest.fixture
def producto_kg(db, almacen):
    """Material con unidad continua (kg) → admite decimales."""
    p = Producto(
        codigo='EP-KG', descripcion='Cable por kilo', categoria='Suministros',
        unidad='kg', stock_actual=Decimal('100'), stock_reservado=Decimal('0'),
        stock_minimo=0, activo=True,
    )
    db.session.add(p); db.session.flush()
    db.session.add(StockAlmacenProyecto(producto_id=p.id, almacen_id=almacen.id,
                                        proyecto_id=None, cantidad=Decimal('100')))
    db.session.add(StockPorAlmacen(producto_id=p.id, almacen_id=almacen.id,
                                    cantidad=Decimal('100')))
    db.session.commit()
    return p


def _crear_y_aprobar(client, solicitante, admin, lineas):
    """Helper: crea solicitud con `lineas` y la pasa a APROBADA.
    Devuelve el dict del response final (con detalles)."""
    r = client.post('/api/v1/solicitudes/', headers=_hdr(solicitante), json={
        'proyecto': 'Proyecto Test',
        'detalles': lineas,
    })
    assert r.status_code == 200, r.get_json()
    sol_id = r.get_json()['id']
    ra = client.patch(f'/api/v1/solicitudes/{sol_id}/estado',
                      headers=_hdr(admin), json={'estatus': 'APROBADA'})
    assert ra.status_code == 200, ra.get_json()
    return ra.get_json()


# ═══════════════════════════════════════════════════════════════════════════════
# PATCH /solicitudes/<id>/detalles/<det_id>
# ═══════════════════════════════════════════════════════════════════════════════

class TestPatchDetalle:

    def test_aprobar_siembra_cantidad_aprobada(self, client, solicitante, inv_admin, producto):
        """Al pasar a APROBADA, cantidad_aprobada queda = cantidad_solicitada (Pausa 8b)."""
        sol = _crear_y_aprobar(client, solicitante, inv_admin, [
            {'tipo_item': 'MATERIAL', 'producto_id': producto.id, 'cantidad_solicitada': 10},
        ])
        det = sol['detalles'][0]
        assert det['cantidad_aprobada'] == 10
        assert det['cantidad_entregada'] == 0

    def test_aprobar_registra_quien_aprobo(self, client, solicitante, inv_admin, producto):
        """Al aprobar se guarda quién la aceptó (aprobada_por)."""
        sol = _crear_y_aprobar(client, solicitante, inv_admin, [
            {'tipo_item': 'MATERIAL', 'producto_id': producto.id, 'cantidad_solicitada': 10},
        ])
        assert sol['aprobada_por'] == (inv_admin.full_name or inv_admin.username)
        assert sol['entregada_por'] is None
        assert sol['entrega_directa'] is False

    def test_bajar_cantidad_aprobada_libera_reserva(self, client, db, solicitante, inv_admin, producto):
        sol = _crear_y_aprobar(client, solicitante, inv_admin, [
            {'tipo_item': 'MATERIAL', 'producto_id': producto.id, 'cantidad_solicitada': 10},
        ])
        # Antes: 10 reservados
        db.session.expire(producto)
        assert float(producto.stock_reservado) == 10.0

        det_id = sol['detalles'][0]['id']
        r = client.patch(
            f'/api/v1/solicitudes/{sol["id"]}/detalles/{det_id}',
            headers=_hdr(inv_admin), json={'cantidad_aprobada': 4},
        )
        assert r.status_code == 200, r.get_json()
        assert r.get_json()['cantidad_aprobada'] == 4

        db.session.expire(producto)
        assert float(producto.stock_reservado) == 4.0  # liberó 6

    def test_subir_cantidad_aprobada_intenta_reservar(self, client, db, solicitante, inv_admin, producto):
        sol = _crear_y_aprobar(client, solicitante, inv_admin, [
            {'tipo_item': 'MATERIAL', 'producto_id': producto.id, 'cantidad_solicitada': 10},
        ])
        det_id = sol['detalles'][0]['id']
        # Primero bajar a 4
        client.patch(f'/api/v1/solicitudes/{sol["id"]}/detalles/{det_id}',
                     headers=_hdr(inv_admin), json={'cantidad_aprobada': 4})
        # Subir a 8
        r = client.patch(f'/api/v1/solicitudes/{sol["id"]}/detalles/{det_id}',
                          headers=_hdr(inv_admin), json={'cantidad_aprobada': 8})
        assert r.status_code == 200
        db.session.expire(producto)
        assert float(producto.stock_reservado) == 8.0

    def test_no_puede_aprobar_mas_que_solicitada(self, client, solicitante, inv_admin, producto):
        sol = _crear_y_aprobar(client, solicitante, inv_admin, [
            {'tipo_item': 'MATERIAL', 'producto_id': producto.id, 'cantidad_solicitada': 5},
        ])
        det_id = sol['detalles'][0]['id']
        r = client.patch(f'/api/v1/solicitudes/{sol["id"]}/detalles/{det_id}',
                          headers=_hdr(inv_admin), json={'cantidad_aprobada': 6})
        assert r.status_code == 422

    def test_no_puede_aprobar_menos_que_entregada(self, client, solicitante, inv_admin, producto, almacen):
        sol = _crear_y_aprobar(client, solicitante, inv_admin, [
            {'tipo_item': 'MATERIAL', 'producto_id': producto.id, 'cantidad_solicitada': 10},
        ])
        det_id = sol['detalles'][0]['id']
        # Entregar 4
        client.post(f'/api/v1/solicitudes/{sol["id"]}/entregar', headers=_hdr(inv_admin), json={
            'almacen_origen_id': almacen.id,
            'entregas': [{'detalle_id': det_id, 'cantidad_entregada': 4}],
        })
        # Intentar bajar aprobada a 3 (menor que 4 ya entregada)
        r = client.patch(f'/api/v1/solicitudes/{sol["id"]}/detalles/{det_id}',
                          headers=_hdr(inv_admin), json={'cantidad_aprobada': 3})
        assert r.status_code == 422

    def test_patch_solo_aprobadas(self, client, solicitante, inv_admin, producto):
        """No se puede editar cantidad_aprobada si la solicitud no está APROBADA."""
        r = client.post('/api/v1/solicitudes/', headers=_hdr(solicitante), json={
            'proyecto': 'Proyecto Test',
            'detalles': [{'tipo_item': 'MATERIAL', 'producto_id': producto.id,
                          'cantidad_solicitada': 5}],
        })
        sol = r.get_json()
        det_id = sol['detalles'][0]['id']
        rp = client.patch(f'/api/v1/solicitudes/{sol["id"]}/detalles/{det_id}',
                           headers=_hdr(inv_admin), json={'cantidad_aprobada': 3})
        assert rp.status_code == 409

    def test_patch_solo_inventario(self, client, solicitante, inv_admin, producto):
        sol = _crear_y_aprobar(client, solicitante, inv_admin, [
            {'tipo_item': 'MATERIAL', 'producto_id': producto.id, 'cantidad_solicitada': 5},
        ])
        det_id = sol['detalles'][0]['id']
        r = client.patch(f'/api/v1/solicitudes/{sol["id"]}/detalles/{det_id}',
                          headers=_hdr(solicitante), json={'cantidad_aprobada': 3})
        assert r.status_code == 403


# ═══════════════════════════════════════════════════════════════════════════════
# POST /solicitudes/<id>/entregar
# ═══════════════════════════════════════════════════════════════════════════════

class TestEntregar:

    def test_entrega_total_marca_entregada(self, client, db, solicitante, inv_admin, producto, almacen):
        sol = _crear_y_aprobar(client, solicitante, inv_admin, [
            {'tipo_item': 'MATERIAL', 'producto_id': producto.id, 'cantidad_solicitada': 10},
        ])
        det_id = sol['detalles'][0]['id']
        r = client.post(f'/api/v1/solicitudes/{sol["id"]}/entregar',
                         headers=_hdr(inv_admin), json={
            'almacen_origen_id': almacen.id,
            'entregas': [{'detalle_id': det_id, 'cantidad_entregada': 10}],
        })
        assert r.status_code == 200, r.get_json()
        body = r.get_json()
        assert body['estatus'] == 'ENTREGADA'
        assert body['detalles'][0]['cantidad_entregada'] == 10

        db.session.expire(producto)
        assert float(producto.stock_actual) == 90.0  # 100 - 10
        assert float(producto.stock_reservado) == 0.0  # reserva liberada
        # Movimiento SALIDA creado
        movs = MovimientoInventario.query.filter_by(producto_id=producto.id, tipo='SALIDA').all()
        assert len(movs) == 1
        assert float(movs[0].cantidad) == 10.0
        assert movs[0].almacen_origen_id == almacen.id

    def test_entrega_parcial_mantiene_aprobada(self, client, db, solicitante, inv_admin, producto, almacen):
        sol = _crear_y_aprobar(client, solicitante, inv_admin, [
            {'tipo_item': 'MATERIAL', 'producto_id': producto.id, 'cantidad_solicitada': 10},
        ])
        det_id = sol['detalles'][0]['id']
        r = client.post(f'/api/v1/solicitudes/{sol["id"]}/entregar',
                         headers=_hdr(inv_admin), json={
            'almacen_origen_id': almacen.id,
            'entregas': [{'detalle_id': det_id, 'cantidad_entregada': 4}],
        })
        assert r.status_code == 200, r.get_json()
        body = r.get_json()
        assert body['estatus'] == 'APROBADA'
        assert body['detalles'][0]['cantidad_entregada'] == 4

        db.session.expire(producto)
        assert float(producto.stock_actual) == 96.0  # 100 - 4
        assert float(producto.stock_reservado) == 6.0  # 10 - 4 sigue apartado

    def test_dos_entregas_parciales_completan(self, client, db, solicitante, inv_admin, producto, almacen):
        sol = _crear_y_aprobar(client, solicitante, inv_admin, [
            {'tipo_item': 'MATERIAL', 'producto_id': producto.id, 'cantidad_solicitada': 10},
        ])
        det_id = sol['detalles'][0]['id']
        # Entrega 1: 4
        r1 = client.post(f'/api/v1/solicitudes/{sol["id"]}/entregar',
                         headers=_hdr(inv_admin), json={
            'almacen_origen_id': almacen.id,
            'entregas': [{'detalle_id': det_id, 'cantidad_entregada': 4}],
        })
        assert r1.status_code == 200
        assert r1.get_json()['estatus'] == 'APROBADA'
        # Entrega 2: 6 (completa)
        r2 = client.post(f'/api/v1/solicitudes/{sol["id"]}/entregar',
                         headers=_hdr(inv_admin), json={
            'almacen_origen_id': almacen.id,
            'entregas': [{'detalle_id': det_id, 'cantidad_entregada': 6}],
        })
        assert r2.status_code == 200
        assert r2.get_json()['estatus'] == 'ENTREGADA'

        db.session.expire(producto)
        assert float(producto.stock_actual) == 90.0
        assert float(producto.stock_reservado) == 0.0

    def test_entrega_multilinea_solo_completa_si_todas(self, client, db, solicitante, inv_admin,
                                                          producto, producto2, almacen):
        sol = _crear_y_aprobar(client, solicitante, inv_admin, [
            {'tipo_item': 'MATERIAL', 'producto_id': producto.id, 'cantidad_solicitada': 5},
            {'tipo_item': 'MATERIAL', 'producto_id': producto2.id, 'cantidad_solicitada': 3},
        ])
        det_a = sol['detalles'][0]['id']
        det_b = sol['detalles'][1]['id']
        # Entregar todo del primero, nada del segundo
        r = client.post(f'/api/v1/solicitudes/{sol["id"]}/entregar',
                         headers=_hdr(inv_admin), json={
            'almacen_origen_id': almacen.id,
            'entregas': [
                {'detalle_id': det_a, 'cantidad_entregada': 5},
                {'detalle_id': det_b, 'cantidad_entregada': 0},  # se ignora
            ],
        })
        assert r.status_code == 200
        assert r.get_json()['estatus'] == 'APROBADA'  # falta producto2
        # Entregar el resto
        r2 = client.post(f'/api/v1/solicitudes/{sol["id"]}/entregar',
                         headers=_hdr(inv_admin), json={
            'almacen_origen_id': almacen.id,
            'entregas': [{'detalle_id': det_b, 'cantidad_entregada': 3}],
        })
        assert r2.status_code == 200
        assert r2.get_json()['estatus'] == 'ENTREGADA'

    def test_no_puede_entregar_mas_que_aprobada(self, client, solicitante, inv_admin, producto, almacen):
        sol = _crear_y_aprobar(client, solicitante, inv_admin, [
            {'tipo_item': 'MATERIAL', 'producto_id': producto.id, 'cantidad_solicitada': 5},
        ])
        det_id = sol['detalles'][0]['id']
        r = client.post(f'/api/v1/solicitudes/{sol["id"]}/entregar',
                         headers=_hdr(inv_admin), json={
            'almacen_origen_id': almacen.id,
            'entregas': [{'detalle_id': det_id, 'cantidad_entregada': 6}],
        })
        assert r.status_code == 422

    def test_no_puede_entregar_si_stock_insuficiente_en_almacen(self, client, db, solicitante,
                                                                  inv_admin, almacen):
        # Producto con 2 en bodega pero stock_actual sembrado en 10 (caso degenerado)
        p = Producto(codigo='EP-LOW', descripcion='Bajo', categoria='X', unidad='pza',
                     stock_actual=Decimal('10'), stock_reservado=Decimal('0'),
                     stock_minimo=0, activo=True)
        db.session.add(p); db.session.flush()
        db.session.add(StockAlmacenProyecto(producto_id=p.id, almacen_id=almacen.id,
                                            proyecto_id=None, cantidad=Decimal('2')))
        db.session.add(StockPorAlmacen(producto_id=p.id, almacen_id=almacen.id,
                                        cantidad=Decimal('2')))
        db.session.commit()
        sol = _crear_y_aprobar(client, solicitante, inv_admin, [
            {'tipo_item': 'MATERIAL', 'producto_id': p.id, 'cantidad_solicitada': 5},
        ])
        det_id = sol['detalles'][0]['id']
        r = client.post(f'/api/v1/solicitudes/{sol["id"]}/entregar',
                         headers=_hdr(inv_admin), json={
            'almacen_origen_id': almacen.id,
            'entregas': [{'detalle_id': det_id, 'cantidad_entregada': 5}],
        })
        assert r.status_code == 409
        assert 'insuficiente' in r.get_json()['detail'].lower()

    def test_solo_solicitudes_aprobadas(self, client, solicitante, inv_admin, producto, almacen):
        r = client.post('/api/v1/solicitudes/', headers=_hdr(solicitante), json={
            'proyecto': 'Proyecto Test',
            'detalles': [{'tipo_item': 'MATERIAL', 'producto_id': producto.id,
                          'cantidad_solicitada': 5}],
        })
        sol = r.get_json()
        re = client.post(f'/api/v1/solicitudes/{sol["id"]}/entregar',
                          headers=_hdr(inv_admin), json={
            'almacen_origen_id': almacen.id,
            'entregas': [{'detalle_id': sol['detalles'][0]['id'], 'cantidad_entregada': 1}],
        })
        assert re.status_code == 409

    def test_detalle_de_otra_solicitud(self, client, solicitante, inv_admin, producto, almacen):
        sol_a = _crear_y_aprobar(client, solicitante, inv_admin, [
            {'tipo_item': 'MATERIAL', 'producto_id': producto.id, 'cantidad_solicitada': 3},
        ])
        sol_b = _crear_y_aprobar(client, solicitante, inv_admin, [
            {'tipo_item': 'MATERIAL', 'producto_id': producto.id, 'cantidad_solicitada': 2},
        ])
        det_b = sol_b['detalles'][0]['id']
        r = client.post(f'/api/v1/solicitudes/{sol_a["id"]}/entregar',
                         headers=_hdr(inv_admin), json={
            'almacen_origen_id': almacen.id,
            'entregas': [{'detalle_id': det_b, 'cantidad_entregada': 1}],  # detalle de otra
        })
        assert r.status_code == 422

    def test_entregar_solo_inventario(self, client, solicitante, inv_admin, producto, almacen):
        sol = _crear_y_aprobar(client, solicitante, inv_admin, [
            {'tipo_item': 'MATERIAL', 'producto_id': producto.id, 'cantidad_solicitada': 3},
        ])
        det_id = sol['detalles'][0]['id']
        r = client.post(f'/api/v1/solicitudes/{sol["id"]}/entregar',
                         headers=_hdr(solicitante), json={
            'almacen_origen_id': almacen.id,
            'entregas': [{'detalle_id': det_id, 'cantidad_entregada': 1}],
        })
        assert r.status_code == 403

    def test_entrega_parcial_luego_patch_baja_aprobada(self, client, db, solicitante, inv_admin,
                                                         producto, almacen):
        """Flujo: aprobar 10, entregar 4, luego bajar aprobado a 4 → ENTREGADA implícita."""
        sol = _crear_y_aprobar(client, solicitante, inv_admin, [
            {'tipo_item': 'MATERIAL', 'producto_id': producto.id, 'cantidad_solicitada': 10},
        ])
        det_id = sol['detalles'][0]['id']
        client.post(f'/api/v1/solicitudes/{sol["id"]}/entregar',
                    headers=_hdr(inv_admin), json={
            'almacen_origen_id': almacen.id,
            'entregas': [{'detalle_id': det_id, 'cantidad_entregada': 4}],
        })
        # Después de la entrega parcial: aprobada=10, entregada=4, reserva=6.
        # Bajar aprobada a 4: debe liberar los 6 restantes.
        rp = client.patch(f'/api/v1/solicitudes/{sol["id"]}/detalles/{det_id}',
                           headers=_hdr(inv_admin), json={'cantidad_aprobada': 4})
        assert rp.status_code == 200
        db.session.expire(producto)
        assert float(producto.stock_reservado) == 0.0
        # La solicitud SIGUE en APROBADA — el endpoint /entregar es quien la pasa a
        # ENTREGADA. Si el usuario quiere cerrarla sin entregar más, debe usar
        # PATCH /estado con ENTREGADA o el flujo legacy.

    def test_entregas_duplicadas_en_payload(self, client, solicitante, inv_admin, producto, almacen):
        sol = _crear_y_aprobar(client, solicitante, inv_admin, [
            {'tipo_item': 'MATERIAL', 'producto_id': producto.id, 'cantidad_solicitada': 10},
        ])
        det_id = sol['detalles'][0]['id']
        r = client.post(f'/api/v1/solicitudes/{sol["id"]}/entregar',
                         headers=_hdr(inv_admin), json={
            'almacen_origen_id': almacen.id,
            'entregas': [
                {'detalle_id': det_id, 'cantidad_entregada': 3},
                {'detalle_id': det_id, 'cantidad_entregada': 2},
            ],
        })
        assert r.status_code == 422


# ═══════════════════════════════════════════════════════════════════════════════
# Entrega de líneas HERRAMIENTA (bug 2026-05-25)
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def trabajador(db):
    t = Trabajador(no_empleado='EP-001', nombre='Juan', nombre_apellidos='Pérez',
                    activo=True)
    db.session.add(t); db.session.commit()
    return t


@pytest.fixture
def solicitante_con_trabajador(db, trabajador):
    u = User(username='ep_sol_t', password_hash=generate_password_hash('Pass123!'),
             role='solicitante_material', trabajador_id=trabajador.id)
    db.session.add(u); db.session.commit()
    return u


@pytest.fixture
def solicitante_sin_trabajador(db):
    u = User(username='ep_sol_nt', password_hash=generate_password_hash('Pass123!'),
             role='solicitante_material')
    db.session.add(u); db.session.commit()
    return u


@pytest.fixture
def herramienta_con_3_unidades(db):
    h = Herramienta(sku='TAL-001', descripcion='Taladro DeWalt', clasificacion='Eléctrica',
                     unidad='pieza', piezas=1, serializada=True, activo=True)
    db.session.add(h); db.session.flush()
    for i in range(3):
        u = HerramientaUnidad(
            herramienta_id=h.id,
            codigo_interno=f'TAL-001-{i+1}',
            qr_code=f'qr-tal-{i+1}-{uuid.uuid4()}',
            estado='DISPONIBLE',
        )
        db.session.add(u)
    db.session.commit()
    return h


class TestEntregaHerramienta:

    def test_entregar_1_herramienta_crea_asignacion(
        self, client, db, solicitante_con_trabajador, inv_admin,
        herramienta_con_3_unidades, trabajador,
    ):
        sol = _crear_y_aprobar(client, solicitante_con_trabajador, inv_admin, [
            {'tipo_item': 'HERRAMIENTA', 'herramienta_id': herramienta_con_3_unidades.id,
             'cantidad_solicitada': 1},
        ])
        det_id = sol['detalles'][0]['id']
        r = client.post(f'/api/v1/solicitudes/{sol["id"]}/entregar',
                         headers=_hdr(inv_admin), json={
            'entregas': [{'detalle_id': det_id, 'cantidad_entregada': 1}],
        })
        assert r.status_code == 200, r.get_json()
        body = r.get_json()
        # Solicitud queda ENTREGADA (única línea completa)
        assert body['estatus'] == 'ENTREGADA'

        # Se creó una AsignacionHerramienta al trabajador del solicitante
        asigs = AsignacionHerramienta.query.filter(
            AsignacionHerramienta.solicitud_id == sol['id'],
        ).all()
        assert len(asigs) == 1
        assert asigs[0].trabajador_id == trabajador.id
        assert asigs[0].estado == 'ACTIVA'

        # La unidad asignada pasó a ASIGNADA y vinculada al trabajador
        unidad = HerramientaUnidad.query.get(asigs[0].unidad_id)
        assert unidad.estado == 'ASIGNADA'
        assert unidad.asignado_trabajador_id == trabajador.id

    def test_entregar_multiples_unidades(
        self, client, db, solicitante_con_trabajador, inv_admin,
        herramienta_con_3_unidades,
    ):
        """Pide 2 → debe crear 2 asignaciones tomando 2 unidades DISPONIBLES."""
        sol = _crear_y_aprobar(client, solicitante_con_trabajador, inv_admin, [
            {'tipo_item': 'HERRAMIENTA', 'herramienta_id': herramienta_con_3_unidades.id,
             'cantidad_solicitada': 2},
        ])
        det_id = sol['detalles'][0]['id']
        r = client.post(f'/api/v1/solicitudes/{sol["id"]}/entregar',
                         headers=_hdr(inv_admin), json={
            'entregas': [{'detalle_id': det_id, 'cantidad_entregada': 2}],
        })
        assert r.status_code == 200
        asigs = AsignacionHerramienta.query.filter(
            AsignacionHerramienta.solicitud_id == sol['id'],
        ).all()
        assert len(asigs) == 2
        # Quedó 1 DISPONIBLE
        disponibles = HerramientaUnidad.query.filter(
            HerramientaUnidad.herramienta_id == herramienta_con_3_unidades.id,
            HerramientaUnidad.estado == 'DISPONIBLE',
        ).count()
        assert disponibles == 1

    def test_entregar_solicitante_sin_trabajador_400(
        self, client, db, solicitante_sin_trabajador, inv_admin,
        herramienta_con_3_unidades,
    ):
        """Si el solicitante (coordinador o usuario sin trabajador) no tiene
        trabajador asociado, no se puede entregar herramienta."""
        sol = _crear_y_aprobar(client, solicitante_sin_trabajador, inv_admin, [
            {'tipo_item': 'HERRAMIENTA', 'herramienta_id': herramienta_con_3_unidades.id,
             'cantidad_solicitada': 1},
        ])
        det_id = sol['detalles'][0]['id']
        r = client.post(f'/api/v1/solicitudes/{sol["id"]}/entregar',
                         headers=_hdr(inv_admin), json={
            'entregas': [{'detalle_id': det_id, 'cantidad_entregada': 1}],
        })
        assert r.status_code == 400
        msg = r.get_json()['detail']
        assert 'trabajador' in msg.lower()

    def test_entregar_sin_unidades_disponibles_409(
        self, client, db, solicitante_con_trabajador, inv_admin,
        herramienta_con_3_unidades,
    ):
        """Pide 4 unidades pero solo hay 3 DISPONIBLES → 409."""
        sol = _crear_y_aprobar(client, solicitante_con_trabajador, inv_admin, [
            {'tipo_item': 'HERRAMIENTA', 'herramienta_id': herramienta_con_3_unidades.id,
             'cantidad_solicitada': 4},
        ])
        det_id = sol['detalles'][0]['id']
        r = client.post(f'/api/v1/solicitudes/{sol["id"]}/entregar',
                         headers=_hdr(inv_admin), json={
            'entregas': [{'detalle_id': det_id, 'cantidad_entregada': 4}],
        })
        assert r.status_code == 409
        # No se creó ninguna asignación (rollback)
        assert AsignacionHerramienta.query.count() == 0

    def test_entregar_mixto_material_y_herramienta(
        self, client, db, solicitante_con_trabajador, inv_admin,
        herramienta_con_3_unidades, producto, almacen,
    ):
        """Una sola entrega procesa material + herramienta en la misma transacción."""
        sol = _crear_y_aprobar(client, solicitante_con_trabajador, inv_admin, [
            {'tipo_item': 'MATERIAL', 'producto_id': producto.id, 'cantidad_solicitada': 5},
            {'tipo_item': 'HERRAMIENTA', 'herramienta_id': herramienta_con_3_unidades.id,
             'cantidad_solicitada': 1},
        ])
        det_mat = next(d for d in sol['detalles'] if d['tipo_item'] == 'MATERIAL')
        det_her = next(d for d in sol['detalles'] if d['tipo_item'] == 'HERRAMIENTA')

        r = client.post(f'/api/v1/solicitudes/{sol["id"]}/entregar',
                         headers=_hdr(inv_admin), json={
            'almacen_origen_id': almacen.id,
            'entregas': [
                {'detalle_id': det_mat['id'], 'cantidad_entregada': 5},
                {'detalle_id': det_her['id'], 'cantidad_entregada': 1},
            ],
        })
        assert r.status_code == 200, r.get_json()
        body = r.get_json()
        assert body['estatus'] == 'ENTREGADA'

        # Material descontado del stock
        db.session.expire(producto)
        assert float(producto.stock_actual) == 95.0

        # Herramienta asignada
        asigs = AsignacionHerramienta.query.filter(
            AsignacionHerramienta.solicitud_id == sol['id'],
        ).all()
        assert len(asigs) == 1

    def test_entregar_parcial_herramienta_queda_aprobada(
        self, client, db, solicitante_con_trabajador, inv_admin,
        herramienta_con_3_unidades,
    ):
        """Pide 2, entrega 1 → solicitud sigue APROBADA, 1 asignación creada."""
        sol = _crear_y_aprobar(client, solicitante_con_trabajador, inv_admin, [
            {'tipo_item': 'HERRAMIENTA', 'herramienta_id': herramienta_con_3_unidades.id,
             'cantidad_solicitada': 2},
        ])
        det_id = sol['detalles'][0]['id']
        r = client.post(f'/api/v1/solicitudes/{sol["id"]}/entregar',
                         headers=_hdr(inv_admin), json={
            'entregas': [{'detalle_id': det_id, 'cantidad_entregada': 1}],
        })
        assert r.status_code == 200
        body = r.get_json()
        assert body['estatus'] == 'APROBADA'  # sigue parcial
        det = body['detalles'][0]
        assert det['cantidad_entregada'] == 1
        assert det['cantidad_aprobada'] == 2
        assert AsignacionHerramienta.query.count() == 1

    def test_entregar_herramienta_decimal_invalido_422(
        self, client, solicitante_con_trabajador, inv_admin, herramienta_con_3_unidades,
    ):
        sol = _crear_y_aprobar(client, solicitante_con_trabajador, inv_admin, [
            {'tipo_item': 'HERRAMIENTA', 'herramienta_id': herramienta_con_3_unidades.id,
             'cantidad_solicitada': 2},
        ])
        det_id = sol['detalles'][0]['id']
        r = client.post(f'/api/v1/solicitudes/{sol["id"]}/entregar',
                         headers=_hdr(inv_admin), json={
            'entregas': [{'detalle_id': det_id, 'cantidad_entregada': 1.5}],
        })
        assert r.status_code == 422

    def test_entregar_herramienta_excede_aprobada_422(
        self, client, solicitante_con_trabajador, inv_admin, herramienta_con_3_unidades,
    ):
        sol = _crear_y_aprobar(client, solicitante_con_trabajador, inv_admin, [
            {'tipo_item': 'HERRAMIENTA', 'herramienta_id': herramienta_con_3_unidades.id,
             'cantidad_solicitada': 1},
        ])
        det_id = sol['detalles'][0]['id']
        r = client.post(f'/api/v1/solicitudes/{sol["id"]}/entregar',
                         headers=_hdr(inv_admin), json={
            'entregas': [{'detalle_id': det_id, 'cantidad_entregada': 2}],
        })
        assert r.status_code == 422

    def test_fecha_devolucion_prevista_se_copia(
        self, client, solicitante_con_trabajador, inv_admin, herramienta_con_3_unidades,
    ):
        sol = _crear_y_aprobar(client, solicitante_con_trabajador, inv_admin, [
            {'tipo_item': 'HERRAMIENTA', 'herramienta_id': herramienta_con_3_unidades.id,
             'cantidad_solicitada': 1},
        ])
        det_id = sol['detalles'][0]['id']
        r = client.post(f'/api/v1/solicitudes/{sol["id"]}/entregar',
                         headers=_hdr(inv_admin), json={
            'fecha_devolucion_prevista': '2026-06-30T17:00:00',
            'entregas': [{'detalle_id': det_id, 'cantidad_entregada': 1}],
        })
        assert r.status_code == 200
        asig = AsignacionHerramienta.query.filter(
            AsignacionHerramienta.solicitud_id == sol['id'],
        ).first()
        assert asig.fecha_devolucion_prevista is not None
        assert asig.fecha_devolucion_prevista.day == 30


# ═══════════════════════════════════════════════════════════════════════════════
# Regla de decimales por unidad (2026-06-25)
#   - Herramientas: SIEMPRE enteras.
#   - Materiales: enteros si la unidad es de conteo (pza); decimales solo si la
#     unidad es continua (kg, m, litro…).
# ═══════════════════════════════════════════════════════════════════════════════

class TestDecimalesPorUnidad:

    # ── Crear ──────────────────────────────────────────────────────────────────
    def test_crear_material_pza_entero_ok(self, client, solicitante, producto):
        r = client.post('/api/v1/solicitudes/', headers=_hdr(solicitante), json={
            'proyecto': 'Proyecto Test',
            'detalles': [{'tipo_item': 'MATERIAL', 'producto_id': producto.id,
                          'cantidad_solicitada': 3}],
        })
        assert r.status_code == 200, r.get_json()

    def test_crear_material_pza_decimal_rechaza(self, client, solicitante, producto):
        r = client.post('/api/v1/solicitudes/', headers=_hdr(solicitante), json={
            'proyecto': 'Proyecto Test',
            'detalles': [{'tipo_item': 'MATERIAL', 'producto_id': producto.id,
                          'cantidad_solicitada': 2.5}],
        })
        assert r.status_code == 400, r.get_json()
        assert 'enteras' in str(r.get_json()['detail']).lower()

    def test_crear_material_kg_decimal_ok(self, client, solicitante, producto_kg):
        r = client.post('/api/v1/solicitudes/', headers=_hdr(solicitante), json={
            'proyecto': 'Proyecto Test',
            'detalles': [{'tipo_item': 'MATERIAL', 'producto_id': producto_kg.id,
                          'cantidad_solicitada': 2.5}],
        })
        assert r.status_code == 200, r.get_json()
        assert float(r.get_json()['detalles'][0]['cantidad_solicitada']) == 2.5

    def test_crear_herramienta_decimal_rechaza(self, client, solicitante,
                                               herramienta_con_3_unidades):
        r = client.post('/api/v1/solicitudes/', headers=_hdr(solicitante), json={
            'proyecto': 'Proyecto Test',
            'detalles': [{'tipo_item': 'HERRAMIENTA',
                          'herramienta_id': herramienta_con_3_unidades.id,
                          'cantidad_solicitada': 1.5}],
        })
        assert r.status_code == 400, r.get_json()
        assert 'enteras' in str(r.get_json()['detail']).lower()

    # ── Editar cantidad aprobada ─────────────────────────────────────────────────
    def test_patch_aprobada_pza_decimal_rechaza(self, client, solicitante, inv_admin, producto):
        sol = _crear_y_aprobar(client, solicitante, inv_admin, [
            {'tipo_item': 'MATERIAL', 'producto_id': producto.id, 'cantidad_solicitada': 10},
        ])
        det_id = sol['detalles'][0]['id']
        r = client.patch(f'/api/v1/solicitudes/{sol["id"]}/detalles/{det_id}',
                         headers=_hdr(inv_admin), json={'cantidad_aprobada': 4.5})
        assert r.status_code == 422, r.get_json()

    def test_patch_aprobada_kg_decimal_ok(self, client, solicitante, inv_admin, producto_kg):
        sol = _crear_y_aprobar(client, solicitante, inv_admin, [
            {'tipo_item': 'MATERIAL', 'producto_id': producto_kg.id, 'cantidad_solicitada': 10},
        ])
        det_id = sol['detalles'][0]['id']
        r = client.patch(f'/api/v1/solicitudes/{sol["id"]}/detalles/{det_id}',
                         headers=_hdr(inv_admin), json={'cantidad_aprobada': 4.5})
        assert r.status_code == 200, r.get_json()
        assert float(r.get_json()['cantidad_aprobada']) == 4.5

    # ── Entregar ────────────────────────────────────────────────────────────────
    def test_entregar_material_pza_decimal_rechaza(self, client, solicitante, inv_admin,
                                                   producto, almacen):
        sol = _crear_y_aprobar(client, solicitante, inv_admin, [
            {'tipo_item': 'MATERIAL', 'producto_id': producto.id, 'cantidad_solicitada': 10},
        ])
        det_id = sol['detalles'][0]['id']
        r = client.post(f'/api/v1/solicitudes/{sol["id"]}/entregar', headers=_hdr(inv_admin), json={
            'almacen_origen_id': almacen.id,
            'entregas': [{'detalle_id': det_id, 'cantidad_entregada': 2.5}],
        })
        assert r.status_code == 422, r.get_json()
        assert 'enteras' in r.get_json()['detail'].lower()

    def test_entregar_material_kg_decimal_ok(self, client, db, solicitante, inv_admin,
                                            producto_kg, almacen):
        sol = _crear_y_aprobar(client, solicitante, inv_admin, [
            {'tipo_item': 'MATERIAL', 'producto_id': producto_kg.id, 'cantidad_solicitada': 10},
        ])
        det_id = sol['detalles'][0]['id']
        r = client.post(f'/api/v1/solicitudes/{sol["id"]}/entregar', headers=_hdr(inv_admin), json={
            'almacen_origen_id': almacen.id,
            'entregas': [{'detalle_id': det_id, 'cantidad_entregada': 2.5}],
        })
        assert r.status_code == 200, r.get_json()
        assert r.get_json()['estatus'] == 'APROBADA'  # 2.5 de 10 → parcial
        assert float(r.get_json()['detalles'][0]['cantidad_entregada']) == 2.5


# ═══════════════════════════════════════════════════════════════════════════════
# Pausa 11 — ubicaciones al atender + descuento de celda al entregar
# ═══════════════════════════════════════════════════════════════════════════════

class TestUbicacionesYCelda:

    def _colocar(self, db, producto, almacen, fila=2, columna=3, cantidad=30):
        """Crea un estante en `almacen` y coloca `producto` en una celda."""
        e = Estante(nombre='Rack EP', almacen_id=almacen.id, qr_code=str(uuid.uuid4()),
                    activo=True, filas=4, columnas=4)
        db.session.add(e); db.session.commit()
        db.session.add(ProductoEstante(
            producto_id=producto.id, estante_id=e.id,
            fila=fila, columna=columna, cantidad=Decimal(str(cantidad)),
        ))
        db.session.commit()
        return e

    def test_ubicaciones_devuelve_celda(self, client, db, solicitante, inv_admin, producto, almacen):
        e = self._colocar(db, producto, almacen, fila=2, columna=3, cantidad=30)
        sol = _crear_y_aprobar(client, solicitante, inv_admin, [
            {'tipo_item': 'MATERIAL', 'producto_id': producto.id, 'cantidad_solicitada': 5},
        ])
        r = client.get(f'/api/v1/solicitudes/{sol["id"]}/ubicaciones', headers=_hdr(inv_admin))
        assert r.status_code == 200, r.get_json()
        por_prod = r.get_json()['por_producto']
        ubic = por_prod[str(producto.id)]['ubicaciones']
        assert len(ubic) == 1
        assert ubic[0]['estante_id'] == e.id
        assert ubic[0]['fila'] == 2 and ubic[0]['columna'] == 3
        assert ubic[0]['cantidad'] == 30

    def test_entregar_con_estante_descuenta_celda(self, client, db, solicitante, inv_admin, producto, almacen):
        e = self._colocar(db, producto, almacen, cantidad=30)
        sol = _crear_y_aprobar(client, solicitante, inv_admin, [
            {'tipo_item': 'MATERIAL', 'producto_id': producto.id, 'cantidad_solicitada': 10},
        ])
        det_id = sol['detalles'][0]['id']
        r = client.post(f'/api/v1/solicitudes/{sol["id"]}/entregar', headers=_hdr(inv_admin), json={
            'almacen_origen_id': almacen.id,
            'entregas': [{'detalle_id': det_id, 'cantidad_entregada': 10, 'estante_id': e.id}],
        })
        assert r.status_code == 200, r.get_json()
        pe = ProductoEstante.query.filter_by(producto_id=producto.id, estante_id=e.id).first()
        assert float(pe.cantidad) == 20  # 30 − 10
        # El stock autoritativo del almacén bajó igual que en el flujo normal.
        spa = StockPorAlmacen.query.filter_by(producto_id=producto.id, almacen_id=almacen.id).first()
        assert float(spa.cantidad) == 90  # 100 − 10

    def test_entregar_sin_estante_no_toca_celdas(self, client, db, solicitante, inv_admin, producto, almacen):
        e = self._colocar(db, producto, almacen, cantidad=30)
        sol = _crear_y_aprobar(client, solicitante, inv_admin, [
            {'tipo_item': 'MATERIAL', 'producto_id': producto.id, 'cantidad_solicitada': 10},
        ])
        det_id = sol['detalles'][0]['id']
        r = client.post(f'/api/v1/solicitudes/{sol["id"]}/entregar', headers=_hdr(inv_admin), json={
            'almacen_origen_id': almacen.id,
            'entregas': [{'detalle_id': det_id, 'cantidad_entregada': 10}],
        })
        assert r.status_code == 200, r.get_json()
        pe = ProductoEstante.query.filter_by(producto_id=producto.id, estante_id=e.id).first()
        assert float(pe.cantidad) == 30  # sin estante_id → celda intacta

    def test_entregar_celda_clamp_no_negativo(self, client, db, solicitante, inv_admin, producto, almacen):
        # Celda con 3, se entregan 10 (hay stock de almacén de sobra) → celda clamp a 0.
        e = self._colocar(db, producto, almacen, cantidad=3)
        sol = _crear_y_aprobar(client, solicitante, inv_admin, [
            {'tipo_item': 'MATERIAL', 'producto_id': producto.id, 'cantidad_solicitada': 10},
        ])
        det_id = sol['detalles'][0]['id']
        r = client.post(f'/api/v1/solicitudes/{sol["id"]}/entregar', headers=_hdr(inv_admin), json={
            'almacen_origen_id': almacen.id,
            'entregas': [{'detalle_id': det_id, 'cantidad_entregada': 10, 'estante_id': e.id}],
        })
        assert r.status_code == 200, r.get_json()
        pe = ProductoEstante.query.filter_by(producto_id=producto.id, estante_id=e.id).first()
        assert float(pe.cantidad) == 0


# ═══════════════════════════════════════════════════════════════════════════════
# POST /solicitudes/entrega-directa  (mostrador)
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def trabajador(db):
    t = Trabajador(
        no_empleado='ED-001', nombre='Juan', nombre_apellidos='Pérez López',
        activo=True,
    )
    db.session.add(t); db.session.commit()
    return t


class TestEntregaDirecta:

    def test_con_trabajador_descuenta_stock_y_queda_entregada(
        self, client, db, inv_admin, almacen, producto, trabajador
    ):
        r = client.post('/api/v1/solicitudes/entrega-directa', headers=_hdr(inv_admin), json={
            'proyecto': 'Obra Norte',
            'solicitante_trabajador_id': trabajador.id,
            'almacen_origen_id': almacen.id,
            'detalles': [{'producto_id': producto.id, 'cantidad': 15}],
        })
        assert r.status_code == 200, r.get_json()
        body = r.get_json()
        assert body['estatus'] == 'ENTREGADA'
        assert body['entrega_directa'] is True
        assert body['solicitante_nombre'] == 'Juan Pérez López'
        assert body['detalles'][0]['cantidad_entregada'] == 15

        # Stock descontado en la bodega y SALIDA registrada.
        spa = StockPorAlmacen.query.filter_by(producto_id=producto.id, almacen_id=almacen.id).first()
        assert float(spa.cantidad) == 85
        mov = MovimientoInventario.query.filter_by(producto_id=producto.id, tipo='SALIDA').first()
        assert mov is not None and float(mov.cantidad) == 15
        # No usa reservas.
        db.session.expire(producto)
        assert float(producto.stock_reservado) == 0

    def test_entrega_directa_registra_entregada_por_sin_aprobada(
        self, client, inv_admin, almacen, producto, trabajador
    ):
        r = client.post('/api/v1/solicitudes/entrega-directa', headers=_hdr(inv_admin), json={
            'proyecto': 'Obra Norte',
            'solicitante_trabajador_id': trabajador.id,
            'almacen_origen_id': almacen.id,
            'detalles': [{'producto_id': producto.id, 'cantidad': 3}],
        })
        assert r.status_code == 200, r.get_json()
        body = r.get_json()
        assert body['entregada_por'] == (inv_admin.full_name or inv_admin.username)
        assert body['aprobada_por'] is None  # la directa no pasa por aprobación

    def test_con_nombre_libre(self, client, inv_admin, almacen, producto):
        r = client.post('/api/v1/solicitudes/entrega-directa', headers=_hdr(inv_admin), json={
            'proyecto': 'Obra Sur',
            'solicitante_nombre': 'Pedro de la Bodega',
            'almacen_origen_id': almacen.id,
            'detalles': [{'producto_id': producto.id, 'cantidad': 5}],
        })
        assert r.status_code == 200, r.get_json()
        assert r.get_json()['solicitante_nombre'] == 'Pedro de la Bodega'

    def test_sin_solicitante_es_422(self, client, inv_admin, almacen, producto):
        r = client.post('/api/v1/solicitudes/entrega-directa', headers=_hdr(inv_admin), json={
            'proyecto': 'Obra Sur',
            'almacen_origen_id': almacen.id,
            'detalles': [{'producto_id': producto.id, 'cantidad': 5}],
        })
        assert r.status_code == 422

    def test_stock_insuficiente_es_409(self, client, inv_admin, almacen, producto):
        r = client.post('/api/v1/solicitudes/entrega-directa', headers=_hdr(inv_admin), json={
            'proyecto': 'Obra Sur',
            'solicitante_nombre': 'Quien sea',
            'almacen_origen_id': almacen.id,
            'detalles': [{'producto_id': producto.id, 'cantidad': 99999}],
        })
        assert r.status_code == 409

    def test_solicitante_material_no_puede_entregar_directo_403(
        self, client, solicitante, almacen, producto
    ):
        r = client.post('/api/v1/solicitudes/entrega-directa', headers=_hdr(solicitante), json={
            'proyecto': 'Obra Sur',
            'solicitante_nombre': 'Quien sea',
            'almacen_origen_id': almacen.id,
            'detalles': [{'producto_id': producto.id, 'cantidad': 1}],
        })
        assert r.status_code == 403

    def test_unidad_entera_rechaza_decimales_422(self, client, inv_admin, almacen, producto):
        r = client.post('/api/v1/solicitudes/entrega-directa', headers=_hdr(inv_admin), json={
            'proyecto': 'Obra Sur',
            'solicitante_nombre': 'Quien sea',
            'almacen_origen_id': almacen.id,
            'detalles': [{'producto_id': producto.id, 'cantidad': 2.5}],  # pza no admite decimales
        })
        assert r.status_code == 422
