"""Resiliencia de la conexión ERP ↔ lector, contra un lector SIMULADO.

Cada prueba reproduce un fallo real o posible del equipo (ver
`tests/lector_falso.py`) y comprueba que la escucha lo supera sin perder
eventos, sin bloquear la cuenta y avisando a los admins cuando corresponde.
"""
from datetime import datetime, timedelta, timezone

import pytest
from werkzeug.security import generate_password_hash

from app.models import (
    DispositivoHikvision, EventoHikvision, Notificacion, SucesoEscuchaHikvision,
    TareaHikvision, Trabajador, User,
)
from app.routes.api_auth import _encode_access_token
from lector_falso import CDMX, LectorFalso

PASSWORD = 'secreto-de-prueba'


def _hdr(user):
    return {'Authorization': f'Bearer {_encode_access_token(user)}'}


@pytest.fixture
def admin(db):
    u = User(username='res_admin', password_hash=generate_password_hash('Pass123!'), role='admin')
    db.session.add(u); db.session.commit()
    return u


@pytest.fixture
def lector(db):
    d = DispositivoHikvision(nombre='Lector Oficina', host='192.168.1.159', puerto=80,
                             usuario='admin', password=PASSWORD,
                             numero_serie='SN-UNO', firmware='V4.48.40')
    db.session.add(d); db.session.commit()
    return d


@pytest.fixture
def equipo(monkeypatch):
    """El lector simulado, conectado a TODO cliente ISAPI de la prueba."""
    from app.services.hikvision.client import ClienteHikvision
    falso = LectorFalso(password=PASSWORD)
    monkeypatch.setattr(ClienteHikvision, 'transporte', falso.transporte())
    return falso


@pytest.fixture
def escucha(app, lector):
    from app.services.hikvision.escucha import EscuchaLector
    return EscuchaLector(app, lector.id)


def _sucesos(lector, tipo):
    return SucesoEscuchaHikvision.query.filter_by(dispositivo_id=lector.id, tipo=tipo).count()


def _alertas(clave):
    return Notificacion.query.filter(Notificacion.referencia.like(f'%:{clave}')).count()


# ─── Flujo normal y cortes ────────────────────────────────────────────────────

def test_sesion_completa_guarda_lo_del_stream(db, lector, equipo, escucha):
    equipo.agregar_evento(avisar=False)            # historial previo
    equipo.agregar_evento()                        # llega por el stream
    equipo.latido()
    equipo.agregar_evento(minor=21, emp='')
    equipo.cortar()

    escucha._sesion()

    assert EventoHikvision.query.count() == 3
    db.session.refresh(lector)
    assert lector.escucha_estado == 'CONECTADO'
    assert _sucesos(lector, 'CONECTADO') == 1
    # Ninguna credencial viajó con un nonce vencido: no cuenta para el bloqueo.
    assert equipo.nonces_vencidos == 0 and equipo.fallos == 0


def test_lo_ocurrido_sin_conexion_se_recupera_al_reconectar(db, lector, equipo, escucha):
    equipo.agregar_evento()
    equipo.cortar()
    escucha._sesion()
    assert EventoHikvision.query.count() == 1

    # Mientras la escucha estaba caída, el lector siguió registrando.
    for _ in range(40):                            # más de una página (30)
        equipo.agregar_evento(avisar=False)
    equipo.cortar()
    escucha._sesion()

    assert EventoHikvision.query.count() == 41
    assert equipo.nonces_vencidos == 0


def test_el_historial_reenviado_no_cuesta_consultas(db, lector, equipo, escucha):
    ev = equipo.agregar_evento(avisar=False)
    equipo.cortar()
    escucha._sesion()
    consultas_antes = sum(1 for m, r in equipo.peticiones if 'AcsEvent' in r)

    # Al reconectar el lector reenvía lo viejo: seriales ya conocidos.
    equipo.enviar_parte({'eventType': 'AccessControllerEvent', 'dateTime': ev['time'],
                         'AccessControllerEvent': {'majorEventType': 5, 'serialNo': ev['serialNo']}})
    equipo.cortar()
    escucha._sesion()
    consultas_despues = sum(1 for m, r in equipo.peticiones if 'AcsEvent' in r)
    # Solo las de arranque (verificar reinicio + ponerse al día), ninguna por el reenvío.
    assert consultas_despues - consultas_antes == 2


# ─── Reinicio de seriales y cambio de equipo ──────────────────────────────────

def test_reinicio_de_seriales_abre_epoca_nueva_y_no_pierde_eventos(db, admin, lector, equipo,
                                                                   escucha):
    antes = equipo.ahora() - timedelta(hours=2)
    for i in range(10):
        equipo.agregar_evento(cuando=antes + timedelta(minutes=i), avisar=False)
    equipo.cortar()
    escucha._sesion()
    assert EventoHikvision.query.count() == 10

    # Reset de fábrica: la numeración vuelve a 1 con accesos NUEVOS.
    equipo.reiniciar_seriales()
    equipo.agregar_evento(avisar=False)
    equipo.agregar_evento(avisar=False)
    equipo.cortar()
    escucha._sesion()

    db.session.refresh(lector)
    assert lector.epoca_eventos == 2
    nuevos = EventoHikvision.query.filter_by(epoca=2).order_by(EventoHikvision.serial_no).all()
    assert [e.serial_no for e in nuevos] == [1, 2]          # no chocaron con los viejos
    assert EventoHikvision.query.count() == 12
    assert _sucesos(lector, 'SERIALES_REINICIADOS') == 1
    assert _alertas('seriales') == 1


def test_lector_sin_eventos_nuevos_no_parece_reiniciado(db, lector, equipo, escucha):
    for _ in range(5):
        equipo.agregar_evento(avisar=False)
    equipo.cortar()
    escucha._sesion()
    equipo.cortar()
    escucha._sesion()
    db.session.refresh(lector)
    assert lector.epoca_eventos == 1


def test_reinicio_detectado_en_vivo_por_el_stream(db, lector, equipo, escucha):
    antes = equipo.ahora() - timedelta(hours=1)
    for i in range(5):
        equipo.agregar_evento(cuando=antes + timedelta(minutes=i), avisar=False)
    equipo.cortar()
    escucha._sesion()

    equipo.reiniciar_seriales()
    # La sesión ya está abierta cuando llega el aviso con serial 1 «viejo»
    # pero fecha NUEVA: se detecta ahí mismo, sin esperar la vigilancia.
    escucha.max_serial = 5
    ev = equipo.agregar_evento(avisar=False)
    from app.services.hikvision.client import ClienteHikvision
    with ClienteHikvision.desde_dispositivo(lector) as cli:
        import json
        escucha.atender_parte(cli, json.dumps({
            'eventType': 'AccessControllerEvent', 'dateTime': ev['time'],
            'AccessControllerEvent': {'majorEventType': 5, 'serialNo': 1}}).encode())
    db.session.refresh(lector)
    assert lector.epoca_eventos == 2
    assert EventoHikvision.query.filter_by(epoca=2).count() == 1


def test_otro_equipo_en_la_misma_ip(db, admin, lector, equipo, escucha):
    equipo.serie = 'SN-DOS'
    equipo.cortar()
    escucha._sesion()
    db.session.refresh(lector)
    assert lector.numero_serie == 'SN-DOS'
    assert lector.epoca_eventos == 2
    assert _sucesos(lector, 'EQUIPO_CAMBIADO') == 1
    assert _alertas('equipo_cambiado') == 1


def test_firmware_nuevo_se_avisa(db, admin, lector, equipo, escucha):
    equipo.firmware = 'V4.50.0'
    equipo.cortar()
    escucha._sesion()
    db.session.refresh(lector)
    assert lector.firmware == 'V4.50.0'
    assert _alertas('firmware') == 1
    assert lector.epoca_eventos == 1       # mismo equipo: los seriales siguen


def test_ip_dinamica_se_avisa(db, admin, lector, equipo, escucha):
    equipo.direccionamiento = 'dynamic'
    equipo.cortar()
    escucha._sesion()
    assert _sucesos(lector, 'IP_DINAMICA') == 1
    assert _alertas('ip_dinamica') == 1


# ─── Credenciales: nunca bloquear la cuenta ───────────────────────────────────

def test_contrasena_incorrecta_informa_intentos_y_espera(db, admin, lector, equipo, escucha):
    from app.services.hikvision.errores import ErrorAutenticacion
    from app.services.hikvision.escucha import ESPERA_AUTENTICACION_S
    equipo.password = 'la-cambiaron-en-el-lector'

    with pytest.raises(ErrorAutenticacion) as info:
        escucha._sesion()
    assert info.value.intentos_restantes == 4
    assert 'Quedan 4 intento' in info.value.mensaje

    assert escucha._tras_rechazo_de_credenciales(info.value) == ESPERA_AUTENTICACION_S
    assert _sucesos(lector, 'AUTENTICACION') == 1
    assert _alertas('credenciales') == 1


def test_con_pocos_intentos_la_escucha_se_detiene_antes_del_bloqueo(db, admin, lector, equipo,
                                                                    escucha):
    from app.services.hikvision.errores import ErrorAutenticacion
    equipo.password = 'otra'
    equipo.fallos = 2                               # ya hubo intentos fallidos
    with pytest.raises(ErrorAutenticacion) as info:
        escucha._sesion()
    assert info.value.intentos_restantes == 2
    assert escucha._tras_rechazo_de_credenciales(info.value) is None   # detenerse
    assert not equipo.bloqueado


def test_cuenta_ya_bloqueada_espera_lo_que_dice_el_lector(db, admin, lector, equipo, escucha):
    from app.services.hikvision.errores import ErrorAutenticacion
    equipo.bloqueado = True
    with pytest.raises(ErrorAutenticacion) as info:
        escucha._sesion()
    assert info.value.bloqueado and info.value.segundos_bloqueo == 1800
    assert escucha._tras_rechazo_de_credenciales(info.value) == 1860
    assert 'bloqueó la cuenta' in info.value.mensaje


def test_error_autenticacion_lee_json_y_xml():
    from app.services.hikvision.client import error_autenticacion
    e = error_autenticacion('{"statusCode":4,"retryTimes":3,"lockStatus":"unlock"}', 'GET /x')
    assert e.intentos_restantes == 3 and not e.bloqueado
    e = error_autenticacion('<lockStatus>locked</lockStatus><resLockTime>600</resLockTime>', 'GET /x')
    assert e.bloqueado and e.segundos_bloqueo == 600
    e = error_autenticacion('<userCheck><statusValue>401</statusValue></userCheck>', 'GET /x')
    assert e.intentos_restantes is None and not e.bloqueado


# ─── Reloj ────────────────────────────────────────────────────────────────────

def test_reloj_desfasado_se_avisa(db, admin, lector, equipo, escucha):
    from app.services.hikvision.client import ClienteHikvision
    equipo.desfase = timedelta(minutes=7)
    with ClienteHikvision.desde_dispositivo(lector) as cli:
        escucha.vigilar_reloj(cli)
    assert _sucesos(lector, 'RELOJ_DESFASADO') == 1
    assert _alertas('reloj') == 1


def test_reloj_en_hora_no_avisa(db, admin, lector, equipo, escucha):
    from app.services.hikvision.client import ClienteHikvision
    with ClienteHikvision.desde_dispositivo(lector) as cli:
        escucha.vigilar_reloj(cli)
    assert _sucesos(lector, 'RELOJ_DESFASADO') == 0


def test_configurar_ntp_escribe_servidor_y_modo(db, lector, equipo):
    from app.services.hikvision.client import ClienteHikvision
    with ClienteHikvision.desde_dispositivo(lector) as cli:
        cli.configurar_ntp('216.239.35.0', 90)
    ntp = equipo.escrituras['/ISAPI/System/time/ntpServers/1']
    assert '<addressingFormatType>ipaddress</addressingFormatType>' in ntp
    assert '<ipAddress>216.239.35.0</ipAddress>' in ntp
    assert '<synchronizeInterval>90</synchronizeInterval>' in ntp
    hora = equipo.escrituras['/ISAPI/System/time']
    assert '<timeMode>NTP</timeMode>' in hora and '<timeZone>CST+6:00:00</timeZone>' in hora


@pytest.mark.parametrize('servidor', ['', 'con espacios', 'a' * 65, 'x;rm -rf'])
def test_configurar_ntp_rechaza_servidores_invalidos(lector, equipo, servidor):
    from app.services.hikvision.client import ClienteHikvision
    from app.services.hikvision.errores import ErrorConfiguracion
    with ClienteHikvision.desde_dispositivo(lector) as cli:
        with pytest.raises(ErrorConfiguracion):
            cli.configurar_ntp(servidor)
    assert '/ISAPI/System/time/ntpServers/1' not in equipo.escrituras


def test_endpoint_ntp(client, admin, lector, equipo):
    r = client.post(f'/api/hikvision/dispositivos/{lector.id}/ntp', headers=_hdr(admin),
                    json={'servidor': 'time.google.com', 'intervalo_min': 60})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['hora']['modo'] == 'NTP'
    r = client.post(f'/api/hikvision/dispositivos/{lector.id}/ntp', headers=_hdr(admin),
                    json={'servidor': 'no valido'})
    assert r.status_code == 422


# ─── Alertas, desconexiones y candado ─────────────────────────────────────────

def test_las_alertas_no_se_repiten(db, admin, lector):
    from app.services.hikvision import vigilancia
    assert vigilancia.alertar(lector, 'reloj', 't', 'm') is True
    db.session.commit()
    assert vigilancia.alertar(lector, 'reloj', 't', 'm') is False
    assert _alertas('reloj') == 1


def test_desconexion_larga_alerta_una_vez_y_reconexion_avisa(app, db, admin, lector, equipo,
                                                              escucha):
    from app.services.hikvision.escucha import Supervisor
    lector.escucha_estado = 'DESCONECTADO'
    lector.escucha_latido = datetime.now(timezone.utc) - timedelta(minutes=10)
    db.session.commit()

    sup = Supervisor(app)
    sup.vigilar_desconexiones()
    sup.vigilar_desconexiones()
    assert _alertas('desconectado') == 1

    equipo.cortar()
    escucha._sesion()
    assert _alertas('reconectado') == 1


def test_desconexion_breve_no_alerta(app, db, admin, lector):
    from app.services.hikvision.escucha import Supervisor
    lector.escucha_estado = 'DESCONECTADO'
    lector.escucha_latido = datetime.now(timezone.utc) - timedelta(minutes=2)
    db.session.commit()
    Supervisor(app).vigilar_desconexiones()
    assert _alertas('desconectado') == 0


class RedisFalso:
    def __init__(self):
        self.datos = {}

    def set(self, k, v, nx=False, ex=None):
        if nx and k in self.datos:
            return None
        self.datos[k] = v
        return True

    def get(self, k):
        return self.datos.get(k)

    def expire(self, k, s):
        return k in self.datos

    def delete(self, k):
        self.datos.pop(k, None)


def test_solo_una_escucha_toma_el_control(app, monkeypatch):
    import app.extensions as ext
    from app.services.hikvision.escucha import Supervisor
    redis = RedisFalso()
    monkeypatch.setattr(ext, 'get_redis', lambda: redis)

    a, b = Supervisor(app), Supervisor(app)
    b._id = 'otro-host:123'
    assert a.tomar_liderazgo() is True
    assert b.tomar_liderazgo() is False
    assert a.tomar_liderazgo() is True           # renueva el suyo
    a.soltar_liderazgo()
    assert b.tomar_liderazgo() is True           # el relevo lo toma


def test_sin_redis_la_escucha_sigue(app, monkeypatch):
    import app.extensions as ext
    from app.services.hikvision.escucha import Supervisor
    monkeypatch.setattr(ext, 'get_redis', lambda: None)
    assert Supervisor(app).tomar_liderazgo() is True


# ─── Sincronización en segundo plano ──────────────────────────────────────────

def _oficinistas(db, n):
    ts = []
    for i in range(n):
        t = Trabajador(no_empleado=f'T{i}', nombre=f'P{i}', nombre_apellidos='X',
                       es_oficina=True, foto_perfil='perfiles/x.webp', activo=True)
        db.session.add(t)
        ts.append(t)
    db.session.commit()
    return ts


@pytest.fixture
def sync_falso(monkeypatch):
    """La sincronización de UN empleado, sin equipo: siempre OK."""
    from app.services.hikvision import sincronizar as svc

    class Cli:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return None

        def capacidades_usuario(self):
            return {'employee_no_max': 32, 'nombre_max': 128}

    monkeypatch.setattr(svc.ClienteHikvision, 'desde_dispositivo', classmethod(lambda cls, d: Cli()))
    monkeypatch.setattr(svc, 'sincronizar_uno', lambda cli, d, t, estados, caps, **kw: {
        'trabajador_id': t.id, 'no_empleado': t.no_empleado, 'nombre_completo': t.nombre_completo,
        'ok': True, 'estado': 'SINCRONIZADO', 'accion': 'creado', 'error': '',
    })


def test_tanda_grande_va_a_segundo_plano(client, db, admin, lector, sync_falso):
    from app.services.hikvision import tareas
    ts = _oficinistas(db, 8)
    r = client.post(f'/api/hikvision/dispositivos/{lector.id}/sincronizar', headers=_hdr(admin),
                    json={'trabajador_ids': [t.id for t in ts]})
    assert r.status_code == 202
    tarea_id = r.get_json()['tarea']['id']
    assert db.session.get(TareaHikvision, tarea_id).estado == 'PENDIENTE'

    tarea = tareas.reclamar_siguiente()
    assert tarea.id == tarea_id and tarea.estado == 'EN_CURSO'
    tareas.ejecutar(tarea)

    r = client.get(f'/api/hikvision/tareas/{tarea_id}', headers=_hdr(admin))
    cuerpo = r.get_json()
    assert cuerpo['estado'] == 'TERMINADA' and cuerpo['procesados'] == 8
    assert cuerpo['resumen'] == {'sincronizados': 8, 'fallidos': 0, 'total': 8}
    from app.models import AuditLog
    assert AuditLog.query.filter(AuditLog.action.like('%(tarea %')).count() == 1


def test_tanda_chica_sigue_siendo_inmediata(client, db, admin, lector, sync_falso):
    ts = _oficinistas(db, 3)
    r = client.post(f'/api/hikvision/dispositivos/{lector.id}/sincronizar', headers=_hdr(admin),
                    json={'trabajador_ids': [t.id for t in ts]})
    assert r.status_code == 200 and r.get_json()['resumen']['sincronizados'] == 3
    assert TareaHikvision.query.count() == 0


def test_tarea_abandonada_se_marca_interrumpida(db, lector):
    from app.services.hikvision import tareas
    vieja = TareaHikvision(dispositivo_id=lector.id, trabajador_ids=[1], estado='EN_CURSO', total=1,
                           iniciada_en=datetime.now(timezone.utc) - timedelta(hours=2))
    reciente = TareaHikvision(dispositivo_id=lector.id, trabajador_ids=[1], estado='EN_CURSO',
                              total=1, iniciada_en=datetime.now(timezone.utc))
    db.session.add_all([vieja, reciente]); db.session.commit()
    assert tareas.recuperar_interrumpidas() == 1
    db.session.commit()
    assert vieja.estado == 'ERROR' and 'Interrumpida' in vieja.error
    assert reciente.estado == 'EN_CURSO'


def test_tarea_con_lector_caido_termina_en_error(db, admin, lector, monkeypatch):
    from app.services.hikvision import sincronizar as svc
    from app.services.hikvision import tareas
    from app.services.hikvision.errores import ErrorConexion
    _oficinistas(db, 1)

    def caido(cls, d):
        raise ErrorConexion('No se pudo conectar con el lector.')

    monkeypatch.setattr(svc.ClienteHikvision, 'desde_dispositivo', classmethod(caido))
    t = tareas.encolar(lector, [Trabajador.query.first().id]); db.session.commit()
    tareas.ejecutar(tareas.reclamar_siguiente())
    assert t.estado == 'ERROR' and 'conectar' in t.error


# ─── Retención y salud ────────────────────────────────────────────────────────

def test_retencion_borra_solo_lo_viejo(db, lector):
    from app.services.hikvision import vigilancia
    from app.services.hikvision.retencion import purgar
    ahora = datetime.now(timezone.utc)
    for serial, dias in ((1, 400), (2, 10)):
        db.session.add(EventoHikvision(
            dispositivo_id=lector.id, serial_no=serial, fecha_hora=ahora - timedelta(days=dias),
            hora_local='x', major=5, minor=75, tipo='permitido'))
    vigilancia.registrar(lector.id, 'CONECTADO')
    db.session.add(SucesoEscuchaHikvision(dispositivo_id=lector.id, tipo='CONECTADO',
                                          creado_en=ahora - timedelta(days=100)))
    db.session.add(TareaHikvision(dispositivo_id=lector.id, trabajador_ids=[], estado='TERMINADA',
                                  creada_en=ahora - timedelta(days=40)))
    db.session.add(TareaHikvision(dispositivo_id=lector.id, trabajador_ids=[], estado='PENDIENTE',
                                  creada_en=ahora - timedelta(days=40)))
    db.session.commit()

    assert purgar(meses=12) == {'eventos': 1, 'sucesos': 1, 'tareas': 1}
    db.session.commit()
    assert EventoHikvision.query.count() == 1
    assert TareaHikvision.query.filter_by(estado='PENDIENTE').count() == 1   # nunca las pendientes


def test_salud_sale_de_la_base(client, db, admin, lector, equipo, escucha):
    equipo.agregar_evento()
    equipo.cortar()
    escucha._sesion()
    peticiones = len(equipo.peticiones)

    r = client.get(f'/api/hikvision/dispositivos/{lector.id}/salud', headers=_hdr(admin))
    assert r.status_code == 200
    cuerpo = r.get_json()
    assert cuerpo['ultimas_24h']['conexiones'] == 1
    assert cuerpo['ultimas_24h']['eventos'] == 1
    assert cuerpo['ultimas_24h']['retraso_mediana_s'] is not None
    assert cuerpo['sucesos'][0]['tipo'] == 'CONECTADO'
    assert len(equipo.peticiones) == peticiones       # no tocó el lector


def test_cada_conexion_cuenta_y_las_caidas_repetidas_no_inflan_la_bitacora(db, lector, equipo,
                                                                          escucha):
    lector.escucha_estado = 'CONECTADO'       # el proceso anterior murió sin avisar
    db.session.commit()
    equipo.cortar()
    escucha._sesion()
    equipo.cortar()
    escucha._sesion()
    assert _sucesos(lector, 'CONECTADO') == 2

    for _ in range(3):                        # reintentos durante una caída
        escucha._marcar('DESCONECTADO', 'No se pudo conectar con el lector.')
    assert _sucesos(lector, 'DESCONECTADO') == 1
