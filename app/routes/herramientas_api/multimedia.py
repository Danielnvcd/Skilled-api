"""Multimedia adjunta a unidades (fotos / evidencias).

Registra:
  /herramientas-unidades/<int:uid>/fotos                       POST
  /herramientas-unidades/<int:uid>/media/<int:media_id>        GET
"""
import os
import uuid

from flask import jsonify, request, current_app, send_file

from app.extensions import db, limiter, get_real_client_ip_flask
from app.models import (
    HerramientaUnidad, EventoHerramienta, MediaHerramienta,
)
from ._core import (
    bp,
    _require_login,
    _require_inventario,
    _audit,
    _media_to_dict,
    _puede_ver_unidad,
    _validar_imagen_archivo,
    _upload_dir,
)


@bp.route('/herramientas-unidades/<int:uid>/fotos', methods=['POST'])
@limiter.limit('15/minute', key_func=lambda: f"ip:{get_real_client_ip_flask()}")
@_require_inventario
def subir_foto_unidad(uid: int):
    unidad = HerramientaUnidad.query.filter(HerramientaUnidad.id == uid).first()
    if not unidad:
        return jsonify({'detail': 'Unidad no encontrada'}), 404

    # IDOR fix: un solicitante_material solo puede subir foto a unidades que
    # tiene asignadas. Inventario/admin/super_admin ven todo.
    if not _puede_ver_unidad(request.current_user, unidad):
        return jsonify({'detail': 'Forbidden'}), 403

    file = request.files.get('foto') or request.files.get('archivo')
    mime, ext, info = _validar_imagen_archivo(file)
    if mime is None:
        return jsonify({'detail': info}), 422

    tipo = (request.form.get('tipo') or 'FOTO_HERRAMIENTA').upper()
    if tipo not in ('FOTO_HERRAMIENTA', 'EVIDENCIA_EVENTO'):
        return jsonify({'detail': 'tipo inválido'}), 422
    evento_id = request.form.get('evento_id', type=int)

    # IDOR fix: evento_id debe pertenecer a la misma unidad para evitar adjuntar
    # evidencia a eventos de otras unidades.
    if evento_id is not None:
        evento = EventoHerramienta.query.filter(
            EventoHerramienta.id == evento_id,
            EventoHerramienta.unidad_id == uid,
        ).first()
        if not evento:
            return jsonify({'detail': 'evento_id no pertenece a esta unidad'}), 422

    folder = _upload_dir(uid)
    fname = f"{uuid.uuid4().hex}{ext}"
    fpath = os.path.join(folder, fname)
    file.save(fpath)
    rel_path = os.path.relpath(fpath, current_app.config.get('UPLOAD_FOLDER', 'uploads'))

    user = request.current_user
    media = MediaHerramienta(
        unidad_id=uid,
        evento_id=evento_id,
        tipo=tipo,
        ruta_archivo=rel_path.replace('\\', '/'),
        nombre_original=file.filename[:250],
        mime=mime,
        tamano_bytes=info,  # size returned in info on success
        subido_por_id=user.id,
    )
    db.session.add(media)
    _audit(user, f"Foto subida a unidad #{uid}")
    db.session.commit()
    db.session.refresh(media)
    return jsonify(_media_to_dict(media)), 201


@bp.route('/herramientas-unidades/<int:uid>/media/<int:media_id>', methods=['GET'])
@_require_login
def get_media_file(uid: int, media_id: int):
    unidad = HerramientaUnidad.query.filter(HerramientaUnidad.id == uid).first()
    if not unidad:
        return jsonify({'detail': 'Unidad no encontrada'}), 404
    if not _puede_ver_unidad(request.current_user, unidad):
        return jsonify({'detail': 'Forbidden'}), 403

    media = MediaHerramienta.query.filter(MediaHerramienta.id == media_id,
                                           MediaHerramienta.unidad_id == uid).first()
    if not media:
        return jsonify({'detail': 'Archivo no encontrado'}), 404
    base = current_app.config.get('UPLOAD_FOLDER', 'uploads')
    full = os.path.join(base, media.ruta_archivo)
    if not os.path.exists(full):
        return jsonify({'detail': 'Archivo perdido en disco'}), 404
    return send_file(full, mimetype=media.mime or 'image/jpeg')
