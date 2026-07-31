"""Disponibilidad por bucket para las pantallas de varias líneas.

Estas pantallas —entrega directa, entrega de una solicitud, registrar
movimiento— validaban contra el stock GLOBAL mientras el backend descuenta POR
BUCKET. El síntoma era decir «hay 500» y fallar al guardar con «disponible 60
(proyecto 40 + general 20)».

Los tests fijan las DOS reglas del sistema, nombradas por la regla y no por el
tipo de movimiento:

    con_fallback = proyecto + general   SALIDA, AJUSTE−, entregas
    exacto       = solo el bucket       TRASPASO, REASIGNACION
"""
import pytest
from werkzeug.security import generate_password_hash

from app.models import (
    Almacen, Producto, Proyecto, StockAlmacenProyecto, StockPorAlmacen, User,
)
from app.routes.api_auth import _encode_access_token

URL = '/api/v1/productos/disponibilidad-buckets'


def _login(client, user):
    client.environ_base['HTTP_AUTHORIZATION'] = f'Bearer {_encode_access_token(user)}'


@pytest.fixture
def admin(db):
    u = User(username='db_admin', password_hash=generate_password_hash('Pass123!'), role='admin')
    db.session.add(u); db.session.commit()
    return u


@pytest.fixture
def almacen(db):
    a = Almacen(nombre='Central', qr_code='QR-DB-1', activo=True)
    db.session.add(a); db.session.commit()
    return a


@pytest.fixture
def proyecto(db):
    p = Proyecto(numero_proyecto='DB-001', nombre='Obra', activo=True)
    db.session.add(p); db.session.commit()
    return p


@pytest.fixture
def repartido(db, almacen, proyecto):
    """100 unidades: 40 apartadas al proyecto, 60 libres."""
    p = Producto(codigo='CEM-01', descripcion='Cemento', categoria='Obra', unidad='kg',
                 stock_actual=100, stock_minimo=0, precio_unitario=10)
    db.session.add(p); db.session.flush()
    db.session.add(StockAlmacenProyecto(producto_id=p.id, almacen_id=almacen.id,
                                        proyecto_id=proyecto.id, cantidad=40))
    db.session.add(StockAlmacenProyecto(producto_id=p.id, almacen_id=almacen.id,
                                        proyecto_id=None, cantidad=60))
    db.session.add(StockPorAlmacen(producto_id=p.id, almacen_id=almacen.id, cantidad=100))
    db.session.commit()
    return p


def _pedir(client, producto, almacen, proyecto=None):
    args = {'ids': str(producto.id), 'almacen_id': almacen.id}
    if proyecto is not None:
        args['proyecto_id'] = proyecto.id
    return client.get(URL, query_string=args)


class TestLasDosReglas:

    def test_separa_lo_del_proyecto_de_lo_libre(self, client, admin, almacen, proyecto, repartido):
        _login(client, admin)
        r = _pedir(client, repartido, almacen, proyecto)
        assert r.status_code == 200, r.get_json()
        it = r.get_json()['items'][0]
        assert it['proyecto'] == 40
        assert it['general'] == 60

    def test_con_fallback_suma_las_dos(self, client, admin, almacen, proyecto, repartido):
        """La regla de SALIDA y de las entregas: se agota el bucket del proyecto
        y el resto sale de General."""
        _login(client, admin)
        assert _pedir(client, repartido, almacen, proyecto).get_json()['items'][0]['con_fallback'] == 100

    def test_exacto_ignora_lo_libre(self, client, admin, almacen, proyecto, repartido):
        """La regla de TRASPASO: mueve el bucket del proyecto conservando su
        etiqueta, así que NO puede echar mano de lo libre. Es justo la que la
        interfaz mostraba mal — enseñaba 100 donde solo se pueden mover 40."""
        _login(client, admin)
        assert _pedir(client, repartido, almacen, proyecto).get_json()['items'][0]['exacto'] == 40

    def test_sin_proyecto_ambas_reglas_dan_lo_libre(self, client, admin, almacen, repartido):
        """Sin proyecto no hay bucket propio del que echar mano: las dos reglas
        colapsan en General. Si no, la interfaz sumaría stock ajeno."""
        _login(client, admin)
        it = _pedir(client, repartido, almacen).get_json()['items'][0]
        assert it['proyecto'] == 0
        assert it['general'] == 60
        assert it['con_fallback'] == 60
        assert it['exacto'] == 60

    def test_no_cuenta_lo_de_otras_obras(self, client, db, admin, almacen, proyecto, repartido):
        """Lo apartado a una obra ajena no es disponible para esta, ni con
        fallback: `_consumir_proyecto_luego_general` nunca lo toca."""
        _login(client, admin)
        otra = Proyecto(numero_proyecto='DB-999', nombre='Ajena', activo=True)
        db.session.add(otra); db.session.flush()
        db.session.add(StockAlmacenProyecto(producto_id=repartido.id, almacen_id=almacen.id,
                                            proyecto_id=otra.id, cantidad=500))
        db.session.commit()

        it = _pedir(client, repartido, almacen, proyecto).get_json()['items'][0]
        assert it['con_fallback'] == 100, 'las 500 de la otra obra no son de esta'


class TestConsultaEnLote:

    def test_responde_varios_productos_de_una_vez(self, client, db, admin, almacen, proyecto, repartido):
        """Una petición por renglón haría que una entrega de 40 líneas lanzara
        40 peticiones al abrir el modal."""
        _login(client, admin)
        otro = Producto(codigo='VAR-01', descripcion='Varilla', categoria='Obra',
                        unidad='pz', stock_actual=7, stock_minimo=0, precio_unitario=1)
        db.session.add(otro); db.session.flush()
        db.session.add(StockAlmacenProyecto(producto_id=otro.id, almacen_id=almacen.id,
                                            proyecto_id=None, cantidad=7))
        db.session.commit()

        r = client.get(URL, query_string={'ids': f'{repartido.id},{otro.id}',
                                          'almacen_id': almacen.id,
                                          'proyecto_id': proyecto.id})
        items = r.get_json()['items']
        assert [i['codigo'] for i in items] == ['CEM-01', 'VAR-01']
        assert items[1]['con_fallback'] == 7

    def test_material_sin_existencia_llega_en_cero(self, client, db, admin, almacen, proyecto):
        """Omitirlo dejaría a la interfaz sin saber si es cero o si falló la
        consulta, y acabaría mostrando un hueco en vez de un aviso."""
        _login(client, admin)
        p = Producto(codigo='NADA-01', descripcion='Sin stock', categoria='Obra',
                     unidad='pz', stock_actual=0, stock_minimo=0, precio_unitario=1)
        db.session.add(p); db.session.commit()

        it = _pedir(client, p, almacen, proyecto).get_json()['items'][0]
        assert it['con_fallback'] == 0
        assert it['exacto'] == 0


class TestValidacion:

    def test_exige_ids(self, client, admin, almacen):
        _login(client, admin)
        assert client.get(URL, query_string={'almacen_id': almacen.id}).status_code == 422

    def test_rechaza_ids_no_numericos(self, client, admin, almacen):
        _login(client, admin)
        r = client.get(URL, query_string={'ids': 'a,b', 'almacen_id': almacen.id})
        assert r.status_code == 422

    def test_topa_la_cantidad_de_ids(self, client, admin, almacen):
        """Sin tope, una URL larga podría pedir el catálogo entero de un golpe."""
        _login(client, admin)
        r = client.get(URL, query_string={'ids': ','.join(str(i) for i in range(1, 502)),
                                          'almacen_id': almacen.id})
        assert r.status_code == 422

    def test_almacen_inexistente(self, client, admin, repartido):
        _login(client, admin)
        r = client.get(URL, query_string={'ids': str(repartido.id), 'almacen_id': 999999})
        assert r.status_code == 404


class TestSinBodega:
    """Sin `almacen_id` se suman todas las bodegas activas.

    Es la pregunta de MIS PEDIDOS: una solicitud todavía no elige bodega, así
    que «¿puedo mover esto desde aquí?» no aplica — la pregunta es «¿existe esto
    para mi proyecto, en algún lado?».
    """

    def test_suma_todas_las_bodegas(self, client, db, admin, almacen, proyecto, repartido):
        _login(client, admin)
        otra = Almacen(nombre='Norte', qr_code='QR-DB-2', activo=True)
        db.session.add(otra); db.session.flush()
        db.session.add(StockAlmacenProyecto(producto_id=repartido.id, almacen_id=otra.id,
                                            proyecto_id=proyecto.id, cantidad=5))
        db.session.add(StockAlmacenProyecto(producto_id=repartido.id, almacen_id=otra.id,
                                            proyecto_id=None, cantidad=7))
        db.session.commit()

        it = client.get(URL, query_string={'ids': str(repartido.id),
                                           'proyecto_id': proyecto.id}).get_json()['items'][0]
        assert it['proyecto'] == 45     # 40 + 5
        assert it['general'] == 67      # 60 + 7
        assert it['con_fallback'] == 112

    def test_ignora_bodegas_inactivas(self, client, db, admin, almacen, proyecto, repartido):
        """Stock en una bodega dada de baja no se puede surtir: contarlo haría
        que la solicitud prometiera material inalcanzable."""
        _login(client, admin)
        muerta = Almacen(nombre='Vieja', qr_code='QR-DB-3', activo=False)
        db.session.add(muerta); db.session.flush()
        db.session.add(StockAlmacenProyecto(producto_id=repartido.id, almacen_id=muerta.id,
                                            proyecto_id=None, cantidad=999))
        db.session.commit()

        it = client.get(URL, query_string={'ids': str(repartido.id),
                                           'proyecto_id': proyecto.id}).get_json()['items'][0]
        assert it['general'] == 60, 'la bodega inactiva no cuenta'

    def test_con_bodega_sigue_acotando(self, client, db, admin, almacen, proyecto, repartido):
        """La forma con bodega no cambia: sigue siendo la que valida movimientos."""
        _login(client, admin)
        otra = Almacen(nombre='Norte', qr_code='QR-DB-4', activo=True)
        db.session.add(otra); db.session.flush()
        db.session.add(StockAlmacenProyecto(producto_id=repartido.id, almacen_id=otra.id,
                                            proyecto_id=None, cantidad=999))
        db.session.commit()

        it = _pedir(client, repartido, almacen, proyecto).get_json()['items'][0]
        assert it['general'] == 60, 'no debe sumar la otra bodega'
