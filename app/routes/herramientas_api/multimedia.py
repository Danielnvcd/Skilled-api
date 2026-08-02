"""Multimedia adjunta a unidades (fotos / evidencias).

Registra:
  /herramientas-unidades/<int:uid>/fotos                       POST
  /herramientas-unidades/<int:uid>/media/<int:media_id>        GET
"""
import uuid

from flask import current_app, jsonify, request

from app.extensions import db, limiter, get_real_client_ip_flask
from app.models import (
    HerramientaUnidad, EventoHerramienta, MediaHerramienta,
)
from app.utils import archivos, image_to_webp
from ._core import (
    bp,
    _require_login,
    _require_inventario,
    _audit,
    _media_to_dict,
    _puede_ver_unidad,
    _validar_imagen_archivo,
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
    mime, _ext, info = _validar_imagen_archivo(file)
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

    # Re-encode a WebP: lo que se almacena es un ráster recién renderizado, así
    # que ningún payload embebido en el original sobrevive. Es la misma defensa
    # que ya tenían las fotos de perfil y los documentos-imagen; validar los
    # magic bytes dice que ES una imagen, no que sea inofensiva. De paso las
    # fotos de campo (que suelen venir de celular) pesan bastante menos.
    try:
        datos = image_to_webp(file).getvalue()
    except Exception as e:
        current_app.logger.warning('Error convirtiendo foto de unidad a webp: %s', e)
        return jsonify({'detail': 'No se pudo procesar la imagen'}), 422

    rel_path = f"herramientas/{uid}/{uuid.uuid4().hex}.webp"
    archivos.guardar(rel_path, datos, 'image/webp')

    user = request.current_user
    media = MediaHerramienta(
        unidad_id=uid,
        evento_id=evento_id,
        tipo=tipo,
        ruta_archivo=rel_path,
        # Se conserva el nombre que subió el usuario (con su extensión original)
        # porque es lo que le sirve para reconocer la foto; lo almacenado es WebP.
        nombre_original=file.filename[:250],
        mime='image/webp',
        tamano_bytes=len(datos),
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
    resp = archivos.enviar(media.ruta_archivo, mimetype=media.mime or 'image/jpeg')
    if resp is None:
        return jsonify({'detail': 'Archivo perdido en disco'}), 404
    return resp
