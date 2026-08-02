"""Foto de perfil + documentos adjuntos al trabajador.

Registra:
  POST   /<int:id>/foto                          subir_foto
  GET    /<int:id>/foto                          get_foto
  GET    /<int:id>/foto/thumb                    get_foto_thumb
  POST   /<int:id>/documentos                    subir_documento
  GET    /documentos/<int:doc_id>                descargar_documento
  DELETE /documentos/<int:doc_id>                eliminar_documento
"""
import os
import time

from flask import current_app, jsonify, request
from werkzeug.utils import secure_filename

from app.extensions import db, limiter
from app.models import DocumentoTrabajador, Trabajador
from app.realtime import emit_to_role
from app.routes.api_auth import jwt_required
from app.utils import allowed_file, archivos, log_action

from ._core import _authorized, _parse_date, _save_foto, bp, thumb_key


# ── Foto de perfil ─────────────────────────────────────────────────────────────

@bp.route('/<int:id>/foto', methods=['POST'])
@jwt_required
def subir_foto(id):
    t = db.session.get(Trabajador, id)
    if not t:
        return jsonify({'error': 'No encontrado'}), 404
    if not _authorized(t):
        return jsonify({'error': 'Acceso denegado'}), 403
    foto = request.files.get('foto_perfil') or request.files.get('foto')
    if not foto or not foto.filename:
        return jsonify({'error': 'No se envió archivo'}), 400
    try:
        _save_foto(t, foto)
        db.session.commit()
        log_action(f'Actualizó foto de perfil del trabajador {t.nombre} ({t.no_empleado})')
        emit_to_role(['admin', 'super_admin'], 'empleado:changed', {
            'id': t.id, 'action': 'foto',
        })
        return jsonify({'foto_perfil': t.foto_perfil})
    except ValueError as e:
        return jsonify({'error': str(e)}), 400


@bp.route('/<int:id>/foto', methods=['GET'])
@jwt_required
def get_foto(id):
    t = db.session.get(Trabajador, id)
    if not t:
        return jsonify({'error': 'No encontrado'}), 404
    if not _authorized(t):
        return jsonify({'error': 'Acceso denegado'}), 403
    if not t.foto_perfil:
        return jsonify({'error': 'Sin foto'}), 404
    resp = archivos.enviar(t.foto_perfil)
    if resp is None:
        return jsonify({'error': 'Foto no encontrada'}), 404
    return resp


@bp.route('/<int:id>/foto/thumb', methods=['GET'])
@jwt_required
def get_foto_thumb(id):
    t = db.session.get(Trabajador, id)
    if not t:
        return jsonify({'error': 'No encontrado'}), 404
    if not _authorized(t):
        return jsonify({'error': 'Acceso denegado'}), 403
    if not t.foto_perfil:
        return jsonify({'error': 'Sin foto'}), 404
    # El thumb puede no existir (fotos viejas, o falló al generarse): en ese caso
    # se sirve la original, igual que siempre.
    resp = archivos.enviar(thumb_key(t.foto_perfil))
    if resp is None:
        resp = archivos.enviar(t.foto_perfil)
    if resp is None:
        return jsonify({'error': 'Foto no encontrada'}), 404
    return resp


# ── Documentos ────────────────────────────────────────────────────────────────

# HIGH-03 fix: para documentos de trabajadores restringimos drásticamente los
# tipos permitidos. El set global `ALLOWED_EXTENSIONS` incluye mp4/mp3/wav y
# formatos de Office con macros (xlsm/docx/pptx) que abren vías de RCE cuando
# el admin descarga y abre el archivo. Los documentos legales de RRHH son PDF
# o imagen — limitarlos baja la superficie sin perder funcionalidad real.
_DOCUMENTO_TRABAJADOR_EXTS = {'pdf', 'jpg', 'jpeg', 'png', 'heic'}
_DOCUMENTO_MAX_BYTES = 20 * 1024 * 1024  # 20 MB por archivo (vs los 50 MB globales)


@bp.route('/<int:id>/documentos', methods=['POST'])
@jwt_required
@limiter.limit('10 per minute')
def subir_documento(id):
    t = db.session.get(Trabajador, id)
    if not t:
        return jsonify({'error': 'No encontrado'}), 404
    if not _authorized(t):
        return jsonify({'error': 'Acceso denegado'}), 403

    file = request.files.get('documento') or request.files.get('file')
    if not file or not file.filename:
        return jsonify({'error': 'No se envió archivo'}), 400

    # HIGH-03: chequeo de tamaño antes que el de magic-bytes (más barato y
    # corta DoS por upload grande).
    file.seek(0, os.SEEK_END)
    size = file.tell()
    file.seek(0)
    if size > _DOCUMENTO_MAX_BYTES:
        return jsonify({
            'error': f'El archivo excede {_DOCUMENTO_MAX_BYTES // (1024 * 1024)} MB.'
        }), 400

    if not allowed_file(file, allowed_exts=_DOCUMENTO_TRABAJADOR_EXTS):
        return jsonify({
            'error': 'Tipo de archivo no permitido. Solo PDF, JPG, PNG o HEIC.'
        }), 400

    tipo_doc = (request.form.get('tipo_documento') or '').strip() or None
    if tipo_doc and len(tipo_doc) > 100:
        return jsonify({'error': 'tipo_documento demasiado largo (máx. 100)'}), 400

    filename = secure_filename(file.filename)
    ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''
    unique_filename = f'{int(time.time())}_{filename}'

    # Imágenes (JPG/PNG/HEIC) se reencoden a WebP para ahorrar espacio y unificar
    # formato. PDF se guarda intacto. El nombre humano (`nombre_archivo`) también
    # se ajusta a .webp para que coincida con lo que el usuario descarga después.
    if ext in {'jpg', 'jpeg', 'png', 'heic'}:
        from app.utils import image_to_webp, replace_ext_with_webp
        try:
            webp_buf = image_to_webp(file)
        except Exception as e:
            current_app.logger.warning('Error convirtiendo documento a webp: %s', e)
            return jsonify({'error': 'No se pudo procesar la imagen'}), 400
        unique_filename = replace_ext_with_webp(unique_filename)
        filename = replace_ext_with_webp(filename)
        contenido, content_type = webp_buf.getvalue(), 'image/webp'
    else:
        file.seek(0)
        contenido, content_type = file.read(), 'application/pdf'

    archivos.guardar(f'trabajadores/{t.id}/{unique_filename}', contenido, content_type)

    doc = DocumentoTrabajador(
        trabajador_id=t.id,
        nombre_archivo=filename,
        ruta_archivo=f'trabajadores/{t.id}/{unique_filename}',
        tipo_documento=tipo_doc,
        fecha_inicio=_parse_date(request.form.get('fecha_inicio')),
        fecha_fin=_parse_date(request.form.get('fecha_fin')),
    )
    db.session.add(doc)
    db.session.commit()
    log_action(f'Subió documento {filename} para {t.nombre} ({t.no_empleado})')
    emit_to_role(['admin', 'super_admin', 'coordinador'], 'documento:changed', {
        'trabajador_id': t.id, 'doc_id': doc.id, 'action': 'created',
    })
    return jsonify(doc.to_dict()), 201


@bp.route('/documentos/<int:doc_id>', methods=['GET'])
@jwt_required
def descargar_documento(doc_id):
    doc = db.session.get(DocumentoTrabajador, doc_id)
    if not doc:
        return jsonify({'error': 'No encontrado'}), 404
    if not _authorized(doc.trabajador):
        return jsonify({'error': 'Acceso denegado'}), 403
    # secure_filename para defender contra header-injection vía nombre con
    # CRLF o caracteres de control que algún antiguo browser podría
    # interpretar mal en el Content-Disposition.
    safe_name = secure_filename(doc.nombre_archivo) or f'documento_{doc_id}'
    resp = archivos.enviar(doc.ruta_archivo, as_attachment=True, download_name=safe_name)
    if resp is None:
        return jsonify({'error': 'Archivo no encontrado'}), 404
    return resp


@bp.route('/documentos/<int:doc_id>', methods=['DELETE'])
@jwt_required
def eliminar_documento(doc_id):
    doc = db.session.get(DocumentoTrabajador, doc_id)
    if not doc:
        return jsonify({'error': 'No encontrado'}), 404
    if not _authorized(doc.trabajador):
        return jsonify({'error': 'Acceso denegado'}), 403
    archivos.eliminar(doc.ruta_archivo)
    log_action(f'Eliminó documento {doc.nombre_archivo} del trabajador {doc.trabajador_id}')
    trabajador_id = doc.trabajador_id
    doc_id = doc.id
    db.session.delete(doc)
    db.session.commit()
    emit_to_role(['admin', 'super_admin', 'coordinador'], 'documento:changed', {
        'trabajador_id': trabajador_id, 'doc_id': doc_id, 'action': 'deleted',
    })
    return jsonify({'ok': True})
