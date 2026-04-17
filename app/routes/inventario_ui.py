from flask import Blueprint, render_template, session, redirect, url_for, flash, jsonify, make_response, current_app
from app.utils import login_required
from app.models import User, Estante, Proyecto, SolicitudMaterial
import io

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

@bp.route('/solicitudes/<int:solicitud_id>/pdf')
@login_required
def solicitud_pdf(solicitud_id):
    from xhtml2pdf import pisa
    import os

    s = SolicitudMaterial.query.get_or_404(solicitud_id)
    fecha = s.fecha_creacion.strftime('%d/%m/%Y %H:%M') if s.fecha_creacion else '—'
    estatus = s.estatus or 'PENDIENTE'

    logo_path = os.path.join(current_app.root_path, '..', 'static', 'imagenes', 'skilled (1).png')
    logo_path = os.path.normpath(logo_path)

    STATUS_COLORS = {
        'PENDIENTE': ('#92400E', '#D97706', '#FFFBEB'),
        'APROBADA':  ('#065F46', '#10B981', '#ECFDF5'),
        'RECHAZADA': ('#991B1B', '#EF4444', '#FEF2F2'),
        'ENTREGADA': ('#1E40AF', '#3B82F6', '#EFF6FF'),
    }
    sc, sb, sbg = STATUS_COLORS.get(estatus, ('#374151', '#6B7280', '#F9FAFB'))

    filas_html = ''.join(f"""
        <tr>
            <td class="td {'td-alt' if i%2==0 else ''}" align="center">{i+1}</td>
            <td class="td {'td-alt' if i%2==0 else ''}" style="font-weight:bold;">{d.producto.descripcion if d.producto else '—'}</td>
            <td class="td {'td-alt' if i%2==0 else ''}" style="font-size:10px;color:#6B7280;font-weight:bold;text-transform:uppercase;">{d.producto.codigo if d.producto else '—'}</td>
            <td class="td {'td-alt' if i%2==0 else ''}" align="center" style="font-weight:bold;font-size:13px;color:#4F46E5;">{int(d.cantidad_solicitada)}</td>
            <td class="td {'td-alt' if i%2==0 else ''}" style="color:#9CA3AF;">{d.producto.unidad if d.producto else 'pza'}</td>
        </tr>""" for i, d in enumerate(s.detalles))

    solicitante_nombre = s.solicitante.username if s.solicitante else '—'
    proyecto = s.proyecto or 'General'

    logo_uri = logo_path.replace('\\', '/')

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<style>
  body {{ font-family: Arial, sans-serif; color: #111; font-size: 12px; margin: 0; padding: 0; }}
  @page {{ margin: 2cm 2cm 2cm 2cm; size: letter; }}
  .lbl {{ font-size: 8px; font-weight: bold; color: #888; text-transform: uppercase; }}
  .val {{ font-size: 12px; font-weight: bold; color: #111; border-bottom: 1px solid #ddd; padding-bottom: 3px; }}
  .sec {{ font-size: 9px; font-weight: bold; color: #6B7280; text-transform: uppercase; letter-spacing: 1px; margin: 14px 0 6px 0; }}
  .th {{ background-color: #1f2937; color: #fff; font-size: 9px; font-weight: bold; text-transform: uppercase; padding: 8px 10px; text-align: left; }}
  .td {{ font-size: 11px; padding: 8px 10px; border-bottom: 1px solid #f1f5f9; }}
  .td-alt {{ background-color: #f9fafb; }}
  .sig-line {{ border-top: 1px solid #374151; padding-top: 6px; font-size: 9px; color: #6B7280; font-weight: bold; text-transform: uppercase; text-align: center; }}
  .status {{ font-size: 9px; font-weight: bold; text-transform: uppercase; padding: 3px 10px; border: 1px solid {sb}; color: {sc}; background-color: {sbg}; }}
</style>
</head>
<body>

<!-- HEADER -->
<table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:4px;">
  <tr>
    <td width="50%" valign="top">
      <img src="file:///{logo_uri}" height="46" alt="Skilled">
      <br>
      <span style="font-size:9px; color:#888; font-weight:bold; text-transform:uppercase;">Solicitud de Materiales</span>
    </td>
    <td width="50%" valign="top" align="right">
      <div style="font-size:16px; font-weight:bold; color:#111;">Solicitud #{s.id}</div>
      <div style="font-size:10px; color:#888; margin-top:3px;">{fecha}</div>
      <div style="margin-top:5px;"><span class="status">{estatus}</span></div>
    </td>
  </tr>
</table>

<hr style="border:none; border-top:2px solid #111; margin:10px 0 14px 0;">

<!-- INFO -->
<table width="100%" cellpadding="4" cellspacing="0" style="margin-bottom:14px;">
  <tr>
    <td width="50%" valign="top">
      <div class="lbl">Solicitante</div>
      <div class="val">{solicitante_nombre}</div>
    </td>
    <td width="50%" valign="top">
      <div class="lbl">Proyecto</div>
      <div class="val">{proyecto}</div>
    </td>
  </tr>
  <tr>
    <td width="50%" valign="top" style="padding-top:8px;">
      <div class="lbl">Total de materiales</div>
      <div class="val">{len(s.detalles)} material{'es' if len(s.detalles) != 1 else ''}</div>
    </td>
    <td width="50%" valign="top" style="padding-top:8px;">
      <div class="lbl">Estado</div>
      <div class="val">{estatus}</div>
    </td>
  </tr>
</table>

<div class="sec">Materiales solicitados</div>
<table width="100%" cellpadding="0" cellspacing="0" style="border: 1px solid #e5e7eb;">
  <thead>
    <tr>
      <th class="th" width="5%" align="center">#</th>
      <th class="th" width="45%">Descripción</th>
      <th class="th" width="25%">Código</th>
      <th class="th" width="15%" align="center">Cantidad</th>
      <th class="th" width="10%">Unidad</th>
    </tr>
  </thead>
  <tbody>
    {filas_html}
  </tbody>
</table>

<br><br>
<table width="100%" cellpadding="0" cellspacing="0" style="margin-top:32px;">
  <tr>
    <td width="33%" style="padding: 0 12px;"><div class="sig-line">Solicitante</div></td>
    <td width="33%" style="padding: 0 12px;"><div class="sig-line">Autorizado por</div></td>
    <td width="33%" style="padding: 0 12px;"><div class="sig-line">Entregado por</div></td>
  </tr>
</table>

</body>
</html>"""

    static_dir = os.path.normpath(os.path.join(current_app.root_path, '..', 'static'))

    def link_callback(uri, rel):
        if uri.startswith('file:///'):
            return uri[8:]
        if uri.startswith('/static/'):
            return os.path.join(static_dir, uri[8:])
        return uri

    buf = io.BytesIO()
    pisa.CreatePDF(html, dest=buf, link_callback=link_callback)
    buf.seek(0)
    response = make_response(buf.read())
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'attachment; filename="Solicitud_{s.id}.pdf"'
    return response


@bp.route('/mis-pedidos')
@login_required
def mis_pedidos():
    """Historial de pedidos del solicitante."""
    user = User.query.get(session.get('user_id'))
    if user.role not in ['solicitante_material', 'admin', 'inventario']:
        flash('No tienes permiso para ver esta página.', 'danger')
        return redirect(url_for('main.home'))
    return render_template('inventario_mis_pedidos.html', user=user)
