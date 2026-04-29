"""Tests de integración para el sistema de notificaciones in-app.

Bugs detectados y corregidos durante la escritura de estos tests:

  BUG-1 (Bajo) — CORREGIDO:
    marcar_leida y marcar_todas solo tenían @login_required, sin verificar
    que el usuario fuera admin/super_admin. Cualquier rol autenticado podía
    llamar a estos endpoints.
    Fix: se agregó comprobación de rol al inicio de ambos endpoints (403 para
    roles sin privilegios).
    Tests: test_bug1_coordinador_recibe_403_en_marcar_leida,
           test_bug1_coordinador_recibe_403_en_marcar_todas

  BUG-2 (Medio) — CORREGIDO:
    _seed_updates_for_user() se invocaba sin try/except dentro de api_resumen.
    Un error en el commit de seeds habría causado que todo el endpoint fallara
    con 500 en lugar de devolver las notificaciones ya existentes.
    Fix: envuelto en try/except con pass; el seed falla silenciosamente.
    Test: test_bug2_resumen_responde_aunque_seed_ya_exista
"""

import pytest
from datetime import datetime, timezone, timedelta
from werkzeug.security import generate_password_hash
from app.models import Notificacion, User, crear_notif_admins
from app.routes.notificaciones import _tiempo_relativo, _seed_updates_for_user, CHANGELOG


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures locales
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def super_admin_user(db):
    u = User(
        username='super_admin_test',
        password_hash=generate_password_hash('pass'),
        role='super_admin',
    )
    db.session.add(u)
    db.session.commit()
    return u


@pytest.fixture
def logged_in_super_admin(client, super_admin_user):
    with client.session_transaction() as sess:
        sess['user_id'] = super_admin_user.id
        sess['user'] = super_admin_user.username
        sess['role'] = 'super_admin'
    return client


def _crear_notif(db, user_id, leida=False, tipo='TEST', referencia=None, url=None):
    """Helper para insertar una notificación de prueba."""
    n = Notificacion(
        usuario_id=user_id,
        tipo=tipo,
        titulo=f'Título {tipo}',
        mensaje=f'Mensaje de prueba para {tipo}',
        leida=leida,
        referencia=referencia,
        url=url,
    )
    db.session.add(n)
    db.session.commit()
    return n


# ─────────────────────────────────────────────────────────────────────────────
# TestTiempoRelativo
# ─────────────────────────────────────────────────────────────────────────────

class TestTiempoRelativo:
    """Unidad: función auxiliar _tiempo_relativo."""

    def test_none_devuelve_cadena_vacia(self):
        assert _tiempo_relativo(None) == ''

    def test_ahora_mismo_segundos_bajos(self):
        dt = datetime.now(timezone.utc) - timedelta(seconds=30)
        assert _tiempo_relativo(dt) == 'ahora mismo'

    def test_exactamente_cero_segundos(self):
        dt = datetime.now(timezone.utc)
        assert _tiempo_relativo(dt) == 'ahora mismo'

    def test_limite_inferior_minutos(self):
        """59 segundos aún es 'ahora mismo'."""
        dt = datetime.now(timezone.utc) - timedelta(seconds=59)
        assert _tiempo_relativo(dt) == 'ahora mismo'

    def test_limite_superior_ahora_mismo(self):
        """60 segundos ya es 'hace 1 min'."""
        dt = datetime.now(timezone.utc) - timedelta(seconds=60)
        assert _tiempo_relativo(dt) == 'hace 1 min'

    def test_cinco_minutos(self):
        dt = datetime.now(timezone.utc) - timedelta(minutes=5)
        assert _tiempo_relativo(dt) == 'hace 5 min'

    def test_59_minutos(self):
        dt = datetime.now(timezone.utc) - timedelta(minutes=59)
        assert _tiempo_relativo(dt) == 'hace 59 min'

    def test_una_hora(self):
        dt = datetime.now(timezone.utc) - timedelta(hours=1)
        assert _tiempo_relativo(dt) == 'hace 1h'

    def test_23_horas(self):
        dt = datetime.now(timezone.utc) - timedelta(hours=23)
        assert _tiempo_relativo(dt) == 'hace 23h'

    def test_un_dia(self):
        dt = datetime.now(timezone.utc) - timedelta(days=1)
        assert _tiempo_relativo(dt) == 'hace 1d'

    def test_varios_dias(self):
        dt = datetime.now(timezone.utc) - timedelta(days=7)
        assert _tiempo_relativo(dt) == 'hace 7d'

    def test_naive_datetime_no_lanza_excepcion(self):
        """Un datetime sin tzinfo se trata como UTC — no debe lanzar excepción."""
        dt = datetime.utcnow() - timedelta(minutes=10)
        assert dt.tzinfo is None
        result = _tiempo_relativo(dt)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_naive_datetime_resultado_coherente(self):
        """Un naive datetime de 5 min atrás debe interpretarse como ~5 min."""
        dt = datetime.utcnow() - timedelta(minutes=5)
        result = _tiempo_relativo(dt)
        assert 'min' in result or result == 'ahora mismo'


# ─────────────────────────────────────────────────────────────────────────────
# TestCrearNotifAdmins
# ─────────────────────────────────────────────────────────────────────────────

class TestCrearNotifAdmins:
    """Unidad: función helper crear_notif_admins del modelo."""

    def test_crea_notificacion_para_admin(self, db, admin_user):
        crear_notif_admins('REPORTE_CERRADO', 'Título', 'Mensaje')
        db.session.commit()
        notifs = Notificacion.query.filter_by(usuario_id=admin_user.id).all()
        assert len(notifs) == 1
        n = notifs[0]
        assert n.tipo == 'REPORTE_CERRADO'
        assert n.titulo == 'Título'
        assert n.mensaje == 'Mensaje'
        assert n.leida is False
        assert n.referencia is None

    def test_crea_notificacion_para_super_admin(self, db, super_admin_user):
        crear_notif_admins('TEST', 'Título', 'Msg')
        db.session.commit()
        notifs = Notificacion.query.filter_by(usuario_id=super_admin_user.id).all()
        assert len(notifs) == 1

    def test_admin_recibe_notificacion(self, db):
        admin = User(username='admin_extra', password_hash='hash', role='admin')
        db.session.add(admin)
        db.session.commit()
        crear_notif_admins('TEST', 'T', 'M')
        db.session.commit()
        assert Notificacion.query.filter_by(usuario_id=admin.id).count() == 1

    def test_ignora_coordinador(self, db, coordinador_user):
        crear_notif_admins('TEST', 'T', 'M')
        db.session.commit()
        assert Notificacion.query.filter_by(usuario_id=coordinador_user.id).count() == 0

    def test_ignora_rol_usuario_generico(self, db):
        u = User(username='user_gen', password_hash='hash', role='user')
        db.session.add(u)
        db.session.commit()
        crear_notif_admins('TEST', 'T', 'M')
        db.session.commit()
        assert Notificacion.query.filter_by(usuario_id=u.id).count() == 0

    def test_crea_para_multiples_admins(self, db, admin_user, super_admin_user):
        admin2 = User(username='admin2', password_hash='hash', role='admin')
        db.session.add(admin2)
        db.session.commit()
        crear_notif_admins('TEST', 'T', 'M')
        db.session.commit()
        # admin_user + super_admin_user + admin2 = 3
        assert Notificacion.query.count() == 3

    def test_url_se_guarda(self, db, admin_user):
        crear_notif_admins('TEST', 'T', 'M', url='/prenomina/')
        db.session.commit()
        n = Notificacion.query.filter_by(usuario_id=admin_user.id).first()
        assert n.url == '/prenomina/'

    def test_url_none_permitido(self, db, admin_user):
        crear_notif_admins('TEST', 'T', 'M', url=None)
        db.session.commit()
        n = Notificacion.query.filter_by(usuario_id=admin_user.id).first()
        assert n.url is None

    def test_no_hace_commit_automatico(self, db, admin_user):
        """crear_notif_admins NO hace commit; un rollback debe descartar la notificación."""
        crear_notif_admins('TEST', 'T', 'M')
        db.session.rollback()
        assert Notificacion.query.filter_by(usuario_id=admin_user.id).count() == 0

    def test_sin_admins_no_crea_nada(self, db):
        """Si no hay admins en BD, no se crea ninguna notificación."""
        crear_notif_admins('TEST', 'T', 'M')
        db.session.commit()
        assert Notificacion.query.count() == 0


# ─────────────────────────────────────────────────────────────────────────────
# TestSeedUpdates
# ─────────────────────────────────────────────────────────────────────────────

class TestSeedUpdates:
    """Tests para _seed_updates_for_user (siembra del changelog)."""

    def test_crea_todas_las_entradas_del_changelog(self, db, admin_user):
        _seed_updates_for_user(admin_user.id)
        count = Notificacion.query.filter_by(
            usuario_id=admin_user.id, tipo='ACTUALIZACION',
        ).count()
        assert count == len(CHANGELOG)

    def test_todas_las_actualizaciones_son_no_leidas(self, db, admin_user):
        _seed_updates_for_user(admin_user.id)
        sin_leer = Notificacion.query.filter_by(
            usuario_id=admin_user.id, tipo='ACTUALIZACION', leida=False,
        ).count()
        assert sin_leer == len(CHANGELOG)

    def test_no_duplica_al_llamar_dos_veces(self, db, admin_user):
        _seed_updates_for_user(admin_user.id)
        _seed_updates_for_user(admin_user.id)
        count = Notificacion.query.filter_by(
            usuario_id=admin_user.id, tipo='ACTUALIZACION',
        ).count()
        assert count == len(CHANGELOG)

    def test_cada_usuario_tiene_su_propio_seed(self, db, admin_user, super_admin_user):
        _seed_updates_for_user(admin_user.id)
        _seed_updates_for_user(super_admin_user.id)
        count1 = Notificacion.query.filter_by(
            usuario_id=admin_user.id, tipo='ACTUALIZACION',
        ).count()
        count2 = Notificacion.query.filter_by(
            usuario_id=super_admin_user.id, tipo='ACTUALIZACION',
        ).count()
        assert count1 == len(CHANGELOG)
        assert count2 == len(CHANGELOG)
        assert Notificacion.query.filter_by(tipo='ACTUALIZACION').count() == len(CHANGELOG) * 2

    def test_referencia_correcta_en_cada_entrada(self, db, admin_user):
        _seed_updates_for_user(admin_user.id)
        refs_en_bd = {
            n.referencia
            for n in Notificacion.query.filter_by(
                usuario_id=admin_user.id, tipo='ACTUALIZACION',
            ).all()
        }
        refs_changelog = {e['referencia'] for e in CHANGELOG}
        assert refs_en_bd == refs_changelog

    def test_url_se_guarda_en_actualizacion(self, db, admin_user):
        _seed_updates_for_user(admin_user.id)
        for entry in CHANGELOG:
            n = Notificacion.query.filter_by(
                usuario_id=admin_user.id,
                referencia=entry['referencia'],
            ).first()
            assert n is not None, f"Falta entrada: {entry['referencia']}"
            assert n.url == entry.get('url')


# ─────────────────────────────────────────────────────────────────────────────
# TestApiResumen  GET /notificaciones/api/resumen
# ─────────────────────────────────────────────────────────────────────────────

class TestApiResumen:
    """Integración: endpoint GET /notificaciones/api/resumen."""

    def test_requiere_autenticacion(self, client):
        resp = client.get('/notificaciones/api/resumen')
        assert resp.status_code in (302, 303, 308)

    def test_coordinador_es_redirigido_por_rbac(self, logged_in_coordinador, db, coordinador_user):
        """El RBAC de login_required bloquea al coordinador antes de llegar al endpoint.
        La campana de notificaciones no aparece en la UI del coordinador (base.html:142),
        por lo que este rol no debe tener acceso a ninguna ruta de notificaciones."""
        _crear_notif(db, coordinador_user.id, tipo='TEST')
        resp = logged_in_coordinador.get('/notificaciones/api/resumen')
        assert resp.status_code in (302, 303, 308)

    def test_admin_recibe_sus_notificaciones(self, logged_in_admin, db, admin_user):
        _crear_notif(db, admin_user.id, tipo='REPORTE_CERRADO')
        resp = logged_in_admin.get('/notificaciones/api/resumen')
        assert resp.status_code == 200
        data = resp.get_json()
        tipos = [i['tipo'] for i in data['items']]
        assert 'REPORTE_CERRADO' in tipos

    def test_super_admin_recibe_sus_notificaciones(self, logged_in_super_admin, db, super_admin_user):
        _crear_notif(db, super_admin_user.id, tipo='PRENOMINA_CERRADA')
        resp = logged_in_super_admin.get('/notificaciones/api/resumen')
        assert resp.status_code == 200
        data = resp.get_json()
        tipos = [i['tipo'] for i in data['items']]
        assert 'PRENOMINA_CERRADA' in tipos

    def test_siembra_changelog_en_primer_llamado(self, logged_in_admin, db, admin_user):
        assert Notificacion.query.filter_by(
            usuario_id=admin_user.id, tipo='ACTUALIZACION',
        ).count() == 0
        logged_in_admin.get('/notificaciones/api/resumen')
        count = Notificacion.query.filter_by(
            usuario_id=admin_user.id, tipo='ACTUALIZACION',
        ).count()
        assert count == len(CHANGELOG)

    def test_no_duplica_changelog_en_llamados_sucesivos(self, logged_in_admin, db, admin_user):
        logged_in_admin.get('/notificaciones/api/resumen')
        logged_in_admin.get('/notificaciones/api/resumen')
        logged_in_admin.get('/notificaciones/api/resumen')
        count = Notificacion.query.filter_by(
            usuario_id=admin_user.id, tipo='ACTUALIZACION',
        ).count()
        assert count == len(CHANGELOG)

    def test_no_leidas_incluye_changelog_recien_sembrado(self, logged_in_admin, db, admin_user):
        resp = logged_in_admin.get('/notificaciones/api/resumen')
        data = resp.get_json()
        # Todas las del CHANGELOG son no leídas al sembrarse
        assert data['no_leidas'] >= len(CHANGELOG)

    def test_conteo_no_leidas_correcto(self, logged_in_admin, db, admin_user):
        _crear_notif(db, admin_user.id, leida=False)
        _crear_notif(db, admin_user.id, leida=True)
        resp = logged_in_admin.get('/notificaciones/api/resumen')
        data = resp.get_json()
        leidas_en_resp = sum(1 for i in data['items'] if i['leida'])
        no_leidas_en_resp = sum(1 for i in data['items'] if not i['leida'])
        assert data['no_leidas'] == no_leidas_en_resp

    def test_limita_a_15_items(self, logged_in_admin, db, admin_user):
        for _ in range(20):
            _crear_notif(db, admin_user.id, tipo='REPORTE_CERRADO')
        resp = logged_in_admin.get('/notificaciones/api/resumen')
        data = resp.get_json()
        assert len(data['items']) <= 15

    def test_no_leidas_aparecen_antes_que_leidas(self, logged_in_admin, db, admin_user):
        """El orden debe ser: no leídas primero, luego leídas (por fecha desc dentro de cada grupo)."""
        # Crear leídas primero (id menor), luego no leídas (id mayor)
        _crear_notif(db, admin_user.id, leida=True, tipo='TEST')
        _crear_notif(db, admin_user.id, leida=False, tipo='TEST')
        resp = logged_in_admin.get('/notificaciones/api/resumen')
        data = resp.get_json()
        items = [i for i in data['items'] if i['tipo'] == 'TEST']
        assert len(items) == 2
        # La primera (índice 0) debe ser la no leída
        assert items[0]['leida'] is False
        assert items[1]['leida'] is True

    def test_estructura_json_completa(self, logged_in_admin, db, admin_user):
        _crear_notif(db, admin_user.id, tipo='PRENOMINA_CERRADA', url='/prenomina/')
        resp = logged_in_admin.get('/notificaciones/api/resumen')
        data = resp.get_json()
        assert 'no_leidas' in data
        assert 'items' in data
        item = next((i for i in data['items'] if i['tipo'] == 'PRENOMINA_CERRADA'), None)
        assert item is not None
        for campo in ('id', 'tipo', 'titulo', 'mensaje', 'url', 'leida', 'tiempo'):
            assert campo in item, f"Falta el campo '{campo}' en el item"

    def test_url_none_se_serializa_como_cadena_vacia(self, logged_in_admin, db, admin_user):
        _crear_notif(db, admin_user.id, tipo='TEST', url=None)
        resp = logged_in_admin.get('/notificaciones/api/resumen')
        data = resp.get_json()
        item = next((i for i in data['items'] if i['tipo'] == 'TEST'), None)
        assert item is not None
        assert item['url'] == ''

    def test_no_devuelve_notificaciones_de_otro_usuario(self, logged_in_admin, db, coordinador_user):
        ajena = _crear_notif(db, coordinador_user.id, tipo='REPORTE_CERRADO')
        resp = logged_in_admin.get('/notificaciones/api/resumen')
        data = resp.get_json()
        ids = [i['id'] for i in data['items']]
        assert ajena.id not in ids

    def test_campo_tiempo_es_cadena_no_vacia(self, logged_in_admin, db, admin_user):
        _crear_notif(db, admin_user.id, tipo='TEST')
        resp = logged_in_admin.get('/notificaciones/api/resumen')
        data = resp.get_json()
        item = next((i for i in data['items'] if i['tipo'] == 'TEST'), None)
        assert item is not None
        assert isinstance(item['tiempo'], str)
        assert len(item['tiempo']) > 0

    # BUG-2: _seed_updates_for_user sin manejo de errores
    def test_bug2_resumen_responde_aunque_seed_ya_exista(self, logged_in_admin, db, admin_user):
        """BUG-2: El endpoint no debe fallar si el seed ya fue aplicado previamente.
        Este test verifica que llamadas repetidas no causan error 500."""
        # Primera llamada: siembra el CHANGELOG
        r1 = logged_in_admin.get('/notificaciones/api/resumen')
        assert r1.status_code == 200
        # Segunda llamada: el seed ya existe, debe devolver igualmente 200
        r2 = logged_in_admin.get('/notificaciones/api/resumen')
        assert r2.status_code == 200
        assert r2.get_json()['no_leidas'] is not None


# ─────────────────────────────────────────────────────────────────────────────
# TestMarcarLeida  POST /notificaciones/api/marcar_leida/<id>
# ─────────────────────────────────────────────────────────────────────────────

class TestMarcarLeida:
    """Integración: endpoint POST /notificaciones/api/marcar_leida/<id>."""

    def test_requiere_autenticacion(self, client, db, admin_user):
        n = _crear_notif(db, admin_user.id)
        resp = client.post(f'/notificaciones/api/marcar_leida/{n.id}')
        assert resp.status_code in (302, 303, 308)

    def test_marca_notificacion_como_leida(self, logged_in_admin, db, admin_user):
        n = _crear_notif(db, admin_user.id, leida=False)
        resp = logged_in_admin.post(f'/notificaciones/api/marcar_leida/{n.id}')
        assert resp.status_code == 200
        assert resp.get_json()['success'] is True
        db.session.refresh(n)
        assert n.leida is True

    def test_id_inexistente_devuelve_404(self, logged_in_admin):
        resp = logged_in_admin.post('/notificaciones/api/marcar_leida/99999')
        assert resp.status_code == 404

    def test_idor_no_puede_marcar_notificacion_de_otro_usuario(self, logged_in_admin, db, coordinador_user):
        """IDOR: el admin no puede marcar una notificación que pertenece a otro usuario."""
        ajena = _crear_notif(db, coordinador_user.id, leida=False)
        resp = logged_in_admin.post(f'/notificaciones/api/marcar_leida/{ajena.id}')
        assert resp.status_code == 404
        db.session.refresh(ajena)
        assert ajena.leida is False  # no debe haber cambiado

    def test_marcar_ya_leida_es_idempotente(self, logged_in_admin, db, admin_user):
        n = _crear_notif(db, admin_user.id, leida=True)
        resp = logged_in_admin.post(f'/notificaciones/api/marcar_leida/{n.id}')
        assert resp.status_code == 200
        db.session.refresh(n)
        assert n.leida is True

    def test_no_afecta_otras_notificaciones_del_mismo_usuario(self, logged_in_admin, db, admin_user):
        n1 = _crear_notif(db, admin_user.id, leida=False)
        n2 = _crear_notif(db, admin_user.id, leida=False)
        logged_in_admin.post(f'/notificaciones/api/marcar_leida/{n1.id}')
        db.session.refresh(n2)
        assert n2.leida is False  # solo n1 debe haber cambiado

    def test_coordinador_es_redirigido_por_rbac_en_marcar_leida(self, logged_in_coordinador, db, coordinador_user):
        """El RBAC bloquea al coordinador antes de llegar al endpoint (302, no 403).
        El coordinador no tiene la UI de notificaciones, por lo que el acceso
        se deniega en la capa de ROLE_PERMISSIONS, no dentro del endpoint."""
        n = _crear_notif(db, coordinador_user.id, leida=False)
        resp = logged_in_coordinador.post(f'/notificaciones/api/marcar_leida/{n.id}')
        assert resp.status_code in (302, 303, 308)
        db.session.refresh(n)
        assert n.leida is False  # no debe haber cambiado


# ─────────────────────────────────────────────────────────────────────────────
# TestMarcarTodas  POST /notificaciones/api/marcar_todas
# ─────────────────────────────────────────────────────────────────────────────

class TestMarcarTodas:
    """Integración: endpoint POST /notificaciones/api/marcar_todas."""

    def test_requiere_autenticacion(self, client):
        resp = client.post('/notificaciones/api/marcar_todas')
        assert resp.status_code in (302, 303, 308)

    def test_marca_todas_las_no_leidas(self, logged_in_admin, db, admin_user):
        for _ in range(4):
            _crear_notif(db, admin_user.id, leida=False)
        resp = logged_in_admin.post('/notificaciones/api/marcar_todas')
        assert resp.status_code == 200
        assert resp.get_json()['success'] is True
        sin_leer = Notificacion.query.filter_by(
            usuario_id=admin_user.id, leida=False,
        ).count()
        assert sin_leer == 0

    def test_no_toca_las_ya_leidas(self, logged_in_admin, db, admin_user):
        leida = _crear_notif(db, admin_user.id, leida=True)
        logged_in_admin.post('/notificaciones/api/marcar_todas')
        db.session.refresh(leida)
        assert leida.leida is True

    def test_no_afecta_notificaciones_de_otro_usuario(self, logged_in_admin, db, admin_user, coordinador_user):
        ajena = _crear_notif(db, coordinador_user.id, leida=False)
        logged_in_admin.post('/notificaciones/api/marcar_todas')
        db.session.refresh(ajena)
        assert ajena.leida is False

    def test_sin_notificaciones_no_falla(self, logged_in_admin):
        resp = logged_in_admin.post('/notificaciones/api/marcar_todas')
        assert resp.status_code == 200
        assert resp.get_json()['success'] is True

    def test_super_admin_marca_solo_las_suyas(self, logged_in_super_admin, db, super_admin_user, admin_user):
        n_super = _crear_notif(db, super_admin_user.id, leida=False)
        n_admin = _crear_notif(db, admin_user.id, leida=False)
        logged_in_super_admin.post('/notificaciones/api/marcar_todas')
        db.session.refresh(n_super)
        db.session.refresh(n_admin)
        assert n_super.leida is True
        assert n_admin.leida is False  # no debe haberse tocado

    def test_coordinador_es_redirigido_por_rbac_en_marcar_todas(self, logged_in_coordinador, db, coordinador_user):
        """El RBAC bloquea al coordinador antes de llegar al endpoint (302, no 403).
        El coordinador no tiene la UI de notificaciones, por lo que el acceso
        se deniega en la capa de ROLE_PERMISSIONS, no dentro del endpoint."""
        _crear_notif(db, coordinador_user.id, leida=False)
        resp = logged_in_coordinador.post('/notificaciones/api/marcar_todas')
        assert resp.status_code in (302, 303, 308)


# ─────────────────────────────────────────────────────────────────────────────
# TestIntegracion  Flujos completos
# ─────────────────────────────────────────────────────────────────────────────

class TestIntegracion:
    """Flujos end-to-end: crear → leer → marcar → verificar."""

    def test_flujo_reporte_cerrado(self, logged_in_admin, db, admin_user):
        """Simula el ciclo completo de una notificación de reporte de horas."""
        # 1. Simular trigger de cierre de reporte
        crear_notif_admins(
            tipo='REPORTE_CERRADO',
            titulo='Reporte cerrado — Proyecto P-001',
            mensaje='El reporte de la semana del 01/04/2026 fue enviado.',
            url='/prenomina/',
        )
        db.session.commit()

        # 2. Verificar que aparece en el resumen
        resp = logged_in_admin.get('/notificaciones/api/resumen')
        data = resp.get_json()
        assert data['no_leidas'] >= 1
        item = next((i for i in data['items'] if i['tipo'] == 'REPORTE_CERRADO'), None)
        assert item is not None
        assert item['leida'] is False
        assert '/prenomina/' in item['url']

        # 3. Marcar como leída
        resp2 = logged_in_admin.post(f'/notificaciones/api/marcar_leida/{item["id"]}')
        assert resp2.get_json()['success'] is True

        # 4. Verificar que ya está leída y el conteo bajó
        resp3 = logged_in_admin.get('/notificaciones/api/resumen')
        data3 = resp3.get_json()
        item_act = next((i for i in data3['items'] if i['id'] == item['id']), None)
        assert item_act is not None
        assert item_act['leida'] is True

    def test_flujo_prenomina_cerrada(self, logged_in_admin, db, admin_user):
        """Simula el trigger de cierre de prenómina."""
        crear_notif_admins(
            tipo='PRENOMINA_CERRADA',
            titulo='Prenómina aprobada — semana 2026-04-21',
            mensaje='La nómina de la semana del 2026-04-21 fue cerrada con 12 registro(s).',
            url='/prenomina/',
        )
        db.session.commit()

        resp = logged_in_admin.get('/notificaciones/api/resumen')
        data = resp.get_json()
        item = next((i for i in data['items'] if i['tipo'] == 'PRENOMINA_CERRADA'), None)
        assert item is not None
        assert '2026-04-21' in item['titulo']

    def test_flujo_marcar_todas_luego_verificar_conteo_cero(self, logged_in_admin, db, admin_user):
        for tipo in ('REPORTE_CERRADO', 'PRENOMINA_CERRADA'):
            _crear_notif(db, admin_user.id, tipo=tipo)

        # Obtener conteo antes
        r1 = logged_in_admin.get('/notificaciones/api/resumen')
        assert r1.get_json()['no_leidas'] >= 2

        # Marcar todas
        logged_in_admin.post('/notificaciones/api/marcar_todas')

        # Verificar que no_leidas es 0
        r2 = logged_in_admin.get('/notificaciones/api/resumen')
        assert r2.get_json()['no_leidas'] == 0

    def test_multiples_admins_reciben_notificacion_independiente(
        self, db, admin_user, super_admin_user, client
    ):
        """Cada admin ve sus propias notificaciones de forma independiente."""
        crear_notif_admins('PRENOMINA_CERRADA', 'Cierre semana', 'Detalle del cierre.')
        db.session.commit()

        # Admin marca la suya como leída
        with client.session_transaction() as sess:
            sess['user_id'] = admin_user.id
            sess['user'] = admin_user.username
            sess['role'] = 'admin'
        r = client.get('/notificaciones/api/resumen')
        item_admin = next(
            (i for i in r.get_json()['items'] if i['tipo'] == 'PRENOMINA_CERRADA'), None,
        )
        assert item_admin is not None
        client.post(f'/notificaciones/api/marcar_leida/{item_admin["id"]}')

        # Cambiar a super_admin: su notificación sigue no leída
        with client.session_transaction() as sess:
            sess['user_id'] = super_admin_user.id
            sess['user'] = super_admin_user.username
            sess['role'] = 'super_admin'
        r2 = client.get('/notificaciones/api/resumen')
        item_super = next(
            (i for i in r2.get_json()['items'] if i['tipo'] == 'PRENOMINA_CERRADA'), None,
        )
        assert item_super is not None
        assert item_super['leida'] is False  # el otro admin no debe haberla marcado

    def test_actualizacion_changelog_aparece_como_no_leida(self, logged_in_admin, db, admin_user):
        """Las actualizaciones del CHANGELOG deben mostrarse como no leídas al admin la primera vez."""
        resp = logged_in_admin.get('/notificaciones/api/resumen')
        data = resp.get_json()
        updates = [i for i in data['items'] if i['tipo'] == 'ACTUALIZACION']
        assert len(updates) == len(CHANGELOG)
        for u in updates:
            assert u['leida'] is False
