"""Tests del API JWT `/api/notificaciones/*`.

Cobertura:
  - GET  /resumen                       contador + 15 más recientes (con seed
                                        de CHANGELOG y purga de leídas viejas)
  - POST /<int:id>/leer                 marcar UNA como leída
  - POST /marcar_todas                  marcar todas del usuario

Reglas no obvias:
  - Roles no-admin reciben `{no_leidas: 0, items: []}` (no 403): el campanazo
    del SPA hace polling y queremos cero ruido para roles operativos.
  - `_seed_updates_for_user` inserta una `Notificacion` por cada entrada de
    CHANGELOG la primera vez que un admin pega al endpoint, deduplicando por
    `referencia`. Llamadas siguientes no duplican.
  - `_purgar_notificaciones_viejas` borra notifs LEÍDAS con más de
    DIAS_EXPIRACION (30) días; las no leídas se conservan aunque sean viejas.
  - Orden de items: no leídas primero, dentro de cada grupo por `created_at` DESC.
  - `marcar_leida` y `marcar_todas` son scope-por-usuario: el WHERE incluye
    `usuario_id=current_user().id`. Tocar la notif de otro usuario → 404.
"""
from datetime import datetime, timedelta, timezone

import pytest
from werkzeug.security import generate_password_hash

from app.models import Notificacion, User
from app.routes.api_auth import _encode_access_token
from app.routes.api_notificaciones._core import CHANGELOG, DIAS_EXPIRACION
from app.extensions import db as flask_db


def _hdr(user):
    return {'Authorization': f'Bearer {_encode_access_token(user)}'}


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def n_admin(db):
    u = User(username='n_admin', password_hash=generate_password_hash('Pass123!'), role='admin')
    db.session.add(u); db.session.commit()
    return u


@pytest.fixture
def n_admin_b(db):
    """Segundo admin — para probar scope per-user en marcar_leida."""
    u = User(username='n_admin_b', password_hash=generate_password_hash('Pass123!'), role='admin')
    db.session.add(u); db.session.commit()
    return u


@pytest.fixture
def n_coord(db):
    u = User(username='n_coord', password_hash=generate_password_hash('Pass123!'),
              role='coordinador')
    db.session.add(u); db.session.commit()
    return u


def _notif(db, user, *, tipo='REPORTE_CERRADO', titulo='X', leida=False,
           referencia=None, created_at=None):
    n = Notificacion(
        usuario_id=user.id, tipo=tipo, titulo=titulo, mensaje='—',
        leida=leida, referencia=referencia,
    )
    if created_at:
        n.created_at = created_at
    db.session.add(n); db.session.commit()
    return n


# ═══════════════════════════════════════════════════════════════════════════════
# 1. AUTH
# ═══════════════════════════════════════════════════════════════════════════════

class TestAuth:

    def test_sin_token_401(self, client):
        r = client.get('/api/notificaciones/resumen')
        assert r.status_code == 401

    def test_coord_recibe_vacio_no_403(self, client, n_coord):
        # Roles no-admin: 200 con counters en cero (no es un 403)
        r = client.get('/api/notificaciones/resumen', headers=_hdr(n_coord))
        assert r.status_code == 200
        body = r.get_json()
        assert body == {'no_leidas': 0, 'items': []}


# ═══════════════════════════════════════════════════════════════════════════════
# 2. RESUMEN — seed de CHANGELOG
# ═══════════════════════════════════════════════════════════════════════════════

class TestSeedChangelog:

    def test_primera_llamada_siembra_entradas(self, client, n_admin):
        # Antes de la llamada no hay ninguna notif para este admin
        assert Notificacion.query.filter_by(usuario_id=n_admin.id).count() == 0

        r = client.get('/api/notificaciones/resumen', headers=_hdr(n_admin))
        assert r.status_code == 200
        # Después de la llamada, CHANGELOG está sembrado
        refs_db = {
            n.referencia
            for n in Notificacion.query.filter_by(usuario_id=n_admin.id).all()
        }
        refs_changelog = {c['referencia'] for c in CHANGELOG}
        assert refs_changelog <= refs_db

    def test_seed_no_duplica_en_segunda_llamada(self, client, n_admin):
        client.get('/api/notificaciones/resumen', headers=_hdr(n_admin))
        n1 = Notificacion.query.filter_by(usuario_id=n_admin.id).count()
        client.get('/api/notificaciones/resumen', headers=_hdr(n_admin))
        n2 = Notificacion.query.filter_by(usuario_id=n_admin.id).count()
        assert n1 == n2

    def test_seed_es_por_usuario(self, client, n_admin, n_admin_b):
        # Cada admin recibe su propio set de notifs de changelog
        client.get('/api/notificaciones/resumen', headers=_hdr(n_admin))
        client.get('/api/notificaciones/resumen', headers=_hdr(n_admin_b))
        a = Notificacion.query.filter_by(usuario_id=n_admin.id).count()
        b = Notificacion.query.filter_by(usuario_id=n_admin_b.id).count()
        assert a == len(CHANGELOG)
        assert b == len(CHANGELOG)


# ═══════════════════════════════════════════════════════════════════════════════
# 3. RESUMEN — contadores y orden
# ═══════════════════════════════════════════════════════════════════════════════

class TestResumen:

    def test_cuenta_no_leidas(self, client, n_admin, db):
        # Las del CHANGELOG sembradas en primera lectura cuentan. Verifiquemos
        # que `no_leidas` refleja todas las nuevas que aún no se han leído.
        _notif(db, n_admin, titulo='Reporte X', leida=False)
        _notif(db, n_admin, titulo='Reporte Y', leida=True)
        r = client.get('/api/notificaciones/resumen', headers=_hdr(n_admin))
        body = r.get_json()
        # CHANGELOG (todas no-leídas al sembrarse) + 1 (Reporte X no leída)
        esperado = len(CHANGELOG) + 1
        assert body['no_leidas'] == esperado

    def test_orden_no_leidas_primero(self, client, n_admin, db):
        # Insertamos una leída más vieja y una no leída más nueva.
        # El orden del endpoint es (leida ASC, created_at DESC), entonces
        # las no-leídas (False=0) salen primero.
        _notif(db, n_admin, titulo='Vieja leída', leida=True,
               created_at=datetime.now(timezone.utc) - timedelta(days=1))
        _notif(db, n_admin, titulo='Nueva sin leer', leida=False,
               created_at=datetime.now(timezone.utc))
        r = client.get('/api/notificaciones/resumen', headers=_hdr(n_admin))
        items = r.get_json()['items']
        # La primera tiene leida=False
        assert items[0]['leida'] is False

    def test_limita_a_15_items(self, client, n_admin, db):
        # Más de 15 → solo 15 vienen
        # (sumando CHANGELOG ya hay len(CHANGELOG) sembradas, agreguemos 20 más)
        for i in range(20):
            _notif(db, n_admin, titulo=f'Notif {i}')
        r = client.get('/api/notificaciones/resumen', headers=_hdr(n_admin))
        assert len(r.get_json()['items']) == 15

    def test_purga_leidas_viejas(self, client, n_admin, db):
        # Una leída vieja (más allá de DIAS_EXPIRACION) debe ser purgada
        vieja = _notif(db, n_admin, titulo='Vieja leída', leida=True,
                       created_at=datetime.now(timezone.utc) - timedelta(days=DIAS_EXPIRACION + 5))
        vieja_id = vieja.id
        client.get('/api/notificaciones/resumen', headers=_hdr(n_admin))
        # Después del resumen, la vieja ya no existe
        assert flask_db.session.get(Notificacion, vieja_id) is None

    def test_no_purga_no_leidas_viejas(self, client, n_admin, db):
        # No leída antigua se preserva (no quieres que las pendientes desaparezcan)
        vieja = _notif(db, n_admin, titulo='Pendiente vieja', leida=False,
                       created_at=datetime.now(timezone.utc) - timedelta(days=DIAS_EXPIRACION + 5))
        vieja_id = vieja.id
        client.get('/api/notificaciones/resumen', headers=_hdr(n_admin))
        assert flask_db.session.get(Notificacion, vieja_id) is not None


# ═══════════════════════════════════════════════════════════════════════════════
# 4. MARCAR LEÍDA
# ═══════════════════════════════════════════════════════════════════════════════

class TestMarcarLeida:

    def test_marca_una_propia(self, client, n_admin, db):
        n = _notif(db, n_admin, titulo='Por leer')
        r = client.post(f'/api/notificaciones/{n.id}/leer', headers=_hdr(n_admin))
        assert r.status_code == 200
        assert r.get_json()['success'] is True
        db.session.refresh(n)
        assert n.leida is True

    def test_marcar_dos_veces_es_idempotente(self, client, n_admin, db):
        n = _notif(db, n_admin, titulo='X')
        client.post(f'/api/notificaciones/{n.id}/leer', headers=_hdr(n_admin))
        r = client.post(f'/api/notificaciones/{n.id}/leer', headers=_hdr(n_admin))
        assert r.status_code == 200

    def test_no_puede_marcar_de_otro_usuario_404(
        self, client, n_admin, n_admin_b, db,
    ):
        ajena = _notif(db, n_admin_b, titulo='No tuya')
        r = client.post(f'/api/notificaciones/{ajena.id}/leer',
                         headers=_hdr(n_admin))
        # El filter_by(usuario_id=current_user) no encuentra → 404
        # (no 403, para no confirmar que la notif existe en otra cuenta)
        assert r.status_code == 404
        # Y la ajena queda intacta
        db.session.refresh(ajena)
        assert ajena.leida is False

    def test_inexistente_404(self, client, n_admin):
        r = client.post('/api/notificaciones/99999/leer', headers=_hdr(n_admin))
        assert r.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# 5. MARCAR TODAS
# ═══════════════════════════════════════════════════════════════════════════════

class TestMarcarTodas:

    def test_marca_todas_del_usuario(self, client, n_admin, db):
        _notif(db, n_admin, titulo='A')
        _notif(db, n_admin, titulo='B')
        r = client.post('/api/notificaciones/marcar_todas', headers=_hdr(n_admin))
        assert r.status_code == 200
        assert r.get_json()['success'] is True
        no_leidas = Notificacion.query.filter_by(
            usuario_id=n_admin.id, leida=False,
        ).count()
        assert no_leidas == 0

    def test_no_toca_las_de_otro_usuario(
        self, client, n_admin, n_admin_b, db,
    ):
        propia = _notif(db, n_admin, titulo='Mía')
        ajena = _notif(db, n_admin_b, titulo='Suya')
        client.post('/api/notificaciones/marcar_todas', headers=_hdr(n_admin))
        db.session.refresh(propia); db.session.refresh(ajena)
        assert propia.leida is True
        assert ajena.leida is False
