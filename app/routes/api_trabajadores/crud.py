"""CRUD principal del catálogo de trabajadores.

Registra:
  GET    /                            listar
  GET    /ficha-tecnica               ficha_tecnica
  GET    /<int:id>                    obtener
  POST   /                            crear
  PUT,POST /<int:id>                  actualizar
  DELETE /<int:id>                    dar_baja
  POST   /<int:id>/reactivar          reactivar
  POST   /bulk                        bulk_accion
"""
import traceback
from datetime import date

from flask import current_app, jsonify, request
from sqlalchemy import func, or_
from sqlalchemy.orm import selectinload

from app.extensions import db, limiter
from app.models import Proyecto, Trabajador
from app.realtime import emit_to_role
from app.routes._api_helpers import current_user, is_admin
from app.routes.api_auth import jwt_required
from app.utils import log_action, validate_lengths

from ._core import (
    bp,
    _authorized, _row_summary, _full_detail,
    _apply_payload, _replace_credenciales, _save_foto,
)


@bp.route('', methods=['GET'])
@jwt_required
def listar():
    # AuthZ explícito: solo admin/super_admin y coordinador deben listar
    # trabajadores. Sin este gate, un solicitante_material o inventario podía
    # enumerar el padrón completo (no_empleado, nombre, área, puesto).
    role = current_user().role
    if role not in ('admin', 'super_admin', 'coordinador'):
        return jsonify({'error': 'Acceso denegado'}), 403

    page = request.args.get('page', 1, type=int)
    per_page = min(request.args.get('per_page', 20, type=int), 10000)
    q = (request.args.get('q') or '').strip()
    estado = (request.args.get('estado') or 'activos').lower()  # activos|bajas|todos

    if estado == 'bajas':
        if not is_admin():
            return jsonify({'error': 'Solo admin puede ver bajas'}), 403
        query = Trabajador.query.filter(or_(Trabajador.activo == False, Trabajador.fecha_baja != None))  # noqa: E712
    elif estado == 'todos':
        if not is_admin():
            return jsonify({'error': 'Solo admin puede ver todos'}), 403
        query = Trabajador.query
    else:
        query = Trabajador.query.filter(Trabajador.activo == True, Trabajador.fecha_baja == None)  # noqa: E712

    if role == 'coordinador':
        mis_proyectos = Proyecto.query.options(selectinload(Proyecto.participantes)).filter_by(
            activo=True, coordinador_id=current_user().id,
        ).all()
        ids_trabajadores = {t.id for p in mis_proyectos for t in p.participantes}
        query = query.filter(Trabajador.id.in_(ids_trabajadores))

    if q:
        like = f'%{q}%'
        query = query.filter(or_(
            Trabajador.nombre.ilike(like),
            Trabajador.nombre_apellidos.ilike(like),
            Trabajador.no_empleado.ilike(like),
            Trabajador.rfc.ilike(like),
        ))

    pagination = query.order_by(func.lower(Trabajador.nombre)).paginate(
        page=page, per_page=per_page, error_out=False,
    )
    return jsonify({
        'items': [_row_summary(t) for t in pagination.items],
        'page': pagination.page,
        'per_page': pagination.per_page,
        'total': pagination.total,
        'pages': pagination.pages,
        'has_next': pagination.has_next,
        'has_prev': pagination.has_prev,
    })


@bp.route('/ficha-tecnica', methods=['GET'])
@jwt_required
def ficha_tecnica():
    """Listado de trabajadores con info médica y contacto de emergencia.

    Coordinadores: solo los trabajadores de sus proyectos activos.
    Admin/super_admin: todos los activos.
    Otros roles: 403.
    """
    role = current_user().role
    if role not in ('coordinador', 'admin', 'super_admin'):
        return jsonify({'error': 'Acceso denegado'}), 403

    if role == 'coordinador':
        mis_proyectos = Proyecto.query.options(selectinload(Proyecto.participantes)).filter_by(
            activo=True, coordinador_id=current_user().id,
        ).all()
        ids = {t.id for p in mis_proyectos for t in p.participantes if t.activo}
        if not ids:
            return jsonify({'items': []})
        trabajadores = (
            Trabajador.query.options(selectinload(Trabajador.documentos))
            .filter(Trabajador.id.in_(ids), Trabajador.activo == True)  # noqa: E712
            .order_by(func.lower(Trabajador.nombre_apellidos))
            .all()
        )
    else:
        trabajadores = (
            Trabajador.query.options(selectinload(Trabajador.documentos))
            .filter(Trabajador.activo == True, Trabajador.fecha_baja == None)  # noqa: E712
            .order_by(func.lower(Trabajador.nombre_apellidos))
            .all()
        )

    return jsonify({
        'items': [
            {
                'id': t.id,
                'no_empleado': t.no_empleado,
                'nombre': t.nombre,
                'nombre_completo': t.nombre_completo,
                'foto_perfil': t.foto_perfil or '',
                'tipo_sangre': t.tipo_sangre or '',
                'alergias': t.alergias or '',
                'enfermedades_cronicas': t.enfermedades_cronicas or '',
                'contacto_emergencia': t.contacto_emergencia or '',
                'parentesco_contacto': t.parentesco_contacto or '',
                'numero_contacto_emerg': t.numero_contacto_emerg or '',
                'documentos': [
                    {'id': d.id, 'nombre_archivo': d.nombre_archivo}
                    for d in t.documentos
                ],
            }
            for t in trabajadores
        ],
    })


@bp.route('/<int:id>', methods=['GET'])
@jwt_required
def obtener(id):
    t = (
        Trabajador.query.options(
            selectinload(Trabajador.credenciales),
            selectinload(Trabajador.documentos),
        )
        .filter_by(id=id)
        .first()
    )
    if not t:
        return jsonify({'error': 'No encontrado'}), 404
    if not _authorized(t):
        return jsonify({'error': 'Acceso denegado'}), 403
    return jsonify(_full_detail(t))


@bp.route('', methods=['POST'])
@jwt_required
@limiter.limit('10 per minute')
def crear():
    # SEGURIDAD: la creación de trabajadores cambia salarios y datos fiscales;
    # solo admin/super_admin puede invocarla. Antes era abierto a cualquier
    # autenticado y la autorización dependía solo de la asignación a proyecto.
    if not is_admin():
        return jsonify({'error': 'Solo admin puede crear trabajadores'}), 403
    try:
        data = request.form
        if not (data.get('no_empleado') or '').strip():
            return jsonify({'error': 'El Número de Empleado es obligatorio'}), 400
        errores_longitud = validate_lengths(data)
        if errores_longitud:
            return jsonify({'error': 'Error de longitud en campos', 'details': errores_longitud}), 400
        if Trabajador.query.filter_by(no_empleado=data.get('no_empleado').strip()).first():
            return jsonify({'error': 'El Número de Empleado ya existe'}), 409
        try:
            salario = float(data.get('salario_real_pactado_x_sem') or 0)
            if salario < 0:
                return jsonify({'error': 'El salario no puede ser negativo'}), 400
        except ValueError:
            return jsonify({'error': 'Salario inválido'}), 400

        t = Trabajador()
        warnings = _apply_payload(t, data, actor_is_admin=True)

        try:
            _replace_credenciales(t, data.get('credenciales_json', '[]'))
        except ValueError as e:
            return jsonify({'error': str(e)}), 400
        except Exception as e:
            current_app.logger.error('Error parsing credentials on create: %s', e)
            warnings.append('Hubo un problema guardando algunas credenciales de planta.')

        foto = request.files.get('foto_perfil')
        if foto and foto.filename:
            try:
                _save_foto(t, foto)
            except ValueError as e:
                return jsonify({'error': str(e)}), 400

        db.session.add(t)
        db.session.commit()
        log_action(f'Agregó al trabajador {t.nombre} ({t.no_empleado})')
        emit_to_role(['admin', 'super_admin'], 'empleado:changed', {
            'id': t.id, 'action': 'created',
        })
        return jsonify({'id': t.id, 'warnings': warnings}), 201
    except Exception as e:
        db.session.rollback()
        current_app.logger.error('Error creando trabajador: %s\n%s', e, traceback.format_exc())
        return jsonify({'error': 'Error al guardar el trabajador'}), 500


@bp.route('/<int:id>', methods=['PUT', 'POST'])
@jwt_required
def actualizar(id):
    t = Trabajador.query.get(id)
    if not t:
        return jsonify({'error': 'No encontrado'}), 404
    if not _authorized(t):
        return jsonify({'error': 'Acceso denegado'}), 403

    actor_is_admin = is_admin()
    try:
        data = request.form
        new_no = (data.get('no_empleado') or '').strip()
        # SEGURIDAD: el coordinador no puede cambiar no_empleado (admin-only).
        # Si lo intenta y el valor difiere del actual, lo bloqueamos en vez de
        # ignorar silenciosamente — el form del coord no debería ni mandarlo.
        if not actor_is_admin and new_no and new_no != t.no_empleado:
            return jsonify({'error': 'No puedes modificar el número de empleado'}), 403
        if actor_is_admin and new_no and new_no != t.no_empleado and Trabajador.query.filter_by(no_empleado=new_no).first():
            return jsonify({'error': 'El Número de Empleado ya existe'}), 409
        errores_longitud = validate_lengths(data)
        if errores_longitud:
            return jsonify({'error': 'Error de longitud en campos', 'details': errores_longitud}), 400

        warnings = _apply_payload(t, data, actor_is_admin=actor_is_admin)

        try:
            _replace_credenciales(t, data.get('credenciales_json', '[]'))
        except ValueError as e:
            return jsonify({'error': str(e)}), 400
        except Exception as e:
            current_app.logger.error('Error parsing credentials on update: %s', e)
            warnings.append('Hubo un problema actualizando algunas credenciales.')

        foto = request.files.get('foto_perfil')
        if foto and foto.filename:
            try:
                _save_foto(t, foto)
            except ValueError as e:
                return jsonify({'error': str(e)}), 400

        db.session.commit()
        log_action(f'Actualizó al trabajador {t.nombre} ({t.no_empleado})')
        # Coordinador también dispara la invalidación: edita campos médicos /
        # contacto que aparecen en la lista de empleados de admin.
        emit_to_role(['admin', 'super_admin'], 'empleado:changed', {
            'id': t.id, 'action': 'updated',
        })
        return jsonify({'id': t.id, 'warnings': warnings})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error('Error actualizando trabajador: %s\n%s', e, traceback.format_exc())
        return jsonify({'error': 'Error al actualizar el trabajador'}), 500


@bp.route('/<int:id>', methods=['DELETE'])
@jwt_required
def dar_baja(id):
    if not is_admin():
        return jsonify({'error': 'Solo admin puede dar de baja'}), 403
    t = Trabajador.query.get(id)
    if not t:
        return jsonify({'error': 'No encontrado'}), 404
    try:
        t.activo = False
        t.fecha_baja = date.today()
        db.session.commit()
        log_action(f'Dio de baja al trabajador {t.nombre} ({t.no_empleado})')
        emit_to_role(['admin', 'super_admin'], 'empleado:changed', {
            'id': t.id, 'action': 'baja',
        })
        return jsonify({'ok': True})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error('Error baja: %s', e)
        return jsonify({'error': 'Error al dar de baja'}), 500


@bp.route('/<int:id>/reactivar', methods=['POST'])
@jwt_required
def reactivar(id):
    if not is_admin():
        return jsonify({'error': 'Solo admin puede reactivar'}), 403
    t = Trabajador.query.get(id)
    if not t:
        return jsonify({'error': 'No encontrado'}), 404
    try:
        t.activo = True
        t.fecha_baja = None
        db.session.commit()
        log_action(f'Reactivó al trabajador {t.nombre} ({t.no_empleado})')
        emit_to_role(['admin', 'super_admin'], 'empleado:changed', {
            'id': t.id, 'action': 'reactivado',
        })
        return jsonify({'ok': True})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error('Error reactivar: %s', e)
        return jsonify({'error': 'Error al reactivar'}), 500


# ── Acciones en lote (baja / reactivar varios a la vez) ──────────────────────

@bp.route('/bulk', methods=['POST'])
@jwt_required
def bulk_accion():
    if not is_admin():
        return jsonify({'error': 'Solo admin puede realizar acciones en lote'}), 403

    payload = request.get_json(silent=True) or {}
    action = (payload.get('action') or '').strip()
    raw_ids = payload.get('ids') or []

    if action not in ('baja', 'reactivar'):
        return jsonify({'error': 'Acción no válida'}), 422
    if not isinstance(raw_ids, list) or not raw_ids:
        return jsonify({'error': 'Lista de IDs vacía'}), 422
    # Tope defensivo: evita una operación accidental sobre miles de filas.
    if len(raw_ids) > 100:
        return jsonify({'error': 'Máximo 100 IDs por operación'}), 422
    try:
        ids = sorted({int(i) for i in raw_ids})
    except (TypeError, ValueError):
        return jsonify({'error': 'IDs deben ser enteros'}), 422

    trabajadores = Trabajador.query.filter(Trabajador.id.in_(ids)).all()
    found_ids = {t.id for t in trabajadores}
    skipped = [{'id': i, 'reason': 'no_encontrado'} for i in ids if i not in found_ids]

    affected = []
    today = date.today()
    for t in trabajadores:
        if action == 'baja':
            if not t.activo:
                skipped.append({'id': t.id, 'reason': 'ya_inactivo'})
                continue
            t.activo = False
            t.fecha_baja = today
        else:  # reactivar
            if t.activo:
                skipped.append({'id': t.id, 'reason': 'ya_activo'})
                continue
            t.activo = True
            t.fecha_baja = None
        affected.append(t.id)

    if not affected:
        return jsonify({'ok': True, 'affected': 0, 'ids': [], 'skipped': skipped})

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        current_app.logger.error('Error bulk %s: %s', action, e)
        return jsonify({'error': 'Error en operación en lote'}), 500

    log_action(f'Bulk {action} sobre {len(affected)} trabajadores: {affected}')
    # Un solo emit cubre la operación completa: otros admins recargan la lista
    # una vez (no N veces). El payload incluye ids para que el receptor pueda
    # invalidar caches selectivas si quisiera más adelante.
    emit_to_role(['admin', 'super_admin'], 'empleado:changed', {
        'action': f'bulk_{action}',
        'ids': affected,
    })
    return jsonify({
        'ok': True,
        'affected': len(affected),
        'ids': affected,
        'skipped': skipped,
    })
