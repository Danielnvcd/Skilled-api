"""Tests del API `/api/hikvision/*` — integración con lectores biométricos.

Cobertura:
  - CRUD de dispositivos (alta, edición, baja, duplicados)
  - La contraseña NUNCA sale en una respuesta
  - Validación anti-SSRF del host (solo red local)
  - Listado de candidatos: solo `es_oficina`, activos y sin baja
  - Sincronización: rechaza sin foto y a quien no es de oficina AUNQUE el
    cliente mande su id a mano (la interfaz no es la defensa)
  - Un empleado que falla no cancela la tanda de los demás
  - Cambiar la foto desde el lector: primero la acepta el equipo, luego el ERP
  - Detección de empleados desactualizados (foto, nombre o número cambiados)
  - Estado del equipo, ajuste de reloj, auditoría ERP vs. equipo y actividad
  - 401 sin token, 403 por rol

El equipo real no se toca: `ClienteHikvision` se sustituye por un doble.
"""
from datetime import date, timedelta, timezone

import pytest
from werkzeug.security import generate_password_hash

from app.extensions import db as flask_db
from app.models import DispositivoHikvision, SyncEmpleadoHikvision, Trabajador, User
from app.routes.api_auth import _encode_access_token


def _hdr(user):
    return {'Authorization': f'Bearer {_encode_access_token(user)}'}


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def sin_red(monkeypatch):
    """Ninguna prueba habla con un lector de verdad.

    El fixture `lector` usa la IP del equipo real de la oficina; sin esto, una
    prueba mal aislada le mandaría credenciales falsas y cada intento cuenta
    para el bloqueo de la cuenta. Cualquier petición que no pase por un doble
    revienta aquí con un mensaje claro.
    """
    import httpx
    from app.services.hikvision.client import ClienteHikvision

    def prohibido(request):
        raise AssertionError(f'La prueba intentó hablar con un lector real: {request.url}')

    monkeypatch.setattr(ClienteHikvision, 'transporte', httpx.MockTransport(prohibido))


@pytest.fixture
def admin(db):
    u = User(username='hik_admin', password_hash=generate_password_hash('Pass123!'), role='admin')
    db.session.add(u); db.session.commit()
    return u


@pytest.fixture
def coord(db):
    u = User(username='hik_coord', password_hash=generate_password_hash('Pass123!'),
             role='coordinador')
    db.session.add(u); db.session.commit()
    return u


@pytest.fixture
def lector(db):
    d = DispositivoHikvision(nombre='Lector Oficina', host='192.168.1.159', puerto=80,
                             usuario='admin', password='secreto-de-prueba')
    db.session.add(d); db.session.commit()
    return d


def _trab(db, no, nombre, *, oficina=True, foto='perfiles/x.webp', activo=True, baja=None):
    t = Trabajador(no_empleado=no, nombre=nombre, nombre_apellidos='Prueba',
                   es_oficina=oficina, foto_perfil=foto, activo=activo, fecha_baja=baja)
    db.session.add(t); db.session.commit()
    return t


class ClienteFalso:
    """Doble de `ClienteHikvision`. `fallar_en` simula un empleado problemático."""

    creados: list = []
    rostros: list = []
    eliminados: list = []
    fallar_en: set = set()
    fallar_rostro: set = set()

    def __init__(self, *a, **kw):
        pass

    @classmethod
    def desde_dispositivo(cls, d):
        return cls()

    def __enter__(self):
        return self

    def __exit__(self, *e):
        return None

    def capacidades_usuario(self):
        return {'employee_no_max': 32, 'nombre_max': 128}

    def info_dispositivo(self):
        return {'modelo': 'DS-K1T342MFWX-E1', 'numero_serie': 'SN123',
                'firmware': 'V3.16.1', 'nombre_dispositivo': 'Access Controller',
                'mac': '00:11:22:33:44:55', 'tipo': 'ACS'}

    def hora_dispositivo(self):
        return {'modo': 'manual', 'hora_local': '2026-09-22T19:00:00-06:00',
                'zona': 'CST+6:00:00'}

    aperturas: list = []


@pytest.fixture
def cliente_falso(monkeypatch):
    """Sustituye el cliente y las operaciones del servicio en los módulos de ruta."""
    from app.routes.api_hikvision import dispositivos as mod_disp
    from app.routes.api_hikvision import sincronizacion as mod_sync
    from app.services.hikvision.errores import ErrorFoto

    ClienteFalso.creados, ClienteFalso.rostros = [], []
    ClienteFalso.eliminados, ClienteFalso.fallar_en = [], set()
    ClienteFalso.fallar_rostro = set()
    ClienteFalso.aperturas = []

    from app.routes.api_hikvision import puerta as mod_puerta
    monkeypatch.setattr(mod_puerta, 'ClienteHikvision', ClienteFalso)
    monkeypatch.setattr(mod_puerta.svc_puertas, 'abrir',
                        lambda cli, puerta=1: ClienteFalso.aperturas.append(puerta))
    monkeypatch.setattr(mod_puerta.svc_puertas, 'parametros', lambda cli, puerta=1: {
        'nombre': '', 'segundos_apertura': 5, 'segundos_apertura_discapacidad': 15,
        'tipo_magnetico': 'alwaysClose', 'tipo_boton': 'alwaysOpen',
    })
    monkeypatch.setattr(mod_puerta.svc_puertas, 'estado', lambda cli: {
        'puerta': 'Normal', 'cerradura': 'Cerrada', 'magnetico': 'Cerrado',
        'lector_en_linea': True, 'red': 'connect', 'antisabotaje': 'close',
    })

    monkeypatch.setattr(mod_disp, 'ClienteHikvision', ClienteFalso)
    monkeypatch.setattr(mod_sync, 'ClienteHikvision', ClienteFalso)
    from app.services.hikvision import sincronizar as svc_sincronizar
    monkeypatch.setattr(svc_sincronizar, 'ClienteHikvision', ClienteFalso)

    def crear_o_actualizar(cli, t, emp, **kw):
        ClienteFalso.creados.append(emp)
        return 'creado'

    def preparar_foto(t, datos=None):
        if t.no_empleado in ClienteFalso.fallar_en:
            raise ErrorFoto('La fotografía no permite reconocimiento facial.')
        return b'jpeg-falso', 'hash-falso'

    def subir_rostro(cli, emp, jpeg):
        # Simula que el MOTOR FACIAL del equipo rechaza la cara: pasa cuando la
        # imagen es válida como archivo pero no contiene un rostro modelable.
        if emp in ClienteFalso.fallar_rostro:
            raise ErrorFoto('La fotografía no permite reconocimiento facial.')
        ClienteFalso.rostros.append(emp)

    monkeypatch.setattr(mod_sync.svc_usuarios, 'crear_o_actualizar', crear_o_actualizar)
    monkeypatch.setattr(mod_sync.svc_usuarios, 'preparar_foto', preparar_foto)
    monkeypatch.setattr(mod_sync.svc_usuarios, 'subir_rostro', subir_rostro)
    monkeypatch.setattr(mod_sync.svc_usuarios, 'tiene_rostro', lambda cli, emp: True)
    monkeypatch.setattr(mod_sync.svc_usuarios, 'eliminar',
                        lambda cli, emp: ClienteFalso.eliminados.append(emp))
    return ClienteFalso


# ─── Permisos ─────────────────────────────────────────────────────────────────

def test_sin_token_es_401(client):
    assert client.get('/api/hikvision/dispositivos').status_code == 401


def test_coordinador_no_entra(client, coord):
    r = client.get('/api/hikvision/dispositivos', headers=_hdr(coord))
    assert r.status_code == 403


# ─── CRUD de dispositivos ─────────────────────────────────────────────────────

def test_alta_y_password_nunca_sale(client, admin, db):
    r = client.post('/api/hikvision/dispositivos', headers=_hdr(admin), json={
        'nombre': 'Lector Puebla', 'host': '192.168.1.159', 'puerto': 80,
        'usuario': 'admin', 'password': 'sup3r-secreta',
    })
    assert r.status_code == 201
    cuerpo = r.get_json()
    assert cuerpo['tiene_password'] is True
    assert 'password' not in cuerpo
    assert 'sup3r-secreta' not in r.get_data(as_text=True)

    # Y en la BD queda cifrada, no en claro.
    crudo = db.session.execute(
        flask_db.text('SELECT password FROM hikvision_dispositivos WHERE id=:i'),
        {'i': cuerpo['id']},
    ).scalar()
    assert crudo != 'sup3r-secreta'


def test_host_publico_rechazado(client, admin):
    """Anti-SSRF: el ERP no debe poder usarse para sondear internet."""
    r = client.post('/api/hikvision/dispositivos', headers=_hdr(admin), json={
        'nombre': 'Falso', 'host': '8.8.8.8', 'usuario': 'admin', 'password': 'x',
    })
    assert r.status_code == 422


def test_metadatos_de_nube_rechazados(client, admin):
    r = client.post('/api/hikvision/dispositivos', headers=_hdr(admin), json={
        'nombre': 'Falso', 'host': '169.254.169.254', 'usuario': 'admin', 'password': 'x',
    })
    assert r.status_code == 422


def test_host_duplicado_es_409(client, admin, lector):
    r = client.post('/api/hikvision/dispositivos', headers=_hdr(admin), json={
        'nombre': 'Otro', 'host': lector.host, 'puerto': lector.puerto,
        'usuario': 'admin', 'password': 'x',
    })
    assert r.status_code == 409


def test_editar_sin_password_conserva_la_anterior(client, admin, lector, db):
    r = client.put(f'/api/hikvision/dispositivos/{lector.id}', headers=_hdr(admin),
                   json={'nombre': 'Renombrado', 'password': ''})
    assert r.status_code == 200
    db.session.refresh(lector)
    assert lector.nombre == 'Renombrado'
    assert lector.password == 'secreto-de-prueba'


def test_probar_conexion_cachea_datos_del_equipo(client, admin, lector, db, cliente_falso):
    r = client.post(f'/api/hikvision/dispositivos/{lector.id}/probar', headers=_hdr(admin))
    assert r.status_code == 200
    assert r.get_json()['ok'] is True
    db.session.refresh(lector)
    assert lector.modelo == 'DS-K1T342MFWX-E1'
    assert lector.ultimo_estado == 'OK'


# ─── Candidatos ───────────────────────────────────────────────────────────────

def test_solo_aparecen_empleados_de_oficina(client, admin, lector, db):
    _trab(db, 'OF1', 'Ana')                              # oficina, con foto
    _trab(db, 'OB1', 'Beto', oficina=False)              # obra → no aparece
    _trab(db, 'OF2', 'Cris', foto='')                    # oficina, sin foto
    _trab(db, 'OF3', 'Dani', activo=False)               # inactivo → no aparece
    _trab(db, 'OF4', 'Eva', baja=date(2026, 1, 1))       # con baja → no aparece

    r = client.get(f'/api/hikvision/dispositivos/{lector.id}/empleados', headers=_hdr(admin))
    assert r.status_code == 200
    cuerpo = r.get_json()
    nums = {i['no_empleado'] for i in cuerpo['items']}
    assert nums == {'OF1', 'OF2'}
    assert cuerpo['resumen'] == {'total': 2, 'con_foto': 1, 'sin_foto': 1,
                                 'sincronizados': 0, 'desactualizados': 0, 'con_error': 0}

    sin_foto = next(i for i in cuerpo['items'] if i['no_empleado'] == 'OF2')
    assert sin_foto['sincronizable'] is False
    assert sin_foto['motivo_no_sincronizable'] == 'Sin fotografía de perfil'


# ─── Sincronización ───────────────────────────────────────────────────────────

def test_sincroniza_y_guarda_estado(client, admin, lector, db, cliente_falso):
    t = _trab(db, 'OF1', 'Ana')
    r = client.post(f'/api/hikvision/dispositivos/{lector.id}/sincronizar',
                    headers=_hdr(admin), json={'trabajador_ids': [t.id]})
    assert r.status_code == 200
    cuerpo = r.get_json()
    assert cuerpo['ok'] is True
    assert cuerpo['resumen'] == {'sincronizados': 1, 'fallidos': 0, 'total': 1}
    assert cliente_falso.creados == ['OF1'] and cliente_falso.rostros == ['OF1']

    fila = SyncEmpleadoHikvision.query.filter_by(trabajador_id=t.id).one()
    assert fila.estado == 'SINCRONIZADO'
    assert fila.employee_no_remoto == 'OF1'
    assert fila.hash_foto and fila.hash_datos


def test_backend_rechaza_empleado_sin_foto_aunque_el_cliente_insista(
        client, admin, lector, db, cliente_falso):
    """La validación del frontend no es la defensa: esta petición la imita un curl."""
    t = _trab(db, 'OF2', 'Cris', foto='')
    r = client.post(f'/api/hikvision/dispositivos/{lector.id}/sincronizar',
                    headers=_hdr(admin), json={'trabajador_ids': [t.id]})
    assert r.status_code == 200
    res = r.get_json()['resultados'][0]
    assert res['ok'] is False and res['estado'] == 'SIN_FOTO'
    # Y nunca se llamó al equipo por esta persona.
    assert cliente_falso.creados == []


def test_backend_rechaza_a_quien_no_es_de_oficina(client, admin, lector, db, cliente_falso):
    t = _trab(db, 'OB1', 'Beto', oficina=False)
    r = client.post(f'/api/hikvision/dispositivos/{lector.id}/sincronizar',
                    headers=_hdr(admin), json={'trabajador_ids': [t.id]})
    assert r.status_code == 200
    res = r.get_json()['resultados'][0]
    assert res['ok'] is False and res['estado'] == 'RECHAZADO'
    assert cliente_falso.creados == []


def test_un_fallo_no_detiene_a_los_demas(client, admin, lector, db, cliente_falso):
    a = _trab(db, 'OF1', 'Ana')
    b = _trab(db, 'OF2', 'Beto')     # este falla al preparar la foto
    c = _trab(db, 'OF3', 'Cris')
    cliente_falso.fallar_en = {'OF2'}

    r = client.post(f'/api/hikvision/dispositivos/{lector.id}/sincronizar',
                    headers=_hdr(admin), json={'trabajador_ids': [a.id, b.id, c.id]})
    assert r.status_code == 200
    cuerpo = r.get_json()
    assert cuerpo['ok'] is False
    assert cuerpo['resumen'] == {'sincronizados': 2, 'fallidos': 1, 'total': 3}
    # Los otros dos SÍ llegaron al equipo.
    assert sorted(cliente_falso.creados) == ['OF1', 'OF3']

    por_num = {r_['no_empleado']: r_ for r_ in cuerpo['resultados']}
    assert por_num['OF2']['error']
    assert SyncEmpleadoHikvision.query.filter_by(trabajador_id=b.id).one().estado == 'ERROR'


def test_si_falla_el_rostro_no_queda_usuario_huerfano(client, admin, lector, db, cliente_falso):
    """Regresión: el alta se revierte si el equipo no puede modelar la cara.

    Se detectó contra hardware real. `preparar_foto` puede tener éxito (la
    imagen se lee y se convierte) y aun así el motor facial del lector rechazar
    el rostro — pero eso ocurre DESPUÉS del alta, así que sin reversión quedaba
    un usuario sin cara dentro del equipo: inútil en un lector facial y ruido
    al auditar quién está dado de alta.
    """
    t = _trab(db, 'OF1', 'Ana')
    cliente_falso.fallar_rostro = {'OF1'}

    r = client.post(f'/api/hikvision/dispositivos/{lector.id}/sincronizar',
                    headers=_hdr(admin), json={'trabajador_ids': [t.id]})
    assert r.status_code == 200
    res = r.get_json()['resultados'][0]
    assert res['ok'] is False

    # Se creó y se borró: el equipo queda como estaba.
    assert cliente_falso.creados == ['OF1']
    assert cliente_falso.eliminados == ['OF1']
    assert SyncEmpleadoHikvision.query.filter_by(trabajador_id=t.id).one().estado == 'ERROR'


def test_si_ya_existia_no_se_borra_al_fallar_el_rostro(client, admin, lector, db,
                                                       cliente_falso, monkeypatch):
    """Contrapeso: una foto nueva mala NO debe dejar fuera a quien ya funcionaba."""
    from app.routes.api_hikvision import sincronizacion as mod_sync
    monkeypatch.setattr(mod_sync.svc_usuarios, 'crear_o_actualizar',
                        lambda cli, t, emp, **kw: 'actualizado')
    t = _trab(db, 'OF1', 'Ana')
    cliente_falso.fallar_rostro = {'OF1'}

    r = client.post(f'/api/hikvision/dispositivos/{lector.id}/sincronizar',
                    headers=_hdr(admin), json={'trabajador_ids': [t.id]})
    assert r.status_code == 200
    assert r.get_json()['resultados'][0]['ok'] is False
    assert cliente_falso.eliminados == []   # su registro anterior sigue intacto


def test_lista_vacia_es_422(client, admin, lector):
    r = client.post(f'/api/hikvision/dispositivos/{lector.id}/sincronizar',
                    headers=_hdr(admin), json={'trabajador_ids': []})
    assert r.status_code == 422


def test_lector_desactivado_no_sincroniza(client, admin, lector, db, cliente_falso):
    lector.activo = False
    db.session.commit()
    t = _trab(db, 'OF1', 'Ana')
    r = client.post(f'/api/hikvision/dispositivos/{lector.id}/sincronizar',
                    headers=_hdr(admin), json={'trabajador_ids': [t.id]})
    assert r.status_code == 409


def test_quitar_usa_el_numero_que_conoce_el_equipo(client, admin, lector, db, cliente_falso):
    """Si el nº de empleado cambió tras sincronizar, el lector sigue conociendo el viejo."""
    t = _trab(db, 'NUEVO', 'Ana')
    fila = SyncEmpleadoHikvision(dispositivo_id=lector.id, trabajador_id=t.id,
                                 employee_no_remoto='VIEJO', estado='SINCRONIZADO')
    db.session.add(fila); db.session.commit()

    r = client.delete(f'/api/hikvision/dispositivos/{lector.id}/empleados/{t.id}',
                      headers=_hdr(admin))
    assert r.status_code == 200
    assert cliente_falso.eliminados == ['VIEJO']
    assert SyncEmpleadoHikvision.query.filter_by(trabajador_id=t.id).first() is None


def _fila_sincronizada(db, lector, t, **kw):
    """Fila como la deja una sincronización exitosa con los datos ACTUALES de `t`."""
    from app.services.hikvision import usuarios as svc_usuarios
    valores = dict(
        dispositivo_id=lector.id, trabajador_id=t.id, employee_no_remoto=t.no_empleado,
        estado='SINCRONIZADO', hash_datos=svc_usuarios.huella_datos(t, t.no_empleado),
        hash_foto='hash-falso', foto_key=t.foto_perfil,
    )
    valores.update(kw)
    fila = SyncEmpleadoHikvision(**valores)
    db.session.add(fila); db.session.commit()
    return fila


def _estado_en_listado(client, admin, lector, t):
    r = client.get(f'/api/hikvision/dispositivos/{lector.id}/empleados', headers=_hdr(admin))
    return next(i for i in r.get_json()['items'] if i['id'] == t.id)


def test_sincronizar_guarda_la_foto_enviada(client, admin, lector, db, cliente_falso):
    t = _trab(db, 'OF1', 'Ana', foto='perfiles/pp_1.webp')
    client.post(f'/api/hikvision/dispositivos/{lector.id}/sincronizar',
                headers=_hdr(admin), json={'trabajador_ids': [t.id]})
    fila = SyncEmpleadoHikvision.query.filter_by(trabajador_id=t.id).one()
    assert fila.foto_key == 'perfiles/pp_1.webp'
    assert _estado_en_listado(client, admin, lector, t)['estado'] == 'SINCRONIZADO'


def test_foto_cambiada_en_la_ficha_marca_desactualizado(client, admin, lector, db):
    t = _trab(db, 'OF1', 'Ana', foto='perfiles/pp_1.webp')
    _fila_sincronizada(db, lector, t)
    t.foto_perfil = 'perfiles/pp_2.webp'
    db.session.commit()

    item = _estado_en_listado(client, admin, lector, t)
    assert item['estado'] == 'DESACTUALIZADO'
    assert item['cambios_pendientes'] == ['fotografía']


def test_nombre_cambiado_marca_desactualizado(client, admin, lector, db):
    t = _trab(db, 'OF1', 'Ana')
    _fila_sincronizada(db, lector, t)
    t.nombre = 'Ana María'
    db.session.commit()
    assert _estado_en_listado(client, admin, lector, t)['cambios_pendientes'] == ['nombre']


def test_fila_sin_foto_key_no_se_marca_desactualizada(client, admin, lector, db):
    """Filas sincronizadas antes de la columna: no se sabe, así que no se alarma."""
    t = _trab(db, 'OF1', 'Ana')
    _fila_sincronizada(db, lector, t, foto_key=None)
    assert _estado_en_listado(client, admin, lector, t)['estado'] == 'SINCRONIZADO'


def test_resincronizar_con_numero_nuevo_quita_el_viejo_del_equipo(
        client, admin, lector, db, cliente_falso):
    """Sin esto, la misma persona quedaría dos veces en el lector."""
    t = _trab(db, 'NUEVO', 'Ana')
    _fila_sincronizada(db, lector, t, employee_no_remoto='VIEJO')

    r = client.post(f'/api/hikvision/dispositivos/{lector.id}/sincronizar',
                    headers=_hdr(admin), json={'trabajador_ids': [t.id]})
    assert r.get_json()['ok'] is True
    assert cliente_falso.eliminados == ['VIEJO']
    fila = SyncEmpleadoHikvision.query.filter_by(trabajador_id=t.id).one()
    assert fila.employee_no_remoto == 'NUEVO'


# ─── Cambiar la foto desde la pantalla del lector ─────────────────────────────

def _png():
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new('RGB', (200, 200), (180, 150, 120)).save(buf, 'PNG')
    buf.seek(0)
    return buf


@pytest.fixture
def guardar_foto_falso(monkeypatch):
    """Sustituye el guardado real (R2/disco + WebP) por uno que solo cambia la key."""
    from app.routes.api_hikvision import sincronizacion as mod_sync
    llamadas = []

    def _save_foto(t, archivo):
        llamadas.append(archivo.read())
        t.foto_perfil = 'perfiles/pp_nueva.webp'

    monkeypatch.setattr(mod_sync, '_save_foto', _save_foto)
    return llamadas


def _subir_foto(client, admin, lector, t, archivo=None, nombre='cara.png'):
    return client.post(
        f'/api/hikvision/dispositivos/{lector.id}/empleados/{t.id}/foto',
        headers=_hdr(admin),
        data={'foto': (archivo or _png(), nombre)},
        content_type='multipart/form-data',
    )


def test_cambiar_foto_la_envia_y_la_guarda(client, admin, lector, db, cliente_falso,
                                           guardar_foto_falso):
    t = _trab(db, 'OF1', 'Ana', foto='perfiles/pp_vieja.webp')
    _fila_sincronizada(db, lector, t)

    r = _subir_foto(client, admin, lector, t)
    assert r.status_code == 200, r.get_json()
    cuerpo = r.get_json()
    assert cuerpo['ok'] is True
    assert cuerpo['empleado']['estado'] == 'SINCRONIZADO'
    assert cliente_falso.rostros == ['OF1']
    # El archivo que se guarda es el mismo que se subió (stream rebobinado).
    assert guardar_foto_falso and guardar_foto_falso[0].startswith(b'\x89PNG')

    db.session.refresh(t)
    fila = SyncEmpleadoHikvision.query.filter_by(trabajador_id=t.id).one()
    assert t.foto_perfil == 'perfiles/pp_nueva.webp'
    assert fila.foto_key == 'perfiles/pp_nueva.webp'


def test_foto_rechazada_por_el_lector_no_cambia_nada(client, admin, lector, db, cliente_falso,
                                                     guardar_foto_falso):
    """Si el motor facial no la acepta, ni el ERP ni el registro que ya funcionaba cambian."""
    t = _trab(db, 'OF1', 'Ana', foto='perfiles/pp_vieja.webp')
    _fila_sincronizada(db, lector, t)
    cliente_falso.fallar_rostro = {'OF1'}

    r = _subir_foto(client, admin, lector, t)
    assert r.status_code == 422
    assert r.get_json()['error']
    assert guardar_foto_falso == []

    db.session.refresh(t)
    fila = SyncEmpleadoHikvision.query.filter_by(trabajador_id=t.id).one()
    assert t.foto_perfil == 'perfiles/pp_vieja.webp'
    assert fila.estado == 'SINCRONIZADO' and fila.ultimo_error is None


def test_cambiar_foto_rechaza_archivo_que_no_es_imagen(client, admin, lector, db, cliente_falso,
                                                       guardar_foto_falso):
    import io
    t = _trab(db, 'OF1', 'Ana')
    r = _subir_foto(client, admin, lector, t, archivo=io.BytesIO(b'no soy imagen'),
                    nombre='cara.png')
    assert r.status_code == 422
    assert cliente_falso.rostros == [] and guardar_foto_falso == []


def test_cambiar_foto_solo_personal_de_oficina(client, admin, lector, db, cliente_falso,
                                               guardar_foto_falso):
    t = _trab(db, 'OB1', 'Beto', oficina=False)
    r = _subir_foto(client, admin, lector, t)
    assert r.status_code == 404
    assert cliente_falso.rostros == []


def test_coordinador_no_cambia_fotos_del_lector(client, coord, lector, db, cliente_falso):
    t = _trab(db, 'OF1', 'Ana')
    r = _subir_foto(client, coord, lector, t)
    assert r.status_code == 403


def test_rostro_en_lector(client, admin, lector, db, cliente_falso, monkeypatch):
    from app.routes.api_hikvision import sincronizacion as mod_sync
    monkeypatch.setattr(mod_sync.svc_usuarios, 'leer_rostro',
                        lambda cli, emp: b'\xff\xd8jpeg' if emp == 'OF1' else None)
    t = _trab(db, 'OF1', 'Ana')
    _fila_sincronizada(db, lector, t)

    r = client.get(f'/api/hikvision/dispositivos/{lector.id}/empleados/{t.id}/rostro',
                   headers=_hdr(admin))
    assert r.status_code == 200
    assert r.mimetype == 'image/jpeg' and r.data == b'\xff\xd8jpeg'


def test_rostro_de_quien_no_esta_en_el_lector_es_404(client, admin, lector, db):
    t = _trab(db, 'OF1', 'Ana')
    r = client.get(f'/api/hikvision/dispositivos/{lector.id}/empleados/{t.id}/rostro',
                   headers=_hdr(admin))
    assert r.status_code == 404


def test_subir_rostro_usa_fdsetup_para_poder_reemplazar():
    """Regresión contra hardware real: `FaceDataRecord` responde
    `deviceUserAlreadyExistFace` si el usuario ya tiene cara, y la única forma
    de cambiar una foto era borrar al empleado del lector. `FDSetUp` crea o
    reemplaza."""
    from app.services.hikvision import usuarios as svc_usuarios

    llamadas = []

    class Cli:
        def enviar_multipart(self, ruta, partes, *, metodo='POST', timeout=None):
            llamadas.append((metodo, ruta, list(partes)))
            return {'statusCode': 1}

    svc_usuarios.subir_rostro(Cli(), 'OF1', b'jpeg')
    assert llamadas == [(
        'PUT', '/ISAPI/Intelligent/FDLib/FDSetUp?format=json', ['FaceDataRecord', 'img'],
    )]


def test_leer_rostro_solo_usa_la_ruta_del_faceurl():
    """El host lo pone el cliente configurado, no la URL que reporta el equipo."""
    from app.services.hikvision import usuarios as svc_usuarios

    pedidas = []

    class Cli:
        def pedir_json(self, metodo, ruta, cuerpo=None):
            return {'UserInfoSearch': {'UserInfo': [{
                'employeeNo': 'OF1', 'numOfFace': 1,
                'faceURL': 'http://10.9.9.9/LOCALS/pic/enrlFace/0/1.jpg@WEB5',
            }]}}

        def descargar(self, ruta):
            pedidas.append(ruta)
            return b'jpeg'

    assert svc_usuarios.leer_rostro(Cli(), 'OF1') == b'jpeg'
    assert pedidas == ['/LOCALS/pic/enrlFace/0/1.jpg@WEB5']


# ─── Marca de oficina en el módulo de empleados ───────────────────────────────

def test_bulk_marcar_oficina(client, admin, db):
    a = _trab(db, 'B1', 'Ana', oficina=False)
    b = _trab(db, 'B2', 'Beto', oficina=False)
    r = client.post('/api/trabajadores/bulk', headers=_hdr(admin),
                    json={'action': 'marcar_oficina', 'ids': [a.id, b.id]})
    assert r.status_code == 200
    assert r.get_json()['affected'] == 2
    db.session.refresh(a); db.session.refresh(b)
    assert a.es_oficina is True and b.es_oficina is True

    # Repetir no vuelve a contarlos.
    r2 = client.post('/api/trabajadores/bulk', headers=_hdr(admin),
                     json={'action': 'marcar_oficina', 'ids': [a.id]})
    assert r2.get_json()['affected'] == 0
    assert r2.get_json()['skipped'][0]['reason'] == 'ya_es_oficina'


# ─── Puerta y relé ────────────────────────────────────────────────────────────

def test_estado_de_puerta(client, admin, lector, cliente_falso):
    r = client.get(f'/api/hikvision/dispositivos/{lector.id}/puerta', headers=_hdr(admin))
    assert r.status_code == 200
    cuerpo = r.get_json()
    assert cuerpo['ok'] is True
    assert cuerpo['parametros']['segundos_apertura'] == 5
    assert cuerpo['estado']['lector_en_linea'] is True


def test_abrir_puerta(client, admin, lector, cliente_falso):
    r = client.post(f'/api/hikvision/dispositivos/{lector.id}/puerta/abrir', headers=_hdr(admin))
    assert r.status_code == 200
    assert r.get_json() == {'ok': True, 'segundos_apertura': 5}
    assert cliente_falso.aperturas == [1]


def test_abrir_puerta_queda_en_bitacora(client, admin, lector, db, cliente_falso):
    """Es la única acción del módulo con efecto físico: auditarla no es opcional."""
    from app.models import AuditLog
    antes = AuditLog.query.count()
    client.post(f'/api/hikvision/dispositivos/{lector.id}/puerta/abrir', headers=_hdr(admin))
    assert AuditLog.query.count() == antes + 1
    ultimo = AuditLog.query.order_by(AuditLog.id.desc()).first()
    assert 'puerta' in ultimo.action.lower()


def test_coordinador_no_puede_abrir_la_puerta(client, coord, lector, cliente_falso):
    r = client.post(f'/api/hikvision/dispositivos/{lector.id}/puerta/abrir', headers=_hdr(coord))
    assert r.status_code == 403
    assert cliente_falso.aperturas == []


def test_lector_desactivado_no_abre(client, admin, lector, db, cliente_falso):
    lector.activo = False
    db.session.commit()
    r = client.post(f'/api/hikvision/dispositivos/{lector.id}/puerta/abrir', headers=_hdr(admin))
    assert r.status_code == 409
    assert cliente_falso.aperturas == []


def test_usuario_sincronizado_recibe_permiso_de_puerta(db):
    """El empleado abre la puerta porque se le da de alta con permiso permanente.

    Si alguien quita `doorRight`/`RightPlan` del payload, los rostros se
    registrarían igual pero el relé no se activaría nunca — un fallo silencioso
    y difícil de diagnosticar. Esta prueba lo fija.
    """
    from app.services.hikvision import usuarios as svc
    t = _trab(db, 'OF1', 'Ana')
    cuerpo = svc.cuerpo_usuario(t, 'OF1')['UserInfo']
    assert cuerpo['doorRight'] == '1'
    assert cuerpo['RightPlan'] == [{'doorNo': 1, 'planTemplateNo': '1'}]
    assert cuerpo['Valid']['enable'] is True


# ─── Estado del equipo, reloj, auditoría y actividad ─────────────────────────

class ClienteEquipo(ClienteFalso):
    """Doble con lo que usan las pestañas «Equipo» y «Actividad»."""

    hora = '2026-09-22T19:00:00-06:00'
    usuarios_equipo: list = []
    eventos: list = []          # crudos de AcsEvent que «tiene» el lector
    horas_ajustadas: list = []
    consultas: list = []        # ('serial', desde) | ('historial',)

    def hora_dispositivo(self):
        return {'modo': 'manual', 'hora_local': ClienteEquipo.hora, 'zona': 'CST+6:00:00'}

    def ajustar_hora(self, ahora_utc):
        ClienteEquipo.horas_ajustadas.append(ahora_utc)
        cdmx = timezone(timedelta(hours=-6))
        ClienteEquipo.hora = ahora_utc.astimezone(cdmx).replace(microsecond=0).isoformat()
        return self.hora_dispositivo()

    def capacidad(self):
        return {'usuarios': 3, 'con_rostro': 2, 'max_usuarios': 1500, 'max_rostros': 1500}

    ntp_configurado: dict = {'servidor': '', 'intervalo_min': 0}

    def ntp(self):
        return dict(ClienteEquipo.ntp_configurado)

    def configurar_ntp(self, servidor, intervalo_min=60):
        ClienteEquipo.ntp_configurado = {'servidor': servidor, 'intervalo_min': intervalo_min}
        return self.hora_dispositivo()

    def red(self):
        return {'direccionamiento': 'static', 'ip': '192.168.1.159'}

    def descargar(self, ruta):
        return b'\xff\xd8captura'


@pytest.fixture
def cliente_equipo(monkeypatch, cliente_falso):
    from app.routes.api_hikvision import actividad as mod_act
    from app.routes.api_hikvision import equipo as mod_eq

    from app.services.hikvision import eventos as svc_eventos

    ClienteEquipo.hora = '2026-09-22T19:00:00-06:00'
    ClienteEquipo.usuarios_equipo, ClienteEquipo.horas_ajustadas = [], []
    ClienteEquipo.eventos, ClienteEquipo.consultas = [], []
    monkeypatch.setattr(mod_eq, 'ClienteHikvision', ClienteEquipo)
    monkeypatch.setattr(mod_act, 'ClienteHikvision', ClienteEquipo)
    monkeypatch.setattr(mod_eq.svc_usuarios, 'listar_todos',
                        lambda cli: ClienteEquipo.usuarios_equipo)

    def desde_serial(cli, ultimo):
        ClienteEquipo.consultas.append(('serial', ultimo))
        return [e for e in ClienteEquipo.eventos if e['serialNo'] > ultimo]

    def recientes(cli, inicio, fin, *, limite):
        ClienteEquipo.consultas.append(('historial',))
        return ClienteEquipo.eventos[:limite]

    monkeypatch.setattr(svc_eventos, 'desde_serial', desde_serial)
    monkeypatch.setattr(svc_eventos, 'recientes', recientes)
    return ClienteEquipo


def test_desfase_del_reloj():
    from datetime import datetime, timezone
    from app.routes.api_hikvision.equipo import desfase_segundos
    ahora = datetime(2026, 9, 23, 1, 0, 0, tzinfo=timezone.utc)
    assert desfase_segundos('2026-09-22T19:01:30-06:00', ahora) == 90
    assert desfase_segundos('2026-09-22T18:59:00-06:00', ahora) == -60
    # Sin desfase horario no se puede comparar: mejor "no sé" que un número falso.
    assert desfase_segundos('2026-09-22T19:00:00', ahora) is None
    assert desfase_segundos('basura', ahora) is None


def test_estado_del_equipo(client, admin, lector, db, cliente_equipo):
    r = client.get(f'/api/hikvision/dispositivos/{lector.id}/estado', headers=_hdr(admin))
    assert r.status_code == 200
    cuerpo = r.get_json()
    assert cuerpo['capacidad']['max_rostros'] == 1500
    assert cuerpo['info']['modelo'] == 'DS-K1T342MFWX-E1'
    # La hora del doble está fija en el pasado: el reloj se reporta desajustado.
    assert cuerpo['hora']['en_hora'] is False
    db.session.refresh(lector)
    assert lector.firmware == 'V3.16.1' and lector.ultimo_estado == 'OK'


def test_ajustar_hora_queda_en_bitacora(client, admin, lector, db, cliente_equipo):
    from app.models import AuditLog
    r = client.post(f'/api/hikvision/dispositivos/{lector.id}/hora', headers=_hdr(admin))
    assert r.status_code == 200
    assert r.get_json()['hora']['en_hora'] is True
    assert len(cliente_equipo.horas_ajustadas) == 1
    assert AuditLog.query.filter(AuditLog.action.like('Ajustó la hora del lector%')).count() == 1


def test_coordinador_no_ajusta_la_hora(client, coord, lector, cliente_equipo):
    r = client.post(f'/api/hikvision/dispositivos/{lector.id}/hora', headers=_hdr(coord))
    assert r.status_code == 403
    assert cliente_equipo.horas_ajustadas == []


def test_ajustar_hora_manda_la_zona_del_equipo():
    """El VPS corre en UTC: la hora debe llegar en el desfase que usa el lector."""
    from datetime import datetime, timezone
    from app.services.hikvision.client import ClienteHikvision

    cli = ClienteHikvision('192.168.1.159', 80, 'admin', 'x')
    enviados = []
    cli.hora_dispositivo = lambda: {
        'modo': 'manual', 'hora_local': '2026-09-22T10:00:00-06:00', 'zona': 'CST+6:00:00',
    }
    cli.pedir_xml = lambda metodo, ruta, cuerpo=None: enviados.append((metodo, ruta, cuerpo))
    cli.ajustar_hora(datetime(2026, 9, 23, 1, 2, 3, 999, tzinfo=timezone.utc))

    metodo, ruta, cuerpo = enviados[0]
    assert (metodo, ruta) == ('PUT', '/ISAPI/System/time')
    assert '<localTime>2026-09-22T19:02:03-06:00</localTime>' in cuerpo
    assert '<timeZone>CST+6:00:00</timeZone>' in cuerpo


def test_auditoria_detecta_usuarios_fuera_del_erp(client, admin, lector, db, cliente_equipo):
    ana = _trab(db, 'OF1', 'Ana')
    beto = _trab(db, 'OF2', 'Beto')
    _fila_sincronizada(db, lector, ana)
    _fila_sincronizada(db, lector, beto)      # el ERP cree que está, el equipo no
    cliente_equipo.usuarios_equipo = [
        {'employeeNo': 'OF1', 'name': 'Ana', 'numOfFace': 1},
        {'employeeNo': 'X9', 'name': 'Alta manual', 'numOfFace': 1, 'numOfCard': 1},
    ]

    r = client.get(f'/api/hikvision/dispositivos/{lector.id}/auditoria', headers=_hdr(admin))
    assert r.status_code == 200
    cuerpo = r.get_json()
    assert [u['employee_no'] for u in cuerpo['solo_en_equipo']] == ['X9']
    assert cuerpo['solo_en_equipo'][0]['tarjetas'] == 1
    assert [u['trabajador_id'] for u in cuerpo['solo_en_erp']] == [beto.id]


def test_borrar_usuario_ajeno(client, admin, lector, db, cliente_equipo):
    r = client.delete(f'/api/hikvision/dispositivos/{lector.id}/usuarios-equipo/X9',
                      headers=_hdr(admin))
    assert r.status_code == 200
    assert cliente_equipo.eliminados == ['X9']


def test_no_se_borra_como_ajeno_a_un_empleado_del_erp(client, admin, lector, db, cliente_equipo):
    """Esos se quitan con «Quitar del lector», que además limpia su fila."""
    t = _trab(db, 'OF1', 'Ana')
    _fila_sincronizada(db, lector, t)
    r = client.delete(f'/api/hikvision/dispositivos/{lector.id}/usuarios-equipo/OF1',
                      headers=_hdr(admin))
    assert r.status_code == 409
    assert cliente_equipo.eliminados == []


def _evento(minor, serial, emp='', *, hace_min=10):
    """Evento crudo de AcsEvent, `hace_min` minutos antes de ahora, en hora de CDMX."""
    from datetime import datetime
    cdmx = timezone(timedelta(hours=-6))
    cuando = (datetime.now(cdmx) - timedelta(minutes=hace_min)).replace(microsecond=0)
    return {'major': 5, 'minor': minor, 'time': cuando.isoformat(), 'serialNo': serial,
            'employeeNoString': emp, 'name': 'Ana' if emp else '',
            'pictureURL': f'http://192.168.1.159/LOCALS/pic/acsLinkCap/{serial}.jpeg' if emp else ''}


def _ingresar(db, lector, crudos):
    from app.services.hikvision import ingesta
    nuevos = ingesta.guardar(lector.id, crudos)
    db.session.commit()
    return nuevos


def test_guardar_es_idempotente_y_enlaza_al_trabajador(db, lector):
    """El lector reenvía su historial al reconectar: no debe duplicarse nada."""
    from app.models import EventoHikvision
    t = _trab(db, 'OF1', 'Ana')
    _fila_sincronizada(db, lector, t)
    lote = [_evento(75, 12, 'OF1'), _evento(21, 13), _evento(75, 12, 'OF1')]

    nuevos = _ingresar(db, lector, lote)
    assert [e.serial_no for e in nuevos] == [12, 13]
    assert _ingresar(db, lector, lote) == []
    assert EventoHikvision.query.count() == 2

    ev = EventoHikvision.query.filter_by(serial_no=12).one()
    assert ev.trabajador_id == t.id and ev.tipo == 'permitido'
    assert ev.captura == '/LOCALS/pic/acsLinkCap/12.jpeg'
    # La hora de la oficina se conserva tal cual para mostrarla.
    assert ev.hora_local.endswith('-06:00')


def test_guardar_descarta_eventos_sin_serial_o_sin_zona(db, lector):
    sin_serial = {**_evento(75, 1), 'serialNo': None}
    sin_zona = {**_evento(75, 2), 'time': '2026-09-22T19:00:00'}
    assert _ingresar(db, lector, [sin_serial, sin_zona]) == []


def test_ponerse_al_dia_primero_trae_historial_y_luego_por_serial(db, lector, cliente_equipo):
    from app.services.hikvision import ingesta
    cliente_equipo.eventos = [_evento(75, 5, 'OF1'), _evento(76, 6)]

    assert len(ingesta.ponerse_al_dia(ClienteEquipo(), lector.id)) == 2
    cliente_equipo.eventos.append(_evento(75, 7, 'OF1'))
    assert [e.serial_no for e in ingesta.ponerse_al_dia(ClienteEquipo(), lector.id)] == [7]
    # Tabla vacía → historial por fechas; después, solo lo posterior al último serial.
    assert cliente_equipo.consultas == [('historial',), ('serial', 6)]


def test_listar_eventos_lee_de_la_base_sin_tocar_el_lector(client, admin, lector, db,
                                                           monkeypatch):
    """El punto de todo esto: abrir la pantalla ya no le pregunta nada al equipo."""
    from app.routes.api_hikvision import actividad as mod_act

    class ClienteProhibido:
        @classmethod
        def desde_dispositivo(cls, d):
            raise AssertionError('listar eventos no debe llamar al lector')

    monkeypatch.setattr(mod_act, 'ClienteHikvision', ClienteProhibido)
    t = _trab(db, 'OF1', 'Ana')
    _ingresar(db, lector, [
        _evento(75, 10, 'OF1', hace_min=30),
        _evento(104, 11, 'OF1', hace_min=20),
        _evento(76, 12, 'X9', hace_min=10),
        _evento(21, 13, hace_min=5),
        _evento(75, 1, 'OF1', hace_min=60 * 24 * 3),    # fuera de las 24 h
    ])
    # Sincronizado DESPUÉS de sus accesos: el enlace se completa al leer.
    _fila_sincronizada(db, lector, t)

    r = client.get(f'/api/hikvision/dispositivos/{lector.id}/eventos?filtro=denegados',
                   headers=_hdr(admin))
    assert r.status_code == 200
    cuerpo = r.get_json()
    assert [e['serial'] for e in cuerpo['items']] == [12, 11]
    assert cuerpo['items'][1]['trabajador_id'] == t.id
    assert cuerpo['items'][0]['trabajador_id'] is None

    todos = client.get(f'/api/hikvision/dispositivos/{lector.id}/eventos',
                       headers=_hdr(admin)).get_json()
    assert [e['serial'] for e in todos['items']] == [13, 12, 11, 10]
    assert todos['tiempo_real']['en_vivo'] is False


def test_tiempo_real_exige_latido_reciente(db, lector):
    from datetime import datetime
    ahora = datetime.now(timezone.utc)
    lector.escucha_estado = 'CONECTADO'
    lector.escucha_latido = ahora - timedelta(seconds=30)
    assert lector.estado_escucha(ahora)['en_vivo'] is True
    # El proceso murió sin poder escribir DESCONECTADO: el latido lo delata.
    lector.escucha_latido = ahora - timedelta(minutes=5)
    assert lector.estado_escucha(ahora)['en_vivo'] is False


def test_traer_eventos_a_mano(client, admin, lector, db, cliente_equipo):
    cliente_equipo.eventos = [_evento(75, 5, 'OF1'), _evento(76, 6)]
    r = client.post(f'/api/hikvision/dispositivos/{lector.id}/eventos/sincronizar',
                    headers=_hdr(admin))
    assert r.status_code == 200 and r.get_json()['nuevos'] == 2
    r = client.post(f'/api/hikvision/dispositivos/{lector.id}/eventos/sincronizar',
                    headers=_hdr(admin))
    assert r.get_json()['nuevos'] == 0


def test_coordinador_no_trae_eventos(client, coord, lector, cliente_equipo):
    r = client.post(f'/api/hikvision/dispositivos/{lector.id}/eventos/sincronizar',
                    headers=_hdr(coord))
    assert r.status_code == 403


# ─── Stream (alertStream) y escucha ──────────────────────────────────────────

def _parte(obj):
    import json
    cuerpo = json.dumps(obj).encode()
    return (b'--MIME_boundary\r\nContent-Disposition: form-data; name="AccessControllerEvent"\r\n'
            b'Content-Type: application/json; charset="UTF-8"\r\n'
            b'Content-Length: ' + str(len(cuerpo)).encode() + b'\r\n\r\n' + cuerpo + b'\r\n')


@pytest.mark.parametrize('tamano', [1, 7, 64, 100_000])
def test_partes_multipart_con_trozos_arbitrarios(tamano):
    """La red corta donde quiere: una cabecera puede quedar partida en dos."""
    from app.services.hikvision.eventos import partes_multipart
    objs = [{'n': 1, 'texto': 'con\r\n\r\nlinea en blanco'}, {'n': 2}, {'n': 3}]
    flujo = b''.join(_parte(o) for o in objs)
    trozos = [flujo[i:i + tamano] for i in range(0, len(flujo), tamano)]

    import json
    partes = list(partes_multipart(trozos))
    assert [json.loads(c)['n'] for _, c in partes] == [1, 2, 3]
    assert all(t == 'application/json' for t, _ in partes)


def test_partes_multipart_corta_si_el_flujo_es_basura(monkeypatch):
    from app.services.hikvision import eventos as svc_eventos
    from app.services.hikvision.errores import ErrorConexion
    monkeypatch.setattr(svc_eventos, 'MAX_BUFFER_STREAM', 100)
    with pytest.raises(ErrorConexion):
        list(svc_eventos.partes_multipart([b'x' * 200]))


def _aviso(serial, major=5):
    import json
    return json.dumps({
        'eventType': 'AccessControllerEvent',
        'AccessControllerEvent': {'majorEventType': major, 'subEventType': 75, 'serialNo': serial},
    }).encode()


def test_escucha_solo_consulta_ante_accesos_nuevos(app, db, lector, monkeypatch):
    """Latidos, operaciones del equipo e historial reenviado no cuestan consultas."""
    import json
    from app.services.hikvision import escucha as mod_escucha

    llamadas = []
    monkeypatch.setattr(mod_escucha.ingesta, 'ponerse_al_dia',
                        lambda cli, disp_id: llamadas.append(disp_id) or [])
    e = mod_escucha.EscuchaLector(app, lector.id)
    e.max_serial = 100

    e.atender_parte(None, json.dumps({'eventType': 'videoloss'}).encode())   # latido
    e.atender_parte(None, _aviso(150, major=3))     # operación del equipo
    e.atender_parte(None, _aviso(90))               # historial reenviado
    e.atender_parte(None, b'no es json')
    assert llamadas == []

    e.atender_parte(None, _aviso(101))
    assert llamadas == [lector.id]
    # Ese serial queda atendido aunque la consulta no haya traído nada.
    e.atender_parte(None, _aviso(101))
    assert llamadas == [lector.id]


def test_escucha_avisa_al_navegador_con_lo_nuevo(app, db, lector, monkeypatch):
    from app.services.hikvision import escucha as mod_escucha
    from app.services.hikvision import eventos as svc_eventos

    monkeypatch.setattr(svc_eventos, 'desde_serial',
                        lambda cli, ultimo: [_evento(75, 101, 'OF1')])
    avisos = []
    monkeypatch.setattr(mod_escucha, '_avisar',
                        lambda disp, eventos, total: avisos.append((disp, total, eventos)))
    _ingresar(db, lector, [_evento(75, 100, 'OF1')])

    e = mod_escucha.EscuchaLector(app, lector.id)
    e.max_serial = 100
    e.atender_parte(None, _aviso(101))

    assert e.max_serial == 101
    assert avisos and avisos[0][0] == lector.id and avisos[0][1] == 1
    assert avisos[0][2][0]['serial'] == 101


def test_supervisor_reinicia_solo_si_cambian_direccion_o_credenciales(app, db, lector,
                                                                      monkeypatch):
    """El estado de la escucha y la última prueba reescriben la fila a cada rato:
    eso no debe tumbar la conexión."""
    from app.services.hikvision import escucha as mod_escucha

    arrancadas = []

    class EscuchaFalsa:
        def __init__(self, app_, disp_id):
            self.disp_id = disp_id
            self.detenida = False
            arrancadas.append(self)

        def iniciar(self):
            pass

        def detener(self):
            self.detenida = True

        def vivo(self):
            return not self.detenida

    monkeypatch.setattr(mod_escucha, 'EscuchaLector', EscuchaFalsa)
    sup = mod_escucha.Supervisor(app)
    sup.revisar()
    assert len(arrancadas) == 1

    lector.ultimo_estado = 'OK'
    lector.escucha_estado = 'CONECTADO'
    lector.firmware = 'V9'
    db.session.commit()
    sup.revisar()
    assert len(arrancadas) == 1 and not arrancadas[0].detenida

    lector.password = 'otra-contrasena'
    db.session.commit()
    sup.revisar()
    assert arrancadas[0].detenida and len(arrancadas) == 2

    lector.activo = False
    db.session.commit()
    sup.revisar()
    assert arrancadas[1].detenida and sup.escuchas == {}


def test_eventos_filtro_invalido(client, admin, lector, cliente_equipo):
    r = client.get(f'/api/hikvision/dispositivos/{lector.id}/eventos?filtro=otro',
                   headers=_hdr(admin))
    assert r.status_code == 422


@pytest.mark.parametrize('ruta', [
    '/ISAPI/System/deviceInfo',
    '/LOCALS/pic/../../etc/passwd',
    'http://10.0.0.1/LOCALS/pic/x.jpg',
    '',
])
def test_captura_solo_sirve_fotos_del_equipo(client, admin, lector, cliente_equipo, ruta):
    r = client.get(f'/api/hikvision/dispositivos/{lector.id}/eventos/captura',
                   headers=_hdr(admin), query_string={'ruta': ruta})
    assert r.status_code == 422


def test_captura_valida(client, admin, lector, cliente_equipo):
    r = client.get(f'/api/hikvision/dispositivos/{lector.id}/eventos/captura',
                   headers=_hdr(admin),
                   query_string={'ruta': '/LOCALS/pic/acsLinkCap/1.jpeg@WEB1'})
    assert r.status_code == 200 and r.mimetype == 'image/jpeg'


def test_normalizar_evento_desconocido_no_inventa_nombre():
    from app.services.hikvision.eventos import normalizar
    e = normalizar({'minor': 999, 'time': 'x', 'pictureURL': 'http://h/otra/ruta.jpg'})
    assert e['tipo'] == 'otro' and e['descripcion'] == 'Evento 999'
    assert e['captura'] is None      # fuera de /LOCALS/pic/ no se ofrece


def test_digest_pide_reto_nuevo_en_cada_peticion():
    """Regresión contra el lector real: invalida el nonce a los pocos segundos y
    responde 401 SIN reto nuevo. `httpx.DigestAuth` reusaba el nonce viejo, así
    que la consulta fallaba como si la contraseña fuera mala (y cortaba la
    escucha en tiempo real). Aquí el «lector» solo acepta cada nonce UNA vez."""
    import itertools
    import httpx
    from app.services.hikvision.client import ClienteHikvision

    contador = itertools.count()
    vigentes = set()
    con_credenciales_rechazadas = []

    def lector(request):
        auth = request.headers.get('Authorization', '')
        if not auth:
            nonce = f'n{next(contador)}'
            vigentes.add(nonce)
            return httpx.Response(401, headers={
                'WWW-Authenticate': f'Digest qop="auth", realm="DS", nonce="{nonce}"',
            })
        nonce = auth.split('nonce="')[1].split('"')[0]
        if nonce not in vigentes:
            con_credenciales_rechazadas.append(nonce)
            return httpx.Response(401)          # sin reto: como el equipo real
        vigentes.discard(nonce)
        return httpx.Response(200, json={'statusCode': 1})

    cli = ClienteHikvision('192.168.1.159', 80, 'admin', 'x')
    http = cli._http()
    http._transport = httpx.MockTransport(lector)

    for _ in range(3):
        assert cli.pedir_json('GET', '/ISAPI/System/status?format=json') == {'statusCode': 1}
    # Nunca se mandó una credencial con nonce vencido: no cuenta para el bloqueo.
    assert con_credenciales_rechazadas == []


# ─── Fase 2: checadas del lector → registros de horas ────────────────────────
# Fechas fijas: el martes 2026-09-22 abre la semana de nómina (martes a lunes).

def _acceso(serial, emp, cuando, minor=75):
    return {'major': 5, 'minor': minor, 'time': f'{cuando}-06:00', 'serialNo': serial,
            'employeeNoString': emp, 'name': 'Ana', 'pictureURL': ''}


def _checar(db, lector, crudos):
    """Ingesta + asistencia, como lo hace la escucha."""
    from app.services.hikvision import asistencia, ingesta
    nuevos = ingesta.guardar(lector.id, crudos)
    resultados = asistencia.aplicar_eventos(nuevos)
    db.session.commit()
    return resultados


def _registro(trabajador, fecha='2026-09-24'):
    from app.models import RegistroDiarioHoras
    return RegistroDiarioHoras.query.filter_by(
        trabajador_id=trabajador.id, fecha=date.fromisoformat(fecha),
    ).first()


@pytest.fixture
def oficinista(db, lector):
    t = _trab(db, 'OF1', 'Ana')
    t.tipo_nomina = 'Por hora'
    db.session.commit()
    _fila_sincronizada(db, lector, t)
    return t


@pytest.mark.parametrize('dia, inicio', [
    ('2026-09-22', '2026-09-22'),   # martes: abre su propia semana
    ('2026-09-24', '2026-09-22'),
    ('2026-09-28', '2026-09-22'),   # lunes: cierra la semana del martes anterior
    ('2026-09-29', '2026-09-29'),
])
def test_semana_de_nomina_va_de_martes_a_lunes(dia, inicio):
    from app.services.hikvision.asistencia import semana_de
    ini, fin = semana_de(date.fromisoformat(dia))
    assert ini.isoformat() == inicio and (fin - ini).days == 6


@pytest.mark.parametrize('hora, esperado', [
    ('08:07:00', '08:00'), ('08:20:59', '08:30'), ('17:44:00', '17:30'),
    ('17:46:00', '18:00'), ('23:50:00', '00:00'),
    # Empates: `round()` de Python va al par, igual que el QR del móvil.
    ('08:15:00', '08:00'), ('08:45:00', '09:00'),
])
def test_redondeo_igual_que_el_qr(hora, esperado):
    from datetime import time
    from app.services.hikvision.asistencia import redondear
    assert redondear(time.fromisoformat(hora)).strftime('%H:%M') == esperado


def test_primer_y_ultimo_acceso_son_entrada_y_salida(db, lector, oficinista):
    from app.models import Proyecto
    _checar(db, lector, [
        _acceso(1, 'OF1', '2026-09-24T08:07:00'),
        _acceso(2, 'OF1', '2026-09-24T13:02:00'),     # pasada intermedia
        _acceso(3, 'OF1', '2026-09-24T17:52:00'),
    ])
    reg = _registro(oficinista)
    assert (reg.hora_entrada.strftime('%H:%M'), reg.hora_salida.strftime('%H:%M')) == ('08:00', '18:00')
    assert float(reg.horas_productivas) == 10.0      # Por hora, sin comida marcada
    assert reg.origen == 'LECTOR'

    # Va al reporte OFICINA de la semana, que se abrió solo.
    oficina = Proyecto.query.filter_by(numero_proyecto='OFICINA').one()
    assert reg.reporte.proyecto_id == oficina.id
    assert reg.reporte.estado == 'BORRADOR'
    assert reg.reporte.fecha_inicio_semana.isoformat() == '2026-09-22'
    assert oficinista in oficina.participantes


def test_un_solo_acceso_deja_solo_la_entrada_y_la_salida_llega_despues(db, lector, oficinista):
    _checar(db, lector, [_acceso(1, 'OF1', '2026-09-24T08:07:00')])
    reg = _registro(oficinista)
    assert reg.hora_entrada.strftime('%H:%M') == '08:00' and reg.hora_salida is None
    assert reg.horas_productivas is None

    _checar(db, lector, [_acceso(2, 'OF1', '2026-09-24T16:10:00')])
    db.session.refresh(reg)
    assert reg.hora_salida.strftime('%H:%M') == '16:00'
    assert float(reg.horas_productivas) == 8.0


def test_reprocesar_no_duplica(db, lector, oficinista):
    from app.models import RegistroDiarioHoras, ReporteSemanal
    from app.services.hikvision import asistencia
    _checar(db, lector, [_acceso(1, 'OF1', '2026-09-24T08:00:00'),
                         _acceso(2, 'OF1', '2026-09-24T17:00:00')])
    resultados = asistencia.recalcular(date(2026, 9, 22), date(2026, 9, 28))
    db.session.commit()
    assert [r['accion'] for r in resultados] == ['sin_cambios']
    assert RegistroDiarioHoras.query.count() == 1
    assert ReporteSemanal.query.count() == 1


def test_rechazos_no_son_checadas(db, lector, oficinista):
    _checar(db, lector, [_acceso(1, 'OF1', '2026-09-24T08:00:00', minor=104),
                         _acceso(2, 'OF1', '2026-09-24T08:01:00', minor=76)])
    assert _registro(oficinista) is None


def test_la_edicion_manual_manda(client, admin, db, lector, oficinista):
    """Si alguien corrige a mano la hora que puso el lector, el lector ya no la pisa."""
    _checar(db, lector, [_acceso(1, 'OF1', '2026-09-24T08:07:00'),
                         _acceso(2, 'OF1', '2026-09-24T17:00:00')])
    reg = _registro(oficinista)

    r = client.put(f'/api/horas/registros/{reg.id}', headers=_hdr(admin), json={
        'hora_entrada': '07:30', 'hora_salida': '17:00', 'tomo_comida': False,
    })
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['origen'] == 'LECTOR_EDITADO'

    _checar(db, lector, [_acceso(3, 'OF1', '2026-09-24T19:00:00')])
    db.session.refresh(reg)
    assert reg.hora_entrada.strftime('%H:%M') == '07:30'
    assert reg.hora_salida.strftime('%H:%M') == '17:00'


def test_guardar_sin_cambiar_horas_no_le_quita_el_registro_al_lector(client, admin, db, lector,
                                                                   oficinista):
    """El guardado reenvía registros intactos; marcar comida tampoco cambia el dueño."""
    _checar(db, lector, [_acceso(1, 'OF1', '2026-09-24T08:00:00'),
                         _acceso(2, 'OF1', '2026-09-24T17:00:00')])
    reg = _registro(oficinista)
    r = client.put(f'/api/horas/registros/{reg.id}', headers=_hdr(admin), json={
        'hora_entrada': '08:00', 'hora_salida': '17:00', 'tomo_comida': True,
    })
    assert r.status_code == 200 and r.get_json()['origen'] == 'LECTOR'

    _checar(db, lector, [_acceso(3, 'OF1', '2026-09-24T18:10:00')])
    db.session.refresh(reg)
    assert reg.hora_salida.strftime('%H:%M') == '18:00'
    assert float(reg.horas_productivas) == 9.0     # 10 h menos la comida que marcó el admin


def test_no_pisa_un_registro_capturado_por_otra_via(db, lector, oficinista):
    from datetime import time
    from app.models import RegistroDiarioHoras
    from app.services.hikvision import asistencia
    reporte, _, _ = asistencia.reporte_oficina(date(2026, 9, 24))
    db.session.add(RegistroDiarioHoras(
        reporte_id=reporte.id, trabajador_id=oficinista.id, fecha=date(2026, 9, 24),
        hora_entrada=time(9, 0), hora_salida=time(14, 0),
    ))
    db.session.commit()

    resultados = _checar(db, lector, [_acceso(1, 'OF1', '2026-09-24T08:00:00'),
                                      _acceso(2, 'OF1', '2026-09-24T17:00:00')])
    assert resultados[0]['accion'] == 'omitido'
    assert _registro(oficinista).hora_entrada == time(9, 0)


def test_no_escribe_en_semana_con_prenomina_guardada(db, lector, oficinista):
    from app.models import Prenomina
    db.session.add(Prenomina(trabajador_id=oficinista.id, fecha_inicio=date(2026, 9, 22),
                             fecha_fin=date(2026, 9, 28)))
    db.session.commit()
    resultados = _checar(db, lector, [_acceso(1, 'OF1', '2026-09-24T08:00:00'),
                                      _acceso(2, 'OF1', '2026-09-24T17:00:00')])
    assert resultados[0]['accion'] == 'omitido'
    assert 'prenómina' in resultados[0]['motivo']
    assert _registro(oficinista) is None


def test_no_escribe_si_el_reporte_oficina_ya_se_cerro(db, lector, oficinista):
    from app.services.hikvision import asistencia
    reporte, _, _ = asistencia.reporte_oficina(date(2026, 9, 24))
    reporte.estado = 'TERMINADO'
    db.session.commit()
    resultados = _checar(db, lector, [_acceso(1, 'OF1', '2026-09-24T08:00:00')])
    assert resultados[0]['accion'] == 'omitido'


def test_cada_dia_va_a_su_registro_y_cada_semana_a_su_reporte(db, lector, oficinista):
    from app.models import ReporteSemanal
    _checar(db, lector, [
        _acceso(1, 'OF1', '2026-09-24T08:00:00'), _acceso(2, 'OF1', '2026-09-24T17:00:00'),
        _acceso(3, 'OF1', '2026-09-28T08:00:00'),     # lunes: misma semana
        _acceso(4, 'OF1', '2026-09-29T08:00:00'),     # martes: semana nueva
    ])
    assert _registro(oficinista, '2026-09-28').reporte_id == _registro(oficinista).reporte_id
    assert _registro(oficinista, '2026-09-29').reporte_id != _registro(oficinista).reporte_id
    assert ReporteSemanal.query.count() == 2


def test_la_escucha_pasa_los_accesos_a_horas(app, db, lector, oficinista, monkeypatch):
    from app.services.hikvision import escucha as mod_escucha
    from app.services.hikvision import eventos as svc_eventos
    monkeypatch.setattr(svc_eventos, 'desde_serial', lambda cli, ultimo: [
        _acceso(11, 'OF1', '2026-09-24T08:00:00'), _acceso(12, 'OF1', '2026-09-24T17:00:00'),
    ])
    monkeypatch.setattr(mod_escucha, '_avisar', lambda *a: None)
    _ingresar(db, lector, [_acceso(10, 'OF1', '2026-09-23T08:00:00')])

    e = mod_escucha.EscuchaLector(app, lector.id)
    e.max_serial = 10
    e.atender_parte(None, _aviso(11))
    assert _registro(oficinista).hora_salida.strftime('%H:%M') == '17:00'


# ─── Estado de la puerta en vivo ──────────────────────────────────────────────

def test_ultimo_estado_puerta_toma_el_evento_mas_reciente(db, lector):
    from app.services.hikvision.eventos import ultimo_estado_puerta
    nuevos = _ingresar(db, lector, [
        _acceso(20, 'OF1', '2026-09-24T08:00:00'),                   # acceso: no es puerta
        _acceso(21, '', '2026-09-24T08:00:01', minor=21),
        _acceso(22, '', '2026-09-24T08:00:06', minor=22),
    ])
    assert ultimo_estado_puerta(nuevos) == {'cerradura': 'Cerrada', 'hora': '2026-09-24T08:00:06-06:00'}
    assert ultimo_estado_puerta(nuevos[:2])['cerradura'] == 'Abierta'
    assert ultimo_estado_puerta(nuevos[:1]) is None


def test_la_escucha_avisa_cuando_cambia_la_cerradura(app, db, lector, monkeypatch):
    from app.services.hikvision import escucha as mod_escucha
    from app.services.hikvision import eventos as svc_eventos
    monkeypatch.setattr(svc_eventos, 'desde_serial',
                        lambda cli, ultimo: [_acceso(31, '', '2026-09-24T09:00:00', minor=21)])
    monkeypatch.setattr(mod_escucha, '_avisar', lambda *a: None)
    emitidos = []
    import app.realtime as rt
    monkeypatch.setattr(rt, 'emit_to_role', lambda roles, ev, carga: emitidos.append((ev, carga)))
    _ingresar(db, lector, [_acceso(30, '', '2026-09-24T08:59:00', minor=22)])

    e = mod_escucha.EscuchaLector(app, lector.id)
    e.max_serial = 30
    e.atender_parte(None, _aviso(31))
    assert ('hikvision:puerta', {'dispositivo_id': lector.id, 'cerradura': 'Abierta',
                                 'hora': '2026-09-24T09:00:00-06:00'}) in emitidos


# ─── Ficha del lector: ubicación, notas y foto ────────────────────────────────

@pytest.fixture
def almacen(monkeypatch):
    """Almacenamiento en memoria: las pruebas NO deben subir nada a R2."""
    from flask import Response
    from app.routes.api_hikvision import dispositivos as mod_disp
    guardado = {}

    monkeypatch.setattr(mod_disp.archivos, 'guardar',
                        lambda key, data, ct=None: guardado.__setitem__(key, data) or False)
    monkeypatch.setattr(mod_disp.archivos, 'eliminar', lambda key: guardado.pop(key, None))
    monkeypatch.setattr(mod_disp.archivos, 'enviar',
                        lambda key, mimetype=None, **kw: Response(guardado[key], mimetype=mimetype)
                        if key in guardado else None)
    return guardado


def test_alta_con_ubicacion_y_notas(client, admin, db):
    r = client.post('/api/hikvision/dispositivos', headers=_hdr(admin), json={
        'nombre': 'Recepción', 'host': '192.168.1.200', 'usuario': 'admin', 'password': 'x',
        'ubicacion': 'Planta baja, puerta principal', 'notas': '  ',
    })
    assert r.status_code == 201
    cuerpo = r.get_json()
    assert cuerpo['ubicacion'] == 'Planta baja, puerta principal'
    assert cuerpo['notas'] == ''                 # solo espacios = sin notas
    assert cuerpo['tiene_foto'] is False

    r = client.put(f"/api/hikvision/dispositivos/{cuerpo['id']}", headers=_hdr(admin),
                   json={'notas': 'Lo instaló TI en 2026'})
    assert r.get_json()['notas'] == 'Lo instaló TI en 2026'
    assert r.get_json()['ubicacion'] == 'Planta baja, puerta principal'   # no se tocó


def test_ubicacion_demasiado_larga(client, admin, lector):
    r = client.put(f'/api/hikvision/dispositivos/{lector.id}', headers=_hdr(admin),
                   json={'ubicacion': 'x' * 121})
    assert r.status_code == 422


def test_foto_del_lector_subir_ver_cambiar_y_quitar(client, admin, db, lector, almacen):
    r = client.post(f'/api/hikvision/dispositivos/{lector.id}/foto', headers=_hdr(admin),
                    data={'foto': (_png(), 'lector.png')}, content_type='multipart/form-data')
    assert r.status_code == 200, r.get_json()
    primera = r.get_json()
    assert primera['tiene_foto'] is True and primera['foto_version']
    [key] = list(almacen)
    assert key.startswith('lectores/') and almacen[key][:4] == b'RIFF'     # WebP

    r = client.get(f'/api/hikvision/dispositivos/{lector.id}/foto', headers=_hdr(admin))
    assert r.status_code == 200 and r.mimetype == 'image/webp'

    # Cambiarla borra la anterior (después de guardar la nueva).
    import time as _t
    _t.sleep(1.1)
    r = client.post(f'/api/hikvision/dispositivos/{lector.id}/foto', headers=_hdr(admin),
                    data={'foto': (_png(), 'otra.png')}, content_type='multipart/form-data')
    assert r.get_json()['foto_version'] != primera['foto_version']
    assert len(almacen) == 1 and key not in almacen

    r = client.delete(f'/api/hikvision/dispositivos/{lector.id}/foto', headers=_hdr(admin))
    assert r.get_json()['tiene_foto'] is False and almacen == {}
    r = client.get(f'/api/hikvision/dispositivos/{lector.id}/foto', headers=_hdr(admin))
    assert r.status_code == 404


def test_foto_del_lector_rechaza_lo_que_no_es_imagen(client, admin, lector, almacen):
    import io
    r = client.post(f'/api/hikvision/dispositivos/{lector.id}/foto', headers=_hdr(admin),
                    data={'foto': (io.BytesIO(b'no soy imagen'), 'x.png')},
                    content_type='multipart/form-data')
    assert r.status_code == 422 and almacen == {}


def test_coordinador_no_sube_foto_del_lector(client, coord, lector, almacen):
    r = client.post(f'/api/hikvision/dispositivos/{lector.id}/foto', headers=_hdr(coord),
                    data={'foto': (_png(), 'lector.png')}, content_type='multipart/form-data')
    assert r.status_code == 403


def test_lista_trae_resumen_sin_tocar_el_lector(client, admin, db, lector):
    t = _trab(db, 'OF1', 'Ana')
    _fila_sincronizada(db, lector, t)
    _ingresar(db, lector, [_evento(75, 1, 'OF1', hace_min=0), _evento(76, 2, 'X', hace_min=0)])

    r = client.get('/api/hikvision/dispositivos', headers=_hdr(admin))
    [item] = r.get_json()['items']
    assert item['resumen']['empleados'] == 1
    assert item['resumen']['accesos_hoy'] == 1         # el rechazo no cuenta
    assert item['resumen']['ultimo_acceso']['nombre'] == 'Ana'
