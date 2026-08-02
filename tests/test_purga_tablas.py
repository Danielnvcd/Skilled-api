"""Purga por tabla desde el panel de sistemas.

Lo que se fija aquí es la POLÍTICA, que es lo que separa "purgar" de "romper":
cada tabla tiene su piso de antigüedad y su filtro, y ninguno de los dos puede
saltarse desde el cliente.
"""
from datetime import datetime, timedelta, timezone

import pyotp
import pytest
from werkzeug.security import generate_password_hash

from app.models import (
    AuditLog, MovimientoInventario, Notificacion, Producto, RefreshToken, User,
)
from app.routes.api_auth import _encode_access_token


def _hdr(user):
    return {'Authorization': f'Bearer {_encode_access_token(user)}'}


def _hace(dias):
    return datetime.now(timezone.utc) - timedelta(days=dias)


@pytest.fixture
def sistemas(db):
    u = User(username='purga_ops', password_hash=generate_password_hash('Pass123!'),
             role='sistemas', password_version=1, totp_secret=pyotp.random_base32())
    db.session.add(u); db.session.commit()
    return u


# ── Catálogo y permisos ───────────────────────────────────────────────────────

def test_catalogo_lista_las_cuatro_tablas(client, sistemas):
    datos = client.get('/api/sistemas/purgar/previa', headers=_hdr(sistemas)).get_json()
    claves = {t['tabla'] for t in datos['tablas']}
    assert claves == {'audit_log', 'refresh_tokens', 'notificaciones', 'movimientos_inventario'}


def test_movimientos_marcado_como_alto_riesgo(client, sistemas):
    datos = client.get('/api/sistemas/purgar/previa', headers=_hdr(sistemas)).get_json()
    mov = next(t for t in datos['tablas'] if t['tabla'] == 'movimientos_inventario')
    assert mov['riesgo'] == 'alto'
    assert mov['min_meses'] == 24


def test_requiere_rol_sistemas(client, admin_user):
    assert client.get('/api/sistemas/purgar/previa', headers=_hdr(admin_user)).status_code == 403
    assert client.post('/api/sistemas/purgar', headers=_hdr(admin_user),
                       json={'tabla': 'audit_log', 'meses': 12}).status_code == 403


def test_tabla_desconocida_se_rechaza(client, sistemas):
    """No se puede purgar cualquier tabla nombrándola: solo las del registro."""
    r = client.post('/api/sistemas/purgar', headers=_hdr(sistemas),
                    json={'tabla': 'users', 'meses': 12})
    assert r.status_code == 400
    assert 'no purgable' in r.get_json()['error'].lower()


# ── Pisos de antigüedad ───────────────────────────────────────────────────────

@pytest.mark.parametrize('tabla,meses', [
    ('audit_log', 2),
    ('refresh_tokens', 0),
    ('notificaciones', 0),
    ('movimientos_inventario', 12),   # su piso son 24
])
def test_no_se_puede_bajar_del_piso(client, sistemas, tabla, meses):
    r = client.post('/api/sistemas/purgar', headers=_hdr(sistemas),
                    json={'tabla': tabla, 'meses': meses})
    assert r.status_code == 400
    assert 'seguridad' in r.get_json()['error'].lower()


# ── Previa: contar sin borrar ─────────────────────────────────────────────────

def test_previa_no_borra_nada(client, db, sistemas):
    db.session.add(AuditLog(user='x', action='vieja', created_at=_hace(400)))
    db.session.commit()
    antes = AuditLog.query.count()

    datos = client.get('/api/sistemas/purgar/previa',
                       headers=_hdr(sistemas),
                       query_string={'tabla': 'audit_log', 'meses': 3}).get_json()

    assert datos['borrables'] >= 1
    assert datos['conservadas'] == datos['total'] - datos['borrables']
    assert AuditLog.query.count() == antes      # nada se tocó


# ── Filtros que protegen datos vivos ─────────────────────────────────────────

def test_no_borra_sesiones_activas(client, db, sistemas):
    """Un token viejo pero VIGENTE es una sesión en uso: no se toca."""
    vivo = RefreshToken(token_hash='vivo', user_id=sistemas.id, revoked=False,
                        created_at=_hace(400), expires_at=_hace(-30))
    muerto = RefreshToken(token_hash='muerto', user_id=sistemas.id, revoked=True,
                          created_at=_hace(400), expires_at=_hace(390))
    db.session.add_all([vivo, muerto]); db.session.commit()

    r = client.post('/api/sistemas/purgar', headers=_hdr(sistemas),
                    json={'tabla': 'refresh_tokens', 'meses': 1})
    assert r.status_code == 200
    assert r.get_json()['borrados'] == 1

    quedan = {t.token_hash for t in RefreshToken.query.all()}
    assert 'vivo' in quedan and 'muerto' not in quedan


def test_no_borra_notificaciones_sin_leer(client, db, sistemas):
    """Una notificación sin leer sigue siendo un pendiente de alguien."""
    db.session.add_all([
        Notificacion(usuario_id=sistemas.id, tipo='X', titulo='sin leer',
                     mensaje='m', leida=False, created_at=_hace(400)),
        Notificacion(usuario_id=sistemas.id, tipo='X', titulo='leida',
                     mensaje='m', leida=True, created_at=_hace(400)),
    ])
    db.session.commit()

    r = client.post('/api/sistemas/purgar', headers=_hdr(sistemas),
                    json={'tabla': 'notificaciones', 'meses': 1})
    assert r.get_json()['borrados'] == 1

    quedan = {n.titulo for n in Notificacion.query.all()}
    assert quedan == {'sin leer'}


# ── Movimientos: no altera el stock ──────────────────────────────────────────

def test_purgar_movimientos_no_altera_el_stock(client, db, sistemas):
    """El stock vive en su propia columna; no se recalcula sumando movimientos.

    Es la razón por la que purgar el historial es aceptable: se pierde
    trazabilidad, no existencias.
    """
    p = Producto(codigo='PRG-1', descripcion='Tornillo', categoria='Ferretería', unidad='pieza',
                 stock_actual=250, activo=True)
    db.session.add(p); db.session.commit()

    db.session.add_all([
        MovimientoInventario(tipo='ENTRADA', producto_id=p.id, cantidad=100, usuario_id=sistemas.id,
                             fecha=_hace(900)),
        MovimientoInventario(tipo='SALIDA', producto_id=p.id, cantidad=10, usuario_id=sistemas.id,
                             fecha=_hace(5)),
    ])
    db.session.commit()

    r = client.post('/api/sistemas/purgar', headers=_hdr(sistemas),
                    json={'tabla': 'movimientos_inventario', 'meses': 24})
    assert r.status_code == 200
    assert r.get_json()['borrados'] == 1        # solo el de hace 900 días

    db.session.refresh(p)
    assert float(p.stock_actual) == 250.0       # intacto
    assert MovimientoInventario.query.count() == 1


def test_purgar_movimientos_respeta_la_ventana_de_consumo(client, db, sistemas):
    """El cálculo de consumo/mínimos mira los últimos 30 días. Con piso de 24
    meses, ninguna purga puede tocar ese rango."""
    p = Producto(codigo='PRG-2', descripcion='Tuerca', categoria='Ferretería', unidad='pieza',
                 stock_actual=10, activo=True)
    db.session.add(p); db.session.commit()
    db.session.add(MovimientoInventario(tipo='SALIDA', producto_id=p.id, usuario_id=sistemas.id,
                                        cantidad=3, fecha=_hace(10)))
    db.session.commit()

    client.post('/api/sistemas/purgar', headers=_hdr(sistemas),
                json={'tabla': 'movimientos_inventario', 'meses': 24})

    recientes = MovimientoInventario.query.filter(
        MovimientoInventario.fecha >= _hace(30)).count()
    assert recientes == 1


# ── Rastro en la bitácora ────────────────────────────────────────────────────

def test_la_purga_queda_registrada(client, db, sistemas):
    db.session.add(Notificacion(usuario_id=sistemas.id, tipo='X', titulo='v',
                                mensaje='m', leida=True, created_at=_hace(400)))
    db.session.commit()

    client.post('/api/sistemas/purgar', headers=_hdr(sistemas),
                json={'tabla': 'notificaciones', 'meses': 1})

    acciones = [a.action for a in AuditLog.query.all()]
    assert any('purgó' in a and 'Notificaciones' in a for a in acciones)


def test_sin_filas_no_falla(client, sistemas):
    r = client.post('/api/sistemas/purgar', headers=_hdr(sistemas),
                    json={'tabla': 'notificaciones', 'meses': 24})
    assert r.status_code == 200
    assert r.get_json()['borrados'] == 0
