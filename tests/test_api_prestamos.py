"""Tests del API JWT `/api/prestamos/*` — préstamos a empleados.

Cobertura:
  - GET   /                              listar (paginación + filtros q/estado)
  - GET   /trabajadores-disponibles      catálogo de trabajadores activos
  - GET   /<id>                          detalle con abonos + total_abonado
  - POST  /                              crear préstamo
  - PUT   /<id>                          editar (con guardas vs LIQUIDADO y
                                         contra `nuevo_monto < total_abonado`)
  - POST  /<id>/abonar                   abono manual (parcial → puede liquidar)
  - POST  /<id>/liquidar                 liquidación total manual
  - GET   /trabajadores/<id>/excel       export Excel

Reglas no obvias que cubrimos:
  - Solo admin/super_admin (`require_admin`) — coordinador 403.
  - Crear: monto, plazo y descuento > 0; fecha YYYY-MM-DD; tipos numéricos
    forzados (string-decimal aceptado).
  - Editar: bloqueado si `estado == 'LIQUIDADO'`; `nuevo_monto` no puede
    ser menor a lo ya abonado.
  - Abonar: si `monto >= monto_restante`, el préstamo pasa a `LIQUIDADO`
    automáticamente.
  - Crear/editar/abonar/liquidar deben recalcular las prenóminas ABIERTAS
    del trabajador (verificación: `descuento_prestamos` se actualiza en
    la prenómina abierta del mismo trabajador).
"""
from datetime import date, timedelta
from decimal import Decimal

import pytest
from werkzeug.security import generate_password_hash

from app.models import AbonoPrestamo, Prenomina, Prestamo, Trabajador, User
from app.routes.api_auth import _encode_access_token


def _hdr(user):
    return {'Authorization': f'Bearer {_encode_access_token(user)}'}


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def pr_admin(db):
    u = User(username='pr_admin', password_hash=generate_password_hash('Pass123!'), role='admin')
    db.session.add(u); db.session.commit()
    return u


@pytest.fixture
def pr_coord(db):
    u = User(username='pr_coord', password_hash=generate_password_hash('Pass123!'),
              role='coordinador')
    db.session.add(u); db.session.commit()
    return u


@pytest.fixture
def pr_trab(db):
    t = Trabajador(
        no_empleado='PR-001', nombre='Iván', nombre_apellidos='Vega',
        activo=True, tipo_nomina='Semanal',
        salario_real_pactado_x_sem=Decimal('5000'),
        hr_extra=0, infonavit=0, ajuste_inbursa=0, viaticos=0,
    )
    db.session.add(t); db.session.commit()
    return t


@pytest.fixture
def pr_trab_b(db):
    t = Trabajador(
        no_empleado='PR-002', nombre='Sara', nombre_apellidos='Rivas',
        activo=True, tipo_nomina='Semanal',
        salario_real_pactado_x_sem=Decimal('4500'),
        hr_extra=0, infonavit=0, ajuste_inbursa=0, viaticos=0,
    )
    db.session.add(t); db.session.commit()
    return t


@pytest.fixture
def prestamo_activo(db, pr_trab):
    p = Prestamo(
        trabajador_id=pr_trab.id,
        monto_total=Decimal('5000'),
        monto_restante=Decimal('5000'),
        plazo_semanas=10,
        descuento_semanal=Decimal('500'),
        motivo='Inicial',
        frecuencia='semanal',
        fecha_inicio=date.today(),
        estado='ACTIVO', activo=True,
    )
    db.session.add(p); db.session.commit()
    return p


@pytest.fixture
def prestamo_liquidado(db, pr_trab):
    p = Prestamo(
        trabajador_id=pr_trab.id,
        monto_total=Decimal('1000'), monto_restante=Decimal('0'),
        plazo_semanas=2, descuento_semanal=Decimal('500'),
        estado='LIQUIDADO', activo=False,
    )
    db.session.add(p); db.session.commit()
    return p


@pytest.fixture
def prenomina_abierta(db, pr_trab):
    """Prenómina ABIERTA del trabajador — debe recalcularse cada vez que se
    crea/edita/abona/liquida un préstamo del mismo trabajador."""
    lunes = date.today() - timedelta(days=date.today().weekday())
    p = Prenomina(
        trabajador_id=pr_trab.id,
        fecha_inicio=lunes,
        fecha_fin=lunes + timedelta(days=6),
        estado='ABIERTA', tipo_pago='EFECTIVO',
        salario_base=Decimal('5000'),
        pago_horas_extras=0, pago_viaticos=0, pago_festivos=0,
        depositos_otros=0, depositos_prestamos=0,
        descuento_infonavit=0, ajuste_inbursa=0, descuentos_otros=0,
        descuento_prestamos=Decimal('0'),
        descuento_incidencias=0, recuperacion_manual=0,
        total_percepciones=Decimal('5000'),
        total_deducciones=Decimal('0'),
        total_a_pagar=Decimal('5000'),
    )
    db.session.add(p); db.session.commit()
    return p


# ═══════════════════════════════════════════════════════════════════════════════
# 1. AUTH
# ═══════════════════════════════════════════════════════════════════════════════

class TestAuth:

    def test_sin_token_401(self, client):
        r = client.get('/api/prestamos')
        assert r.status_code == 401

    def test_coord_403(self, client, pr_coord):
        r = client.get('/api/prestamos', headers=_hdr(pr_coord))
        assert r.status_code == 403

    def test_admin_200(self, client, pr_admin):
        r = client.get('/api/prestamos', headers=_hdr(pr_admin))
        assert r.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════════
# 2. LISTAR
# ═══════════════════════════════════════════════════════════════════════════════

class TestListar:

    def test_listar_vacio(self, client, pr_admin):
        r = client.get('/api/prestamos', headers=_hdr(pr_admin))
        body = r.get_json()
        assert body['items'] == []
        assert body['total'] == 0

    def test_listar_con_prestamo(
        self, client, pr_admin, prestamo_activo, pr_trab,
    ):
        r = client.get('/api/prestamos', headers=_hdr(pr_admin))
        body = r.get_json()
        assert body['total'] == 1
        item = body['items'][0]
        assert item['monto_total'] == 5000.0
        assert item['monto_restante'] == 5000.0
        assert item['estado'] == 'ACTIVO'
        assert item['trabajador']['no_empleado'] == 'PR-001'

    def test_filtro_q_por_no_empleado(
        self, client, pr_admin, prestamo_activo, pr_trab_b, db,
    ):
        # Segundo préstamo de otro trabajador
        p2 = Prestamo(
            trabajador_id=pr_trab_b.id, monto_total=Decimal('1000'),
            monto_restante=Decimal('1000'), plazo_semanas=4,
            descuento_semanal=Decimal('250'), estado='ACTIVO', activo=True,
        )
        db.session.add(p2); db.session.commit()

        r = client.get('/api/prestamos?q=PR-001', headers=_hdr(pr_admin))
        items = r.get_json()['items']
        assert {i['trabajador']['no_empleado'] for i in items} == {'PR-001'}

    def test_filtro_estado_liquidado(
        self, client, pr_admin, prestamo_activo, prestamo_liquidado,
    ):
        r = client.get('/api/prestamos?estado=LIQUIDADO', headers=_hdr(pr_admin))
        items = r.get_json()['items']
        assert len(items) == 1
        assert items[0]['estado'] == 'LIQUIDADO'

    def test_paginacion(self, client, pr_admin, prestamo_activo, pr_trab_b, db):
        p2 = Prestamo(
            trabajador_id=pr_trab_b.id, monto_total=Decimal('1000'),
            monto_restante=Decimal('1000'), plazo_semanas=4,
            descuento_semanal=Decimal('250'), estado='ACTIVO', activo=True,
        )
        db.session.add(p2); db.session.commit()
        r = client.get('/api/prestamos?per_page=1', headers=_hdr(pr_admin))
        body = r.get_json()
        assert len(body['items']) == 1
        assert body['total'] == 2
        assert body['pages'] == 2

    def test_sort_monto_asc(self, client, pr_admin, prestamo_activo, pr_trab_b, db):
        # prestamo_activo=5000; p2=1000 → asc pone p2 primero
        p2 = Prestamo(
            trabajador_id=pr_trab_b.id, monto_total=Decimal('1000'),
            monto_restante=Decimal('1000'), plazo_semanas=4,
            descuento_semanal=Decimal('250'), estado='ACTIVO', activo=True,
        )
        db.session.add(p2); db.session.commit()
        r = client.get('/api/prestamos?sort=monto&dir=asc', headers=_hdr(pr_admin))
        montos = [i['monto_total'] for i in r.get_json()['items']]
        assert montos == [1000.0, 5000.0]

    def test_sort_trabajador_con_q(self, client, pr_admin, prestamo_activo, pr_trab_b, db):
        # sort=trabajador combinado con q: no debe duplicar el join ni tronar.
        p2 = Prestamo(
            trabajador_id=pr_trab_b.id, monto_total=Decimal('1000'),
            monto_restante=Decimal('1000'), plazo_semanas=4,
            descuento_semanal=Decimal('250'), estado='ACTIVO', activo=True,
        )
        db.session.add(p2); db.session.commit()
        r = client.get('/api/prestamos?q=PR-&sort=trabajador&dir=asc', headers=_hdr(pr_admin))
        assert r.status_code == 200
        nombres = [i['trabajador']['nombre'] for i in r.get_json()['items']]
        assert nombres == sorted(nombres, key=str.lower)

    def test_sort_invalido_cae_a_default(self, client, pr_admin, prestamo_activo):
        r = client.get('/api/prestamos?sort=hax&dir=desc', headers=_hdr(pr_admin))
        assert r.status_code == 200
        assert r.get_json()['total'] == 1


# ═══════════════════════════════════════════════════════════════════════════════
# 3. TRABAJADORES DISPONIBLES
# ═══════════════════════════════════════════════════════════════════════════════

class TestTrabajadoresDisponibles:

    def test_lista_solo_activos(self, client, pr_admin, pr_trab, pr_trab_b, db):
        # Crear un inactivo que NO debe aparecer
        inactivo = Trabajador(
            no_empleado='PR-INA', nombre='Z', nombre_apellidos='Z',
            activo=False, tipo_nomina='Semanal',
            salario_real_pactado_x_sem=Decimal('1000'),
        )
        db.session.add(inactivo); db.session.commit()
        r = client.get('/api/prestamos/trabajadores-disponibles', headers=_hdr(pr_admin))
        codigos = {t['no_empleado'] for t in r.get_json()}
        assert {'PR-001', 'PR-002'} <= codigos
        assert 'PR-INA' not in codigos

    def test_coord_403(self, client, pr_coord):
        r = client.get('/api/prestamos/trabajadores-disponibles', headers=_hdr(pr_coord))
        assert r.status_code == 403


# ═══════════════════════════════════════════════════════════════════════════════
# 4. DETALLE
# ═══════════════════════════════════════════════════════════════════════════════

class TestDetalle:

    def test_detalle_incluye_abonos(
        self, client, pr_admin, prestamo_activo, db,
    ):
        # Agregar dos abonos para asegurar `total_abonado` correcto y orden DESC
        db.session.add_all([
            AbonoPrestamo(prestamo_id=prestamo_activo.id, monto=Decimal('500'),
                           fecha_abono=date.today() - timedelta(days=7),
                           tipo='NOMINA', notas='wk1'),
            AbonoPrestamo(prestamo_id=prestamo_activo.id, monto=Decimal('200'),
                           fecha_abono=date.today(),
                           tipo='MANUAL', notas='abono extra'),
        ])
        db.session.commit()
        r = client.get(f'/api/prestamos/{prestamo_activo.id}', headers=_hdr(pr_admin))
        body = r.get_json()
        assert body['total_abonado'] == 700.0
        assert len(body['abonos']) == 2
        # Orden descendente por fecha → el manual (hoy) viene primero
        assert body['abonos'][0]['tipo'] == 'MANUAL'

    def test_inexistente_404(self, client, pr_admin):
        r = client.get('/api/prestamos/99999', headers=_hdr(pr_admin))
        assert r.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# 5. CREAR
# ═══════════════════════════════════════════════════════════════════════════════

class TestCrear:

    def _payload(self, t):
        return {
            'trabajador_id': t.id,
            'monto_total': 6000,
            'plazo_semanas': 12,
            'descuento_semanal': 500,
            'motivo': 'Emergencia familiar',
            'frecuencia': 'semanal',
            'fecha_inicio': date.today().isoformat(),
        }

    def test_admin_crea(self, client, pr_admin, pr_trab, db):
        r = client.post('/api/prestamos', headers=_hdr(pr_admin),
                        json=self._payload(pr_trab))
        assert r.status_code == 201, r.get_json()
        pid = r.get_json()['id']
        p = Prestamo.query.get(pid)
        assert p.monto_total == Decimal('6000')
        assert p.monto_restante == Decimal('6000')
        assert p.estado == 'ACTIVO'

    def test_coord_403(self, client, pr_coord, pr_trab):
        r = client.post('/api/prestamos', headers=_hdr(pr_coord),
                        json=self._payload(pr_trab))
        assert r.status_code == 403

    def test_falta_campo_400(self, client, pr_admin, pr_trab):
        r = client.post('/api/prestamos', headers=_hdr(pr_admin), json={
            'trabajador_id': pr_trab.id,
            'monto_total': 1000,
            # falta plazo_semanas y descuento_semanal
        })
        assert r.status_code == 400

    def test_monto_cero_400(self, client, pr_admin, pr_trab):
        payload = self._payload(pr_trab); payload['monto_total'] = 0
        r = client.post('/api/prestamos', headers=_hdr(pr_admin), json=payload)
        assert r.status_code == 400

    def test_plazo_negativo_400(self, client, pr_admin, pr_trab):
        payload = self._payload(pr_trab); payload['plazo_semanas'] = -1
        r = client.post('/api/prestamos', headers=_hdr(pr_admin), json=payload)
        assert r.status_code == 400

    def test_valores_no_numericos_400(self, client, pr_admin, pr_trab):
        payload = self._payload(pr_trab); payload['monto_total'] = 'mucho'
        r = client.post('/api/prestamos', headers=_hdr(pr_admin), json=payload)
        assert r.status_code == 400

    def test_fecha_invalida_400(self, client, pr_admin, pr_trab):
        payload = self._payload(pr_trab); payload['fecha_inicio'] = 'no-fecha'
        r = client.post('/api/prestamos', headers=_hdr(pr_admin), json=payload)
        assert r.status_code == 400

    def test_crear_recalcula_prenomina_abierta(
        self, client, pr_admin, pr_trab, prenomina_abierta, db,
    ):
        # Antes de crear: descuento_prestamos == 0
        assert prenomina_abierta.descuento_prestamos == 0
        r = client.post('/api/prestamos', headers=_hdr(pr_admin),
                        json=self._payload(pr_trab))
        assert r.status_code == 201
        db.session.refresh(prenomina_abierta)
        # Después de crear préstamo activo con descuento_semanal=500
        # la prenómina abierta del mismo trabajador refleja esa deducción
        assert float(prenomina_abierta.descuento_prestamos) == 500.0


# ═══════════════════════════════════════════════════════════════════════════════
# 6. EDITAR
# ═══════════════════════════════════════════════════════════════════════════════

class TestEditar:

    def test_admin_edita(self, client, pr_admin, prestamo_activo, db):
        r = client.put(f'/api/prestamos/{prestamo_activo.id}', headers=_hdr(pr_admin), json={
            'monto_total': 7000,
            'descuento_semanal': 700,
            'plazo_semanas': 10,
            'motivo': 'Editado',
        })
        assert r.status_code == 200, r.get_json()
        db.session.refresh(prestamo_activo)
        assert prestamo_activo.monto_total == Decimal('7000')
        assert prestamo_activo.descuento_semanal == Decimal('700')
        # `monto_restante` se recalcula como nuevo_monto - total_abonado
        # (sin abonos previos → 7000)
        assert prestamo_activo.monto_restante == Decimal('7000')

    def test_editar_liquidado_400(self, client, pr_admin, prestamo_liquidado):
        r = client.put(f'/api/prestamos/{prestamo_liquidado.id}', headers=_hdr(pr_admin), json={
            'monto_total': 2000,
        })
        assert r.status_code == 400

    def test_nuevo_monto_menor_que_abonado_400(
        self, client, pr_admin, prestamo_activo, db,
    ):
        # Hay $3000 ya abonados al préstamo
        db.session.add(AbonoPrestamo(
            prestamo_id=prestamo_activo.id, monto=Decimal('3000'),
            fecha_abono=date.today(), tipo='MANUAL',
        ))
        db.session.commit()
        # Bajar el monto total a $2000 → debería rechazarse (2000 < 3000)
        r = client.put(f'/api/prestamos/{prestamo_activo.id}', headers=_hdr(pr_admin), json={
            'monto_total': 2000,
        })
        assert r.status_code == 400

    def test_valores_negativos_400(self, client, pr_admin, prestamo_activo):
        r = client.put(f'/api/prestamos/{prestamo_activo.id}', headers=_hdr(pr_admin), json={
            'descuento_semanal': -100,
        })
        assert r.status_code == 400

    def test_inexistente_404(self, client, pr_admin):
        r = client.put('/api/prestamos/99999', headers=_hdr(pr_admin), json={
            'monto_total': 1000,
        })
        assert r.status_code == 404

    def test_editar_recalcula_prenomina_abierta(
        self, client, pr_admin, prestamo_activo, prenomina_abierta, db,
    ):
        # Subir descuento_semanal a 800 → prenómina abierta debe recalcularse
        r = client.put(f'/api/prestamos/{prestamo_activo.id}', headers=_hdr(pr_admin), json={
            'descuento_semanal': 800,
        })
        assert r.status_code == 200
        db.session.refresh(prenomina_abierta)
        assert float(prenomina_abierta.descuento_prestamos) == 800.0


# ═══════════════════════════════════════════════════════════════════════════════
# 7. ABONAR
# ═══════════════════════════════════════════════════════════════════════════════

class TestAbonar:

    def test_abono_parcial(self, client, pr_admin, prestamo_activo, db):
        r = client.post(
            f'/api/prestamos/{prestamo_activo.id}/abonar',
            headers=_hdr(pr_admin),
            json={'monto': 1500, 'notas': 'Adelanto'},
        )
        assert r.status_code == 200
        body = r.get_json()
        assert body['monto_restante'] == 3500.0
        assert body['estado'] == 'ACTIVO'
        # Persistencia: queda un AbonoPrestamo tipo MANUAL
        abonos = AbonoPrestamo.query.filter_by(prestamo_id=prestamo_activo.id).all()
        assert len(abonos) == 1
        assert abonos[0].tipo == 'MANUAL'
        assert abonos[0].notas == 'Adelanto'

    def test_abono_que_liquida(self, client, pr_admin, prestamo_activo, db):
        # Saldo es 5000 → un abono de 5000 (o más) debe liquidar
        r = client.post(
            f'/api/prestamos/{prestamo_activo.id}/abonar',
            headers=_hdr(pr_admin),
            json={'monto': 5000},
        )
        assert r.status_code == 200
        body = r.get_json()
        assert body['monto_restante'] == 0.0
        assert body['estado'] == 'LIQUIDADO'
        db.session.refresh(prestamo_activo)
        assert prestamo_activo.activo is False

    def test_abono_mayor_al_saldo_se_capa_a_cero(
        self, client, pr_admin, prestamo_activo,
    ):
        r = client.post(
            f'/api/prestamos/{prestamo_activo.id}/abonar',
            headers=_hdr(pr_admin),
            json={'monto': 99999},
        )
        assert r.status_code == 200
        # max(0, saldo - monto) — nunca negativo
        assert r.get_json()['monto_restante'] == 0.0

    def test_abonar_liquidado_400(self, client, pr_admin, prestamo_liquidado):
        r = client.post(
            f'/api/prestamos/{prestamo_liquidado.id}/abonar',
            headers=_hdr(pr_admin),
            json={'monto': 100},
        )
        assert r.status_code == 400

    def test_monto_cero_400(self, client, pr_admin, prestamo_activo):
        r = client.post(
            f'/api/prestamos/{prestamo_activo.id}/abonar',
            headers=_hdr(pr_admin),
            json={'monto': 0},
        )
        assert r.status_code == 400

    def test_monto_negativo_400(self, client, pr_admin, prestamo_activo):
        r = client.post(
            f'/api/prestamos/{prestamo_activo.id}/abonar',
            headers=_hdr(pr_admin),
            json={'monto': -50},
        )
        assert r.status_code == 400

    def test_monto_no_numerico_400(self, client, pr_admin, prestamo_activo):
        r = client.post(
            f'/api/prestamos/{prestamo_activo.id}/abonar',
            headers=_hdr(pr_admin),
            json={'monto': 'mucho'},
        )
        assert r.status_code == 400

    def test_falta_monto_400(self, client, pr_admin, prestamo_activo):
        r = client.post(
            f'/api/prestamos/{prestamo_activo.id}/abonar',
            headers=_hdr(pr_admin),
            json={'notas': 'X'},
        )
        assert r.status_code == 400

    def test_coord_403(self, client, pr_coord, prestamo_activo):
        r = client.post(
            f'/api/prestamos/{prestamo_activo.id}/abonar',
            headers=_hdr(pr_coord),
            json={'monto': 100},
        )
        assert r.status_code == 403

    def test_inexistente_404(self, client, pr_admin):
        r = client.post('/api/prestamos/99999/abonar', headers=_hdr(pr_admin),
                        json={'monto': 100})
        assert r.status_code == 404

    def test_abono_recalcula_prenomina_si_liquida(
        self, client, pr_admin, prestamo_activo, prenomina_abierta, db,
    ):
        # Antes: prenómina abierta no tiene descuento de préstamo (prestamo
        # existía pero no se había recalculado tras crearla). Forzamos el
        # recálculo abonando: el saldo queda 0 → préstamo LIQUIDADO →
        # `descuento_prestamos` regresa a 0 al recalcular.
        prenomina_abierta.descuento_prestamos = Decimal('500')
        db.session.commit()
        r = client.post(
            f'/api/prestamos/{prestamo_activo.id}/abonar',
            headers=_hdr(pr_admin),
            json={'monto': 5000},
        )
        assert r.status_code == 200
        db.session.refresh(prenomina_abierta)
        # Al liquidar, ya no se descuenta el préstamo de la prenómina
        assert float(prenomina_abierta.descuento_prestamos) == 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# 8. LIQUIDAR
# ═══════════════════════════════════════════════════════════════════════════════

class TestLiquidar:

    def test_liquidar_con_saldo_crea_abono(
        self, client, pr_admin, prestamo_activo, db,
    ):
        r = client.post(
            f'/api/prestamos/{prestamo_activo.id}/liquidar',
            headers=_hdr(pr_admin),
        )
        assert r.status_code == 200
        body = r.get_json()
        assert body['estado'] == 'LIQUIDADO'
        assert body['monto_restante'] == 0.0
        db.session.refresh(prestamo_activo)
        assert prestamo_activo.activo is False
        # Se generó el abono que cubre todo el saldo
        abonos = AbonoPrestamo.query.filter_by(prestamo_id=prestamo_activo.id).all()
        assert len(abonos) == 1
        assert abonos[0].monto == Decimal('5000')
        assert abonos[0].tipo == 'MANUAL'

    def test_liquidar_sin_saldo_no_crea_abono(
        self, client, pr_admin, prestamo_activo, db,
    ):
        # Bajar saldo a 0 a mano (sin pasar al estado liquidado)
        prestamo_activo.monto_restante = Decimal('0')
        db.session.commit()
        r = client.post(
            f'/api/prestamos/{prestamo_activo.id}/liquidar',
            headers=_hdr(pr_admin),
        )
        assert r.status_code == 200
        # No se crea abono porque el saldo ya era 0
        abonos = AbonoPrestamo.query.filter_by(prestamo_id=prestamo_activo.id).all()
        assert abonos == []
        db.session.refresh(prestamo_activo)
        assert prestamo_activo.estado == 'LIQUIDADO'

    def test_coord_403(self, client, pr_coord, prestamo_activo):
        r = client.post(
            f'/api/prestamos/{prestamo_activo.id}/liquidar',
            headers=_hdr(pr_coord),
        )
        assert r.status_code == 403

    def test_inexistente_404(self, client, pr_admin):
        r = client.post('/api/prestamos/99999/liquidar', headers=_hdr(pr_admin))
        assert r.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# 9. EXCEL
# ═══════════════════════════════════════════════════════════════════════════════

class TestExcel:

    def test_descarga(self, client, pr_admin, pr_trab, prestamo_activo):
        r = client.get(
            f'/api/prestamos/trabajadores/{pr_trab.id}/excel',
            headers=_hdr(pr_admin),
        )
        assert r.status_code == 200
        assert r.mimetype == (
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )

    def test_sin_prestamos_404(self, client, pr_admin, pr_trab_b):
        r = client.get(
            f'/api/prestamos/trabajadores/{pr_trab_b.id}/excel',
            headers=_hdr(pr_admin),
        )
        assert r.status_code == 404

    def test_trabajador_inexistente_404(self, client, pr_admin):
        r = client.get(
            '/api/prestamos/trabajadores/99999/excel',
            headers=_hdr(pr_admin),
        )
        assert r.status_code == 404

    def test_coord_403(self, client, pr_coord, pr_trab):
        r = client.get(
            f'/api/prestamos/trabajadores/{pr_trab.id}/excel',
            headers=_hdr(pr_coord),
        )
        assert r.status_code == 403
