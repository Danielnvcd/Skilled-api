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

# ── Segunda capa: contadores EXACTOS por día ────────────────────────────────
# El buffer de arriba es una MUESTRA con detalle; sirve para "¿qué está pasando
# ahora?" pero no para analizar: va muestreado y solo caben 500 eventos.
#
# Estos contadores son la otra mitad: se incrementan en TODA petición, sin
# muestreo, así que los totales son reales. Contar no es lo mismo que guardar —
# aquí solo viven números, no eventos, así que un día completo ocupa unos pocos
# KB por más tráfico que haya.
#
# Es la separación clásica entre métricas (todo, agregado) y muestras (algunas,
# con detalle). Cada una responde preguntas que la otra no puede.
#
# Todo vive en UN hash por día para que el registro sea un solo round-trip:
#     obs:dia:2026-07-30
#       total, errores, lentas, ms_suma       -> globales del día
#       r:GET /api/users                      -> conteo por método+ruta
#       t:GET /api/users                      -> suma de ms por método+ruta
#       s:200, s:403 …                        -> conteo por código
#       h:<=50, h:<=250 …                     -> histograma para percentiles
#
# El número de campos está acotado por la cantidad de rutas de la app (~200),
# no por el volumen de tráfico.
_PREFIJO_DIA = 'obs:dia'
_TTL_CONTADORES = 30 * 86400  # 30 días de histórico; Redis los limpia solo

# Cortes del histograma, en ms. Permiten estimar percentiles sin guardar cada
# medición. Los tramos son más finos abajo porque es donde vive el tráfico sano.
_CORTES_HISTOGRAMA = (50, 100, 250, 500, 1000, 3000)


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
        ruta = _ruta_normalizada()

        # Contadores exactos: SIEMPRE, sin muestreo. Es lo que hace que los
        # totales del panel sean reales y no una estimación.
        _incrementar_contadores(request.method, ruta, status, duracion_ms)

        if not _debe_registrar(status, duracion_ms):
            return response

        from flask import g
        usuario = getattr(g, '_jwt_user', None)

        evento = json.dumps({
            'ts': time.time(),
            'metodo': request.method,
            'ruta': ruta,
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


# ── Contadores exactos ──────────────────────────────────────────────────────

def _clave_dia(fecha=None) -> str:
    from datetime import date
    return f'{_PREFIJO_DIA}:{(fecha or date.today()).isoformat()}'


def _tramo_histograma(ms: float) -> str:
    for corte in _CORTES_HISTOGRAMA:
        if ms <= corte:
            return f'h:<={corte}'
    return f'h:>{_CORTES_HISTOGRAMA[-1]}'


def _incrementar_contadores(metodo: str, ruta: str, status: int, ms: float) -> None:
    """Suma esta petición a los agregados del día.

    Todo va en UNA pipeline: aunque son ~7 comandos, viajan juntos en un solo
    round-trip. Contra un Redis local eso es tiempo despreciable frente a
    cualquier consulta a Postgres de la propia petición.

    `redis_call` lo envuelve, así que un Redis caído degrada a no-op y jamás
    frena la respuesta. Y el timeout de socket del cliente (ver
    `get_redis`) garantiza que un Redis *colgado* tampoco lo haga.
    """
    from app.extensions import redis_call

    clave = _clave_dia()
    campo_ruta = f'{metodo} {ruta}'

    def _sumar(r):
        pipe = r.pipeline()
        pipe.hincrby(clave, 'total', 1)
        # Redondeamos los ms a entero: HINCRBYFLOAT es más caro y para una suma
        # que luego se divide entre miles de peticiones, la precisión sub-ms no
        # aporta nada.
        pipe.hincrby(clave, 'ms_suma', int(ms))
        if status >= 400:
            pipe.hincrby(clave, 'errores', 1)
        if ms >= _UMBRAL_LENTA_MS:
            pipe.hincrby(clave, 'lentas', 1)
        pipe.hincrby(clave, f'r:{campo_ruta}', 1)
        pipe.hincrby(clave, f't:{campo_ruta}', int(ms))
        pipe.hincrby(clave, f's:{status}', 1)
        pipe.hincrby(clave, _tramo_histograma(ms), 1)
        # EXPIRE en cada escritura: la clave del día siempre caduca 30 días
        # después de su ÚLTIMA actividad, así Redis se limpia solo sin tarea
        # de mantenimiento que pueda fallar.
        pipe.expire(clave, _TTL_CONTADORES)
        return pipe.execute()

    redis_call(_sumar)


def leer_contadores(dias: int = 7) -> dict:
    """Agregados exactos de los últimos `dias` días.

    Devuelve el detalle del día de hoy y una serie diaria para ver tendencia.
    """
    from datetime import date, timedelta

    from app.extensions import redis_call

    dias = max(1, min(int(dias or 7), 30))
    hoy = date.today()
    fechas = [hoy - timedelta(days=i) for i in range(dias)]

    def _leer(r):
        pipe = r.pipeline()
        for f in fechas:
            pipe.hgetall(_clave_dia(f))
        return pipe.execute()

    crudos = redis_call(_leer, default=None)
    if crudos is None:
        return {'disponible': False, 'serie': [], 'hoy': None}

    serie = []
    for fecha, datos in zip(fechas, crudos):
        datos = datos or {}
        total = int(datos.get('total') or 0)
        serie.append({
            'fecha': fecha.isoformat(),
            'total': total,
            'errores': int(datos.get('errores') or 0),
            'lentas': int(datos.get('lentas') or 0),
            'ms_promedio': round(int(datos.get('ms_suma') or 0) / total, 1) if total else 0,
        })

    return {
        'disponible': True,
        # Del más viejo al más reciente: así se lee como una línea de tiempo.
        'serie': list(reversed(serie)),
        'hoy': _detalle_dia(crudos[0] or {}),
    }


def _detalle_dia(datos: dict) -> dict:
    """Desglosa el hash de un día en rutas, códigos y percentiles."""
    total = int(datos.get('total') or 0)
    if not total:
        return {'total': 0, 'errores': 0, 'lentas': 0, 'ms_promedio': 0,
                'por_ruta': [], 'por_status': [], 'percentiles': {}}

    rutas = {}
    status = {}
    histograma = {}
    for campo, valor in datos.items():
        try:
            n = int(valor)
        except (TypeError, ValueError):
            continue
        if campo.startswith('r:'):
            rutas.setdefault(campo[2:], {})['conteo'] = n
        elif campo.startswith('t:'):
            rutas.setdefault(campo[2:], {})['ms_total'] = n
        elif campo.startswith('s:'):
            status[campo[2:]] = n
        elif campo.startswith('h:'):
            histograma[campo[2:]] = n

    por_ruta = []
    for nombre, vals in rutas.items():
        conteo = vals.get('conteo', 0)
        if not conteo:
            continue
        metodo, _, ruta = nombre.partition(' ')
        por_ruta.append({
            'metodo': metodo,
            'ruta': ruta,
            'conteo': conteo,
            'ms_promedio': round(vals.get('ms_total', 0) / conteo, 1),
        })
    por_ruta.sort(key=lambda f: f['conteo'], reverse=True)

    return {
        'total': total,
        'errores': int(datos.get('errores') or 0),
        'lentas': int(datos.get('lentas') or 0),
        'ms_promedio': round(int(datos.get('ms_suma') or 0) / total, 1),
        'por_ruta': por_ruta[:30],
        'por_status': sorted(
            ({'status': k, 'conteo': v} for k, v in status.items()),
            key=lambda x: x['conteo'], reverse=True,
        ),
        'percentiles': _percentiles_desde_histograma(histograma, total),
        'histograma': _histograma_ordenado(histograma),
    }


def _orden_tramo(etiqueta: str) -> float:
    """Ordena '<=50', '<=250', '>3000' por su cota numérica."""
    try:
        if etiqueta.startswith('<='):
            return float(etiqueta[2:])
        if etiqueta.startswith('>'):
            return float(etiqueta[1:]) + 1
    except ValueError:
        pass
    return float('inf')


def _histograma_ordenado(histograma: dict) -> list:
    return [
        {'tramo': k, 'conteo': v}
        for k, v in sorted(histograma.items(), key=lambda kv: _orden_tramo(kv[0]))
    ]


def _percentiles_desde_histograma(histograma: dict, total: int) -> dict:
    """p50/p95/p99 aproximados a partir de los tramos.

    Son APROXIMADOS por construcción: solo sabemos cuántas peticiones cayeron en
    cada tramo, no sus valores exactos. Se devuelve la cota superior del tramo
    donde cae el percentil, o sea "el p95 es como mucho X ms". Para dimensionar
    y detectar degradación es suficiente, y cuesta una fracción de lo que
    costaría guardar cada medición.
    """
    if not histograma or not total:
        return {}
    tramos = sorted(histograma.items(), key=lambda kv: _orden_tramo(kv[0]))
    salida = {}
    for etiqueta, objetivo in (('p50', 0.50), ('p95', 0.95), ('p99', 0.99)):
        acumulado = 0
        for nombre, conteo in tramos:
            acumulado += conteo
            if acumulado >= total * objetivo:
                salida[etiqueta] = nombre
                break
    return salida


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
