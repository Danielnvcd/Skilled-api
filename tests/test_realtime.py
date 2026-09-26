"""
Capa de tiempo real (`app/realtime.py`): contrato de emisión y de eventos.

Dos cosas se fijan aquí:

1. **Los emit nunca tumban una request.** Los blueprints emiten DESPUÉS del
   commit; si `socketio.emit` levantara, el endpoint devolvería 500 con el
   trabajo ya guardado en base. Todos los helpers tragan la excepción y
   loguean, y eso tiene que seguir siendo cierto.

2. **Los nombres de evento no cambian en silencio.** El SPA invalida sus cachés
   escuchando estos nombres (`invalidateOn`); renombrar uno en el backend rompe
   la actualización en vivo sin que falle ningún test ni se vea en el log. El
   test de contrato congela la lista.

Se mockea `socketio.emit` a propósito: montar un servidor Socket.IO real haría
la suite lenta y frágil, y lo que se quiere verificar es a QUÉ SALA se emite y
QUÉ pasa cuando el transporte falla.
"""
import ast
import pathlib
from unittest.mock import MagicMock, patch

import pytest

from app import realtime

RAIZ_APP = pathlib.Path(__file__).resolve().parents[1] / 'app'


@pytest.fixture
def emit():
    """Sustituye `socketio.emit` por un mock y lo entrega."""
    with patch.object(realtime.socketio, 'emit') as m:
        yield m


def _salas(mock_emit):
    """Salas a las que se emitió, en orden."""
    return [kw.get('to') for _, kw in mock_emit.call_args_list]


# ═══════════════════════════════════════════════════════════════════════════
# emit_to_role
# ═══════════════════════════════════════════════════════════════════════════

class TestEmitToRole:

    def test_un_string_se_trata_como_un_rol(self, emit):
        realtime.emit_to_role('admin', 'producto:changed', {'id': 1})
        assert _salas(emit) == ['role:admin']

    def test_emite_una_vez_por_rol(self, emit):
        realtime.emit_to_role(['admin', 'inventario'], 'producto:changed', {'id': 1})
        assert _salas(emit) == ['role:admin', 'role:inventario']

    def test_normaliza_a_minusculas(self, emit):
        """La sala se une en minúsculas durante el handshake; si el emisor manda
        'Admin' y no se normalizara, el evento se perdería en el vacío."""
        realtime.emit_to_role(['Admin', 'SUPER_ADMIN'], 'x:y', {})
        assert _salas(emit) == ['role:admin', 'role:super_admin']

    @pytest.mark.parametrize('roles', [[], None, [''], [None]])
    def test_no_emite_a_la_sala_vacia(self, emit, roles):
        """Un rol vacío daría la sala 'role:', que es un broadcast accidental
        a nadie (o peor, a una sala compartida). Se descarta."""
        realtime.emit_to_role(roles, 'x:y', {})
        assert emit.call_args_list == []

    def test_conserva_evento_y_payload(self, emit):
        realtime.emit_to_role('inventario', 'movimiento:changed', {'id': 7, 'tipo': 'ENTRADA'})
        args, kwargs = emit.call_args
        assert args[0] == 'movimiento:changed'
        assert args[1] == {'id': 7, 'tipo': 'ENTRADA'}

    def test_un_fallo_de_transporte_no_propaga(self):
        """INVARIANTE CRÍTICO: el emit va después del commit. Si levantara, el
        endpoint respondería 500 con el cambio ya persistido."""
        with patch.object(realtime.socketio, 'emit', side_effect=RuntimeError('redis caído')):
            realtime.emit_to_role(['admin'], 'producto:changed', {'id': 1})  # no debe levantar

    def test_si_un_rol_falla_los_demas_siguen_recibiendo(self):
        fallos = [RuntimeError('boom'), None]
        with patch.object(realtime.socketio, 'emit', side_effect=fallos) as m:
            realtime.emit_to_role(['admin', 'inventario'], 'x:y', {})
        assert _salas(m) == ['role:admin', 'role:inventario']


# ═══════════════════════════════════════════════════════════════════════════
# emit_to_user / emit_to_reporte
# ═══════════════════════════════════════════════════════════════════════════

class TestEmitDirigido:

    def test_emit_to_user_usa_la_sala_del_usuario(self, emit):
        realtime.emit_to_user(42, 'notif:new', {'id': 9})
        assert _salas(emit) == ['user:42']

    def test_emit_to_reporte_usa_la_sala_del_reporte(self, emit):
        realtime.emit_to_reporte(7, 'reporte:estado_cambio', {'estado': 'CERRADO'})
        assert _salas(emit) == ['reporte:7']

    def test_emit_to_user_no_propaga_fallos(self):
        with patch.object(realtime.socketio, 'emit', side_effect=RuntimeError('boom')):
            realtime.emit_to_user(1, 'notif:new', {})

    def test_emit_to_reporte_no_propaga_fallos(self):
        with patch.object(realtime.socketio, 'emit', side_effect=RuntimeError('boom')):
            realtime.emit_to_reporte(1, 'reporte:estado_cambio', {})


# ═══════════════════════════════════════════════════════════════════════════
# force_logout_user  (acción de seguridad)
# ═══════════════════════════════════════════════════════════════════════════

class TestForceLogout:

    def test_avisa_al_spa_por_la_sala_del_usuario(self, emit):
        with patch.object(realtime.socketio, 'server', MagicMock()):
            realtime.force_logout_user(42)
        args, kwargs = emit.call_args
        assert args[0] == 'auth:force_logout'
        assert kwargs.get('to') == 'user:42'

    def test_desconecta_los_sockets_locales_del_usuario(self, emit):
        server = MagicMock()
        server.manager.rooms = {'/': {'user:42': {'sid-a': None, 'sid-b': None}}}
        with patch.object(realtime.socketio, 'server', server):
            realtime.force_logout_user(42)
        desconectados = {c.args[0] for c in server.disconnect.call_args_list}
        assert desconectados == {'sid-a', 'sid-b'}

    def test_no_toca_sockets_de_otros_usuarios(self, emit):
        server = MagicMock()
        server.manager.rooms = {'/': {'user:42': {'mio': None}, 'user:99': {'ajeno': None}}}
        with patch.object(realtime.socketio, 'server', server):
            realtime.force_logout_user(42)
        desconectados = {c.args[0] for c in server.disconnect.call_args_list}
        assert desconectados == {'mio'}

    def test_sin_sockets_locales_no_revienta(self, emit):
        """Caso multi-worker: los sockets viven en otro worker. El emit por Redis
        es lo que los cierra; aquí no hay nada que desconectar."""
        server = MagicMock()
        server.manager.rooms = {'/': {}}
        with patch.object(realtime.socketio, 'server', server):
            realtime.force_logout_user(42)
        assert server.disconnect.call_args_list == []

    def test_un_fallo_no_propaga(self):
        """Se llama desde revocar-sesiones y cambio de password: si levantara,
        la acción de seguridad fallaría con 500 pese a haberse aplicado."""
        with patch.object(realtime.socketio, 'emit', side_effect=RuntimeError('boom')):
            with patch.object(realtime.socketio, 'server', MagicMock(side_effect=RuntimeError)):
                realtime.force_logout_user(42)


# ═══════════════════════════════════════════════════════════════════════════
# Contrato de nombres de evento
# ═══════════════════════════════════════════════════════════════════════════

# Nombres que el backend emite y el SPA escucha para invalidar sus cachés.
# NO renombrar sin cambiar también el `invalidateOn` correspondiente en
# plantilla-frontend (`src/`), o la UI dejará de refrescarse en vivo sin que
# falle nada visible.
EVENTOS_ESPERADOS = {
    'abono:new', 'ajuste:changed', 'almacen:changed', 'archivo:sync_progreso',
    'asignacion:changed',
    'baja:changed', 'bitacora:new', 'compra:changed', 'credencial:changed',
    'documento:changed', 'empleado:changed', 'estante:changed',
    'herramienta:changed', 'incidencia:changed', 'mantenimiento:changed',
    'movimiento:changed', 'nota:changed', 'notif:new', 'notif:read',
    'notif:read_all', 'prenomina:changed', 'prestamo:changed',
    'producto:changed', 'producto:imagen_progreso', 'proyecto:changed',
    'proyecto_material:changed', 'reporte:estado_cambio',
    'reporte:lista_changed', 'reporte:registros_cambio',
    # Gemelo de `bitacora:new` para el rol `sistemas`, que no recibe aquél.
    # Solo lleva el id; la pantalla recarga por REST (ver realtime.py).
    'seguridad:new',
    'solicitud:changed',
    'toma:changed', 'usuario:changed',
}


def _eventos_emitidos_por_la_app() -> set[str]:
    """Recorre `app/` con AST y junta el nombre de evento de cada `emit_to_*`."""
    encontrados = set()
    for ruta in RAIZ_APP.rglob('*.py'):
        if '__pycache__' in str(ruta):
            continue
        try:
            arbol = ast.parse(ruta.read_text(encoding='utf-8'))
        except SyntaxError:  # pragma: no cover
            continue
        for nodo in ast.walk(arbol):
            if not isinstance(nodo, ast.Call):
                continue
            f = nodo.func
            nombre = f.id if isinstance(f, ast.Name) else getattr(f, 'attr', '')
            if not nombre.startswith('emit_to_'):
                continue
            for arg in nodo.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and ':' in arg.value:
                    encontrados.add(arg.value)
                    break
    return encontrados


class TestContratoDeEventos:

    def test_los_nombres_de_evento_no_cambiaron(self):
        """Congela la lista de eventos. Si este test falla porque agregaste uno,
        súmalo a EVENTOS_ESPERADOS y añade su `invalidateOn` en el SPA. Si falla
        porque desapareció uno, revisa qué pantalla dejó de refrescarse."""
        actuales = _eventos_emitidos_por_la_app()
        assert actuales == EVENTOS_ESPERADOS, (
            f'nuevos: {sorted(actuales - EVENTOS_ESPERADOS)} | '
            f'desaparecidos: {sorted(EVENTOS_ESPERADOS - actuales)}'
        )

    def test_todos_usan_el_formato_dominio_dos_puntos_accion(self):
        for ev in _eventos_emitidos_por_la_app():
            dominio, _, accion = ev.partition(':')
            assert dominio and accion, f'evento mal formado: {ev!r}'
            assert ev.islower(), f'evento debe ir en minúsculas: {ev!r}'
