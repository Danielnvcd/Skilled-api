"""Tests del API JWT `/api/horas/*` — reportes semanales y registros diarios.

Cubre:
  - GET    /reportes                              listar + filtros + ownership coord
  - GET    /proyectos-disponibles                 catálogo (filtra por coord)
  - POST   /reportes                              abrir reporte (solapamiento,
                                                  prenómina cerrada, fechas)
  - GET    /reportes/<id>                         detalle (incluye semana_fechas)
  - POST   /reportes/<id>/cerrar                  BORRADOR → TERMINADO
  - POST   /reportes/<id>/registros               crear (+ idempotencia
                                                  client_record_id)
  - POST   /reportes/<id>/registros/bulk          upsert masivo con `skipped`
  - PUT    /registros/<id>                        editar (+ LWW conflict 409)
  - DELETE /registros/<id>                        eliminar

NO se prueban aquí: `/qr/*`, `/rfid/*` (ya están en test_api_horas_rfid.py),
`/movil/*` (kiosko coord), `/qr-check` — se reservan para otro turno.

Reglas no obvias:
  - Coordinador solo accede a reportes de SUS proyectos.
  - Abrir reporte: no se puede solapar con otro del mismo proyecto, y la
    semana no puede estar dentro de una prenómina CERRADA.
  - Modificar registros de un reporte cerrado → 409.
  - Cerrar reporte sin registros → 400.
  - Fecha del registro debe caer dentro de la semana del reporte.
  - Trabajador debe estar asignado al proyecto.
  - Idempotencia por `client_record_id`: misma key + mismo reporte → 200 con
    el registro existente; misma key + otro reporte → 404 (no leak).
  - Edición con `modificado_en` < el de BD → 409 conflicto.
"""
from datetime import date, time, timedelta
from decimal import Decimal

import pytest
from werkzeug.security import generate_password_hash

from app.models import (
    Proyecto, RegistroDiarioHoras, ReporteSemanal, Trabajador, User,
)
from app.routes.api_auth import _encode_access_token


def _hdr(user):
    return {'Authorization': f'Bearer {_encode_access_token(user)}'}


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def h_admin(db):
    u = User(username='h_admin', password_hash=generate_password_hash('Pass123!'), role='admin')
    db.session.add(u); db.session.commit()
    return u


@pytest.fixture
def h_coord(db):
    u = User(username='h_coord', password_hash=generate_password_hash('Pass123!'),
              role='coordinador')
    db.session.add(u); db.session.commit()
    return u


@pytest.fixture
def h_coord_b(db):
    """Segundo coord para probar ownership entre proyectos."""
    u = User(username='h_coord_b', password_hash=generate_password_hash('Pass123!'),
              role='coordinador')
    db.session.add(u); db.session.commit()
    return u


@pytest.fixture
def h_outsider(db):
    u = User(username='h_out', password_hash=generate_password_hash('Pass123!'),
              role='solicitante_material')
    db.session.add(u); db.session.commit()
    return u


@pytest.fixture
def h_trab(db):
    t = Trabajador(
        no_empleado='H-001', nombre='Hugo', nombre_apellidos='Reyes',
        activo=True, tipo_nomina='Semanal',
        salario_real_pactado_x_sem=Decimal('5000'),
        viaticos=Decimal('50'), pago_dia_festivo=Decimal('100'),
    )
    db.session.add(t); db.session.commit()
    return t


@pytest.fixture
def h_trab_b(db):
    """Trabajador NO asignado a `proyecto_coord` — para probar rejects."""
    t = Trabajador(
        no_empleado='H-002', nombre='Hilda', nombre_apellidos='Soto',
        activo=True, tipo_nomina='Semanal',
        salario_real_pactado_x_sem=Decimal('4500'),
    )
    db.session.add(t); db.session.commit()
    return t


@pytest.fixture
def proyecto_coord(db, h_coord, h_trab):
    p = Proyecto(numero_proyecto='HP-001', nombre='Obra H',
                  activo=True, coordinador_id=h_coord.id)
    p.participantes.append(h_trab)
    db.session.add(p); db.session.commit()
    return p


@pytest.fixture
def proyecto_otro(db, h_coord_b):
    p = Proyecto(numero_proyecto='HP-OTRO', nombre='Obra otro coord',
                  activo=True, coordinador_id=h_coord_b.id)
    db.session.add(p); db.session.commit()
    return p


@pytest.fixture
def fecha_lunes():
    return date(2026, 3, 2)


@pytest.fixture
def fecha_domingo(fecha_lunes):
    return fecha_lunes + timedelta(days=6)


@pytest.fixture
def reporte(db, proyecto_coord, h_coord, fecha_lunes, fecha_domingo):
    r = ReporteSemanal(
        fecha_inicio_semana=fecha_lunes,
        fecha_fin_semana=fecha_domingo,
        proyecto_id=proyecto_coord.id,
        estado='BORRADOR',
        creado_por_id=h_coord.id,
    )
    db.session.add(r); db.session.commit()
    return r


@pytest.fixture
def reporte_cerrado(db, proyecto_coord, h_coord):
    """Reporte ya cerrado (TERMINADO) en otra semana."""
    inicio = date(2026, 2, 2)
    r = ReporteSemanal(
        fecha_inicio_semana=inicio,
        fecha_fin_semana=inicio + timedelta(days=6),
        proyecto_id=proyecto_coord.id,
        estado='TERMINADO',
        creado_por_id=h_coord.id,
    )
    db.session.add(r); db.session.commit()
    return r


@pytest.fixture
def registro(db, reporte, h_trab, fecha_lunes):
    """Un registro existente de lunes 8-17."""
    reg = RegistroDiarioHoras(
        reporte_id=reporte.id, trabajador_id=h_trab.id,
        fecha=fecha_lunes,
        hora_entrada=time(8, 0), hora_salida=time(17, 0),
        tomo_comida=True, horas_productivas=Decimal('8'),
        tipo_nomina='Semanal',
    )
    db.session.add(reg); db.session.commit()
    return reg


# ═══════════════════════════════════════════════════════════════════════════════
# 1. AUTH
# ═══════════════════════════════════════════════════════════════════════════════

class TestAuth:

    def test_sin_token_401(self, client):
        r = client.get('/api/horas/reportes')
        assert r.status_code == 401

    def test_admin_200(self, client, h_admin):
        r = client.get('/api/horas/reportes', headers=_hdr(h_admin))
        assert r.status_code == 200

    def test_coord_200(self, client, h_coord):
        r = client.get('/api/horas/reportes', headers=_hdr(h_coord))
        assert r.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════════
# 2. LISTAR REPORTES
# ═══════════════════════════════════════════════════════════════════════════════

class TestListar:

    def test_admin_ve_todos(
        self, client, h_admin, reporte, reporte_cerrado, proyecto_otro, db,
    ):
        # Crear un reporte de otro coord (para confirmar que admin lo ve)
        r3 = ReporteSemanal(
            fecha_inicio_semana=date(2026, 4, 6),
            fecha_fin_semana=date(2026, 4, 12),
            proyecto_id=proyecto_otro.id, estado='BORRADOR',
        )
        db.session.add(r3); db.session.commit()
        r = client.get('/api/horas/reportes', headers=_hdr(h_admin))
        items = r.get_json()['items']
        ids = {i['id'] for i in items}
        assert {reporte.id, reporte_cerrado.id, r3.id} <= ids

    def test_coord_solo_ve_los_suyos(
        self, client, h_coord, h_coord_b, reporte, proyecto_otro, db,
    ):
        # Crear reporte ajeno
        r3 = ReporteSemanal(
            fecha_inicio_semana=date(2026, 4, 6),
            fecha_fin_semana=date(2026, 4, 12),
            proyecto_id=proyecto_otro.id, estado='BORRADOR',
        )
        db.session.add(r3); db.session.commit()
        r = client.get('/api/horas/reportes', headers=_hdr(h_coord))
        ids = {i['id'] for i in r.get_json()['items']}
        assert reporte.id in ids
        assert r3.id not in ids

    def test_coord_sin_proyectos_vacio(self, client, h_coord_b):
        # h_coord_b no tiene proyecto_otro creado → 0 proyectos asignados
        r = client.get('/api/horas/reportes', headers=_hdr(h_coord_b))
        body = r.get_json()
        assert body['items'] == [] and body['total'] == 0

    def test_filtro_estado(self, client, h_admin, reporte, reporte_cerrado):
        r = client.get('/api/horas/reportes?estado=TERMINADO',
                        headers=_hdr(h_admin))
        items = r.get_json()['items']
        estados = {i['estado'] for i in items}
        assert estados == {'TERMINADO'}

    def test_filtro_q_por_proyecto(
        self, client, h_admin, reporte, proyecto_otro, db,
    ):
        r3 = ReporteSemanal(
            fecha_inicio_semana=date(2026, 4, 6),
            fecha_fin_semana=date(2026, 4, 12),
            proyecto_id=proyecto_otro.id, estado='BORRADOR',
        )
        db.session.add(r3); db.session.commit()
        r = client.get('/api/horas/reportes?q=HP-OTRO', headers=_hdr(h_admin))
        ids = {i['id'] for i in r.get_json()['items']}
        assert ids == {r3.id}


# ═══════════════════════════════════════════════════════════════════════════════
# 3. PROYECTOS DISPONIBLES
# ═══════════════════════════════════════════════════════════════════════════════

class TestProyectosDisponibles:

    def test_admin_ve_todos_activos(
        self, client, h_admin, proyecto_coord, proyecto_otro,
    ):
        r = client.get('/api/horas/proyectos-disponibles', headers=_hdr(h_admin))
        codigos = {p['numero_proyecto'] for p in r.get_json()}
        assert {'HP-001', 'HP-OTRO'} <= codigos

    def test_coord_solo_ve_los_suyos(
        self, client, h_coord, proyecto_coord, proyecto_otro,
    ):
        r = client.get('/api/horas/proyectos-disponibles', headers=_hdr(h_coord))
        codigos = {p['numero_proyecto'] for p in r.get_json()}
        assert codigos == {'HP-001'}


# ═══════════════════════════════════════════════════════════════════════════════
# 4. CREAR REPORTE
# ═══════════════════════════════════════════════════════════════════════════════

class TestCrearReporte:

    def test_admin_abre_reporte(self, client, h_admin, proyecto_coord, db):
        r = client.post('/api/horas/reportes', headers=_hdr(h_admin), json={
            'proyecto_id': proyecto_coord.id,
            'fecha_inicio': '2026-04-06', 'fecha_fin': '2026-04-12',
        })
        assert r.status_code == 201, r.get_json()
        body = r.get_json()
        assert body['estado'] == 'BORRADOR'
        rep = ReporteSemanal.query.get(body['id'])
        assert rep.proyecto_id == proyecto_coord.id

    def test_coord_abre_proyecto_propio(self, client, h_coord, proyecto_coord):
        r = client.post('/api/horas/reportes', headers=_hdr(h_coord), json={
            'proyecto_id': proyecto_coord.id,
            'fecha_inicio': '2026-04-06', 'fecha_fin': '2026-04-12',
        })
        assert r.status_code == 201

    def test_coord_no_puede_abrir_de_otro_403(
        self, client, h_coord, proyecto_otro,
    ):
        r = client.post('/api/horas/reportes', headers=_hdr(h_coord), json={
            'proyecto_id': proyecto_otro.id,
            'fecha_inicio': '2026-04-06', 'fecha_fin': '2026-04-12',
        })
        assert r.status_code == 403

    def test_falta_campos_400(self, client, h_admin, proyecto_coord):
        r = client.post('/api/horas/reportes', headers=_hdr(h_admin), json={
            'proyecto_id': proyecto_coord.id,
        })
        assert r.status_code == 400

    def test_proyecto_inexistente_404(self, client, h_admin):
        r = client.post('/api/horas/reportes', headers=_hdr(h_admin), json={
            'proyecto_id': 99999,
            'fecha_inicio': '2026-04-06', 'fecha_fin': '2026-04-12',
        })
        assert r.status_code == 404

    def test_fecha_invalida_400(self, client, h_admin, proyecto_coord):
        r = client.post('/api/horas/reportes', headers=_hdr(h_admin), json={
            'proyecto_id': proyecto_coord.id,
            'fecha_inicio': 'no-fecha', 'fecha_fin': '2026-04-12',
        })
        assert r.status_code == 400

    def test_fecha_invertida_400(self, client, h_admin, proyecto_coord):
        r = client.post('/api/horas/reportes', headers=_hdr(h_admin), json={
            'proyecto_id': proyecto_coord.id,
            'fecha_inicio': '2026-04-12', 'fecha_fin': '2026-04-06',
        })
        assert r.status_code == 400

    def test_solapamiento_409(
        self, client, h_admin, proyecto_coord, reporte, fecha_lunes,
    ):
        # `reporte` cubre 2026-03-02 → 03-08. Pidamos 03-05 → 03-12 (solapa)
        r = client.post('/api/horas/reportes', headers=_hdr(h_admin), json={
            'proyecto_id': proyecto_coord.id,
            'fecha_inicio': (fecha_lunes + timedelta(days=3)).isoformat(),
            'fecha_fin': (fecha_lunes + timedelta(days=10)).isoformat(),
        })
        assert r.status_code == 409

    def test_prenomina_cerrada_409(
        self, client, h_admin, proyecto_coord, db, fecha_lunes,
    ):
        # Simulamos que la semana del 2026-04-06 ya tiene prenómina cerrada
        cerrado = ReporteSemanal(
            fecha_inicio_semana=date(2026, 4, 6),
            fecha_fin_semana=date(2026, 4, 12),
            proyecto_id=proyecto_coord.id,
            estado='PRENOMINA_CERRADA',
        )
        db.session.add(cerrado); db.session.commit()
        # Otro proyecto distinto intenta abrir un reporte en esa misma ventana
        otro_proy = Proyecto(numero_proyecto='HP-X', nombre='Otro',
                              activo=True, coordinador_id=1)
        db.session.add(otro_proy); db.session.commit()
        r = client.post('/api/horas/reportes', headers=_hdr(h_admin), json={
            'proyecto_id': otro_proy.id,
            'fecha_inicio': '2026-04-08', 'fecha_fin': '2026-04-14',
        })
        assert r.status_code == 409


# ═══════════════════════════════════════════════════════════════════════════════
# 5. DETALLE REPORTE
# ═══════════════════════════════════════════════════════════════════════════════

class TestDetalle:

    def test_detalle_incluye_semana_y_participantes(
        self, client, h_coord, reporte, h_trab,
    ):
        r = client.get(f'/api/horas/reportes/{reporte.id}', headers=_hdr(h_coord))
        assert r.status_code == 200
        body = r.get_json()
        assert body['estado'] == 'BORRADOR'
        assert body['editable'] is True
        # 7 días en `semana_fechas` (lunes a domingo)
        assert len(body['semana_fechas']) == 7
        # Participantes del proyecto vienen serializados
        ids = {t['id'] for t in body['trabajadores']}
        assert h_trab.id in ids

    def test_coord_no_accede_a_ajeno_403(
        self, client, h_coord, proyecto_otro, db,
    ):
        r3 = ReporteSemanal(
            fecha_inicio_semana=date(2026, 4, 6),
            fecha_fin_semana=date(2026, 4, 12),
            proyecto_id=proyecto_otro.id, estado='BORRADOR',
        )
        db.session.add(r3); db.session.commit()
        r = client.get(f'/api/horas/reportes/{r3.id}', headers=_hdr(h_coord))
        assert r.status_code == 403

    def test_detalle_inexistente_404(self, client, h_admin):
        r = client.get('/api/horas/reportes/99999', headers=_hdr(h_admin))
        assert r.status_code == 404

    def test_detalle_cerrado_no_editable(
        self, client, h_admin, reporte_cerrado,
    ):
        r = client.get(f'/api/horas/reportes/{reporte_cerrado.id}',
                        headers=_hdr(h_admin))
        body = r.get_json()
        assert body['editable'] is False


# ═══════════════════════════════════════════════════════════════════════════════
# 6. CERRAR REPORTE
# ═══════════════════════════════════════════════════════════════════════════════

class TestCerrar:

    def test_cerrar_con_registros_ok(
        self, client, h_admin, reporte, registro, db,
    ):
        r = client.post(f'/api/horas/reportes/{reporte.id}/cerrar',
                         headers=_hdr(h_admin))
        assert r.status_code == 200
        assert r.get_json()['estado'] == 'TERMINADO'
        db.session.refresh(reporte)
        assert reporte.estado == 'TERMINADO'

    def test_cerrar_sin_registros_400(self, client, h_admin, reporte):
        r = client.post(f'/api/horas/reportes/{reporte.id}/cerrar',
                         headers=_hdr(h_admin))
        assert r.status_code == 400

    def test_cerrar_ya_cerrado_409(self, client, h_admin, reporte_cerrado):
        r = client.post(f'/api/horas/reportes/{reporte_cerrado.id}/cerrar',
                         headers=_hdr(h_admin))
        assert r.status_code == 409

    def test_cerrar_inexistente_404(self, client, h_admin):
        r = client.post('/api/horas/reportes/99999/cerrar', headers=_hdr(h_admin))
        assert r.status_code == 404

    def test_coord_ajeno_403(
        self, client, h_coord, proyecto_otro, h_trab_b, db,
    ):
        r3 = ReporteSemanal(
            fecha_inicio_semana=date(2026, 4, 6),
            fecha_fin_semana=date(2026, 4, 12),
            proyecto_id=proyecto_otro.id, estado='BORRADOR',
        )
        db.session.add(r3); db.session.commit()
        r = client.post(f'/api/horas/reportes/{r3.id}/cerrar', headers=_hdr(h_coord))
        assert r.status_code == 403


# ═══════════════════════════════════════════════════════════════════════════════
# 7. CREAR REGISTRO
# ═══════════════════════════════════════════════════════════════════════════════

class TestCrearRegistro:

    def test_crea_registro_normal(
        self, client, h_admin, reporte, h_trab, fecha_lunes,
    ):
        r = client.post(
            f'/api/horas/reportes/{reporte.id}/registros',
            headers=_hdr(h_admin),
            json={
                'trabajador_id': h_trab.id,
                'fecha': fecha_lunes.isoformat(),
                'hora_entrada': '08:00', 'hora_salida': '17:00',
                'tomo_comida': True,
            },
        )
        assert r.status_code == 201, r.get_json()
        body = r.get_json()
        assert body['hora_entrada'] == '08:00'
        assert body['horas_productivas'] > 0

    def test_solo_entrada_o_solo_incidencia_es_ok(
        self, client, h_admin, reporte, h_trab, fecha_lunes,
    ):
        # Solo incidencia (sin entrada/salida)
        r = client.post(
            f'/api/horas/reportes/{reporte.id}/registros',
            headers=_hdr(h_admin),
            json={
                'trabajador_id': h_trab.id,
                'fecha': fecha_lunes.isoformat(),
                'incidencia': 'Vacaciones',
            },
        )
        assert r.status_code == 201

    def test_salida_sin_entrada_400(
        self, client, h_admin, reporte, h_trab, fecha_lunes,
    ):
        r = client.post(
            f'/api/horas/reportes/{reporte.id}/registros',
            headers=_hdr(h_admin),
            json={
                'trabajador_id': h_trab.id,
                'fecha': fecha_lunes.isoformat(),
                'hora_salida': '17:00',
            },
        )
        assert r.status_code == 400

    def test_sin_entrada_ni_incidencia_400(
        self, client, h_admin, reporte, h_trab, fecha_lunes,
    ):
        r = client.post(
            f'/api/horas/reportes/{reporte.id}/registros',
            headers=_hdr(h_admin),
            json={
                'trabajador_id': h_trab.id,
                'fecha': fecha_lunes.isoformat(),
                'tomo_comida': True,
            },
        )
        assert r.status_code == 400

    def test_fecha_fuera_de_semana_400(
        self, client, h_admin, reporte, h_trab, fecha_lunes,
    ):
        # Fuera de la semana del reporte
        afuera = (fecha_lunes + timedelta(days=20)).isoformat()
        r = client.post(
            f'/api/horas/reportes/{reporte.id}/registros',
            headers=_hdr(h_admin),
            json={
                'trabajador_id': h_trab.id, 'fecha': afuera,
                'hora_entrada': '08:00', 'hora_salida': '17:00',
            },
        )
        assert r.status_code == 400

    def test_trabajador_no_en_proyecto_400(
        self, client, h_admin, reporte, h_trab_b, fecha_lunes,
    ):
        r = client.post(
            f'/api/horas/reportes/{reporte.id}/registros',
            headers=_hdr(h_admin),
            json={
                'trabajador_id': h_trab_b.id,
                'fecha': fecha_lunes.isoformat(),
                'hora_entrada': '08:00', 'hora_salida': '17:00',
            },
        )
        assert r.status_code == 400

    def test_trabajador_inexistente_404(
        self, client, h_admin, reporte, fecha_lunes,
    ):
        r = client.post(
            f'/api/horas/reportes/{reporte.id}/registros',
            headers=_hdr(h_admin),
            json={
                'trabajador_id': 99999,
                'fecha': fecha_lunes.isoformat(),
                'hora_entrada': '08:00', 'hora_salida': '17:00',
            },
        )
        assert r.status_code == 404

    def test_falta_campos_400(self, client, h_admin, reporte):
        r = client.post(
            f'/api/horas/reportes/{reporte.id}/registros',
            headers=_hdr(h_admin),
            json={'hora_entrada': '08:00'},
        )
        assert r.status_code == 400

    def test_reporte_cerrado_409(
        self, client, h_admin, reporte_cerrado, h_trab, fecha_lunes,
    ):
        r = client.post(
            f'/api/horas/reportes/{reporte_cerrado.id}/registros',
            headers=_hdr(h_admin),
            json={
                'trabajador_id': h_trab.id,
                'fecha': reporte_cerrado.fecha_inicio_semana.isoformat(),
                'hora_entrada': '08:00', 'hora_salida': '17:00',
            },
        )
        assert r.status_code == 409

    def test_idempotencia_mismo_client_record_id(
        self, client, h_admin, reporte, h_trab, fecha_lunes,
    ):
        crid = 'kiosk-uuid-001'
        payload = {
            'trabajador_id': h_trab.id,
            'fecha': fecha_lunes.isoformat(),
            'hora_entrada': '08:00', 'hora_salida': '17:00',
            'client_record_id': crid,
        }
        r1 = client.post(
            f'/api/horas/reportes/{reporte.id}/registros',
            headers=_hdr(h_admin), json=payload,
        )
        assert r1.status_code == 201
        id_creado = r1.get_json()['id']
        # Reintento con misma key → devuelve mismo registro, no crea otro
        r2 = client.post(
            f'/api/horas/reportes/{reporte.id}/registros',
            headers=_hdr(h_admin), json=payload,
        )
        assert r2.status_code == 200
        assert r2.get_json()['id'] == id_creado
        # Solo hay 1 registro
        regs = RegistroDiarioHoras.query.filter_by(
            reporte_id=reporte.id, trabajador_id=h_trab.id,
        ).all()
        assert len(regs) == 1

    def test_idempotencia_cross_reporte_404(
        self, client, h_admin, reporte, h_trab, fecha_lunes, db,
        proyecto_coord, h_coord,
    ):
        # Creamos el registro con client_record_id en reporte1
        crid = 'kiosk-uuid-cross'
        client.post(
            f'/api/horas/reportes/{reporte.id}/registros',
            headers=_hdr(h_admin),
            json={
                'trabajador_id': h_trab.id,
                'fecha': fecha_lunes.isoformat(),
                'hora_entrada': '08:00', 'hora_salida': '17:00',
                'client_record_id': crid,
            },
        )
        # Otro reporte del mismo proyecto en otra semana
        r2 = ReporteSemanal(
            fecha_inicio_semana=date(2026, 4, 6),
            fecha_fin_semana=date(2026, 4, 12),
            proyecto_id=proyecto_coord.id, estado='BORRADOR',
            creado_por_id=h_coord.id,
        )
        db.session.add(r2); db.session.commit()
        # Mismo client_record_id pero contra otro reporte → 404 (no leak)
        r = client.post(
            f'/api/horas/reportes/{r2.id}/registros',
            headers=_hdr(h_admin),
            json={
                'trabajador_id': h_trab.id,
                'fecha': '2026-04-06',
                'hora_entrada': '08:00', 'hora_salida': '17:00',
                'client_record_id': crid,
            },
        )
        assert r.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# 8. BULK UPSERT
# ═══════════════════════════════════════════════════════════════════════════════

class TestBulkUpsert:

    def test_bulk_crea_varios(
        self, client, h_admin, reporte, h_trab, fecha_lunes,
    ):
        rows = [
            {
                'trabajador_id': h_trab.id,
                'fecha': (fecha_lunes + timedelta(days=i)).isoformat(),
                'hora_entrada': '08:00', 'hora_salida': '17:00',
            }
            for i in range(3)
        ]
        r = client.post(
            f'/api/horas/reportes/{reporte.id}/registros/bulk',
            headers=_hdr(h_admin), json={'registros': rows},
        )
        assert r.status_code == 200, r.get_json()
        body = r.get_json()
        assert body['created'] == 3
        assert body['updated'] == 0
        assert body['skipped'] == []

    def test_bulk_upserts_existente(
        self, client, h_admin, reporte, registro, h_trab, fecha_lunes,
    ):
        # `registro` ya existe lunes; bulk con la misma fecha+trab debe UPDATE
        r = client.post(
            f'/api/horas/reportes/{reporte.id}/registros/bulk',
            headers=_hdr(h_admin),
            json={'registros': [{
                'trabajador_id': h_trab.id,
                'fecha': fecha_lunes.isoformat(),
                'hora_entrada': '09:00', 'hora_salida': '18:00',
            }]},
        )
        assert r.status_code == 200
        body = r.get_json()
        assert body['updated'] == 1
        assert body['created'] == 0

    def test_bulk_skipped_por_fila_invalida(
        self, client, h_admin, reporte, h_trab, h_trab_b, fecha_lunes,
    ):
        # Mezclamos válidos e inválidos: el endpoint NO falla, los skipea
        rows = [
            {  # válido
                'trabajador_id': h_trab.id,
                'fecha': fecha_lunes.isoformat(),
                'hora_entrada': '08:00', 'hora_salida': '17:00',
            },
            {  # trabajador no asignado al proyecto
                'trabajador_id': h_trab_b.id,
                'fecha': fecha_lunes.isoformat(),
                'hora_entrada': '08:00', 'hora_salida': '17:00',
            },
            {  # fecha fuera de semana
                'trabajador_id': h_trab.id,
                'fecha': (fecha_lunes + timedelta(days=30)).isoformat(),
                'hora_entrada': '08:00', 'hora_salida': '17:00',
            },
            {  # fecha vacía
                'trabajador_id': h_trab.id,
                'hora_entrada': '08:00', 'hora_salida': '17:00',
            },
            {  # fecha inválida
                'trabajador_id': h_trab.id,
                'fecha': 'no-fecha',
                'hora_entrada': '08:00', 'hora_salida': '17:00',
            },
            {  # trabajador_id no numérico
                'trabajador_id': 'abc',
                'fecha': fecha_lunes.isoformat(),
            },
        ]
        r = client.post(
            f'/api/horas/reportes/{reporte.id}/registros/bulk',
            headers=_hdr(h_admin), json={'registros': rows},
        )
        assert r.status_code == 200
        body = r.get_json()
        assert body['created'] == 1
        assert len(body['skipped']) == 5

    def test_bulk_vacia_422(self, client, h_admin, reporte):
        r = client.post(
            f'/api/horas/reportes/{reporte.id}/registros/bulk',
            headers=_hdr(h_admin), json={'registros': []},
        )
        assert r.status_code == 422

    def test_bulk_mas_de_200_422(self, client, h_admin, reporte):
        rows = [{'trabajador_id': 1, 'fecha': '2026-03-02'} for _ in range(201)]
        r = client.post(
            f'/api/horas/reportes/{reporte.id}/registros/bulk',
            headers=_hdr(h_admin), json={'registros': rows},
        )
        assert r.status_code == 422

    def test_bulk_reporte_cerrado_409(
        self, client, h_admin, reporte_cerrado, h_trab,
    ):
        r = client.post(
            f'/api/horas/reportes/{reporte_cerrado.id}/registros/bulk',
            headers=_hdr(h_admin),
            json={'registros': [{
                'trabajador_id': h_trab.id,
                'fecha': reporte_cerrado.fecha_inicio_semana.isoformat(),
                'hora_entrada': '08:00', 'hora_salida': '17:00',
            }]},
        )
        assert r.status_code == 409


# ═══════════════════════════════════════════════════════════════════════════════
# 9. EDITAR REGISTRO
# ═══════════════════════════════════════════════════════════════════════════════

class TestEditar:

    def test_editar_ok(self, client, h_admin, registro, db):
        r = client.put(
            f'/api/horas/registros/{registro.id}',
            headers=_hdr(h_admin),
            json={'hora_entrada': '09:00', 'hora_salida': '18:00'},
        )
        assert r.status_code == 200, r.get_json()
        db.session.refresh(registro)
        assert registro.hora_entrada == time(9, 0)
        assert registro.hora_salida == time(18, 0)

    def test_editar_inexistente_404(self, client, h_admin):
        r = client.put('/api/horas/registros/99999', headers=_hdr(h_admin), json={
            'hora_entrada': '08:00', 'hora_salida': '17:00',
        })
        assert r.status_code == 404

    def test_editar_reporte_cerrado_409(
        self, client, h_admin, reporte_cerrado, h_trab, db,
    ):
        reg = RegistroDiarioHoras(
            reporte_id=reporte_cerrado.id, trabajador_id=h_trab.id,
            fecha=reporte_cerrado.fecha_inicio_semana,
            hora_entrada=time(8, 0), hora_salida=time(17, 0),
            tipo_nomina='Semanal',
        )
        db.session.add(reg); db.session.commit()
        r = client.put(
            f'/api/horas/registros/{reg.id}',
            headers=_hdr(h_admin),
            json={'hora_entrada': '09:00', 'hora_salida': '18:00'},
        )
        assert r.status_code == 409

    def test_editar_hora_igual_400(
        self, client, h_admin, registro,
    ):
        r = client.put(
            f'/api/horas/registros/{registro.id}',
            headers=_hdr(h_admin),
            json={'hora_entrada': '10:00', 'hora_salida': '10:00'},
        )
        assert r.status_code == 400

    def test_lww_conflict_409(self, client, h_admin, registro, db):
        # Servidor ya tiene `modificado_en` reciente; cliente declara uno viejo
        from datetime import datetime, timezone
        registro.modificado_en = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
        db.session.commit()
        r = client.put(
            f'/api/horas/registros/{registro.id}',
            headers=_hdr(h_admin),
            json={
                'hora_entrada': '09:00', 'hora_salida': '18:00',
                'modificado_en': '2026-01-01T00:00:00Z',
            },
        )
        assert r.status_code == 409
        assert r.get_json()['conflicto'] is True

    def test_modificado_en_formato_invalido_400(
        self, client, h_admin, registro, db,
    ):
        # Necesario que reg.modificado_en exista para que se valide el parse
        from datetime import datetime, timezone
        registro.modificado_en = datetime.now(timezone.utc)
        db.session.commit()
        r = client.put(
            f'/api/horas/registros/{registro.id}',
            headers=_hdr(h_admin),
            json={
                'hora_entrada': '09:00', 'hora_salida': '18:00',
                'modificado_en': 'no-iso',
            },
        )
        assert r.status_code == 400


# ═══════════════════════════════════════════════════════════════════════════════
# 10. ELIMINAR REGISTRO
# ═══════════════════════════════════════════════════════════════════════════════

class TestEliminar:

    def test_eliminar_ok(self, client, h_admin, registro):
        r = client.delete(f'/api/horas/registros/{registro.id}', headers=_hdr(h_admin))
        assert r.status_code == 200
        assert RegistroDiarioHoras.query.get(registro.id) is None

    def test_eliminar_inexistente_404(self, client, h_admin):
        r = client.delete('/api/horas/registros/99999', headers=_hdr(h_admin))
        assert r.status_code == 404

    def test_eliminar_reporte_cerrado_409(
        self, client, h_admin, reporte_cerrado, h_trab, db,
    ):
        reg = RegistroDiarioHoras(
            reporte_id=reporte_cerrado.id, trabajador_id=h_trab.id,
            fecha=reporte_cerrado.fecha_inicio_semana,
            hora_entrada=time(8, 0), hora_salida=time(17, 0),
            tipo_nomina='Semanal',
        )
        db.session.add(reg); db.session.commit()
        r = client.delete(f'/api/horas/registros/{reg.id}', headers=_hdr(h_admin))
        assert r.status_code == 409

    def test_coord_ajeno_403(
        self, client, h_coord, proyecto_otro, h_trab_b, db,
    ):
        r3 = ReporteSemanal(
            fecha_inicio_semana=date(2026, 4, 6),
            fecha_fin_semana=date(2026, 4, 12),
            proyecto_id=proyecto_otro.id, estado='BORRADOR',
        )
        db.session.add(r3); db.session.flush()
        reg = RegistroDiarioHoras(
            reporte_id=r3.id, trabajador_id=h_trab_b.id,
            fecha=date(2026, 4, 6), hora_entrada=time(8, 0),
            hora_salida=time(17, 0), tipo_nomina='Semanal',
        )
        db.session.add(reg); db.session.commit()
        r = client.delete(f'/api/horas/registros/{reg.id}', headers=_hdr(h_coord))
        assert r.status_code == 403
