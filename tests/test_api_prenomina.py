"""Tests del API JWT `/api/prenomina/*` — semanas, ajustes manuales y cierre.

Cobertura:
  - GET    /semanas                              listar semanas con prenómina
  - GET    /semanas/<fecha>/preview              calcular preview (no persiste)
  - POST   /semanas/<fecha>/guardar              persistir prenóminas (ABIERTA)
  - GET    /semanas/<fecha>/editar               detalle editor
  - POST   /semanas/<fecha>/cerrar               ABIERTA → APROBADO + abonos
  - POST   /descuentos, DELETE /descuentos/<id>
  - POST   /depositos,  DELETE /depositos/<id>
  - PATCH  /viaticos, /festivos
  - 401 sin token, 403 sin rol admin, 400/404 errores conocidos

Auth: JWT real vía `_encode_access_token` (mismo patrón que test_etiquetas.py).
NO se prueban los endpoints de PDF / correo / Excel porque dependen de
`render_template` y el repo es API-only sin carpeta templates/.
"""
from datetime import date, time, timedelta
from decimal import Decimal

import pytest
from werkzeug.security import generate_password_hash

from app.extensions import db as flask_db
from app.models import (
    User, Trabajador, Proyecto, ReporteSemanal, RegistroDiarioHoras,
    Prenomina, DescuentoPrenomina, DepositoExtra,
)
from app.routes.api_auth import _encode_access_token


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _hdr(user):
    return {'Authorization': f'Bearer {_encode_access_token(user)}'}


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def pn_admin(db):
    u = User(username='pn_admin', password_hash=generate_password_hash('Pass123!'), role='admin')
    db.session.add(u); db.session.commit()
    return u


@pytest.fixture
def pn_coord(db):
    u = User(username='pn_coord', password_hash=generate_password_hash('Pass123!'),
              role='coordinador')
    db.session.add(u); db.session.commit()
    return u


@pytest.fixture
def pn_trabajador(db):
    t = Trabajador(
        no_empleado='PN-001', nombre_apellidos='López', nombre='Carlos',
        activo=True, tipo_nomina='Semanal',
        salario_real_pactado_x_sem=Decimal('5000'),
        hr_extra=Decimal('100'),
        infonavit=Decimal('200'),
        ajuste_inbursa=Decimal('0'),
        viaticos=Decimal('50'),
    )
    db.session.add(t); db.session.commit()
    return t


@pytest.fixture
def pn_proyecto(db, pn_admin, pn_trabajador):
    p = Proyecto(
        numero_proyecto='PN-PROY-001', nombre='Proyecto Prenómina',
        activo=True, coordinador_id=pn_admin.id,
    )
    p.participantes.append(pn_trabajador)
    db.session.add(p); db.session.commit()
    return p


@pytest.fixture
def fecha_lunes():
    """Lunes 2026-01-05 — semana cerrada y predecible."""
    return date(2026, 1, 5)


@pytest.fixture
def reporte_terminado(db, pn_proyecto, pn_admin, pn_trabajador, fecha_lunes):
    """Crea un ReporteSemanal TERMINADO con 5 días × 8h productivas (=40h)."""
    r = ReporteSemanal(
        fecha_inicio_semana=fecha_lunes,
        fecha_fin_semana=fecha_lunes + timedelta(days=6),
        proyecto_id=pn_proyecto.id,
        estado='TERMINADO',
        creado_por_id=pn_admin.id,
    )
    db.session.add(r); db.session.flush()
    for i in range(5):
        db.session.add(RegistroDiarioHoras(
            reporte_id=r.id,
            trabajador_id=pn_trabajador.id,
            fecha=fecha_lunes + timedelta(days=i),
            hora_entrada=time(8, 0),
            hora_salida=time(17, 0),
            tomo_comida=True,
            horas_productivas=Decimal('8'),
            aplica_viaticos=False,
            aplica_dia_festivo=False,
            tipo_nomina='Semanal',
        ))
    db.session.commit()
    return r


@pytest.fixture
def prenomina_guardada(db, reporte_terminado, pn_trabajador, fecha_lunes):
    """Una Prenomina en estado ABIERTA lista para recibir ajustes."""
    p = Prenomina(
        trabajador_id=pn_trabajador.id,
        fecha_inicio=fecha_lunes,
        fecha_fin=fecha_lunes + timedelta(days=6),
        estado='ABIERTA',
        tipo_pago='EFECTIVO',
        salario_base=Decimal('5000'),
        pago_horas_extras=Decimal('0'),
        pago_viaticos=Decimal('0'),
        pago_festivos=Decimal('0'),
        depositos_otros=Decimal('0'),
        depositos_prestamos=Decimal('0'),
        descuento_infonavit=Decimal('200'),
        ajuste_inbursa=Decimal('0'),
        descuentos_otros=Decimal('0'),
        descuento_prestamos=Decimal('0'),
        descuento_incidencias=Decimal('0'),
        recuperacion_manual=Decimal('0'),
        total_percepciones=Decimal('5000'),
        total_deducciones=Decimal('200'),
        total_a_pagar=Decimal('4800'),
    )
    db.session.add(p); db.session.commit()
    # Marcar el reporte como PRENOMINA_CERRADA — necesario para algunos endpoints
    reporte_terminado.estado = 'PRENOMINA_CERRADA'
    db.session.commit()
    return p


# ═══════════════════════════════════════════════════════════════════════════════
# 1. AUTENTICACIÓN / AUTORIZACIÓN
# ═══════════════════════════════════════════════════════════════════════════════

class TestAuth:

    def test_sin_token_retorna_401(self, client):
        r = client.get('/api/prenomina/semanas')
        assert r.status_code == 401

    def test_coordinador_sin_acceso_admin_retorna_403(self, client, pn_coord):
        r = client.get('/api/prenomina/semanas', headers=_hdr(pn_coord))
        assert r.status_code == 403

    def test_admin_accede(self, client, pn_admin):
        r = client.get('/api/prenomina/semanas', headers=_hdr(pn_admin))
        assert r.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════════
# 2. LISTAR SEMANAS
# ═══════════════════════════════════════════════════════════════════════════════

class TestListarSemanas:

    def test_sin_reportes_devuelve_vacio(self, client, pn_admin):
        r = client.get('/api/prenomina/semanas', headers=_hdr(pn_admin))
        assert r.status_code == 200
        assert r.get_json()['items'] == []

    def test_con_reporte_lo_lista(self, client, pn_admin, reporte_terminado, fecha_lunes):
        r = client.get('/api/prenomina/semanas', headers=_hdr(pn_admin))
        assert r.status_code == 200
        items = r.get_json()['items']
        assert len(items) == 1
        assert items[0]['fecha_inicio'] == fecha_lunes.isoformat()
        assert items[0]['estado_reportes'] == 'TERMINADO'
        assert items[0]['estado_prenomina'] == 'PENDIENTE'
        assert any(p['numero_proyecto'] == 'PN-PROY-001' for p in items[0]['proyectos'])


# ═══════════════════════════════════════════════════════════════════════════════
# 3. PREVIEW
# ═══════════════════════════════════════════════════════════════════════════════

class TestPreview:

    def test_fecha_invalida_400(self, client, pn_admin):
        r = client.get('/api/prenomina/semanas/no-fecha/preview', headers=_hdr(pn_admin))
        assert r.status_code == 400

    def test_semana_sin_reportes_404(self, client, pn_admin, fecha_lunes):
        r = client.get(
            f'/api/prenomina/semanas/{fecha_lunes.isoformat()}/preview',
            headers=_hdr(pn_admin),
        )
        assert r.status_code == 404

    def test_preview_calcula_salario_y_no_persiste(
        self, client, pn_admin, reporte_terminado, fecha_lunes, pn_trabajador, db,
    ):
        r = client.get(
            f'/api/prenomina/semanas/{fecha_lunes.isoformat()}/preview',
            headers=_hdr(pn_admin),
        )
        assert r.status_code == 200, r.get_json()
        body = r.get_json()
        assert body['ya_guardada'] is False
        assert body['estado_actual'] == 'PENDIENTE'
        prenominas = body['prenominas']
        assert len(prenominas) == 1
        p = prenominas[0]
        # tipo_nomina=Semanal, 40h trabajadas (≤50) → solo salario base
        assert p['salario_base'] == 5000.0
        assert p['pago_horas_extras'] == 0.0
        assert p['descuento_infonavit'] == 200.0
        # Total = 5000 - 200 = 4800
        assert p['total_a_pagar'] == 4800.0
        # No debe haber Prenominas persistidas
        assert Prenomina.query.filter_by(fecha_inicio=fecha_lunes).count() == 0

    def test_preview_horas_extras_arriba_de_50(
        self, client, pn_admin, pn_trabajador, pn_proyecto, fecha_lunes, db,
    ):
        # 6 días × 10h = 60h → 10h extras × $100 = $1000
        r = ReporteSemanal(
            fecha_inicio_semana=fecha_lunes,
            fecha_fin_semana=fecha_lunes + timedelta(days=6),
            proyecto_id=pn_proyecto.id, estado='TERMINADO',
            creado_por_id=pn_admin.id,
        )
        db.session.add(r); db.session.flush()
        for i in range(6):
            db.session.add(RegistroDiarioHoras(
                reporte_id=r.id, trabajador_id=pn_trabajador.id,
                fecha=fecha_lunes + timedelta(days=i),
                horas_productivas=Decimal('10'),
            ))
        db.session.commit()

        resp = client.get(
            f'/api/prenomina/semanas/{fecha_lunes.isoformat()}/preview',
            headers=_hdr(pn_admin),
        )
        assert resp.status_code == 200
        p = resp.get_json()['prenominas'][0]
        assert p['pago_horas_extras'] == 1000.0   # 10 * 100
        assert p['salario_base'] == 5000.0


# ═══════════════════════════════════════════════════════════════════════════════
# 4. GUARDAR (PENDIENTE → ABIERTA)
# ═══════════════════════════════════════════════════════════════════════════════

class TestGuardar:

    def test_guardar_crea_prenominas(
        self, client, pn_admin, reporte_terminado, fecha_lunes, db,
    ):
        r = client.post(
            f'/api/prenomina/semanas/{fecha_lunes.isoformat()}/guardar',
            headers=_hdr(pn_admin),
        )
        assert r.status_code == 200
        body = r.get_json()
        assert body['success'] is True
        assert body['creadas'] == 1
        # Estado del reporte rebautizado a PRENOMINA_CERRADA
        flask_db.session.refresh(reporte_terminado)
        assert reporte_terminado.estado == 'PRENOMINA_CERRADA'
        # Prenómina en ABIERTA
        p = Prenomina.query.filter_by(fecha_inicio=fecha_lunes).one()
        assert p.estado == 'ABIERTA'

    def test_guardar_duplicado_devuelve_409(
        self, client, pn_admin, reporte_terminado, fecha_lunes,
    ):
        url = f'/api/prenomina/semanas/{fecha_lunes.isoformat()}/guardar'
        client.post(url, headers=_hdr(pn_admin))
        r = client.post(url, headers=_hdr(pn_admin))
        assert r.status_code == 409

    def test_guardar_sin_reportes_400(self, client, pn_admin, fecha_lunes):
        r = client.post(
            f'/api/prenomina/semanas/{fecha_lunes.isoformat()}/guardar',
            headers=_hdr(pn_admin),
        )
        assert r.status_code == 400


# ═══════════════════════════════════════════════════════════════════════════════
# 5. EDITOR
# ═══════════════════════════════════════════════════════════════════════════════

class TestEditor:

    def test_sin_prenominas_404(self, client, pn_admin, fecha_lunes):
        r = client.get(
            f'/api/prenomina/semanas/{fecha_lunes.isoformat()}/editar',
            headers=_hdr(pn_admin),
        )
        assert r.status_code == 404

    def test_editor_devuelve_detalle(
        self, client, pn_admin, prenomina_guardada, fecha_lunes,
    ):
        r = client.get(
            f'/api/prenomina/semanas/{fecha_lunes.isoformat()}/editar',
            headers=_hdr(pn_admin),
        )
        assert r.status_code == 200
        body = r.get_json()
        assert body['estado_actual'] == 'ABIERTA'
        assert body['editable'] is True
        assert len(body['prenominas']) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# 6. DESCUENTOS (POST / DELETE)
# ═══════════════════════════════════════════════════════════════════════════════

class TestDescuentos:

    def test_agregar_descuento_recalcula_totales(
        self, client, pn_admin, prenomina_guardada,
    ):
        r = client.post('/api/prenomina/descuentos', headers=_hdr(pn_admin), json={
            'prenomina_id': prenomina_guardada.id,
            'tipo': 'MANUAL',
            'concepto': 'Anticipo de cobranza',
            'monto': 250,
        })
        assert r.status_code == 201, r.get_json()
        body = r.get_json()
        # 200 (infonavit) + 250 (nuevo) = 450
        assert body['total_deducciones'] == 450.0
        assert body['total_a_pagar'] == 4550.0

    def test_descuento_tipo_invalido_400(self, client, pn_admin, prenomina_guardada):
        r = client.post('/api/prenomina/descuentos', headers=_hdr(pn_admin), json={
            'prenomina_id': prenomina_guardada.id,
            'tipo': 'INVENTADO', 'concepto': 'X', 'monto': 10,
        })
        assert r.status_code == 400

    def test_descuento_monto_no_positivo_400(self, client, pn_admin, prenomina_guardada):
        r = client.post('/api/prenomina/descuentos', headers=_hdr(pn_admin), json={
            'prenomina_id': prenomina_guardada.id,
            'tipo': 'MANUAL', 'concepto': 'X', 'monto': 0,
        })
        assert r.status_code == 400

    def test_descuento_fecha_futura_400(self, client, pn_admin, prenomina_guardada):
        manana = (date.today() + timedelta(days=1)).isoformat()
        r = client.post('/api/prenomina/descuentos', headers=_hdr(pn_admin), json={
            'prenomina_id': prenomina_guardada.id,
            'tipo': 'INCIDENCIA', 'concepto': 'Retardo', 'monto': 50,
            'fecha_incidencia': manana,
        })
        assert r.status_code == 400

    def test_descuento_prenomina_no_abierta_400(
        self, client, pn_admin, prenomina_guardada, db,
    ):
        prenomina_guardada.estado = 'APROBADO'
        db.session.commit()
        r = client.post('/api/prenomina/descuentos', headers=_hdr(pn_admin), json={
            'prenomina_id': prenomina_guardada.id,
            'tipo': 'MANUAL', 'concepto': 'X', 'monto': 10,
        })
        assert r.status_code == 400

    def test_eliminar_descuento(self, client, pn_admin, prenomina_guardada, db):
        d = DescuentoPrenomina(
            prenomina_id=prenomina_guardada.id,
            trabajador_id=prenomina_guardada.trabajador_id,
            tipo='MANUAL', concepto='Borrar', monto=Decimal('100'),
        )
        db.session.add(d); db.session.commit()
        r = client.delete(
            f'/api/prenomina/descuentos/{d.id}',
            headers=_hdr(pn_admin),
        )
        assert r.status_code == 200
        assert DescuentoPrenomina.query.get(d.id) is None


# ═══════════════════════════════════════════════════════════════════════════════
# 7. DEPÓSITOS (POST / DELETE)
# ═══════════════════════════════════════════════════════════════════════════════

class TestDepositos:

    def test_agregar_deposito_recalcula(self, client, pn_admin, prenomina_guardada):
        r = client.post('/api/prenomina/depositos', headers=_hdr(pn_admin), json={
            'prenomina_id': prenomina_guardada.id,
            'concepto': 'Reembolso gasolina', 'monto': 300,
        })
        assert r.status_code == 201, r.get_json()
        body = r.get_json()
        # 5000 + 300 = 5300
        assert body['total_percepciones'] == 5300.0
        assert body['total_a_pagar'] == 5100.0  # 5300 - 200 infonavit

    def test_deposito_concepto_vacio_400(self, client, pn_admin, prenomina_guardada):
        r = client.post('/api/prenomina/depositos', headers=_hdr(pn_admin), json={
            'prenomina_id': prenomina_guardada.id,
            'concepto': '   ', 'monto': 100,
        })
        assert r.status_code == 400

    def test_eliminar_deposito(self, client, pn_admin, prenomina_guardada, db):
        d = DepositoExtra(
            prenomina_id=prenomina_guardada.id,
            trabajador_id=prenomina_guardada.trabajador_id,
            monto=Decimal('150'), concepto='Borrar',
        )
        db.session.add(d); db.session.commit()
        r = client.delete(
            f'/api/prenomina/depositos/{d.id}',
            headers=_hdr(pn_admin),
        )
        assert r.status_code == 200
        assert DepositoExtra.query.get(d.id) is None


# ═══════════════════════════════════════════════════════════════════════════════
# 8. PATCH VIÁTICOS / FESTIVOS
# ═══════════════════════════════════════════════════════════════════════════════

class TestPatchMontos:

    def test_patch_viaticos_actualiza_y_recalcula(
        self, client, pn_admin, prenomina_guardada,
    ):
        r = client.patch('/api/prenomina/viaticos', headers=_hdr(pn_admin), json={
            'prenomina_id': prenomina_guardada.id,
            'monto_viaticos': 500,
        })
        assert r.status_code == 200, r.get_json()
        body = r.get_json()
        assert body['pago_viaticos'] == 500.0
        # 5000 base + 500 viáticos = 5500 percepciones
        assert body['total_percepciones'] == 5500.0
        assert body['total_a_pagar'] == 5300.0

    def test_patch_festivos_actualiza(self, client, pn_admin, prenomina_guardada):
        r = client.patch('/api/prenomina/festivos', headers=_hdr(pn_admin), json={
            'prenomina_id': prenomina_guardada.id,
            'monto_festivos': 250,
        })
        assert r.status_code == 200
        body = r.get_json()
        assert body['pago_festivos'] == 250.0

    def test_patch_negativo_400(self, client, pn_admin, prenomina_guardada):
        r = client.patch('/api/prenomina/viaticos', headers=_hdr(pn_admin), json={
            'prenomina_id': prenomina_guardada.id,
            'monto_viaticos': -10,
        })
        assert r.status_code == 400

    def test_patch_prenomina_aprobada_400(
        self, client, pn_admin, prenomina_guardada, db,
    ):
        prenomina_guardada.estado = 'APROBADO'
        db.session.commit()
        r = client.patch('/api/prenomina/viaticos', headers=_hdr(pn_admin), json={
            'prenomina_id': prenomina_guardada.id,
            'monto_viaticos': 100,
        })
        assert r.status_code == 400

    def test_patch_prenomina_inexistente_404(self, client, pn_admin):
        r = client.patch('/api/prenomina/viaticos', headers=_hdr(pn_admin), json={
            'prenomina_id': 999999, 'monto_viaticos': 100,
        })
        assert r.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# 9. CERRAR (ABIERTA → APROBADO)
# ═══════════════════════════════════════════════════════════════════════════════

class TestCerrar:

    def test_cerrar_aprueba_prenominas(
        self, client, pn_admin, prenomina_guardada, fecha_lunes, db,
    ):
        r = client.post(
            f'/api/prenomina/semanas/{fecha_lunes.isoformat()}/cerrar',
            headers=_hdr(pn_admin),
        )
        assert r.status_code == 200, r.get_json()
        assert r.get_json()['aprobadas'] == 1
        db.session.refresh(prenomina_guardada)
        assert prenomina_guardada.estado == 'APROBADO'

    def test_cerrar_sin_reporte_400(self, client, pn_admin, fecha_lunes):
        r = client.post(
            f'/api/prenomina/semanas/{fecha_lunes.isoformat()}/cerrar',
            headers=_hdr(pn_admin),
        )
        assert r.status_code == 400

    def test_cerrar_sin_prenominas_abiertas_400(
        self, client, pn_admin, reporte_terminado, fecha_lunes,
    ):
        # Hay reporte pero ninguna Prenomina ABIERTA
        r = client.post(
            f'/api/prenomina/semanas/{fecha_lunes.isoformat()}/cerrar',
            headers=_hdr(pn_admin),
        )
        assert r.status_code == 400


# ═══════════════════════════════════════════════════════════════════════════════
# 10. ENVÍO (PDF / EXCEL)
# ═══════════════════════════════════════════════════════════════════════════════

class TestImprimirYExcel:
    """Endpoints que rinden PDF/Excel. La regresión que disparó esta sección
    fue un TypeError por `os.path.join(current_app.static_folder, ...)` cuando
    la app está construida con `static_folder=None` (modo API-only). Estos
    tests bloquean que el bug vuelva."""

    def test_imprimir_consolidado_devuelve_pdf(
        self, client, pn_admin, prenomina_guardada, fecha_lunes,
    ):
        r = client.get(
            f'/api/prenomina/semanas/{fecha_lunes.isoformat()}/imprimir',
            headers=_hdr(pn_admin),
        )
        assert r.status_code == 200, r.data[:200]
        assert r.mimetype == 'application/pdf'
        assert r.data.startswith(b'%PDF-')

    def test_imprimir_fecha_invalida_400(self, client, pn_admin):
        r = client.get(
            '/api/prenomina/semanas/no-fecha/imprimir',
            headers=_hdr(pn_admin),
        )
        assert r.status_code == 400

    def test_imprimir_sin_reportes_404(self, client, pn_admin, fecha_lunes):
        r = client.get(
            f'/api/prenomina/semanas/{fecha_lunes.isoformat()}/imprimir',
            headers=_hdr(pn_admin),
        )
        assert r.status_code == 404

    def test_imprimir_individual_devuelve_pdf(
        self, client, pn_admin, prenomina_guardada, fecha_lunes, pn_trabajador,
    ):
        r = client.get(
            f'/api/prenomina/semanas/{fecha_lunes.isoformat()}'
            f'/trabajadores/{pn_trabajador.id}/imprimir',
            headers=_hdr(pn_admin),
        )
        assert r.status_code == 200, r.data[:200]
        assert r.mimetype == 'application/pdf'

    def test_imprimir_individual_trabajador_no_existe_404(
        self, client, pn_admin, prenomina_guardada, fecha_lunes,
    ):
        r = client.get(
            f'/api/prenomina/semanas/{fecha_lunes.isoformat()}'
            f'/trabajadores/99999/imprimir',
            headers=_hdr(pn_admin),
        )
        assert r.status_code == 404

    def test_excel_descarga(
        self, client, pn_admin, prenomina_guardada, fecha_lunes,
    ):
        r = client.get(
            f'/api/prenomina/semanas/{fecha_lunes.isoformat()}/excel',
            headers=_hdr(pn_admin),
        )
        assert r.status_code == 200, r.data[:200]
        assert r.mimetype == (
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )

    def test_excel_sin_datos_404(self, client, pn_admin, fecha_lunes):
        r = client.get(
            f'/api/prenomina/semanas/{fecha_lunes.isoformat()}/excel',
            headers=_hdr(pn_admin),
        )
        assert r.status_code == 404

    def test_envio_sin_token_401(self, client, fecha_lunes):
        r = client.get(f'/api/prenomina/semanas/{fecha_lunes.isoformat()}/imprimir')
        assert r.status_code == 401

    def test_envio_coordinador_403(self, client, pn_coord, fecha_lunes):
        r = client.get(
            f'/api/prenomina/semanas/{fecha_lunes.isoformat()}/imprimir',
            headers=_hdr(pn_coord),
        )
        assert r.status_code == 403
