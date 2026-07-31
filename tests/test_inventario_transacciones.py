"""
Invariantes del manejo transaccional de `inventario_api`.

El módulo maneja los fallos esperados de stock con `ErrorDeNegocio`, que el
decorador `@transaccion_de_stock` traduce a una respuesta JSON tras hacer
rollback. Estos tests fijan las dos reglas que sostienen ese esquema:

  1. Un fallo INESPERADO no se disfraza de 4xx: se hace rollback y se re-lanza,
     así un bug real sigue saliendo como 500 y no queda enmascarado.
  2. Las vistas que NO llevan el decorador pero llaman a helpers que levantan
     `ErrorDeNegocio` deben atraparlo ellas mismas. Hoy la única puerta es
     `_resolver_partes` (partes del vale en un movimiento), que lo captura y
     devuelve la respuesta. Si alguien agrega ahí otra llamada a un resolver
     sin cubrirla, estos tests lo cazan: el 4xx se volvería un 500.

Convenciones iguales a `test_inventario_api.py`: JWT real vía `_login`,
fixtures locales y `db` con rollback automático por test.
"""
import uuid

import pytest
from werkzeug.security import generate_password_hash

from app.models import Almacen, Producto, User
from app.routes.api_auth import _encode_access_token


def _login(client, user):
    client.environ_base['HTTP_AUTHORIZATION'] = f'Bearer {_encode_access_token(user)}'


@pytest.fixture
def inv_user(db):
    u = User(username='inv_tx', password_hash=generate_password_hash('Pass123!'),
             role='inventario')
    db.session.add(u)
    db.session.commit()
    return u


@pytest.fixture
def almacen(db):
    a = Almacen(nombre='Bodega TX', qr_code=str(uuid.uuid4()), activo=True)
    db.session.add(a)
    db.session.commit()
    return a


@pytest.fixture
def producto(db):
    p = Producto(codigo='TX-1', descripcion='Producto TX', categoria='TX',
                 unidad='pza', activo=True)
    db.session.add(p)
    db.session.commit()
    return p


class TestErroresEsperadosNoSonQuinientos:
    """Vistas sin @transaccion_de_stock que alcanzan helpers que lo levantan."""

    def test_movimiento_con_trabajador_inexistente_da_422(
        self, client, inv_user, almacen, producto, db,
    ):
        """`_resolver_partes` → `resolver_trabajador_activo` levanta
        ErrorDeNegocio; `create_movimiento` no lleva el decorador, así que debe
        atraparlo y responder 422 (no 500)."""
        _login(client, inv_user)
        resp = client.post('/api/v1/movimientos/', json={
            'tipo': 'ENTRADA',
            'producto_id': producto.id,
            'cantidad': 1,
            'almacen_destino_id': almacen.id,
            'entrega_trabajador_id': 999999,   # no existe
        })
        assert resp.status_code == 422, resp.get_json()
        assert 'no existe' in str(resp.get_json()['detail'])

    def test_movimiento_rapido_con_producto_inexistente_da_404(
        self, client, inv_user, almacen, db,
    ):
        _login(client, inv_user)
        resp = client.post('/api/v1/movimientos/rapido', json={
            'producto_qr': 'NO-EXISTE-XYZ',
            'tipo': 'ENTRADA',
            'cantidad': 1,
        })
        assert resp.status_code == 404, resp.get_json()


class TestTransaccionDeStock:
    """Contrato del decorador que envuelve las vistas que mutan existencias."""

    def test_error_inesperado_se_relanza_y_hace_rollback(self, app):
        """Un bug interno NO debe convertirse en 409/200: se revierte y se
        propaga para que salga como 500 con su traza en el log."""
        from unittest.mock import patch

        from app.routes.inventario_api._core.http import transaccion_de_stock

        @transaccion_de_stock
        def vista_con_bug():
            raise RuntimeError('bug interno')

        with patch('app.routes.inventario_api._core.http.db') as fake_db:
            with pytest.raises(RuntimeError):
                vista_con_bug()
            assert fake_db.session.rollback.called

    def test_error_de_negocio_solo_expone_lo_que_se_le_pasa(self, app):
        """El cuerpo del 4xx no debe arrastrar trazas ni detalles del driver."""
        from app.routes.inventario_api._core.http import ErrorDeNegocio

        exc = ErrorDeNegocio('Stock insuficiente', 409, errores=['TX-1: faltan 3'])
        with app.test_request_context():
            cuerpo, status = exc.como_respuesta()
            datos = cuerpo.get_json()
        assert status == 409
        assert set(datos) == {'detail', 'errores'}
        assert datos['detail'] == 'Stock insuficiente'
        assert 'Traceback' not in str(datos)
