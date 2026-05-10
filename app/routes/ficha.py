from flask import Blueprint, render_template, session, redirect, url_for, flash, request
from app.extensions import db
from app.models import Proyecto, Trabajador
from app.utils import login_required

bp = Blueprint('ficha', __name__, url_prefix='/ficha')


def _get_trabajadores(user_id):
    proyectos = Proyecto.query.filter_by(coordinador_id=user_id).all()
    trabajadores_set = set()
    for p in proyectos:
        for t in p.participantes:
            if t.activo:
                trabajadores_set.add(t)
    return sorted(list(trabajadores_set), key=lambda x: x.nombre_apellidos)


@bp.route('/', methods=['GET'])
@login_required
def index():
    user_id = session.get('user_id')
    user_role = session.get('role', 'user')

    if user_role != 'coordinador':
        flash('Acceso denegado. Solo coordinadores pueden ver esta sección.', 'danger')
        return redirect(url_for('main.home'))

    is_mobile = request.user_agent.platform in ('android', 'iphone', 'ipad') or \
                'mobi' in request.user_agent.string.lower()
    if is_mobile:
        return redirect(url_for('ficha.movil'))

    trabajadores = _get_trabajadores(user_id)
    return render_template('ficha_tecnica.html', trabajadores=trabajadores)


@bp.route('/movil', methods=['GET'])
@login_required
def movil():
    user_id = session.get('user_id')
    user_role = session.get('role', 'user')

    if user_role != 'coordinador':
        flash('Acceso denegado.', 'danger')
        return redirect(url_for('main.home'))

    trabajadores = _get_trabajadores(user_id)
    return render_template('ficha_movil.html', trabajadores=trabajadores)
