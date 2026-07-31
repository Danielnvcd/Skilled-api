"""Registro de peticiones HTTP para el panel de sistemas.

────────────────────────────────────────────────────────────────────────────
POR QUÉ NO VA A LA BASE DE DATOS
────────────────────────────────────────────────────────────────────────────
Lo obvio sería una tabla `peticiones` y un INSERT por request. Sería un error:
cada request de lectura pasaría a hacer también una escritura, duplicando la
carga de Postgres y consumiendo una conexión del pool (que está dimensionado
en 10+10 por worker). Con varios coordinadores y el kiosko RFID trabajando a
la vez, el propio panel de diagnóstico se volvería la causa del problema que
intenta diagnosticar. Y habría que inventar además una política de purga.

En su lugar: un buffer circular en Redis.
  - `LPUSH` + `LTRIM` en una sola pipeline → un round-trip, O(1), sin BD.
  - El `LTRIM` acota la memoria por construcción: nunca más de _MAX_EVENTOS.
  - `EXPIRE` para que un servidor inactivo no deje datos viejos colgados.
  - Si Redis no está, `redis_call` degrada y la request sigue igual de bien:
    el panel se queda sin datos, la aplicación no se entera.

────────────────────────────────────────────────────────────────────────────
QUÉ SE GUARDA (Y QUÉ NO)
────────────────────────────────────────────────────────────────────────────
Se guarda la REGLA de ruta (`/api/users/<int:user_id>`), no la URL concreta
(`/api/users/47`). Dos razones:

  1. Privacidad. El panel lo ve el rol `sistemas`, que a propósito NO tiene
     acceso a los datos de RRHH. Si guardáramos URLs crudas, el panel filtraría
     por la puerta de atrás qué empleado, préstamo o proyecto se consultó —
     justo la información que el rol no debería ver.
  2. Utilidad. Agrupar por regla permite contar y medir por endpoint; con URLs
     crudas cada llamada sería única y no se podría agregar nada.

NUNCA se guardan: cuerpos de request o response, headers, query strings,
cookies ni tokens. La query string es especialmente peligrosa porque arrastra
filtros con nombres y folios.

────────────────────────────────────────────────────────────────────────────
MUESTREO
────────────────────────────────────────────────────────────────────────────
Lo que sirve para diagnosticar es lo anómalo. Se registra SIEMPRE lo que
importa (errores y lentitud) y se muestrea el tráfico sano, que es la mayoría:
así el buffer no se llena de ruido y el costo por request tiende a cero.
"""
from __future__ import annotations

import json
import random
import time

from flask import request

# Clave del buffer circular y su tamaño máximo. 500 eventos con ~200 bytes cada
# uno son ~100 KB en Redis: irrelevante, y suficiente para ver qué pasó en los
# últimos minutos de tráfico.
_CLAVE_BUFFER = 'obs:peticiones'
_MAX_EVENTOS = 500
_TTL_BUFFER = 24 * 3600

# Umbral de "lenta". Coincide con el que ya usa el logger de la app para no
# tener dos definiciones distintas de lentitud en el mismo sistema.
_UMBRAL_LENTA_MS = 500

# 1 de cada N requests sanas y rápidas. Los errores y las lentas se guardan
# siempre, sin muestrear.
_MUESTREO_OK = 20


def _ruta_normalizada() -> str:
    """Regla de la ruta (`/api/users/<int:user_id>`) en vez de la URL concreta.

    Si Flask no pudo emparejar la request con ninguna regla (404), devolvemos
    un marcador genérico en lugar del path real: un escáner probando rutas
    llenaría el buffer de basura y, peor, sus URLs quedarían almacenadas.
    """
    regla = getattr(request, 'url_rule', None)
    if regla is not None and getattr(regla, 'rule', None):
        return regla.rule
    return '<desconocida>'


def _debe_registrar(status: int, duracion_ms: float) -> bool:
    if status >= 400:
        return True
    if duracion_ms >= _UMBRAL_LENTA_MS:
        return True
    return random.randint(1, _MUESTREO_OK) == 1


def registrar_peticion(response):
    """Hook `after_request`. Nunca debe lanzar ni frenar la respuesta."""
    try:
        # El preflight de CORS no aporta nada al diagnóstico y duplicaría el
        # volumen de eventos en un despliegue cross-site como el nuestro.
        if request.method == 'OPTIONS':
            return response

        inicio = getattr(request, '_start_time', None)
        if inicio is None:
            return response
        duracion_ms = (time.time() - inicio) * 1000.0
        status = response.status_code

        if not _debe_registrar(status, duracion_ms):
            return response

        from flask import g
        usuario = getattr(g, '_jwt_user', None)

        evento = json.dumps({
            'ts': time.time(),
            'metodo': request.method,
            'ruta': _ruta_normalizada(),
            'status': status,
            'ms': round(duracion_ms, 1),
            # Solo el id, nunca el nombre: el panel resuelve el username al
            # leer, y así un volcado del buffer no expone quién es quién.
            'uid': getattr(usuario, 'id', None),
            'ip': _ip_cliente(),
        }, separators=(',', ':'))

        from app.extensions import redis_call

        def _empujar(r):
            # Pipeline: LPUSH + LTRIM + EXPIRE viajan en un solo round-trip.
            # El LTRIM es lo que hace que el buffer sea circular y la memoria
            # esté acotada pase lo que pase.
            pipe = r.pipeline()
            pipe.lpush(_CLAVE_BUFFER, evento)
            pipe.ltrim(_CLAVE_BUFFER, 0, _MAX_EVENTOS - 1)
            pipe.expire(_CLAVE_BUFFER, _TTL_BUFFER)
            return pipe.execute()

        redis_call(_empujar)
    except Exception:
        # La observabilidad jamás puede romper una respuesta real. Si algo falla
        # aquí, se pierde un evento del panel y nada más.
        pass
    return response


def _ip_cliente() -> str:
    try:
        from app.extensions import get_real_client_ip_flask
        return get_real_client_ip_flask() or ''
    except Exception:
        return ''


def leer_peticiones(limite: int = 200) -> list[dict]:
    """Últimos eventos, del más reciente al más viejo."""
    from app.extensions import redis_call
    limite = max(1, min(int(limite or 200), _MAX_EVENTOS))
    crudos = redis_call(lambda r: r.lrange(_CLAVE_BUFFER, 0, limite - 1), default=[]) or []
    salida = []
    for c in crudos:
        try:
            salida.append(json.loads(c))
        except Exception:
            continue
    return salida


def resumen(eventos: list[dict]) -> dict:
    """Agregados calculados AL LEER, no al escribir.

    Mantener contadores incrementales en Redis obligaría a más comandos en el
    camino caliente de cada request. Como el buffer tiene un tope de 500
    eventos, agregarlo en el momento de la consulta es trivial y deja el
    registro con el costo mínimo.
    """
    if not eventos:
        return {
            'total': 0, 'errores': 0, 'lentas': 0,
            'ms_promedio': 0, 'ms_p95': 0, 'por_ruta': [],
        }

    duraciones = sorted(e.get('ms', 0) for e in eventos)
    errores = sum(1 for e in eventos if (e.get('status') or 0) >= 400)
    lentas = sum(1 for e in eventos if (e.get('ms') or 0) >= _UMBRAL_LENTA_MS)

    # p95 por índice sobre la lista ordenada. Con ≤500 muestras no vale la pena
    # nada más sofisticado.
    idx_p95 = min(len(duraciones) - 1, int(len(duraciones) * 0.95))

    agrupado: dict[tuple, dict] = {}
    for e in eventos:
        clave = (e.get('metodo'), e.get('ruta'))
        fila = agrupado.setdefault(clave, {
            'metodo': e.get('metodo'), 'ruta': e.get('ruta'),
            'conteo': 0, 'errores': 0, 'ms_total': 0.0, 'ms_max': 0.0,
        })
        fila['conteo'] += 1
        fila['ms_total'] += e.get('ms') or 0
        fila['ms_max'] = max(fila['ms_max'], e.get('ms') or 0)
        if (e.get('status') or 0) >= 400:
            fila['errores'] += 1

    por_ruta = []
    for fila in agrupado.values():
        por_ruta.append({
            'metodo': fila['metodo'],
            'ruta': fila['ruta'],
            'conteo': fila['conteo'],
            'errores': fila['errores'],
            'ms_promedio': round(fila['ms_total'] / fila['conteo'], 1),
            'ms_max': round(fila['ms_max'], 1),
        })
    # Las rutas más lentas primero: es lo que se busca al abrir el panel.
    por_ruta.sort(key=lambda f: f['ms_promedio'], reverse=True)

    return {
        'total': len(eventos),
        'errores': errores,
        'lentas': lentas,
        'ms_promedio': round(sum(duraciones) / len(duraciones), 1),
        'ms_p95': round(duraciones[idx_p95], 1),
        'por_ruta': por_ruta[:25],
        # Para que el panel explique bien lo que muestra: el tráfico sano está
        # muestreado, así que "total" no es el número real de requests.
        'muestreo_ok': _MUESTREO_OK,
        'umbral_lenta_ms': _UMBRAL_LENTA_MS,
    }
