"""Destinatarios de los eventos de WebSocket.

Un evento que se emite a los roles equivocados no rompe nada visible: la
pantalla simplemente se queda con datos viejos hasta que vence la caché. Por eso
estos tres casos se congelan aquí — se detectaron cruzando a qué roles se emite
cada evento contra qué roles pueden abrir la pantalla que lo escucha.
"""
import ast
from pathlib import Path

import pytest

from app.audit_seguridad import PATRONES_SEGURIDAD, es_evento_de_seguridad

RAIZ_APP = Path(__file__).resolve().parent.parent / 'app'


def _constantes_de(arbol) -> dict[str, set[str]]:
    """Asignaciones `NOMBRE = ['a', 'b']` de un árbol, como {nombre: {valores}}."""
    fuera = {}
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Assign) and isinstance(nodo.value, (ast.List, ast.Tuple)):
            for destino in nodo.targets:
                if isinstance(destino, ast.Name):
                    fuera[destino.id] = {
                        e.value for e in nodo.value.elts
                        if isinstance(e, ast.Constant) and isinstance(e.value, str)
                    }
    return fuera


def _arboles():
    for ruta in RAIZ_APP.rglob('*.py'):
        if '__pycache__' in str(ruta):
            continue
        try:
            yield ruta, ast.parse(ruta.read_text(encoding='utf-8'))
        except SyntaxError:  # pragma: no cover
            continue


# Índice global de constantes. Hace falta porque una lista de roles puede vivir
# en otro módulo que el del `emit_to_role` (p. ej. `ROLES_TODOS` en realtime.py,
# usada desde api_users/). Sin esto el resolutor devolvía un conjunto vacío y el
# test pasaba o fallaba por el motivo equivocado.
_CONSTANTES_GLOBALES = {}
for _ruta, _arbol in _arboles():
    _CONSTANTES_GLOBALES.update(_constantes_de(_arbol))


def _destinatarios_de(evento: str) -> list[set[str]]:
    """Roles de cada `emit_to_role(<roles>, '<evento>', …)` del código.

    Resuelve el primer argumento tanto si es una lista literal como si es un
    nombre: primero contra las constantes del propio archivo, y si no está,
    contra el índice global (constantes importadas de otro módulo).
    """
    salida = []
    for ruta, arbol in _arboles():
        constantes = {**_CONSTANTES_GLOBALES, **_constantes_de(arbol)}

        for nodo in ast.walk(arbol):
            if not isinstance(nodo, ast.Call):
                continue
            nombre = getattr(nodo.func, 'id', None) or getattr(nodo.func, 'attr', '')
            if nombre != 'emit_to_role' or len(nodo.args) < 2:
                continue
            arg_evento = nodo.args[1]
            if not (isinstance(arg_evento, ast.Constant) and arg_evento.value == evento):
                continue
            arg_roles = nodo.args[0]
            if isinstance(arg_roles, (ast.List, ast.Tuple)):
                salida.append({
                    e.value for e in arg_roles.elts
                    if isinstance(e, ast.Constant) and isinstance(e.value, str)
                })
            elif isinstance(arg_roles, ast.Name):
                resuelto = constantes.get(arg_roles.id)
                assert resuelto is not None, (
                    f'{ruta.name}: no se pudo resolver la constante de roles '
                    f'{arg_roles.id!r} para {evento!r}. Si la moviste de módulo, '
                    f'este test dejaría de comprobar nada — por eso falla aquí.'
                )
                salida.append(resuelto)
    return salida


class TestDestinatarios:
    """Cada caso corresponde a una pantalla que se quedaba sin refrescar."""

    def test_seguridad_new_llega_al_rol_sistemas(self):
        """/sistemas/seguridad la abre `sistemas`, que NO recibe `bitacora:new`."""
        emisiones = _destinatarios_de('seguridad:new')
        assert emisiones, 'nadie emite seguridad:new'
        for roles in emisiones:
            assert 'sistemas' in roles
            assert 'super_admin' in roles

    def test_bitacora_new_sigue_sin_ir_a_sistemas(self):
        """La bitácora COMPLETA lleva actividad de RRHH; `sistemas` no debe verla.

        Es la contraparte del test anterior: el arreglo fue añadir un evento
        filtrado, no ampliar los destinatarios de éste.
        """
        for roles in _destinatarios_de('bitacora:new'):
            assert 'sistemas' not in roles

    def test_usuario_changed_llega_a_todos_los_roles(self):
        """El Directorio y Mi perfil están abiertos a cualquier sesión iniciada."""
        emisiones = _destinatarios_de('usuario:changed')
        assert emisiones, 'nadie emite usuario:changed'
        esperados = {
            'admin', 'super_admin', 'sistemas', 'coordinador',
            'inventario', 'finanzas', 'solicitante_material',
        }
        for roles in emisiones:
            assert esperados <= roles, f'faltan: {sorted(esperados - roles)}'

    def test_proyecto_changed_llega_a_los_roles_de_inventario(self):
        """Seis pantallas de inventario lo escuchan; las abren estos dos roles."""
        emisiones = _destinatarios_de('proyecto:changed')
        assert emisiones, 'nadie emite proyecto:changed'
        for roles in emisiones:
            assert 'inventario' in roles
            assert 'solicitante_material' in roles
            # No perder a los de antes.
            assert {'admin', 'super_admin', 'coordinador'} <= roles


class TestFiltroDeSeguridad:
    """`es_evento_de_seguridad` debe coincidir con el ILIKE del listado REST."""

    @pytest.mark.parametrize('accion', [
        'Login fallido para admin',
        'Usuario activó 2FA',
        'Antivirus rechazó documento.pdf',
        'Revocó la sesión #12',
        'Creó usuario juan con rol coordinador',
        'Cambió su contraseña',
        'Detección de robo de refresh token',
    ])
    def test_reconoce_los_eventos_de_seguridad(self, accion):
        assert es_evento_de_seguridad(accion)

    @pytest.mark.parametrize('accion', [
        'Actualizó al empleado Juan Pérez',
        'Cerró la nómina del 2026-01-15',
        'Registró un movimiento de inventario',
        'Generó el reporte de horas',
    ])
    def test_ignora_la_operacion_normal(self, accion):
        """Lo que NO debe empujar nada al rol sistemas."""
        assert not es_evento_de_seguridad(accion)

    def test_tolera_vacios(self):
        assert not es_evento_de_seguridad(None)
        assert not es_evento_de_seguridad('')

    def test_no_distingue_mayusculas_como_ilike(self):
        assert es_evento_de_seguridad('LOGIN FALLIDO')
        assert es_evento_de_seguridad('Login Fallido')

    def test_el_listado_rest_usa_esta_misma_lista(self):
        """Una sola fuente de verdad: si divergen, el push y la lista discrepan."""
        from app.routes.api_sistemas.endpoints import _PATRONES_SEGURIDAD
        assert _PATRONES_SEGURIDAD is PATRONES_SEGURIDAD
