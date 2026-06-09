"""Tests del API JWT `/api/ajustes/*` — periodos Inbursa.

Cobertura:
  - GET    /periodos                            listar (paginación + q)
  - POST   /periodos                            crear (con trabajadores+meta)
  - GET    /periodos/<id>                       detalle (totales por trabajador)
  - POST   /periodos/<id>/cerrar                ABIERTO → CERRADO
  - GET    /periodos/<id>/excel                 export
  - GET    /trabajadores-disponibles            catálogo
  - POST   /periodos/<id>/descuentos            agregar descuento
  - DELETE /descuentos/<id>                     eliminar (guarda contra cobrado/cerrado)
  - POST   /descuentos/bulk-delete              borrado masivo con `skipped`

Reglas no obvias:
  - Solo admin/super_admin.
  - Solapamiento de fechas con otro periodo → 409.
  - `fecha_inicio < fecha_fin`; sin esto → 400.
  - Periodo debe tener al menos un trabajador con `monto_meta > 0`.
  - Agregar descuento: la fecha debe caer dentro del periodo, el trabajador
    debe estar asignado al periodo, el monto > 0.
  - Periodo CERRADO bloquea agregar y eliminar descuentos.
  - Eliminar descuento `cobrado=True` (ya consumido por prenómina) → 400.
  - Bulk-delete: salta los inelegibles en `skipped` con `reason`, no falla.
"""
from datetime import date
from decimal import Decimal

import pytest
from werkzeug.security import generate_password_hash

from app.models import (
    AjusteDescuento, AjustePeriodo, AjusteTrabajadorPeriodo,
    Trabajador, User,
)
from app.routes.api_auth import _encode_access_token


def _hdr(user):
    return {'Authorization': f'Bearer {_encode_access_token(user)}'}


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def aj_admin(db):
    u = User(username='aj_admin', password_hash=generate_password_hash('Pass123!'), role='admin')
    db.session.add(u); db.session.commit()
    return u


@pytest.fixture
def aj_coord(db):
    u = User(username='aj_coord', password_hash=generate_password_hash('Pass123!'),
              role='coordinador')
    db.session.add(u); db.session.commit()
    return u


@pytest.fixture
def aj_trab_a(db):
    t = Trabajador(no_empleado='AJ-A', nombre='Alma', nombre_apellidos='Vega',
                    activo=True, tipo_nomina='Semanal',
                    salario_real_pactado_x_sem=Decimal('5000'))
    db.session.add(t); db.session.commit()
    return t


@pytest.fixture
def aj_trab_b(db):
    t = Trabajador(no_empleado='AJ-B', nombre='Bruno', nombre_apellidos='Lara',
                    activo=True, tipo_nomina='Semanal',
                    salario_real_pactado_x_sem=Decimal('4500'))
    db.session.add(t); db.session.commit()
    return t


@pytest.fixture
def periodo_abierto(db, aj_trab_a):
    """Periodo ABIERTO de febrero 2026 con un trabajador (meta $1000)."""
    p = AjustePeriodo(nombre='Febrero 2026',
                      fecha_inicio=date(2026, 2, 1),
                      fecha_fin=date(2026, 2, 28),
                      estado='ABIERTO')
    db.session.add(p); db.session.flush()
    db.session.add(AjusteTrabajadorPeriodo(
        periodo_id=p.id, trabajador_id=aj_trab_a.id,
        monto_meta=Decimal('1000'),
    ))
    db.session.commit()
    return p


@pytest.fixture
def periodo_cerrado(db, aj_trab_a):
    p = AjustePeriodo(nombre='Enero 2026',
                      fecha_inicio=date(2026, 1, 1),
                      fecha_fin=date(2026, 1, 31),
                      estado='CERRADO')
    db.session.add(p); db.session.flush()
    db.session.add(AjusteTrabajadorPeriodo(
        periodo_id=p.id, trabajador_id=aj_trab_a.id,
        monto_meta=Decimal('500'),
    ))
    db.session.commit()
    return p


# ═══════════════════════════════════════════════════════════════════════════════
# 1. AUTH
# ═══════════════════════════════════════════════════════════════════════════════

class TestAuth:

    def test_sin_token_401(self, client):
        r = client.get('/api/ajustes/periodos')
        assert r.status_code == 401

    def test_coord_403(self, client, aj_coord):
        r = client.get('/api/ajustes/periodos', headers=_hdr(aj_coord))
        assert r.status_code == 403

    def test_admin_200(self, client, aj_admin):
        r = client.get('/api/ajustes/periodos', headers=_hdr(aj_admin))
        assert r.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════════
# 2. LISTAR PERIODOS
# ═══════════════════════════════════════════════════════════════════════════════

class TestListar:

    def test_listar_vacio(self, client, aj_admin):
        r = client.get('/api/ajustes/periodos', headers=_hdr(aj_admin))
        body = r.get_json()
        assert body['items'] == [] and body['total'] == 0

    def test_listar_con_periodos(
        self, client, aj_admin, periodo_abierto, periodo_cerrado,
    ):
        r = client.get('/api/ajustes/periodos', headers=_hdr(aj_admin))
        items = r.get_json()['items']
        nombres = {p['nombre'] for p in items}
        assert {'Febrero 2026', 'Enero 2026'} <= nombres

    def test_filtro_q_por_nombre(
        self, client, aj_admin, periodo_abierto, periodo_cerrado,
    ):
        r = client.get('/api/ajustes/periodos?q=enero', headers=_hdr(aj_admin))
        items = r.get_json()['items']
        nombres = {p['nombre'] for p in items}
        assert nombres == {'Enero 2026'}

    def test_paginacion(
        self, client, aj_admin, periodo_abierto, periodo_cerrado,
    ):
        r = client.get('/api/ajustes/periodos?per_page=1', headers=_hdr(aj_admin))
        body = r.get_json()
        assert len(body['items']) == 1
        assert body['total'] == 2
        assert body['pages'] == 2


# ═══════════════════════════════════════════════════════════════════════════════
# 3. CREAR PERIODO
# ═══════════════════════════════════════════════════════════════════════════════

class TestCrear:

    def _payload(self, trab, *, nombre='Marzo 2026',
                  ini='2026-03-01', fin='2026-03-31', monto=750):
        return {
            'nombre': nombre,
            'fecha_inicio': ini, 'fecha_fin': fin,
            'trabajadores': [{'trabajador_id': trab.id, 'monto_meta': monto}],
        }

    def test_admin_crea(self, client, aj_admin, aj_trab_a, db):
        r = client.post('/api/ajustes/periodos', headers=_hdr(aj_admin),
                        json=self._payload(aj_trab_a))
        assert r.status_code == 201, r.get_json()
        body = r.get_json()
        assert body['creados'] == 1
        # Persiste y asocia el trabajador
        p = AjustePeriodo.query.get(body['id'])
        assert p.nombre == 'Marzo 2026'
        tps = AjusteTrabajadorPeriodo.query.filter_by(periodo_id=p.id).all()
        assert len(tps) == 1
        assert tps[0].trabajador_id == aj_trab_a.id
        assert float(tps[0].monto_meta) == 750.0

    def test_coord_403(self, client, aj_coord, aj_trab_a):
        r = client.post('/api/ajustes/periodos', headers=_hdr(aj_coord),
                        json=self._payload(aj_trab_a))
        assert r.status_code == 403

    def test_falta_nombre_400(self, client, aj_admin, aj_trab_a):
        payload = self._payload(aj_trab_a); payload['nombre'] = ''
        r = client.post('/api/ajustes/periodos', headers=_hdr(aj_admin), json=payload)
        assert r.status_code == 400

    def test_falta_fecha_400(self, client, aj_admin, aj_trab_a):
        payload = self._payload(aj_trab_a); payload['fecha_fin'] = None
        r = client.post('/api/ajustes/periodos', headers=_hdr(aj_admin), json=payload)
        assert r.status_code == 400

    def test_fecha_invalida_400(self, client, aj_admin, aj_trab_a):
        payload = self._payload(aj_trab_a, ini='no-fecha')
        r = client.post('/api/ajustes/periodos', headers=_hdr(aj_admin), json=payload)
        assert r.status_code == 400

    def test_fecha_inicio_no_anterior_a_fin_400(self, client, aj_admin, aj_trab_a):
        payload = self._payload(aj_trab_a, ini='2026-03-31', fin='2026-03-01')
        r = client.post('/api/ajustes/periodos', headers=_hdr(aj_admin), json=payload)
        assert r.status_code == 400

    def test_solapamiento_con_existente_409(
        self, client, aj_admin, aj_trab_a, periodo_abierto,
    ):
        # `periodo_abierto` cubre 1-28 feb; pidamos 15 feb a 15 mar → solapa
        payload = self._payload(aj_trab_a, ini='2026-02-15', fin='2026-03-15')
        r = client.post('/api/ajustes/periodos', headers=_hdr(aj_admin), json=payload)
        assert r.status_code == 409

    def test_sin_trabajadores_400(self, client, aj_admin):
        r = client.post('/api/ajustes/periodos', headers=_hdr(aj_admin), json={
            'nombre': 'Abril 2026',
            'fecha_inicio': '2026-04-01', 'fecha_fin': '2026-04-30',
            'trabajadores': [],
        })
        assert r.status_code == 400

    def test_todos_los_trabajadores_con_meta_cero_400(
        self, client, aj_admin, aj_trab_a,
    ):
        r = client.post('/api/ajustes/periodos', headers=_hdr(aj_admin), json={
            'nombre': 'Abril 2026',
            'fecha_inicio': '2026-04-01', 'fecha_fin': '2026-04-30',
            'trabajadores': [{'trabajador_id': aj_trab_a.id, 'monto_meta': 0}],
        })
        assert r.status_code == 400

    def test_meta_cero_descartada_pero_otras_validas_pasan(
        self, client, aj_admin, aj_trab_a, aj_trab_b, db,
    ):
        r = client.post('/api/ajustes/periodos', headers=_hdr(aj_admin), json={
            'nombre': 'Abril 2026',
            'fecha_inicio': '2026-04-01', 'fecha_fin': '2026-04-30',
            'trabajadores': [
                {'trabajador_id': aj_trab_a.id, 'monto_meta': 0},  # descartado
                {'trabajador_id': aj_trab_b.id, 'monto_meta': 600},
            ],
        })
        assert r.status_code == 201
        assert r.get_json()['creados'] == 1


# ═══════════════════════════════════════════════════════════════════════════════
# 4. DETALLE
# ═══════════════════════════════════════════════════════════════════════════════

class TestDetalle:

    def test_detalle_sin_descuentos(self, client, aj_admin, periodo_abierto):
        r = client.get(
            f'/api/ajustes/periodos/{periodo_abierto.id}',
            headers=_hdr(aj_admin),
        )
        body = r.get_json()
        assert body['estado'] == 'ABIERTO'
        assert body['editable'] is True
        assert len(body['trabajadores']) == 1
        t = body['trabajadores'][0]
        assert t['monto_meta'] == 1000.0
        assert t['total_descontado'] == 0.0
        assert t['restante'] == 1000.0
        assert t['porcentaje'] == 0

    def test_detalle_con_descuentos_calcula_porcentaje(
        self, client, aj_admin, periodo_abierto, aj_trab_a, db,
    ):
        # Dos descuentos por $300 → 60% de la meta $1000
        for monto in (200, 100):
            db.session.add(AjusteDescuento(
                periodo_id=periodo_abierto.id,
                trabajador_id=aj_trab_a.id,
                monto=Decimal(str(monto)),
                fecha_descuento=date(2026, 2, 10),
                notas='X',
            ))
        db.session.commit()
        r = client.get(
            f'/api/ajustes/periodos/{periodo_abierto.id}',
            headers=_hdr(aj_admin),
        )
        t = r.get_json()['trabajadores'][0]
        assert t['total_descontado'] == 300.0
        assert t['restante'] == 700.0
        assert t['porcentaje'] == 30

    def test_detalle_cerrado_no_es_editable(self, client, aj_admin, periodo_cerrado):
        r = client.get(
            f'/api/ajustes/periodos/{periodo_cerrado.id}',
            headers=_hdr(aj_admin),
        )
        body = r.get_json()
        assert body['estado'] == 'CERRADO'
        assert body['editable'] is False

    def test_detalle_inexistente_404(self, client, aj_admin):
        r = client.get('/api/ajustes/periodos/99999', headers=_hdr(aj_admin))
        assert r.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# 5. CERRAR
# ═══════════════════════════════════════════════════════════════════════════════

class TestCerrar:

    def test_cerrar_abre_periodo(self, client, aj_admin, periodo_abierto, db):
        r = client.post(
            f'/api/ajustes/periodos/{periodo_abierto.id}/cerrar',
            headers=_hdr(aj_admin),
        )
        assert r.status_code == 200
        assert r.get_json()['estado'] == 'CERRADO'
        db.session.refresh(periodo_abierto)
        assert periodo_abierto.estado == 'CERRADO'

    def test_cerrar_ya_cerrado_400(self, client, aj_admin, periodo_cerrado):
        r = client.post(
            f'/api/ajustes/periodos/{periodo_cerrado.id}/cerrar',
            headers=_hdr(aj_admin),
        )
        assert r.status_code == 400

    def test_cerrar_inexistente_404(self, client, aj_admin):
        r = client.post('/api/ajustes/periodos/99999/cerrar', headers=_hdr(aj_admin))
        assert r.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# 6. EXCEL
# ═══════════════════════════════════════════════════════════════════════════════

class TestExcel:

    def test_excel_descarga(self, client, aj_admin, periodo_abierto):
        r = client.get(
            f'/api/ajustes/periodos/{periodo_abierto.id}/excel',
            headers=_hdr(aj_admin),
        )
        assert r.status_code == 200
        assert r.mimetype == (
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )

    def test_excel_periodo_vacio_404(self, client, aj_admin, db):
        # Periodo sin trabajadores ni ajustes (creado a mano)
        vacio = AjustePeriodo(nombre='Vacío', fecha_inicio=date(2026, 5, 1),
                               fecha_fin=date(2026, 5, 31), estado='ABIERTO')
        db.session.add(vacio); db.session.commit()
        r = client.get(
            f'/api/ajustes/periodos/{vacio.id}/excel',
            headers=_hdr(aj_admin),
        )
        assert r.status_code == 404

    def test_excel_inexistente_404(self, client, aj_admin):
        r = client.get('/api/ajustes/periodos/99999/excel', headers=_hdr(aj_admin))
        assert r.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# 7. TRABAJADORES DISPONIBLES
# ═══════════════════════════════════════════════════════════════════════════════

class TestTrabajadoresDisponibles:

    def test_solo_activos(self, client, aj_admin, aj_trab_a, aj_trab_b, db):
        inactivo = Trabajador(no_empleado='AJ-INA', nombre='Z', nombre_apellidos='Z',
                               activo=False, tipo_nomina='Semanal',
                               salario_real_pactado_x_sem=Decimal('1000'))
        db.session.add(inactivo); db.session.commit()
        r = client.get('/api/ajustes/trabajadores-disponibles', headers=_hdr(aj_admin))
        codigos = {t['no_empleado'] for t in r.get_json()}
        assert {'AJ-A', 'AJ-B'} <= codigos
        assert 'AJ-INA' not in codigos

    def test_coord_403(self, client, aj_coord):
        r = client.get('/api/ajustes/trabajadores-disponibles', headers=_hdr(aj_coord))
        assert r.status_code == 403


# ═══════════════════════════════════════════════════════════════════════════════
# 8. AGREGAR DESCUENTO
# ═══════════════════════════════════════════════════════════════════════════════

class TestAgregarDescuento:

    def test_agrega_descuento_dentro_del_periodo(
        self, client, aj_admin, periodo_abierto, aj_trab_a,
    ):
        r = client.post(
            f'/api/ajustes/periodos/{periodo_abierto.id}/descuentos',
            headers=_hdr(aj_admin),
            json={
                'trabajador_id': aj_trab_a.id,
                'monto': 250,
                'fecha_descuento': '2026-02-15',
                'notas': 'Cuota 1',
            },
        )
        assert r.status_code == 201, r.get_json()
        body = r.get_json()
        assert body['monto'] == 250.0
        assert body['cobrado'] is False
        # Persistencia
        descs = AjusteDescuento.query.filter_by(periodo_id=periodo_abierto.id).all()
        assert len(descs) == 1

    def test_falta_campos_400(self, client, aj_admin, periodo_abierto):
        r = client.post(
            f'/api/ajustes/periodos/{periodo_abierto.id}/descuentos',
            headers=_hdr(aj_admin),
            json={'monto': 100},  # falta trabajador_id y fecha_descuento
        )
        assert r.status_code == 400

    def test_monto_cero_400(
        self, client, aj_admin, periodo_abierto, aj_trab_a,
    ):
        r = client.post(
            f'/api/ajustes/periodos/{periodo_abierto.id}/descuentos',
            headers=_hdr(aj_admin),
            json={
                'trabajador_id': aj_trab_a.id, 'monto': 0,
                'fecha_descuento': '2026-02-10',
            },
        )
        assert r.status_code == 400

    def test_fecha_fuera_del_periodo_400(
        self, client, aj_admin, periodo_abierto, aj_trab_a,
    ):
        # periodo va 1-28 feb → 5 marzo está fuera
        r = client.post(
            f'/api/ajustes/periodos/{periodo_abierto.id}/descuentos',
            headers=_hdr(aj_admin),
            json={
                'trabajador_id': aj_trab_a.id, 'monto': 100,
                'fecha_descuento': '2026-03-05',
            },
        )
        assert r.status_code == 400

    def test_trabajador_no_asignado_al_periodo_400(
        self, client, aj_admin, periodo_abierto, aj_trab_b,
    ):
        # `aj_trab_b` no está en `periodo_abierto`
        r = client.post(
            f'/api/ajustes/periodos/{periodo_abierto.id}/descuentos',
            headers=_hdr(aj_admin),
            json={
                'trabajador_id': aj_trab_b.id, 'monto': 100,
                'fecha_descuento': '2026-02-15',
            },
        )
        assert r.status_code == 400

    def test_periodo_cerrado_400(
        self, client, aj_admin, periodo_cerrado, aj_trab_a,
    ):
        r = client.post(
            f'/api/ajustes/periodos/{periodo_cerrado.id}/descuentos',
            headers=_hdr(aj_admin),
            json={
                'trabajador_id': aj_trab_a.id, 'monto': 100,
                'fecha_descuento': '2026-01-15',
            },
        )
        assert r.status_code == 400

    def test_periodo_inexistente_404(self, client, aj_admin, aj_trab_a):
        r = client.post(
            '/api/ajustes/periodos/99999/descuentos',
            headers=_hdr(aj_admin),
            json={
                'trabajador_id': aj_trab_a.id, 'monto': 100,
                'fecha_descuento': '2026-02-15',
            },
        )
        assert r.status_code == 404

    def test_coord_403(
        self, client, aj_coord, periodo_abierto, aj_trab_a,
    ):
        r = client.post(
            f'/api/ajustes/periodos/{periodo_abierto.id}/descuentos',
            headers=_hdr(aj_coord),
            json={
                'trabajador_id': aj_trab_a.id, 'monto': 100,
                'fecha_descuento': '2026-02-15',
            },
        )
        assert r.status_code == 403


# ═══════════════════════════════════════════════════════════════════════════════
# 9. ELIMINAR DESCUENTO
# ═══════════════════════════════════════════════════════════════════════════════

class TestEliminarDescuento:

    def _descuento(self, db, periodo, trab, monto=100, cobrado=False):
        d = AjusteDescuento(
            periodo_id=periodo.id, trabajador_id=trab.id,
            monto=Decimal(str(monto)),
            fecha_descuento=date(2026, 2, 10),
            cobrado=cobrado,
        )
        db.session.add(d); db.session.commit()
        return d

    def test_eliminar_ok(self, client, aj_admin, periodo_abierto, aj_trab_a, db):
        d = self._descuento(db, periodo_abierto, aj_trab_a)
        r = client.delete(f'/api/ajustes/descuentos/{d.id}', headers=_hdr(aj_admin))
        assert r.status_code == 200
        assert AjusteDescuento.query.get(d.id) is None

    def test_eliminar_de_periodo_cerrado_400(
        self, client, aj_admin, periodo_cerrado, aj_trab_a, db,
    ):
        d = self._descuento(db, periodo_cerrado, aj_trab_a)
        r = client.delete(f'/api/ajustes/descuentos/{d.id}', headers=_hdr(aj_admin))
        assert r.status_code == 400

    def test_eliminar_cobrado_400(
        self, client, aj_admin, periodo_abierto, aj_trab_a, db,
    ):
        d = self._descuento(db, periodo_abierto, aj_trab_a, cobrado=True)
        r = client.delete(f'/api/ajustes/descuentos/{d.id}', headers=_hdr(aj_admin))
        assert r.status_code == 400

    def test_eliminar_inexistente_404(self, client, aj_admin):
        r = client.delete('/api/ajustes/descuentos/99999', headers=_hdr(aj_admin))
        assert r.status_code == 404

    def test_coord_403(self, client, aj_coord, periodo_abierto, aj_trab_a, db):
        d = self._descuento(db, periodo_abierto, aj_trab_a)
        r = client.delete(f'/api/ajustes/descuentos/{d.id}', headers=_hdr(aj_coord))
        assert r.status_code == 403


# ═══════════════════════════════════════════════════════════════════════════════
# 10. BULK DELETE
# ═══════════════════════════════════════════════════════════════════════════════

class TestBulkDelete:

    def _descuento(self, db, periodo, trab, monto=50, cobrado=False):
        d = AjusteDescuento(
            periodo_id=periodo.id, trabajador_id=trab.id,
            monto=Decimal(str(monto)),
            fecha_descuento=date(2026, 2, 10),
            cobrado=cobrado,
        )
        db.session.add(d); db.session.commit()
        return d

    def test_bulk_delete_solo_elegibles(
        self, client, aj_admin, periodo_abierto, periodo_cerrado, aj_trab_a, db,
    ):
        ok = self._descuento(db, periodo_abierto, aj_trab_a)
        cobrado = self._descuento(db, periodo_abierto, aj_trab_a, cobrado=True)
        cerrado = self._descuento(db, periodo_cerrado, aj_trab_a)
        r = client.post('/api/ajustes/descuentos/bulk-delete',
                         headers=_hdr(aj_admin),
                         json={'descuento_ids': [ok.id, cobrado.id, cerrado.id]})
        assert r.status_code == 200, r.get_json()
        body = r.get_json()
        assert body['deleted'] == 1
        assert body['ids'] == [ok.id]
        razones = {s['reason'] for s in body['skipped']}
        assert {'ya_cobrado', 'periodo_cerrado'} <= razones

    def test_bulk_id_inexistente_va_a_skipped(
        self, client, aj_admin, periodo_abierto, aj_trab_a, db,
    ):
        ok = self._descuento(db, periodo_abierto, aj_trab_a)
        r = client.post('/api/ajustes/descuentos/bulk-delete',
                         headers=_hdr(aj_admin),
                         json={'descuento_ids': [ok.id, 99999]})
        body = r.get_json()
        assert body['deleted'] == 1
        skipped_ids = {s['id'] for s in body['skipped']}
        assert 99999 in skipped_ids

    def test_bulk_lista_vacia_422(self, client, aj_admin):
        r = client.post('/api/ajustes/descuentos/bulk-delete',
                         headers=_hdr(aj_admin),
                         json={'descuento_ids': []})
        assert r.status_code == 422

    def test_bulk_mas_de_200_422(self, client, aj_admin):
        r = client.post('/api/ajustes/descuentos/bulk-delete',
                         headers=_hdr(aj_admin),
                         json={'descuento_ids': list(range(201))})
        assert r.status_code == 422

    def test_bulk_ids_no_enteros_422(self, client, aj_admin):
        r = client.post('/api/ajustes/descuentos/bulk-delete',
                         headers=_hdr(aj_admin),
                         json={'descuento_ids': ['abc']})
        assert r.status_code == 422

    def test_bulk_coord_403(self, client, aj_coord):
        r = client.post('/api/ajustes/descuentos/bulk-delete',
                         headers=_hdr(aj_coord),
                         json={'descuento_ids': [1]})
        assert r.status_code == 403
