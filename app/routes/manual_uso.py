from flask import Blueprint, render_template, session, redirect, url_for
from app.utils import login_required

bp = Blueprint('manual_uso', __name__, url_prefix='/manual')

@bp.route('/', methods=['GET'])
@login_required
def index():
    if session.get('role') != 'admin':
        return redirect(url_for('main.home'))
    return render_template('manual_uso.html')
