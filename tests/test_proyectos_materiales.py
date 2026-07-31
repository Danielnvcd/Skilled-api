"""Tests de Inventario → Proyectos: plan de materiales, consumo y costos +
upsert de importación con precio. Verifica el flujo completo plan → solicitud
ligada al proyecto → aprobación → entrega → panel de detalle."""
import io
import pytest
from werkzeug.security import generate_password_hash

from app.models import User, Almacen, Producto, StockPorAlmacen, StockAlmacenProyecto, Proyecto
from app.routes.api_auth import _encode_access_token


def _login(client, user):
    client.environ_base['HTTP_AUTHORIZATION'] = f'Bearer {_encode_access_token(user)}'


@pytest.fixture
def admin(db):
    u = User(username='pm_admin', password_hash=generate_password_hash('Pass123!'), role='admin')
    db.session.add(u)
    db.session.commit()
    return u


@pytest.fixture
def almacen(db):
    a = Almacen(nombre='Central', qr_code='QR-PM-1', activo=True)
    db.session.add(a)
    db.session.commit()
    return a


@pytest.fixture
def proyecto(db):
    p = Proyecto(numero_proyecto='PM-001', nombre='Obra Demo', activo=True)
    db.session.add(p)
    db.session.commit()
    return p


@pytest.fixture
def producto(db, almacen):
    p = Producto(codigo='CEM-01', descripcion='Cemento', categoria='Obra', unidad='kg',
                 stock_actual=100, stock_minimo=0, precio_unitario=50)
    db.session.add(p)
    db.session.flush()
    db.session.add(StockAlmacenProyecto(producto_id=p.id, almacen_id=almacen.id,
                                        proyecto_id=None, cantidad=100))
    db.session.add(StockPorAlmacen(producto_id=p.id, almacen_id=almacen.id, cantidad=100))
    db.session.commit()
    return p


def test_flujo_plan_consumo_costos(client, db, admin, almacen, proyecto, producto):
    _login(client, admin)

    # 1) Capturar plan: 10 kg planeados.
    r = client.post(f'/api/v1/proyectos-materiales/{proyecto.id}/plan',
                    json={'lineas': [{'producto_id': producto.id, 'cantidad_planeada': 10}]})
    assert r.status_code == 200, r.get_json()

    # 2) Crear solicitud ligada al proyecto por proyecto_id (8 kg).
    r = client.post('/api/v1/solicitudes/', json={
        'proyecto': proyecto.numero_proyecto,
        'proyecto_id': proyecto.id,
        'detalles': [{'tipo_item': 'MATERIAL', 'producto_id': producto.id, 'cantidad_solicitada': 8}],
    })
    assert r.status_code == 200, r.get_json()
    sol = r.get_json()
    assert sol['proyecto_id'] == proyecto.id
    sol_id = sol['id']
    det_id = sol['detalles'][0]['id']

    # 3) Aprobar y entregar 8 kg.
    r = client.patch(f'/api/v1/solicitudes/{sol_id}/estado', json={'estatus': 'APROBADA'})
    assert r.status_code == 200, r.get_json()
    r = client.post(f'/api/v1/solicitudes/{sol_id}/entregar', json={
        'almacen_origen_id': almacen.id,
        'entregas': [{'detalle_id': det_id, 'cantidad_entregada': 8}],
    })
    assert r.status_code == 200, r.get_json()

    # 4) Detalle del proyecto: 8/10 = 80%, diferencia -2, costos a $50.
    r = client.get(f'/api/v1/proyectos-materiales/{proyecto.id}')
    assert r.status_code == 200
    data = r.get_json()
    fila = next(m for m in data['materiales'] if m['producto_id'] == producto.id)
    assert fila['cantidad_planeada'] == 10
    assert fila['cantidad_consumida'] == 8
    assert fila['porcentaje_consumido'] == 80.0
    assert fila['diferencia'] == -2          # se ocupó menos que el plan
    assert fila['costo_planeado'] == 500.0   # 10 × 50
    assert fila['costo_consumido'] == 400.0  # 8 × 50
    tot = data['totales']
    assert tot['costo_consumido'] == 400.0
    assert tot['sobre_presupuesto'] is False

    # 5) Resumen general lista el proyecto con su % de consumo.
    r = client.get('/api/v1/proyectos-materiales/')
    assert r.status_code == 200
    resumen = {p['id']: p for p in r.get_json()}
    assert resumen[proyecto.id]['porcentaje_consumido'] == 80.0


def test_pedidos_del_proyecto(client, db, admin, almacen, proyecto, producto):
    """El endpoint de pedidos lista las solicitudes ligadas al proyecto con su detalle."""
    _login(client, admin)

    # Crear una solicitud ligada al proyecto.
    r = client.post('/api/v1/solicitudes/', json={
        'proyecto': proyecto.numero_proyecto,
        'proyecto_id': proyecto.id,
        'detalles': [{'tipo_item': 'MATERIAL', 'producto_id': producto.id, 'cantidad_solicitada': 5}],
    })
    assert r.status_code == 200, r.get_json()
    sol_id = r.get_json()['id']

    # Listar pedidos del proyecto.
    r = client.get(f'/api/v1/proyectos-materiales/{proyecto.id}/pedidos')
    assert r.status_code == 200, r.get_json()
    pedidos = r.get_json()
    assert len(pedidos) == 1
    assert pedidos[0]['id'] == sol_id
    assert pedidos[0]['proyecto_id'] == proyecto.id
    assert pedidos[0]['estatus'] == 'PENDIENTE'
    assert pedidos[0]['detalles'][0]['item_codigo'] == 'CEM-01'
    assert pedidos[0]['detalles'][0]['cantidad_solicitada'] == 5.0

    # El PDF de esa solicitud responde.
    r = client.get(f'/api/v1/solicitudes/{sol_id}/pdf')
    assert r.status_code == 200
    assert r.mimetype == 'application/pdf'


def test_proyectos_materiales_acceso_por_rol(client, db, admin, almacen, proyecto, producto):
    """Acceso y scoping por dueño del panel de materiales por proyecto:

    - `solicitante_material` NO puede leerlo (evita ver planes/costos/pedidos de
      otros vía API directa — IDOR).
    - `coordinador` SÍ, pero SOLO en SUS proyectos (`Proyecto.coordinador_id`):
      lee y escribe el plan de los propios y recibe 403 en los ajenos.
    - inventario/admin ven y editan todos.
    """
    solicitante = User(username='sol_x', password_hash=generate_password_hash('Pass123!'),
                       role='solicitante_material')
    coordinador = User(username='coord_x', password_hash=generate_password_hash('Pass123!'),
                       role='coordinador')
    otro_coord = User(username='coord_y', password_hash=generate_password_hash('Pass123!'),
                      role='coordinador')
    db.session.add_all([solicitante, coordinador, otro_coord])
    db.session.commit()

    # `proyecto` (PM-001) es del coordinador; `ajeno` (PM-002) es de otro.
    proyecto.coordinador_id = coordinador.id
    ajeno = Proyecto(numero_proyecto='PM-002', nombre='Ajeno', activo=True,
                     coordinador_id=otro_coord.id)
    db.session.add(ajeno)
    db.session.commit()

    rutas_propias = [
        '/api/v1/proyectos-materiales/',
        '/api/v1/proyectos-materiales/proyectos',
        f'/api/v1/proyectos-materiales/{proyecto.id}',
        f'/api/v1/proyectos-materiales/{proyecto.id}/historial',
        f'/api/v1/proyectos-materiales/{proyecto.id}/pedidos',
    ]

    # Solicitante de material: bloqueado en todo el panel.
    _login(client, solicitante)
    for ruta in rutas_propias:
        r = client.get(ruta)
        assert r.status_code == 403, f'solicitante_material pudo acceder a {ruta}: {r.status_code}'

    # Coordinador: lectura permitida en SUS rutas.
    _login(client, coordinador)
    for ruta in rutas_propias:
        r = client.get(ruta)
        assert r.status_code == 200, f'coordinador NO pudo acceder a {ruta}: {r.status_code}'

    # El selector "crear/abrir plan" solo lista SUS proyectos.
    ids_selector = {p['id'] for p in client.get('/api/v1/proyectos-materiales/proyectos').get_json()}
    assert proyecto.id in ids_selector
    assert ajeno.id not in ids_selector

    # Coordinador puede ESCRIBIR el plan de SU proyecto (seleccionar materiales).
    r = client.post(f'/api/v1/proyectos-materiales/{proyecto.id}/plan',
                    json={'lineas': [{'producto_id': producto.id, 'cantidad_planeada': 5}]})
    assert r.status_code == 200, r.get_json()

    # ...pero NO puede leer ni escribir el plan de un proyecto AJENO (403).
    assert client.get(f'/api/v1/proyectos-materiales/{ajeno.id}').status_code == 403
    assert client.get(f'/api/v1/proyectos-materiales/{ajeno.id}/pedidos').status_code == 403
    r = client.post(f'/api/v1/proyectos-materiales/{ajeno.id}/plan',
                    json={'lineas': [{'producto_id': producto.id, 'cantidad_planeada': 3}]})
    assert r.status_code == 403

    # Aunque el ajeno tenga plan (lo captura el admin), no aparece en el resumen
    # del coordinador.
    _login(client, admin)
    client.post(f'/api/v1/proyectos-materiales/{ajeno.id}/plan',
                json={'lineas': [{'producto_id': producto.id, 'cantidad_planeada': 9}]})
    _login(client, coordinador)
    ids_resumen = {p['id'] for p in client.get('/api/v1/proyectos-materiales/').get_json()}
    assert proyecto.id in ids_resumen      # el suyo (ya tiene plan)
    assert ajeno.id not in ids_resumen     # el ajeno NO, aunque tenga plan

    # El admin (rol inventario-admin) sí accede a todo, incluido el ajeno.
    _login(client, admin)
    assert client.get(f'/api/v1/proyectos-materiales/{ajeno.id}/pedidos').status_code == 200


def test_coordinador_solo_solicita_para_sus_proyectos(client, db, almacen, proyecto, producto):
    """El coordinador solo puede crear solicitudes de material ligadas a SUS
    proyectos. Enforcement de backend (no solo el filtro del selector del SPA):
    intentar el proyecto de otro coordinador vía API responde 403."""
    coordinador = User(username='coord_sol', password_hash=generate_password_hash('Pass123!'),
                       role='coordinador')
    otro_coord = User(username='coord_otro', password_hash=generate_password_hash('Pass123!'),
                      role='coordinador')
    db.session.add_all([coordinador, otro_coord])
    db.session.commit()

    proyecto.coordinador_id = coordinador.id
    ajeno = Proyecto(numero_proyecto='PM-900', nombre='Ajeno', activo=True,
                     coordinador_id=otro_coord.id)
    db.session.add(ajeno)
    db.session.commit()

    def _payload(proy):
        return {
            'proyecto': proy.numero_proyecto,
            'proyecto_id': proy.id,
            'detalles': [{'tipo_item': 'MATERIAL', 'producto_id': producto.id, 'cantidad_solicitada': 1}],
        }

    _login(client, coordinador)

    # Su propio proyecto: OK.
    r = client.post('/api/v1/solicitudes/', json=_payload(proyecto))
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['proyecto_id'] == proyecto.id

    # Proyecto de otro coordinador (por id): 403.
    r = client.post('/api/v1/solicitudes/', json=_payload(ajeno))
    assert r.status_code == 403, r.get_json()

    # Aunque intente saltarse el id y mande solo el texto del proyecto ajeno: 403.
    r = client.post('/api/v1/solicitudes/', json={
        'proyecto': ajeno.numero_proyecto,
        'detalles': [{'tipo_item': 'MATERIAL', 'producto_id': producto.id, 'cantidad_solicitada': 1}],
    })
    assert r.status_code == 403, r.get_json()


def test_plan_decimales_segun_unidad(client, db, admin, almacen, proyecto, producto):
    """Unidades contables (pza) rechazan decimales; unidades medibles (kg) los aceptan."""
    _login(client, admin)

    # `producto` es kg: 2.5 es válido.
    r = client.post(f'/api/v1/proyectos-materiales/{proyecto.id}/plan',
                    json={'lineas': [{'producto_id': producto.id, 'cantidad_planeada': 2.5}]})
    assert r.status_code == 200, r.get_json()

    # Producto en piezas: 3.5 debe rechazarse con 422.
    tornillo = Producto(codigo='TOR-01', descripcion='Tornillo', categoria='Obra', unidad='pza',
                        stock_actual=100, stock_minimo=0, precio_unitario=2)
    db.session.add(tornillo)
    db.session.commit()

    r = client.post(f'/api/v1/proyectos-materiales/{proyecto.id}/plan',
                    json={'lineas': [{'producto_id': tornillo.id, 'cantidad_planeada': 3.5}]})
    assert r.status_code == 422, r.get_json()
    assert 'entera' in r.get_json()['detail']

    # En piezas, un entero (4) sí pasa.
    r = client.post(f'/api/v1/proyectos-materiales/{proyecto.id}/plan',
                    json={'lineas': [{'producto_id': tornillo.id, 'cantidad_planeada': 4}]})
    assert r.status_code == 200, r.get_json()


def test_historial_registra_cambios_del_plan(client, db, admin, almacen, proyecto, producto):
    """Cada guardado con cambios deja una entrada en el historial con su desglose."""
    _login(client, admin)

    # 1) Primer guardado: agrega el material (10 kg).
    r = client.post(f'/api/v1/proyectos-materiales/{proyecto.id}/plan',
                    json={'lineas': [{'producto_id': producto.id, 'cantidad_planeada': 10}]})
    assert r.status_code == 200, r.get_json()

    # 2) Segundo guardado: modifica la cantidad (10 → 15).
    r = client.post(f'/api/v1/proyectos-materiales/{proyecto.id}/plan',
                    json={'lineas': [{'producto_id': producto.id, 'cantidad_planeada': 15}]})
    assert r.status_code == 200, r.get_json()

    # 3) Tercer guardado sin cambios: NO debe crear entrada.
    r = client.post(f'/api/v1/proyectos-materiales/{proyecto.id}/plan',
                    json={'lineas': [{'producto_id': producto.id, 'cantidad_planeada': 15}]})
    assert r.status_code == 200, r.get_json()

    # 4) Historial: 2 entradas, más reciente primero (la modificación).
    r = client.get(f'/api/v1/proyectos-materiales/{proyecto.id}/historial')
    assert r.status_code == 200, r.get_json()
    hist = r.get_json()
    assert len(hist) == 2

    modif = hist[0]
    assert modif['usuario'] == 'pm_admin'
    assert modif['n_modificados'] == 1
    assert modif['cambios']['modificados'][0]['antes'] == 10.0
    assert modif['cambios']['modificados'][0]['despues'] == 15.0

    alta = hist[1]
    assert alta['n_agregados'] == 1
    assert alta['cambios']['agregados'][0]['codigo'] == 'CEM-01'
    assert alta['cambios']['agregados'][0]['cantidad'] == 10.0

    # 5) Cambiar SOLO las notas (cantidad igual) también se registra.
    r = client.post(f'/api/v1/proyectos-materiales/{proyecto.id}/plan',
                    json={'lineas': [{'producto_id': producto.id, 'cantidad_planeada': 15, 'notas': 'Comprar con proveedor X'}]})
    assert r.status_code == 200, r.get_json()
    hist = client.get(f'/api/v1/proyectos-materiales/{proyecto.id}/historial').get_json()
    assert len(hist) == 3
    solo_notas = hist[0]['cambios']['modificados'][0]
    assert 'antes' not in solo_notas                 # la cantidad no cambió
    assert solo_notas['notas_antes'] == ''
    assert solo_notas['notas_despues'] == 'Comprar con proveedor X'


def test_import_upsert_actualiza_sin_pisar_stock(client, db, admin, almacen):
    """Reimportar un SKU existente actualiza descripción/precio pero NO el stock."""
    _login(client, admin)
    import openpyxl

    # Producto previo con stock 100 y precio 10.
    p = Producto(codigo='UPS-01', descripcion='Viejo', categoria='Cat', unidad='pza',
                 stock_actual=100, stock_minimo=5, precio_unitario=10)
    db.session.add(p)
    db.session.commit()
    pid = p.id

    # Excel con headers oficiales y una fila que actualiza UPS-01.
    wb = openpyxl.Workbook()
    ws = wb.active
    headers = ['Código (SKU)', 'Descripción', 'Categoría', 'Unidad',
               'Stock Inicial', 'Stock Mínimo', 'Precio Unitario', 'URL Imagen (opcional)']
    ws.append(headers)
    ws.append(['UPS-01', 'Nuevo nombre', 'Cat', 'pza', 999, 7, 25, ''])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    r = client.post('/api/v1/productos/importar',
                    data={'archivo': (buf, 'plantilla.xlsx')},
                    content_type='multipart/form-data')
    assert r.status_code == 200, r.get_json()
    res = r.get_json()
    assert res['exitosos'] == 0
    assert res['actualizados'] == 1

    db.session.expire_all()
    actualizado = db.session.get(Producto, pid)
    assert actualizado.descripcion == 'Nuevo nombre'
    assert float(actualizado.precio_unitario) == 25.0
    assert float(actualizado.stock_minimo) == 7.0
    # Stock NO se toca aunque el Excel traía 999.
    assert float(actualizado.stock_actual) == 100.0


# ═══════════════════════════════════════════════════════════════════════════════
# EXISTENCIAS FÍSICAS DEL PROYECTO
# ═══════════════════════════════════════════════════════════════════════════════

class TestExistenciasDelProyecto:
    """Material que está guardado AHORA a nombre del proyecto.

    Es una pregunta distinta de la que responde el detalle (planeado vs.
    consumido): esto lee `stock_almacen_proyecto`, la existencia física. Antes
    no había forma de consultarlo salvo entrando bodega por bodega.
    """

    def _apartar(self, db, producto, almacen, proyecto, cantidad):
        """Pone `cantidad` del producto en el bucket del proyecto."""
        db.session.add(StockAlmacenProyecto(
            producto_id=producto.id, almacen_id=almacen.id,
            proyecto_id=proyecto.id, cantidad=cantidad,
        ))
        db.session.commit()

    def test_lista_el_material_apartado(self, client, db, admin, almacen, proyecto, producto):
        _login(client, admin)
        self._apartar(db, producto, almacen, proyecto, 30)

        r = client.get(f'/api/v1/proyectos-materiales/{proyecto.id}/existencias')
        assert r.status_code == 200, r.get_json()
        d = r.get_json()
        assert d['totales']['materiales'] == 1
        assert d['totales']['unidades'] == 30
        fila = d['materiales'][0]
        assert fila['codigo'] == 'CEM-01'
        assert fila['total'] == 30
        # precio 50 × 30 unidades
        assert d['totales']['valor'] == 1500

    def test_no_incluye_el_stock_general(self, client, db, admin, almacen, proyecto, producto):
        """El fixture deja 100 en el bucket GENERAL. Ese stock no es de ningún
        proyecto y no debe aparecer aquí — es justamente la distinción que da
        sentido a la vista."""
        _login(client, admin)
        self._apartar(db, producto, almacen, proyecto, 30)

        r = client.get(f'/api/v1/proyectos-materiales/{proyecto.id}/existencias')
        assert r.get_json()['totales']['unidades'] == 30, 'se coló el stock general'

    def test_desglosa_por_bodega(self, client, db, admin, almacen, proyecto, producto):
        otra = Almacen(nombre='Sucursal', qr_code='QR-PM-2', activo=True)
        db.session.add(otra); db.session.commit()
        self._apartar(db, producto, almacen, proyecto, 30)
        self._apartar(db, producto, otra, proyecto, 20)
        _login(client, admin)

        r = client.get(f'/api/v1/proyectos-materiales/{proyecto.id}/existencias')
        d = r.get_json()
        assert {a['nombre'] for a in d['almacenes']} == {'Central', 'Sucursal'}
        fila = d['materiales'][0]
        assert fila['total'] == 50
        assert sum(fila['por_almacen'].values()) == 50

    def test_solo_lista_bodegas_con_existencia(self, client, db, admin, almacen, proyecto, producto):
        """Una bodega sin material de este proyecto no debe generar columna:
        llenaría la tabla de columnas vacías."""
        db.session.add(Almacen(nombre='Vacia', qr_code='QR-PM-3', activo=True))
        db.session.commit()
        self._apartar(db, producto, almacen, proyecto, 10)
        _login(client, admin)

        r = client.get(f'/api/v1/proyectos-materiales/{proyecto.id}/existencias')
        assert [a['nombre'] for a in r.get_json()['almacenes']] == ['Central']

    def test_sin_plan_la_cobertura_es_null_no_cero(self, client, db, admin, almacen, proyecto, producto):
        """`null` significa «no había plan para esto», que no es lo mismo que
        0 % de avance. La interfaz los distingue, así que el API también."""
        _login(client, admin)
        self._apartar(db, producto, almacen, proyecto, 10)

        r = client.get(f'/api/v1/proyectos-materiales/{proyecto.id}/existencias')
        fila = r.get_json()['materiales'][0]
        assert fila['cantidad_planeada'] == 0
        assert fila['cobertura'] is None

    def test_con_plan_calcula_la_cobertura(self, client, db, admin, almacen, proyecto, producto):
        _login(client, admin)
        client.post(f'/api/v1/proyectos-materiales/{proyecto.id}/plan',
                    json={'lineas': [{'producto_id': producto.id, 'cantidad_planeada': 40}]})
        self._apartar(db, producto, almacen, proyecto, 10)

        r = client.get(f'/api/v1/proyectos-materiales/{proyecto.id}/existencias')
        fila = r.get_json()['materiales'][0]
        assert fila['cantidad_planeada'] == 40
        assert fila['cobertura'] == 25.0   # 10 de 40

    def test_proyecto_sin_material_devuelve_vacio(self, client, db, admin, proyecto):
        _login(client, admin)
        r = client.get(f'/api/v1/proyectos-materiales/{proyecto.id}/existencias')
        assert r.status_code == 200
        assert r.get_json()['materiales'] == []
        assert r.get_json()['totales']['unidades'] == 0

    def test_proyecto_inexistente_404(self, client, admin):
        _login(client, admin)
        assert client.get('/api/v1/proyectos-materiales/999999/existencias').status_code == 404

    def test_requiere_permiso(self, client, db, proyecto):
        u = User(username='pm_forastero', password_hash=generate_password_hash('Pass123!'),
                 role='solicitante_material')
        db.session.add(u); db.session.commit()
        _login(client, u)
        r = client.get(f'/api/v1/proyectos-materiales/{proyecto.id}/existencias')
        assert r.status_code == 403
