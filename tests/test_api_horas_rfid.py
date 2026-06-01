"""Tests del kiosko de asistencias RFID (offline-first).

Cubre los cambios añadidos para soportar la app Electron+React+Python:
  - Idempotencia en POST /api/horas/reportes/<id>/registros (client_record_id).
  - LWW en PUT /api/horas/registros/<id> (modificado_en).
  - Endpoints nuevos: POST /api/horas/rfid/asociar y
    GET /api/horas/rfid/trabajadores-reporte/<id>.

Auth: JWT real (Bearer), igual que el resto de tests del SPA.
"""
import time
import uuid
from datetime import date, datetime, timedelta, timezone

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
def admin(db):
    u = User(username='rfid_admin', password_hash=generate_password_hash('Pass123!'), role='admin')
    db.session.add(u); db.session.commit()
    return u


@pytest.fixture
def coord(db):
    u = User(username='rfid_coord', password_hash=generate_password_hash('Pass123!'), role='coordinador')
    db.session.add(u); db.session.commit()
    return u


@pytest.fixture
def coord_otro(db):
    u = User(username='rfid_coord_b', password_hash=generate_password_hash('Pass123!'), role='coordinador')
    db.session.add(u); db.session.commit()
    return u


@pytest.fixture
def t_juan(db):
    t = Trabajador(no_empleado='RFID-001', nombre_apellidos='Pérez',
                    nombre='Juan', activo=True, tipo_nomina='Semanal',
                    salario_real_pactado_x_sem=5000, viaticos=50, pago_dia_festivo=200)
    db.session.add(t); db.session.commit()
    return t


@pytest.fixture
def t_maria(db):
    t = Trabajador(no_empleado='RFID-002', nombre_apellidos='Ruiz',
                    nombre='María', activo=True, tipo_nomina='Semanal',
                    salario_real_pactado_x_sem=5000)
    db.session.add(t); db.session.commit()
    return t


@pytest.fixture
def proyecto_coord(db, coord, t_juan):
    p = Proyecto(numero_proyecto='RFID-P1', nombre='Proyecto RFID',
                 activo=True, coordinador_id=coord.id)
    p.participantes.append(t_juan)
    db.session.add(p); db.session.commit()
    return p


@pytest.fixture
def reporte_borrador(db, proyecto_coord):
    hoy = date.today()
    inicio = hoy - timedelta(days=hoy.weekday())
    fin = inicio + timedelta(days=6)
    r = ReporteSemanal(
        proyecto_id=proyecto_coord.id,
        fecha_inicio_semana=inicio,
        fecha_fin_semana=fin,
        estado='BORRADOR',
    )
    db.session.add(r); db.session.commit()
    return r


# ═══════════════════════════════════════════════════════════════════════════════
# Idempotencia: POST con mismo client_record_id no duplica
# ═══════════════════════════════════════════════════════════════════════════════

class TestIdempotenciaCrearRegistro:

    def _payload(self, t_juan, reporte_borrador, **overrides):
        base = {
            'trabajador_id': t_juan.id,
            'fecha': reporte_borrador.fecha_inicio_semana.isoformat(),
            'hora_entrada': '07:13',  # NO redondeado a media hora
            'hora_salida': '17:47',
        }
        base.update(overrides)
        return base

    def test_post_con_client_record_id_crea_y_devuelve_uuid(self, client, coord, t_juan, reporte_borrador):
        crid = str(uuid.uuid4())
        r = client.post(
            f'/api/horas/reportes/{reporte_borrador.id}/registros',
            headers=_hdr(coord),
            json=self._payload(t_juan, reporte_borrador, client_record_id=crid),
        )
        assert r.status_code == 201, r.get_json()
        body = r.get_json()
        assert body['client_record_id'] == crid
        assert body['hora_entrada'] == '07:13'
        assert body['hora_salida'] == '17:47'

    def test_post_repetido_mismo_uuid_devuelve_existente_sin_duplicar(
        self, client, db, coord, t_juan, reporte_borrador,
    ):
        crid = str(uuid.uuid4())
        payload = self._payload(t_juan, reporte_borrador, client_record_id=crid)

        r1 = client.post(
            f'/api/horas/reportes/{reporte_borrador.id}/registros',
            headers=_hdr(coord), json=payload,
        )
        assert r1.status_code == 201
        id1 = r1.get_json()['id']

        # Reintento por pérdida de conectividad / timeout — mismo UUID
        r2 = client.post(
            f'/api/horas/reportes/{reporte_borrador.id}/registros',
            headers=_hdr(coord), json=payload,
        )
        assert r2.status_code == 200, r2.get_json()
        assert r2.get_json()['id'] == id1

        # Solo existe un registro en BD
        total = RegistroDiarioHoras.query.filter_by(
            reporte_id=reporte_borrador.id, trabajador_id=t_juan.id,
        ).count()
        assert total == 1

    def test_post_sin_client_record_id_funciona_como_antes(
        self, client, coord, t_juan, reporte_borrador,
    ):
        r = client.post(
            f'/api/horas/reportes/{reporte_borrador.id}/registros',
            headers=_hdr(coord),
            json=self._payload(t_juan, reporte_borrador),
        )
        assert r.status_code == 201
        assert r.get_json()['client_record_id'] is None


# ═══════════════════════════════════════════════════════════════════════════════
# LWW (Last Write Wins) en PUT
# ═══════════════════════════════════════════════════════════════════════════════

class TestLWWEditarRegistro:

    @pytest.fixture
    def registro(self, db, coord, t_juan, reporte_borrador):
        reg = RegistroDiarioHoras(
            reporte_id=reporte_borrador.id,
            trabajador_id=t_juan.id,
            fecha=reporte_borrador.fecha_inicio_semana,
            tipo_nomina='Semanal',
        )
        db.session.add(reg); db.session.commit()
        return reg

    def test_put_sin_modificado_en_aplica_directo(self, client, coord, registro):
        r = client.put(
            f'/api/horas/registros/{registro.id}',
            headers=_hdr(coord),
            json={'hora_entrada': '08:15', 'hora_salida': '17:45'},
        )
        assert r.status_code == 200, r.get_json()
        assert r.get_json()['hora_entrada'] == '08:15'

    def test_put_con_modificado_en_anterior_devuelve_409(self, client, coord, registro):
        # Cliente dice "lo modifiqué en una fecha vieja" → servidor (más reciente) gana.
        cliente_dt = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        r = client.put(
            f'/api/horas/registros/{registro.id}',
            headers=_hdr(coord),
            json={
                'hora_entrada': '08:15', 'hora_salida': '17:45',
                'modificado_en': cliente_dt,
            },
        )
        assert r.status_code == 409, r.get_json()
        body = r.get_json()
        assert body.get('conflicto') is True
        assert 'servidor' in body
        assert body['servidor']['id'] == registro.id

    def test_put_con_modificado_en_futuro_aplica(self, client, db, coord, registro):
        # Cliente con timestamp futuro → su edición gana.
        cliente_dt = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        r = client.put(
            f'/api/horas/registros/{registro.id}',
            headers=_hdr(coord),
            json={
                'hora_entrada': '09:30', 'hora_salida': '18:00',
                'modificado_en': cliente_dt,
            },
        )
        assert r.status_code == 200, r.get_json()
        assert r.get_json()['hora_entrada'] == '09:30'

    def test_put_modificado_en_formato_invalido_da_400(self, client, coord, registro):
        r = client.put(
            f'/api/horas/registros/{registro.id}',
            headers=_hdr(coord),
            json={'hora_entrada': '08:00', 'modificado_en': 'no-es-iso'},
        )
        assert r.status_code == 400, r.get_json()


# ═══════════════════════════════════════════════════════════════════════════════
# Endpoint POST /api/horas/rfid/asociar
# ═══════════════════════════════════════════════════════════════════════════════

class TestRFIDAsociar:

    def test_asocia_uid_y_normaliza(self, client, db, coord, t_juan, proyecto_coord):
        r = client.post(
            '/api/horas/rfid/asociar',
            headers=_hdr(coord),
            json={'trabajador_id': t_juan.id, 'uid': '0x ab-cd:12 34'},
        )
        assert r.status_code == 200, r.get_json()
        # Normaliza: uppercase, sin prefijo 0x, sin guiones/espacios/dos puntos.
        assert r.get_json()['rfid_uid'] == 'ABCD1234'
        db.session.refresh(t_juan)
        assert t_juan.rfid_uid == 'ABCD1234'

    def test_uid_duplicado_devuelve_409(self, client, db, coord, t_juan, t_maria, proyecto_coord):
        proyecto_coord.participantes.append(t_maria)
        db.session.commit()

        client.post('/api/horas/rfid/asociar',
                    headers=_hdr(coord),
                    json={'trabajador_id': t_juan.id, 'uid': 'CARDX'})
        r = client.post('/api/horas/rfid/asociar',
                        headers=_hdr(coord),
                        json={'trabajador_id': t_maria.id, 'uid': 'CARDX'})
        assert r.status_code == 409, r.get_json()
        assert 'Juan' in r.get_json()['error']

    def test_coordinador_no_puede_asociar_trabajador_ajeno(
        self, client, db, coord_otro, t_juan, proyecto_coord,
    ):
        # coord_otro NO es coordinador del proyecto donde está t_juan.
        r = client.post(
            '/api/horas/rfid/asociar',
            headers=_hdr(coord_otro),
            json={'trabajador_id': t_juan.id, 'uid': 'CARDY'},
        )
        assert r.status_code == 403

    def test_payload_incompleto_da_400(self, client, coord, t_juan):
        r = client.post('/api/horas/rfid/asociar',
                        headers=_hdr(coord),
                        json={'trabajador_id': t_juan.id})
        assert r.status_code == 400


# ═══════════════════════════════════════════════════════════════════════════════
# Endpoint GET /api/horas/rfid/trabajadores-reporte/<id>
# ═══════════════════════════════════════════════════════════════════════════════

class TestRFIDTrabajadoresReporte:

    def test_devuelve_trabajadores_con_uid_y_perfil(
        self, client, db, coord, t_juan, reporte_borrador,
    ):
        t_juan.rfid_uid = 'FACE001'
        db.session.commit()

        r = client.get(
            f'/api/horas/rfid/trabajadores-reporte/{reporte_borrador.id}',
            headers=_hdr(coord),
        )
        assert r.status_code == 200, r.get_json()
        body = r.get_json()
        assert body['reporte_id'] == reporte_borrador.id
        assert len(body['trabajadores']) == 1
        t = body['trabajadores'][0]
        assert t['no_empleado'] == 'RFID-001'
        assert t['rfid_uid'] == 'FACE001'
        assert t['tipo_nomina'] == 'Semanal'
        assert t['viaticos'] == 50.0
        assert t['pago_dia_festivo'] == 200.0

    def test_coordinador_ajeno_es_rechazado(
        self, client, coord_otro, reporte_borrador,
    ):
        r = client.get(
            f'/api/horas/rfid/trabajadores-reporte/{reporte_borrador.id}',
            headers=_hdr(coord_otro),
        )
        assert r.status_code == 403
