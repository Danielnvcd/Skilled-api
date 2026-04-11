from flask import Blueprint, render_template, session, redirect, url_for, flash, jsonify
from app.utils import login_required
from app.models import User, Estante, Proyecto

bp = Blueprint('inventario_ui', __name__, url_prefix='/inventario')

@bp.route('/movil')
@login_required
def movil():
    user = User.query.get(session.get('user_id'))
    if user.role not in ['inventario', 'admin']:
        flash('No tienes permiso para ver esta página.', 'danger')
        return redirect(url_for('main.home'))
    return render_template('inventario_movil.html', user=user)

@bp.route('/web')
@login_required
def web():
    user = User.query.get(session.get('user_id'))
    if user.role not in ['inventario', 'admin']:
        flash('No tienes permiso para ver esta página.', 'danger')
        return redirect(url_for('main.home'))
    # Redirigir a catálogo por defecto si alguien entra a /web
    return redirect(url_for('inventario_ui.catalogo'))

@bp.route('/catalogo')
@login_required
def catalogo():
    user = User.query.get(session.get('user_id'))
    if user.role not in ['inventario', 'admin']:
        flash('No tienes permiso.', 'danger')
        return redirect(url_for('main.home'))
    return render_template('inventario_catalogo.html', user=user)

@bp.route('/catalogo/<categoria>')
@login_required
def catalogo_categoria(categoria):
    user = User.query.get(session.get('user_id'))
    if user.role not in ['inventario', 'admin']:
        flash('No tienes permiso.', 'danger')
        return redirect(url_for('main.home'))
    return render_template('inventario_categoria.html', user=user, categoria=categoria)

@bp.route('/estantes')
@login_required
def estantes():
    user = User.query.get(session.get('user_id'))
    if user.role not in ['inventario', 'admin']:
        flash('No tienes permiso.', 'danger')
        return redirect(url_for('main.home'))
    return render_template('inventario_estantes.html', user=user)

@bp.route('/qr/estante/<int:estante_id>')
@login_required
def qr_estante(estante_id):
    """Página de impresión del QR de un estante."""
    user = User.query.get(session.get('user_id'))
    if user.role not in ['inventario', 'admin']:
        flash('No tienes permiso.', 'danger')
        return redirect(url_for('main.home'))
    estante = Estante.query.get_or_404(estante_id)
    return render_template('inventario_qr_print.html', estante=estante)

@bp.route('/solicitar')
@login_required
def solicitar():
    """Formulario para que los solicitantes pidan material."""
    user = User.query.get(session.get('user_id'))
    if user.role not in ['solicitante_material', 'admin', 'inventario']:
        flash('No tienes permiso para solicitar material.', 'danger')
        return redirect(url_for('main.home'))
    return render_template('inventario_solicitar.html', user=user)

@bp.route('/api/proyectos')
@login_required
def api_proyectos():
    proyectos = Proyecto.query.filter_by(activo=True).order_by(Proyecto.numero_proyecto).all()
    return jsonify([{
        'id': p.id,
        'numero_proyecto': p.numero_proyecto,
        'nombre': p.nombre or ''
    } for p in proyectos])

@bp.route('/solicitudes')
@login_required
def solicitudes():
    """Pantalla para gestionar solicitudes (aprobar/denegar)."""
    user = User.query.get(session.get('user_id'))
    if user.role not in ['inventario', 'admin']:
        flash('Acceso restringido a personal de inventario.', 'danger')
        return redirect(url_for('main.home'))
    return render_template('inventario_solicitudes.html', user=user)

@bp.route('/mis-pedidos')
@login_required
def mis_pedidos():
    """Historial de pedidos del solicitante."""
    user = User.query.get(session.get('user_id'))
    if user.role not in ['solicitante_material', 'admin', 'inventario']:
        flash('No tienes permiso para ver esta página.', 'danger')
        return redirect(url_for('main.home'))
    return render_template('inventario_mis_pedidos.html', user=user)
