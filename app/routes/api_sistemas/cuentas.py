"""Cuentas: bloqueos por intentos fallidos y estado del 2FA.

  GET    /api/sistemas/bloqueos             quién está bloqueado ahora
  DELETE /api/sistemas/bloqueos/<tipo>/<id> libera un bloqueo
  GET    /api/sistemas/sin-2fa              cuentas sin segundo factor

Por qué existe la parte de bloqueos: el lockout escalado llega hasta 24 horas.
Si un usuario falla cinco veces se queda fuera y hasta ahora no había forma de
liberarlo salvo esperar o borrar llaves de Redis a mano. Es la petición de
soporte más frecuente que el sistema no sabía atender.
"""
from flask import g, jsonify

from app.extensions import db, limiter
from app.models import User
from app.routes._api_helpers import require_panel_sistemas
from app.routes.api_auth import jwt_required
from app.utils import log_action

from ._core import bp


# ── Lectura de bloqueos ──────────────────────────────────────────────────────
# CRÍTICO: se usa SCAN, nunca KEYS.
#
# `KEYS patrón` recorre TODO el espacio de claves de golpe y BLOQUEA a Redis
# mientras lo hace. En producción, con Redis compartido por los 4 workers y
# atendiendo además la blacklist de JWT en cada petición autenticada, un KEYS
# sobre un espacio grande congelaría la aplicación entera.
#
# SCAN devuelve resultados por lotes con un cursor y deja respirar a Redis entre
# iteración e iteración. Se acota además con un tope de vueltas para que ni
# siquiera un espacio de claves patológico pueda alargar esta consulta.
_MAX_ITERACIONES_SCAN = 50
_TAM_LOTE_SCAN = 200


def _escanear(patron: str) -> list:
    """Devuelve las claves que casan con `patron`, sin bloquear Redis."""
    from app.extensions import redis_call

    def _hacer(r):
        encontradas = []
        cursor = 0
        for _ in range(_MAX_ITERACIONES_SCAN):
            cursor, lote = r.scan(cursor=cursor, match=patron, count=_TAM_LOTE_SCAN)
            encontradas.extend(lote)
            if cursor == 0:
                break
        return encontradas

    return redis_call(_hacer, default=[]) or []


def _ttl_de(clave: str):
    from app.extensions import redis_call
    ttl = redis_call(lambda r: r.ttl(clave), default=None)
    return ttl if (ttl is not None and ttl > 0) else None


# Ventana hacia atrás para buscar de dónde vinieron los intentos fallidos.
# El contador de fallos usa una ventana de 15 min y el bloqueo más corto dura
# 10, así que 45 min cubre con holgura los intentos que provocaron el bloqueo
# sin arrastrar ruido de horas anteriores.
_VENTANA_ORIGENES_MIN = 45


def _origenes_de_intentos(usernames: list) -> dict:
    """Desde qué IPs vinieron los intentos fallidos de cada cuenta bloqueada.

    El bloqueo vive en Redis indexado por cuenta: ahí no hay ninguna IP. Pero
    la bitácora sí registra cada intento fallido con su IP real (validada
    contra los rangos de Cloudflare, así que no es falsificable desde fuera).

    Es el dato que responde la pregunta que importa al ver una cuenta
    bloqueada: ¿fue la persona olvidando su contraseña desde su lugar de
    siempre, o hay varias IPs desconocidas intentando entrar?

    Se resuelve en UNA sola consulta para todas las cuentas, no una por cuenta.
    """
    if not usernames:
        return {}

    from datetime import datetime, timedelta, timezone

    from sqlalchemy import or_

    from app.models import AuditLog

    desde = datetime.now(timezone.utc) - timedelta(minutes=_VENTANA_ORIGENES_MIN)
    filas = (
        db.session.query(AuditLog.user, AuditLog.ip, AuditLog.created_at)
        .filter(
            AuditLog.created_at >= desde,
            AuditLog.user.in_(usernames),
            or_(
                AuditLog.action.ilike('%login fallido%'),
                AuditLog.action.ilike('%2FA fallido%'),
            ),
        )
        .all()
    )

    por_usuario = {}
    for usuario, ip, cuando in filas:
        registro = por_usuario.setdefault(usuario, {})
        actual = registro.get(ip)
        # Se guarda el conteo por IP y el intento más reciente de cada una.
        if actual:
            actual['intentos'] += 1
            if cuando and (not actual['ultimo'] or cuando > actual['ultimo']):
                actual['ultimo'] = cuando
        else:
            registro[ip] = {'intentos': 1, 'ultimo': cuando}

    salida = {}
    for usuario, ips in por_usuario.items():
        salida[usuario] = sorted(
            (
                {
                    'ip': ip or 'desconocida',
                    'intentos': datos['intentos'],
                    'ultimo': datos['ultimo'].isoformat() if datos['ultimo'] else None,
                }
                for ip, datos in ips.items()
            ),
            key=lambda x: x['intentos'],
            reverse=True,
        )
    return salida


@bp.route('/bloqueos', methods=['GET'])
@jwt_required
@limiter.limit('60 per minute')
def bloqueos():
    """Cuentas bloqueadas ahora mismo, por contraseña y por 2FA."""
    err = require_panel_sistemas()
    if err:
        return err

    salida = []

    # Bloqueos por contraseña: la clave lleva el username normalizado.
    for clave in _escanear('login_lockout:*'):
        username = clave.split(':', 1)[1] if ':' in clave else clave
        ttl = _ttl_de(clave)
        if not ttl:
            continue
        u = User.query.filter(db.func.lower(User.username) == username).first()
        salida.append({
            'tipo': 'password',
            'identificador': username,
            'username': u.username if u else username,
            'usuario_id': u.id if u else None,
            'rol': u.role if u else None,
            'segundos_restantes': ttl,
        })

    # Bloqueos por 2FA: la clave lleva el id de usuario.
    for clave in _escanear('twofa_lockout:*'):
        crudo = clave.split(':', 1)[1] if ':' in clave else ''
        ttl = _ttl_de(clave)
        if not ttl:
            continue
        try:
            uid = int(crudo)
        except (TypeError, ValueError):
            continue
        u = db.session.get(User, uid)
        salida.append({
            'tipo': '2fa',
            'identificador': str(uid),
            'username': u.username if u else f'#{uid}',
            'usuario_id': uid,
            'rol': u.role if u else None,
            'segundos_restantes': ttl,
        })

    # Enriquecer con el origen de los intentos. Una sola consulta para todas
    # las cuentas de la lista.
    origenes = _origenes_de_intentos([b['username'] for b in salida if b['username']])
    for b in salida:
        b['origenes'] = origenes.get(b['username'], [])

    salida.sort(key=lambda x: x['segundos_restantes'], reverse=True)
    return jsonify(salida)


@bp.route('/bloqueos/<string:tipo>/<string:identificador>', methods=['DELETE'])
@jwt_required
# Límite estricto: es una ESCRITURA que reabre el acceso a una cuenta. Los de
# solo lectura del panel van a 60/min porque se consultan al navegar; este no.
@limiter.limit('20 per minute')
def liberar_bloqueo(tipo: str, identificador: str):
    """Libera un bloqueo para que la persona pueda volver a intentar.

    Se borran también el contador de fallos y el NIVEL de escalación. Lo del
    nivel es deliberado: si solo quitáramos el bloqueo, el siguiente fallo
    dispararía la duración del escalón siguiente (30 min, 1 h, 3 h…) y el
    usuario volvería a quedar fuera casi de inmediato. Liberar debe dejar la
    cuenta como si nunca se hubiera bloqueado.
    """
    err = require_panel_sistemas()
    if err:
        return err

    from app.extensions import redis_call

    if tipo == 'password':
        objetivo = (identificador or '').lower().strip()
        if not objetivo:
            return jsonify({'error': 'Identificador vacío'}), 400
        claves = [
            f'login_lockout:{objetivo}',
            f'login_fails:{objetivo}',
            f'login_lockout_level:{objetivo}',
        ]
        etiqueta = objetivo
    elif tipo == '2fa':
        try:
            uid = int(identificador)
        except (TypeError, ValueError):
            return jsonify({'error': 'Identificador inválido'}), 400
        claves = [
            f'twofa_lockout:{uid}',
            f'twofa_fails:{uid}',
            f'twofa_lockout_level:{uid}',
        ]
        u = db.session.get(User, uid)
        etiqueta = u.username if u else f'#{uid}'
    else:
        return jsonify({'error': 'Tipo de bloqueo no válido'}), 400

    borradas = redis_call(lambda r: r.delete(*claves), default=0)
    if not borradas:
        return jsonify({
            'error': 'No hay un bloqueo activo para esa cuenta (pudo expirar solo).',
        }), 404

    log_action(
        f"Panel de sistemas liberó el bloqueo de {tipo} de '{etiqueta}' "
        f"(ejecutado por {g._jwt_user.username})"
    )
    return jsonify({'ok': True})


@bp.route('/sin-2fa', methods=['GET'])
@jwt_required
@limiter.limit('60 per minute')
def sin_2fa():
    """Cuentas activas sin segundo factor.

    No bloquea nada — es visibilidad. Los roles privilegiados van marcados
    porque son los que más importan: una cuenta con acceso a nómina o al
    sistema sin 2FA es una contraseña filtrada de distancia del compromiso.
    """
    err = require_panel_sistemas()
    if err:
        return err

    _ROLES_SENSIBLES = ('super_admin', 'sistemas', 'admin', 'finanzas')

    usuarios = (
        User.query
        .filter(User.activo == True, User.totp_secret == None)  # noqa: E711,E712
        .all()
    )
    filas = [
        {
            'id': u.id,
            'username': u.username,
            'full_name': u.full_name,
            'rol': u.role,
            'sensible': u.role in _ROLES_SENSIBLES,
            'ultimo_acceso': u.last_seen.isoformat() if u.last_seen else None,
        }
        for u in usuarios
    ]
    # Los roles sensibles primero: es lo que hay que atender.
    filas.sort(key=lambda f: (not f['sensible'], (f['username'] or '').lower()))

    total_activos = User.query.filter(User.activo == True).count()  # noqa: E712
    return jsonify({
        'usuarios': filas,
        'total_sin_2fa': len(filas),
        'total_activos': total_activos,
        'sensibles_sin_2fa': sum(1 for f in filas if f['sensible']),
    })
