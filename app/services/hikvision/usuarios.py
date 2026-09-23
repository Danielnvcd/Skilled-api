"""Alta, consulta y baja de usuarios (y su rostro) en un lector Hikvision.

Mapea un `Trabajador` del ERP a un `UserInfo` del equipo. La correspondencia es
directa y no inventa identificadores:

    Trabajador.no_empleado   →  UserInfo.employeeNo   (máx. 32 en este equipo)
    Trabajador.nombre_completo → UserInfo.name        (máx. 128)
    Trabajador.foto_perfil   →  FDLib blackFD / FDID 1, FPID = employeeNo

Endpoints usados (todos verificados contra un DS-K1T342MFWX-E1 FW V3.16.1):

    POST /ISAPI/AccessControl/UserInfo/Record?format=json       alta
    PUT  /ISAPI/AccessControl/UserInfo/Modify?format=json       edición
    POST /ISAPI/AccessControl/UserInfo/Search?format=json       consulta
    PUT  /ISAPI/AccessControl/UserInfo/Delete?format=json       baja
    GET  /ISAPI/AccessControl/UserInfo/Count?format=json        conteo
    PUT  /ISAPI/Intelligent/FDLib/FDSetUp?format=json           rostro (multipart, alta o reemplazo)
    POST /ISAPI/Intelligent/FDLib/FDSearch?format=json          verificar rostro
"""
from __future__ import annotations

import hashlib
import json
import logging
from urllib.parse import urlsplit

from .client import MAX_RESULTS, TIMEOUT_LECTURA_FOTO, es_fin_de_paginacion
from .errores import ErrorConfiguracion
from .fotos import huella, preparar_jpeg

logger = logging.getLogger(__name__)

# Biblioteca de rostros del equipo. NO son valores adivinados: salen de
# `GET /ISAPI/Intelligent/FDLib?format=json`, que en este modelo devuelve
# FDID "1" → blackFD (la de acceso) y FDID "2" → infraredFD.
FACE_LIB_TYPE = 'blackFD'
FDID = '1'

# Vigencia del usuario en el equipo. Se manda amplia y habilitada porque las
# altas y bajas las decide el ERP (marcando o desmarcando al empleado), no el
# calendario interno del lector.
VIGENCIA_INICIO = '2020-01-01T00:00:00'
VIGENCIA_FIN = '2037-12-31T23:59:59'


def employee_no_de(trabajador, *, maximo: int = 32) -> str:
    """`employeeNo` para el equipo a partir del trabajador del ERP.

    Se usa `no_empleado` tal cual porque YA es el identificador único del
    empleado en PostgreSQL (`unique=True, nullable=False`). Inventar uno nuevo
    solo agregaría una correspondencia más que mantener y una forma más de
    desincronizarse.

    Si no cabe en el equipo se falla en voz alta en vez de truncar: dos números
    truncados podrían colisionar y un empleado abriría la puerta con la
    identidad de otro.
    """
    valor = (trabajador.no_empleado or '').strip()
    if not valor:
        raise ErrorConfiguracion(
            f'{trabajador.nombre_completo} no tiene número de empleado.',
            detalle=f'trabajador {trabajador.id} con no_empleado vacío',
        )
    if len(valor) > maximo:
        raise ErrorConfiguracion(
            f'El número de empleado "{valor}" tiene {len(valor)} caracteres y el '
            f'lector admite {maximo} como máximo.',
            detalle=f'no_empleado {valor!r} excede {maximo}',
        )
    return valor


def nombre_para_equipo(trabajador, *, maximo: int = 128) -> str:
    """Nombre a mostrar en el lector, recortado al límite del equipo.

    Aquí el recorte SÍ es seguro (a diferencia del `employeeNo`): el nombre es
    decorativo, la identidad la lleva el número.
    """
    return (trabajador.nombre_completo or '').strip()[:maximo]


def cuerpo_usuario(trabajador, employee_no: str, *, nombre_max: int = 128) -> dict:
    """Payload `UserInfo` para alta o edición."""
    return {
        'UserInfo': {
            'employeeNo': employee_no,
            'name': nombre_para_equipo(trabajador, maximo=nombre_max),
            'userType': 'normal',
            'Valid': {
                'enable': True,
                'beginTime': VIGENCIA_INICIO,
                'endTime': VIGENCIA_FIN,
                'timeType': 'local',
            },
            'doorRight': '1',
            'RightPlan': [{'doorNo': 1, 'planTemplateNo': '1'}],
        }
    }


def huella_datos(trabajador, employee_no: str) -> str:
    """SHA-256 de lo que se manda al equipo (sin la foto).

    Comparar esto contra `SyncEmpleadoHikvision.hash_datos` responde "¿cambió
    algo desde la última sincronización?" sin llamar al lector.
    """
    material = '|'.join([
        employee_no,
        nombre_para_equipo(trabajador),
        VIGENCIA_INICIO,
        VIGENCIA_FIN,
    ])
    return hashlib.sha256(material.encode('utf-8')).hexdigest()


# ── Operaciones contra el equipo ─────────────────────────────────────────────

def buscar(cli, employee_no: str) -> dict | None:
    """Devuelve el `UserInfo` del equipo para ese número, o None si no está."""
    datos = cli.pedir_json('POST', '/ISAPI/AccessControl/UserInfo/Search?format=json', {
        'UserInfoSearchCond': {
            'searchID': f'erp-{employee_no}',
            'searchResultPosition': 0,
            'maxResults': 1,
            'EmployeeNoList': [{'employeeNo': employee_no}],
        },
    })
    bloque = datos.get('UserInfoSearch') or {}
    usuarios = bloque.get('UserInfo') or []
    return usuarios[0] if usuarios else None


def listar_todos(cli) -> list[dict]:
    """Todos los usuarios del equipo, paginando de 30 en 30.

    El tope de 30 lo impone el firmware (`maxResults.@max`); pedir más hace que
    el equipo rechace la consulta.
    """
    salida: list[dict] = []
    posicion = 0
    # Cota dura: sin esto, un firmware que nunca marque el fin de página deja el
    # worker girando indefinidamente.
    for _ in range(200):
        datos = cli.pedir_json('POST', '/ISAPI/AccessControl/UserInfo/Search?format=json', {
            'UserInfoSearchCond': {
                'searchID': 'erp-listado',
                'searchResultPosition': posicion,
                'maxResults': MAX_RESULTS,
            },
        })
        bloque = datos.get('UserInfoSearch') or {}
        lote = bloque.get('UserInfo') or []
        salida.extend(lote)
        if es_fin_de_paginacion(bloque) or not lote:
            break
        posicion += len(lote)
    return salida


def contar(cli) -> dict:
    """Conteo de usuarios y de cuántos tienen rostro/huella/tarjeta."""
    datos = cli.pedir_json('GET', '/ISAPI/AccessControl/UserInfo/Count?format=json')
    return datos.get('UserInfoCount') or {}


def crear_o_actualizar(cli, trabajador, employee_no: str, *, nombre_max: int = 128) -> str:
    """Da de alta al usuario, o lo edita si ya existía. Devuelve 'creado'|'actualizado'.

    Se consulta antes en vez de intentar el alta y reaccionar al error: el
    equipo devuelve códigos distintos según firmware para "ya existe", y una
    consulta barata es más confiable que adivinar el código.
    """
    cuerpo = cuerpo_usuario(trabajador, employee_no, nombre_max=nombre_max)

    if buscar(cli, employee_no) is not None:
        cli.pedir_json('PUT', '/ISAPI/AccessControl/UserInfo/Modify?format=json', cuerpo)
        return 'actualizado'

    cli.pedir_json('POST', '/ISAPI/AccessControl/UserInfo/Record?format=json', cuerpo)
    return 'creado'


def eliminar(cli, employee_no: str) -> None:
    """Quita al usuario del equipo. Su rostro se va con él."""
    cli.pedir_json('PUT', '/ISAPI/AccessControl/UserInfo/Delete?format=json', {
        'UserInfoDelCond': {'EmployeeNoList': [{'employeeNo': employee_no}]},
    })


def subir_rostro(cli, employee_no: str, jpeg: bytes) -> None:
    """Registra o REEMPLAZA el rostro del usuario con `FDSetUp` (multipart).

    No se usa `FaceDataRecord`: ese endpoint solo da de alta, y si el usuario
    ya tiene cara el equipo responde `deviceUserAlreadyExistFace`. Con él, la
    única forma de cambiar una foto era borrar al usuario y volver a crearlo.
    `FDSetUp` crea el rostro si no existe y lo sustituye si ya existe
    (`supportFDFunction` incluye `setUp` en este firmware, y se probó contra el
    equipo real). Si la foto nueva no se puede modelar el equipo rechaza la
    petición y conserva el rostro anterior.

    El orden y el nombre de las partes importan: primero el JSON con nombre
    `FaceDataRecord`, después la imagen con nombre `img`. La parte JSON va SIN
    nombre de archivo (por eso el `None`), igual que la manda `curl -F`.

    Si el equipo no puede modelar el rostro responde con
    `SubpicAnalysisModelingError`, que `ClienteHikvision` ya convierte en
    `ErrorFoto` con un mensaje accionable para el usuario.
    """
    meta = {'faceLibType': FACE_LIB_TYPE, 'FDID': FDID, 'FPID': employee_no}
    partes = {
        'FaceDataRecord': (None, json.dumps(meta), 'application/json'),
        'img': (f'{employee_no}.jpg', jpeg, 'image/jpeg'),
    }
    cli.enviar_multipart(
        '/ISAPI/Intelligent/FDLib/FDSetUp?format=json',
        partes,
        metodo='PUT',
        timeout=TIMEOUT_LECTURA_FOTO,
    )


def tiene_rostro(cli, employee_no: str) -> bool:
    """Confirma contra el equipo que el rostro quedó registrado.

    Es el paso de verificación: sin esto, un alta "exitosa" podría dejar al
    empleado en el lector sin cara con la que identificarse.
    """
    datos = cli.pedir_json('POST', '/ISAPI/Intelligent/FDLib/FDSearch?format=json', {
        'searchResultPosition': 0,
        'maxResults': 1,
        'faceLibType': FACE_LIB_TYPE,
        'FDID': FDID,
        'FPID': employee_no,
    })
    return int(datos.get('totalMatches') or 0) > 0


def leer_rostro(cli, employee_no: str) -> bytes | None:
    """JPEG del rostro que el equipo tiene registrado, o None si no hay.

    Es lo que permite mostrar en el ERP la cara que el lector usa DE VERDAD
    para comparar, que puede no coincidir con la foto de perfil actual si
    alguien la cambió después de sincronizar.

    El `faceURL` lo reporta el propio equipo con su IP; de él solo se toma la
    ruta, y la descarga va contra el host configurado (ver `cli.descargar`).
    """
    usuario = buscar(cli, employee_no)
    if not usuario or not int(usuario.get('numOfFace') or 0):
        return None
    url = urlsplit(usuario.get('faceURL') or '')
    if not url.path:
        return None
    ruta = url.path + (f'?{url.query}' if url.query else '')
    return cli.descargar(ruta)


def preparar_foto(trabajador, datos: bytes | None = None) -> tuple[bytes, str]:
    """JPEG listo para el equipo + su huella SHA-256.

    Aquí es donde se rechaza de verdad a un empleado sin fotografía: da igual
    que el frontend lo haya dejado pasar o que la petición venga por curl.

    `datos` permite mandar una foto que TODAVÍA no es la de perfil (la que se
    acaba de subir desde la pantalla del lector): primero se prueba contra el
    equipo y solo si la acepta se guarda en el ERP.
    """
    from .fotos import leer_foto_trabajador

    if datos is None:
        datos = leer_foto_trabajador(trabajador)
    jpeg = preparar_jpeg(datos)
    return jpeg, huella(jpeg)
