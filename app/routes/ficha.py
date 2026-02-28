from flask import Blueprint, render_template, session, redirect, url_for, flash
from app.extensions import db
from app.models import Proyecto, Trabajador
from app.utils import login_required

bp = Blueprint('ficha', __name__, url_prefix='/ficha')

@bp.route('/', methods=['GET'])
@login_required
def index():
    user_id = session.get('user_id')
    user_role = session.get('role', 'user')

    if user_role != 'coordinador':
        flash('Acceso denegado. Solo coordinadores pueden ver esta sección.', 'danger')
        return redirect(url_for('main.home'))

    # Retrieve projects for this coordinator
    proyectos = Proyecto.query.filter_by(coordinador_id=user_id).all()
    
    # Get all active workers from these projects
    trabajadores_set = set()
    for p in proyectos:
        for t in p.participantes:
            if t.activo:
                trabajadores_set.add(t)

    # Sort them by name
    trabajadores = sorted(list(trabajadores_set), key=lambda x: x.nombre_apellidos)

    return render_template('ficha_tecnica.html', trabajadores=trabajadores)
