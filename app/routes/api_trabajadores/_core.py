"""Núcleo compartido del paquete `api_trabajadores`.

Define el blueprint `bp`, helpers de autorización JWT, los serializers
_row_summary/_full_detail, la whitelist de campos editables por rol y los
helpers de mutación (_apply_payload, _replace_credenciales, _save_foto).

No registres rutas en este archivo. Las rutas viven en los submódulos por
dominio: crud.py, timeline.py, credenciales.py, multimedia.py, importar.py,
exportar.py.
"""
import json
import os
import re
import time
from datetime import datetime as dt

from flask import current_app
from sqlalchemy.orm import joinedload
from werkzeug.utils import secure_filename

from flask import Blueprint

from app.models import CredencialPlanta, Proyecto, Trabajador
from app.routes._api_helpers import current_user, is_admin
from app.utils import allowed_image_file


_CURP_RE = re.compile(r'^[A-Z]{4}[0-9]{6}[HM][A-Z]{5}[A-Z0-9]{2}$')
_RFC_RE  = re.compile(r'^[A-Z&Ñ]{3,4}[0-9]{6}[A-Z0-9]{3}$')


def _parse_date(value):
    """Convierte string YYYY-MM-DD a date. Retorna None si está vacío o es inválido."""
    if not value:
        return None
    try:
        return dt.strptime(value, '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return None


def _mask_pii(value: str, visible: int = 4) -> str:
    """Oculta los caracteres centrales de un campo PII, dejando `visible` al inicio y al final."""
    if not value or len(value) <= visible * 2:
        return value
    return value[:visible] + '*' * (len(value) - visible * 2) + value[-visible:]


def _generate_thumbnail(original_path: str, thumb_path: str, size: tuple = (100, 100)) -> None:
    """Genera un thumbnail WebP de `size` px a partir de `original_path`."""
    from PIL import Image, ImageOps
    with Image.open(original_path) as img:
        img = ImageOps.exif_transpose(img)
        if img.mode not in ('RGB', 'RGBA'):
            img = img.convert('RGBA' if 'A' in img.mode else 'RGB')
        img.thumbnail(size, Image.LANCZOS)
        img.save(thumb_path, 'WEBP', quality=82, method=6)


def _save_profile_picture(file, pp_folder: str, unique_filename: str) -> str:
    """Guarda la foto original convertida a WebP y genera su thumbnail también WebP.

    Devuelve la ruta relativa al UPLOAD_FOLDER (p.ej. 'perfiles/pp_123_foto.webp').
    El thumbnail se guarda como 'perfiles/thumb_pp_123_foto.webp'.
    """
    from app.utils import image_to_webp, replace_ext_with_webp

    os.makedirs(pp_folder, exist_ok=True)
    webp_filename = replace_ext_with_webp(unique_filename)
    original_path = os.path.join(pp_folder, webp_filename)

    webp_buf = image_to_webp(file)
    with open(original_path, 'wb') as f:
        f.write(webp_buf.getvalue())

    thumb_filename = f"thumb_{webp_filename}"
    thumb_path = os.path.join(pp_folder, thumb_filename)
    try:
        _generate_thumbnail(original_path, thumb_path)
    except Exception as e:
        current_app.logger.warning(f"No se pudo generar thumbnail para {webp_filename}: {e}")

    return f"perfiles/{webp_filename}"


def _delete_profile_picture(foto_perfil_rel: str) -> None:
    """Elimina la foto original y su thumbnail dado el path relativo almacenado en BD."""
    upload_folder = current_app.config['UPLOAD_FOLDER']
    original_path = os.path.join(upload_folder, foto_perfil_rel)
    try:
        if os.path.exists(original_path):
            os.remove(original_path)
    except Exception as e:
        current_app.logger.error(f"Error eliminando foto original: {e}")

    # thumb vive en la misma carpeta con prefijo 'thumb_'
    folder, filename = os.path.split(original_path)
    thumb_path = os.path.join(folder, f"thumb_{filename}")
    try:
        if os.path.exists(thumb_path):
            os.remove(thumb_path)
    except Exception as e:
        current_app.logger.error(f"Error eliminando thumbnail: {e}")

bp = Blueprint('api_trabajadores', __name__, url_prefix='/api/trabajadores')


def _authorized(t: Trabajador) -> bool:
    """Versión JWT de `trabajadores.is_authorized_for_worker`."""
    if is_admin():
        return True
    uid = current_user().id
    for p in t.proyectos.all():
        if p.activo and p.coordinador_id == uid:
            return True
    return False


def _row_summary(t: Trabajador) -> dict:
    """Subset usado por la lista (no manda PII completa, ni objetos relacionados)."""
    return {
        'id': t.id,
        'no_empleado': t.no_empleado,
        'nombre': t.nombre,
        'nombre_apellidos': t.nombre_apellidos,
        'area': t.area,
        'puesto': t.puesto,
        'tipo_nomina': t.tipo_nomina,
        'salario_real_pactado_x_sem': float(t.salario_real_pactado_x_sem) if t.salario_real_pactado_x_sem else None,
        'fecha_ingreso': t.fecha_ingreso.isoformat() if t.fecha_ingreso else None,
        'fecha_baja': t.fecha_baja.isoformat() if t.fecha_baja else None,
        'activo': t.activo,
        'foto_perfil': t.foto_perfil,
    }


def _full_detail(t: Trabajador) -> dict:
    """Detalle completo (idéntico al endpoint clásico /trabajadores/get/<id>)."""
    credenciales = [
        {
            'planta': c.planta,
            'credencial_id': c.credencial_id,
            'fecha_caducidad': c.fecha_caducidad.isoformat() if c.fecha_caducidad else None,
        }
        for c in t.credenciales
    ]
    documentos = [d.to_dict() for d in t.documentos]

    proyectos_activos = (
        t.proyectos.options(joinedload(Proyecto.coordinador)).filter_by(activo=True).all()
    )
    coordinadores_set = set()
    for p in proyectos_activos:
        if p.coordinador:
            coordinadores_set.add(p.coordinador.full_name or p.coordinador.username)

    curp, rfc, nss = (t.curp or ''), (t.rfc or ''), (t.nss or '')
    if not is_admin():
        curp = _mask_pii(curp)
        rfc = _mask_pii(rfc)
        nss = _mask_pii(nss)

    return {
        'id': t.id,
        'no_empleado': t.no_empleado,
        'nombre': t.nombre,
        'nombre_apellidos': t.nombre_apellidos,
        # Laborales
        'tipo_mov': t.tipo_mov or '',
        'tipo_cont': t.tipo_cont or '',
        'area': t.area or '',
        'puesto': t.puesto or '',
        'tipo_jornada': t.tipo_jornada or '',
        'fecha_ingreso': t.fecha_ingreso.isoformat() if t.fecha_ingreso else '',
        'descripcion_servicio': t.descripcion_servicio or '',
        'inicio': t.inicio.isoformat() if t.inicio else '',
        'termino_prueba': t.termino_prueba.isoformat() if t.termino_prueba else '',
        'fecha_baja': t.fecha_baja.isoformat() if t.fecha_baja else '',
        # Generales
        'curp': curp,
        'rfc': rfc,
        'nss': nss,
        'fecha_nacimiento': t.fecha_nacimiento.isoformat() if t.fecha_nacimiento else '',
        'sexo': t.sexo or '',
        'estado_civil': t.estado_civil or '',
        'nacionalidad': t.nacionalidad or '',
        'edad': t.edad or '',
        'domicilio': t.domicilio or '',
        # Contacto
        'correo': t.correo or '',
        'celular': t.celular or '',
        # Médicos
        'tipo_sangre': t.tipo_sangre or '',
        'alergias': t.alergias or '',
        'enfermedades_cronicas': t.enfermedades_cronicas or '',
        'contacto_emergencia': t.contacto_emergencia or '',
        'parentesco_contacto': t.parentesco_contacto or '',
        'numero_contacto_emerg': t.numero_contacto_emerg or '',
        'lentes': t.lentes or '',
        'licencia_conducir': t.licencia_conducir or '',
        'estatura': t.estatura or '',
        # Finanzas
        'salario_real_pactado_x_sem': str(t.salario_real_pactado_x_sem) if t.salario_real_pactado_x_sem else '',
        'tipo_pago': t.tipo_pago or '',
        'tipo_nomina': t.tipo_nomina or '',
        'sb': str(t.sb) if t.sb else '',
        'sdi': str(t.sdi) if t.sdi else '',
        'letra': t.letra or '',
        'hr_extra': str(t.hr_extra) if t.hr_extra else '',
        'infonavit': str(t.infonavit) if t.infonavit else '',
        'ajuste_inbursa': str(t.ajuste_inbursa) if t.ajuste_inbursa else '',
        'caja_ahorro': str(t.caja_ahorro) if t.caja_ahorro else '',
        'viaticos': str(t.viaticos) if t.viaticos else '',
        'pago_dia_festivo': str(t.pago_dia_festivo) if t.pago_dia_festivo else '',
        'pagos_efectivo': str(t.pagos_efectivo) if t.pagos_efectivo else '',
        'folio_mov_idse': t.folio_mov_idse or '',
        # Operación
        'ubicacion_actual': t.ubicacion_actual or '',
        'ubicacion_estado': t.ubicacion_estado or '',
        'coordinadores_actuales': ', '.join(sorted(coordinadores_set)) if coordinadores_set else 'Ninguno asignado',
        'no_proyecto': t.no_proyecto or '',
        'observaciones': t.observaciones or '',
        # Multimedia / Relaciones
        'foto_perfil': t.foto_perfil or '',
        'qr_code': t.qr_code or '',
        'activo': t.activo,
        'credenciales': credenciales,
        'documentos': documentos,
    }


# ── Whitelist de campos editables por rol ────────────────────────────────────
# CRIT-03 fix: `_apply_payload` antes asignaba CUALQUIER campo del form al modelo
# sin discriminar por rol. Un coordinador "autorizado" (vía proyecto) podía
# modificar salario, RFC, CURP, NSS, etc. Ahora separamos los campos en buckets
# y solo admin/super_admin puede tocar financieros y PII fiscal.

_ADMIN_ONLY_FIELDS = {
    # Identificación principal
    'no_empleado',
    # PII fiscal / regulada
    'curp', 'rfc', 'nss',
    # Datos personales formales (los coordinadores no deben tocarlos)
    'nombre', 'nombre_apellidos', 'fecha_nacimiento', 'sexo', 'estado_civil',
    'nacionalidad', 'edad', 'domicilio',
    # Laborales / contractuales
    'tipo_mov', 'tipo_cont', 'tipo_jornada', 'fecha_ingreso',
    'descripcion_servicio', 'inicio', 'termino_prueba', 'fecha_baja',
    'area', 'puesto',
    # Finanzas — la TOTALIDAD bloqueada para no-admin
    'salario_real_pactado_x_sem', 'tipo_pago', 'tipo_nomina',
    'sb', 'sdi', 'letra', 'hr_extra', 'infonavit', 'ajuste_inbursa',
    'caja_ahorro', 'viaticos', 'pago_dia_festivo', 'pagos_efectivo',
    'folio_mov_idse',
    # Contacto formal (admin)
    'correo',
}

# Campos editables por coordinador (operativos / contacto en campo):
_COORD_ALLOWED_FIELDS = {
    'celular',
    'tipo_sangre', 'alergias', 'enfermedades_cronicas',
    'contacto_emergencia', 'parentesco_contacto', 'numero_contacto_emerg',
    'lentes', 'licencia_conducir', 'estatura',
    'ubicacion_estado', 'observaciones',
}


def _apply_payload(t: Trabajador, data, *, actor_is_admin: bool) -> list[str]:
    """Aplica los campos del formulario al modelo respetando la whitelist por rol.

    - admin/super_admin: puede tocar todos los campos.
    - coordinador: solo `_COORD_ALLOWED_FIELDS` (operativos + datos médicos +
      contacto de emergencia). Cualquier intento de modificar campos admin-only
      se ignora silenciosamente y se loggea como warning.
    """
    warnings = []

    if actor_is_admin:
        editable = _ADMIN_ONLY_FIELDS | _COORD_ALLOWED_FIELDS
    else:
        # Coord: detectar y advertir si está intentando tocar campos prohibidos
        bloqueados = [k for k in data.keys() if k in _ADMIN_ONLY_FIELDS]
        if bloqueados:
            warnings.append(
                'Algunos campos solo pueden ser editados por un administrador y '
                f'fueron ignorados: {", ".join(sorted(bloqueados))}.'
            )
        editable = _COORD_ALLOWED_FIELDS

    # Validar formato CURP/RFC solo si se está actualizando (admin)
    if 'curp' in editable:
        curp_v = (data.get('curp') or '').strip().upper()
        if curp_v and not _CURP_RE.match(curp_v):
            warnings.append('El formato de CURP no es válido.')
    if 'rfc' in editable:
        rfc_v = (data.get('rfc') or '').strip().upper()
        if rfc_v and not _RFC_RE.match(rfc_v):
            warnings.append('El formato de RFC no es válido.')

    # ── Asignaciones por bucket (solo si el campo es editable para este rol) ──

    # Identificación / strings simples
    _STR_FIELDS = {
        'no_empleado', 'nombre', 'nombre_apellidos',
        'tipo_mov', 'tipo_cont', 'area', 'puesto', 'tipo_jornada',
        'descripcion_servicio', 'curp', 'rfc', 'nss',
        'sexo', 'estado_civil', 'nacionalidad', 'domicilio',
        'correo', 'celular',
        'tipo_sangre', 'alergias', 'enfermedades_cronicas',
        'contacto_emergencia', 'parentesco_contacto', 'numero_contacto_emerg',
        'lentes', 'licencia_conducir', 'estatura',
        'tipo_pago', 'tipo_nomina', 'letra', 'folio_mov_idse',
        'ubicacion_estado', 'observaciones',
    }
    for f in _STR_FIELDS:
        if f in editable and f in data:
            val = data.get(f)
            # no_empleado se strip-ea
            if f == 'no_empleado':
                val = (val or '').strip()
            setattr(t, f, val)

    # Fechas
    _DATE_FIELDS = {'fecha_ingreso', 'inicio', 'termino_prueba', 'fecha_baja', 'fecha_nacimiento'}
    for f in _DATE_FIELDS:
        if f in editable and f in data:
            setattr(t, f, _parse_date(data.get(f)))

    # Numéricos (con default 0)
    _NUM_FIELDS_ZERO = {
        'sb', 'sdi', 'hr_extra', 'infonavit', 'ajuste_inbursa',
        'caja_ahorro', 'viaticos', 'pago_dia_festivo', 'pagos_efectivo',
    }
    for f in _NUM_FIELDS_ZERO:
        if f in editable and f in data:
            setattr(t, f, data.get(f) or 0)

    # Edad: int o None
    if 'edad' in editable and 'edad' in data:
        t.edad = data.get('edad') or None

    # Salario: float estricto
    if 'salario_real_pactado_x_sem' in editable and 'salario_real_pactado_x_sem' in data:
        try:
            t.salario_real_pactado_x_sem = float(data.get('salario_real_pactado_x_sem') or 0)
        except (TypeError, ValueError):
            warnings.append('Salario inválido — se mantuvo el valor anterior.')

    return warnings


def _replace_credenciales(t: Trabajador, payload_str: str) -> None:
    """Reemplaza completamente las credenciales del trabajador con el array recibido."""
    if not payload_str:
        return
    credenciales_data = json.loads(payload_str)
    t.credenciales = []
    for c in credenciales_data:
        planta = str(c.get('planta', '')).strip().upper()
        credencial_id = str(c.get('credencial_id', '')).strip()
        if len(planta) > 100 or len(credencial_id) > 40:
            raise ValueError(
                f'Longitud inválida en credencial (planta {len(planta)}/100, id {len(credencial_id)}/40)'
            )
        t.credenciales.append(CredencialPlanta(
            planta=planta,
            credencial_id=credencial_id,
            fecha_caducidad=_parse_date(c.get('fecha_caducidad')),
        ))


def _save_foto(t: Trabajador, file) -> None:
    if not allowed_image_file(file):
        raise ValueError('Foto rechazada: solo se permiten imágenes JPG o PNG reales.')
    if t.foto_perfil:
        _delete_profile_picture(t.foto_perfil)
    unique_filename = f"pp_{int(time.time())}_{secure_filename(file.filename)}"
    pp_folder = os.path.join(current_app.config['UPLOAD_FOLDER'], 'perfiles')
    t.foto_perfil = _save_profile_picture(file, pp_folder, unique_filename)
