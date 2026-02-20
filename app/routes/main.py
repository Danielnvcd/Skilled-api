from flask import Blueprint, render_template
from app.utils import login_required

bp = Blueprint('main', __name__)

@bp.route('/')
@login_required
def home():
    return render_template('index.html')

@bp.route('/credenciales')
@login_required
def credenciales():
    return render_template('credenciales.html')

@bp.route('/prenomina')
@login_required
def prenomina():
    return render_template('prenomina.html')

@bp.route('/supervisores')
@login_required
def supervisores():
    return render_template('supervisores.html')
