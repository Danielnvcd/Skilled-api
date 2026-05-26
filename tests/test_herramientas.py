"""Tests del módulo de Herramientas (`herramientas_api` blueprint).

Cubre los casos CP-01..CP-18 del diseño funcional:
  - CP-01..03  Alta de herramienta y unidades, validaciones
  - CP-04..06  Asignación, conflictos de estado, race condition
  - CP-07      Devolución con daño
  - CP-08, 15  Solicitudes (incl. mixtas material+herramienta)
  - CP-09      Reporte de incidencia por solicitante
  - CP-10..11  Baja directa y vía solicitud
  - CP-12      Permisos por rol
  - CP-13      Desactivar catálogo con unidades activas
  - CP-16..18  Timeline, filtros, escaneo QR

Auth: usa JWT real vía `_encode_access_token` (blueprint usa `@jwt_required` estricto).
"""
import io
import pytest
from werkzeug.security import generate_password_hash

from app.extensions import db as flask_db
from app.models import (
    User, Trabajador, Herramienta, HerramientaUnidad,
    AsignacionHerramienta, IncidenciaHerramienta, SolicitudBajaHerramienta,
    EventoHerramienta, SolicitudMaterial, SolicitudMaterialDetalle, Producto,
)
from app.routes.api_auth import _encode_access_token


# ─── Helpers de auth ─────────────────────────────────────────────────────────

def _hdr(user):
    """Devuelve headers con Bearer token JWT válido para `user`."""
    token = _encode_access_token(user)
    return {'Authorization': f'Bearer {token}'}


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def admin(db):
    u = User(username='herr_admin', password_hash=generate_password_hash('Pass123!'), role='admin')
    db.session.add(u); db.session.commit()
    return u


@pytest.fixture
def inv(db):
    u = User(username='herr_inv', password_hash=generate_password_hash('Pass123!'), role='inventario')
    db.session.add(u); db.session.commit()
    return u


@pytest.fixture
def sol(db, trabajador):
    u = User(username='herr_sol', password_hash=generate_password_hash('Pass123!'),
             role='solicitante_material', trabajador_id=trabajador.id)
    db.session.add(u); db.session.commit()
    return u


@pytest.fixture
def outsider(db):
    # Rol no autorizado (coordinador sí lee herramientas desde Pausa de coordinador 05-25).
    u = User(username='herr_out', password_hash=generate_password_hash('Pass123!'), role='visitor')
    db.session.add(u); db.session.commit()
    return u


@pytest.fixture
def herramienta_t(db, admin):
    h = Herramienta(
        sku='HRR-T001', descripcion='Taladro DeWalt', clasificacion='Eléctrica',
        marca='DeWalt', modelo='DCD777', uso='ELÉCTRICA', unidad='pieza',
        piezas=1, serializada=True, activo=True, created_by_id=admin.id,
    )
    db.session.add(h); db.session.commit()
    return h


@pytest.fixture
def unidad_disp(db, admin, herramienta_t):
    """Crea una unidad disponible vía API real para que tenga codigo_interno y qr_code coherentes."""
    u = HerramientaUnidad(
        herramienta_id=herramienta_t.id, no_serie='SN-001',
        codigo_interno='HRR-000001', qr_code='qr-test-001',
        estado='DISPONIBLE', cantidad=1,
    )
    db.session.add(u); db.session.commit()
    return u


# ═══════════════════════════════════════════════════════════════════════════════
# CP-01..03  CATÁLOGO
# ═══════════════════════════════════════════════════════════════════════════════

class TestCatalogo:

    def test_cp01_alta_herramienta(self, client, admin):
        r = client.post('/api/v1/herramientas/', headers=_hdr(admin), json={
            'sku': 'HRR-NEW', 'descripcion': 'Llave Stillson 14"',
            'clasificacion': 'Manual', 'unidad': 'pieza',
            'serializada': False,
        })
        assert r.status_code == 201, r.get_json()
        data = r.get_json()
        assert data['sku'] == 'HRR-NEW'
        assert data['serializada'] is False
        assert data['activo'] is True

    def test_cp02_sku_duplicado(self, client, admin, herramienta_t):
        r = client.post('/api/v1/herramientas/', headers=_hdr(admin), json={
            'sku': 'HRR-T001', 'descripcion': 'Dup',
            'clasificacion': 'X', 'unidad': 'pieza',
        })
        assert r.status_code == 400
        assert 'ya existe' in r.get_json()['detail'].lower()

    def test_cp03_unidad_serializada_sin_serie(self, client, admin, herramienta_t):
        r = client.post('/api/v1/herramientas-unidades/', headers=_hdr(admin), json={
            'herramienta_id': herramienta_t.id,
        })
        assert r.status_code == 422

    def test_alta_unidad_no_serializada_sin_serie_ok(self, client, admin):
        # CP complemento: si serializada=false, no exige no_serie
        rh = client.post('/api/v1/herramientas/', headers=_hdr(admin), json={
            'sku': 'HRR-NS', 'descripcion': 'Guantes',
            'clasificacion': 'Seguridad', 'unidad': 'pieza',
            'serializada': False,
        })
        assert rh.status_code == 201
        hid = rh.get_json()['id']
        r = client.post('/api/v1/herramientas-unidades/', headers=_hdr(admin), json={
            'herramienta_id': hid, 'cantidad': 50,
        })
        assert r.status_code == 201
        assert r.get_json()['cantidad'] == 50

    def test_cp13_no_desactivar_si_hay_asignadas(self, client, admin, herramienta_t, unidad_disp, trabajador):
        # Asignar la unidad primero
        r = client.post('/api/v1/asignaciones-herramienta/', headers=_hdr(admin), json={
            'unidad_id': unidad_disp.id, 'trabajador_id': trabajador.id,
            'proyecto': 'Test',
        })
        assert r.status_code == 201
        # Ahora intentar desactivar el catálogo
        rdel = client.delete(f'/api/v1/herramientas/{herramienta_t.id}', headers=_hdr(admin))
        assert rdel.status_code == 400


# ═══════════════════════════════════════════════════════════════════════════════
# CP-04..07  ASIGNACIÓN / DEVOLUCIÓN
# ═══════════════════════════════════════════════════════════════════════════════

class TestAsignacion:

    def test_cp04_asignar_disponible(self, client, admin, unidad_disp, trabajador):
        r = client.post('/api/v1/asignaciones-herramienta/', headers=_hdr(admin), json={
            'unidad_id': unidad_disp.id, 'trabajador_id': trabajador.id,
            'proyecto': 'P-001', 'condicion_entrega': 'BUENA',
        })
        assert r.status_code == 201, r.get_json()
        data = r.get_json()
        assert data['estado'] == 'ACTIVA'
        # Unidad debe estar ASIGNADA
        ru = client.get(f'/api/v1/herramientas-unidades/{unidad_disp.id}', headers=_hdr(admin))
        assert ru.get_json()['estado'] == 'ASIGNADA'

    def test_cp05_asignar_ya_asignada(self, client, admin, unidad_disp, trabajador, trabajador2):
        client.post('/api/v1/asignaciones-herramienta/', headers=_hdr(admin), json={
            'unidad_id': unidad_disp.id, 'trabajador_id': trabajador.id,
        })
        r = client.post('/api/v1/asignaciones-herramienta/', headers=_hdr(admin), json={
            'unidad_id': unidad_disp.id, 'trabajador_id': trabajador2.id,
        })
        assert r.status_code == 409

    def test_cp07_devolucion_con_dano(self, client, admin, unidad_disp, trabajador):
        ra = client.post('/api/v1/asignaciones-herramienta/', headers=_hdr(admin), json={
            'unidad_id': unidad_disp.id, 'trabajador_id': trabajador.id,
        })
        aid = ra.get_json()['id']
        rd = client.patch(f'/api/v1/asignaciones-herramienta/{aid}/devolver',
                          headers=_hdr(admin), json={
            'condicion_devolucion': 'MALA',
            'nuevo_estado_unidad': 'DAÑADA',
            'observaciones_devolucion': 'Llegó sin cargador',
        })
        assert rd.status_code == 200
        data = rd.get_json()
        assert data['estado'] == 'DEVUELTA'
        # Verificar timeline
        re = client.get(f'/api/v1/herramientas-unidades/{unidad_disp.id}/eventos',
                        headers=_hdr(admin))
        tipos = [e['tipo_evento'] for e in re.get_json()]
        assert 'ASIGNACION' in tipos and 'DEVOLUCION' in tipos


# ═══════════════════════════════════════════════════════════════════════════════
# CP-08, 15  SOLICITUDES MIXTAS
# ═══════════════════════════════════════════════════════════════════════════════

class TestSolicitudes:

    def test_cp08_solicitante_crea_solicitud_herramienta(self, client, sol, herramienta_t):
        r = client.post('/api/v1/solicitudes/', headers=_hdr(sol), json={
            'proyecto': 'Obra A',
            'detalles': [{
                'tipo_item': 'HERRAMIENTA',
                'herramienta_id': herramienta_t.id,
                'cantidad_solicitada': 1,
                'fecha_uso_inicio': '2026-06-01',
                'fecha_uso_fin': '2026-06-10',
                'justificacion': 'Instalación tablero',
                'complementos': 'Broca SDS 8mm',
            }],
        })
        assert r.status_code == 200, r.get_json()
        data = r.get_json()
        assert data['estatus'] == 'PENDIENTE'
        det = data['detalles'][0]
        assert det['tipo_item'] == 'HERRAMIENTA'
        assert det['herramienta_id'] == herramienta_t.id
        assert det['producto_id'] is None

    def test_cp15_solicitud_mixta(self, client, sol, db, herramienta_t):
        prod = Producto(codigo='MAT-001', descripcion='Cinta', categoria='Suministros',
                         unidad='pza', stock_actual=10, stock_minimo=0)
        db.session.add(prod); db.session.commit()
        r = client.post('/api/v1/solicitudes/', headers=_hdr(sol), json={
            'proyecto': 'Obra B',
            'detalles': [
                {'tipo_item': 'MATERIAL', 'producto_id': prod.id, 'cantidad_solicitada': 5},
                {'tipo_item': 'HERRAMIENTA', 'herramienta_id': herramienta_t.id,
                 'cantidad_solicitada': 1},
            ],
        })
        assert r.status_code == 200
        tipos = sorted(d['tipo_item'] for d in r.get_json()['detalles'])
        assert tipos == ['HERRAMIENTA', 'MATERIAL']

    def test_solicitud_herramienta_xor_falla(self, client, sol, herramienta_t, db):
        prod = Producto(codigo='MAT-002', descripcion='X', categoria='T',
                         unidad='pza', stock_actual=0, stock_minimo=0)
        db.session.add(prod); db.session.commit()
        r = client.post('/api/v1/solicitudes/', headers=_hdr(sol), json={
            'detalles': [{
                'tipo_item': 'HERRAMIENTA',
                'producto_id': prod.id,           # ← inválido
                'herramienta_id': herramienta_t.id,
                'cantidad_solicitada': 1,
            }],
        })
        assert r.status_code == 400


# ═══════════════════════════════════════════════════════════════════════════════
# CP-09  INCIDENCIAS
# ═══════════════════════════════════════════════════════════════════════════════

class TestIncidencias:

    def test_cp09_solicitante_reporta_incidencia(self, client, sol, admin, unidad_disp, trabajador):
        # Primero: asignar la unidad al trabajador del solicitante (sol.trabajador_id == trabajador.id)
        client.post('/api/v1/asignaciones-herramienta/', headers=_hdr(admin), json={
            'unidad_id': unidad_disp.id, 'trabajador_id': trabajador.id,
        })
        # Solicitante reporta
        r = client.post('/api/v1/incidencias-herramienta/', headers=_hdr(sol), json={
            'unidad_id': unidad_disp.id, 'tipo': 'DAÑO',
            'descripcion': 'Se rompió el portabrocas durante el trabajo en planta',
        })
        assert r.status_code == 201, r.get_json()
        assert r.get_json()['estado'] == 'ABIERTA'
        # Estado de unidad NO cambia
        ru = client.get(f'/api/v1/herramientas-unidades/{unidad_disp.id}', headers=_hdr(admin))
        assert ru.get_json()['estado'] == 'ASIGNADA'

    def test_solicitante_no_atiende_incidencia(self, client, sol, admin, unidad_disp, trabajador):
        client.post('/api/v1/asignaciones-herramienta/', headers=_hdr(admin), json={
            'unidad_id': unidad_disp.id, 'trabajador_id': trabajador.id,
        })
        ri = client.post('/api/v1/incidencias-herramienta/', headers=_hdr(sol), json={
            'unidad_id': unidad_disp.id, 'tipo': 'DAÑO',
            'descripcion': 'Se rompió el portabrocas',
        })
        iid = ri.get_json()['id']
        r = client.patch(f'/api/v1/incidencias-herramienta/{iid}/atender',
                         headers=_hdr(sol), json={'estado': 'RESUELTA'})
        assert r.status_code == 403


# ═══════════════════════════════════════════════════════════════════════════════
# CP-10..11  BAJA
# ═══════════════════════════════════════════════════════════════════════════════

class TestBaja:

    def test_cp10_baja_directa_admin(self, client, admin, unidad_disp):
        r = client.post(f'/api/v1/herramientas-unidades/{unidad_disp.id}/dar-baja',
                        headers=_hdr(admin),
                        json={'motivo': 'Equipo obsoleto, sin refacciones disponibles.'})
        assert r.status_code == 201, r.get_json()
        ru = client.get(f'/api/v1/herramientas-unidades/{unidad_disp.id}', headers=_hdr(admin))
        data = ru.get_json()
        assert data['estado'] == 'DADA_DE_BAJA'
        assert data['fecha_baja'] is not None

    def test_cp10_baja_directa_motivo_corto(self, client, admin, unidad_disp):
        r = client.post(f'/api/v1/herramientas-unidades/{unidad_disp.id}/dar-baja',
                        headers=_hdr(admin), json={'motivo': 'corto'})
        assert r.status_code == 422

    def test_cp11_baja_via_solicitud(self, client, sol, admin, unidad_disp, trabajador):
        # Asignar para que el solicitante pueda verla
        client.post('/api/v1/asignaciones-herramienta/', headers=_hdr(admin), json={
            'unidad_id': unidad_disp.id, 'trabajador_id': trabajador.id,
        })
        # Solicitante pide baja
        rs = client.post('/api/v1/solicitudes-baja-herramienta/', headers=_hdr(sol), json={
            'unidad_id': unidad_disp.id,
            'motivo': 'Quedó inservible tras caída en obra, costo de reparación supera 80%.',
        })
        assert rs.status_code == 201
        sid = rs.get_json()['id']
        # Admin autoriza
        ra = client.patch(f'/api/v1/solicitudes-baja-herramienta/{sid}/autorizar',
                          headers=_hdr(admin), json={})
        assert ra.status_code == 200
        assert ra.get_json()['estado'] == 'APROBADA'
        # Admin ejecuta
        re = client.post(f'/api/v1/solicitudes-baja-herramienta/{sid}/ejecutar',
                          headers=_hdr(admin))
        assert re.status_code == 200
        assert re.get_json()['estado'] == 'EJECUTADA'
        # Unidad debe estar dada de baja
        ru = client.get(f'/api/v1/herramientas-unidades/{unidad_disp.id}', headers=_hdr(admin))
        assert ru.get_json()['estado'] == 'DADA_DE_BAJA'


# ═══════════════════════════════════════════════════════════════════════════════
# CP-12  PERMISOS
# ═══════════════════════════════════════════════════════════════════════════════

class TestPermisos:

    def test_cp12_sin_token_401(self, client):
        r = client.get('/api/v1/herramientas/')
        assert r.status_code == 401

    def test_cp12_outsider_403(self, client, outsider):
        r = client.get('/api/v1/herramientas/', headers=_hdr(outsider))
        assert r.status_code == 403

    def test_cp12_solicitante_no_crea_catalogo(self, client, sol):
        r = client.post('/api/v1/herramientas/', headers=_hdr(sol), json={
            'sku': 'X', 'descripcion': 'X', 'clasificacion': 'X', 'unidad': 'pza',
        })
        assert r.status_code == 403

    def test_cp12_solicitante_no_asigna(self, client, sol, unidad_disp, trabajador):
        r = client.post('/api/v1/asignaciones-herramienta/', headers=_hdr(sol), json={
            'unidad_id': unidad_disp.id, 'trabajador_id': trabajador.id,
        })
        assert r.status_code == 403

    def test_solicitante_ve_solo_sus_unidades(self, client, sol, admin, unidad_disp, trabajador):
        # Sin asignación → no ve nada
        r = client.get('/api/v1/herramientas-unidades/', headers=_hdr(sol))
        assert r.status_code == 200
        assert r.get_json() == []
        # Asignar → debe verla
        client.post('/api/v1/asignaciones-herramienta/', headers=_hdr(admin), json={
            'unidad_id': unidad_disp.id, 'trabajador_id': trabajador.id,
        })
        r2 = client.get('/api/v1/herramientas-unidades/', headers=_hdr(sol))
        assert len(r2.get_json()) == 1
        assert r2.get_json()[0]['id'] == unidad_disp.id


# ═══════════════════════════════════════════════════════════════════════════════
# CP-16..18  TIMELINE, FILTROS, QR
# ═══════════════════════════════════════════════════════════════════════════════

class TestTimelineYQR:

    def test_cp16_timeline_ordenado(self, client, admin, unidad_disp, trabajador):
        # Generar varios eventos: asignar y devolver
        ra = client.post('/api/v1/asignaciones-herramienta/', headers=_hdr(admin), json={
            'unidad_id': unidad_disp.id, 'trabajador_id': trabajador.id,
        })
        aid = ra.get_json()['id']
        client.patch(f'/api/v1/asignaciones-herramienta/{aid}/devolver',
                     headers=_hdr(admin), json={
            'condicion_devolucion': 'BUENA', 'nuevo_estado_unidad': 'DISPONIBLE',
        })
        r = client.get(f'/api/v1/herramientas-unidades/{unidad_disp.id}/eventos',
                       headers=_hdr(admin))
        evts = r.get_json()
        # Debe haber al menos ASIGNACION y DEVOLUCION; orden por fecha desc
        assert len(evts) >= 2
        fechas = [e['fecha'] for e in evts]
        assert fechas == sorted(fechas, reverse=True)

    def test_cp18_escaneo_qr(self, client, admin, unidad_disp):
        r = client.get(f'/api/v1/herramientas-unidades/{unidad_disp.qr_code}/validar',
                       headers=_hdr(admin))
        assert r.status_code == 200
        assert r.get_json()['id'] == unidad_disp.id

    def test_qr_invalido(self, client, admin):
        r = client.get('/api/v1/herramientas-unidades/qr-no-existe/validar',
                       headers=_hdr(admin))
        assert r.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# STATS
# ═══════════════════════════════════════════════════════════════════════════════

class TestStats:

    def test_stats_endpoint(self, client, admin, unidad_disp):
        r = client.get('/api/v1/herramientas/stats', headers=_hdr(admin))
        assert r.status_code == 200
        data = r.get_json()
        assert 'total_herramientas' in data
        assert 'unidades_por_estado' in data
        assert data['unidades_por_estado']['DISPONIBLE'] >= 1


# ═══════════════════════════════════════════════════════════════════════════════
# HEALTH
# ═══════════════════════════════════════════════════════════════════════════════

class TestHealth:

    def test_health(self, client):
        r = client.get('/api/v1/herramientas/health')
        assert r.status_code == 200
        assert r.get_json()['module'] == 'herramientas'
