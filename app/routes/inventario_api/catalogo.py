"""Proyectos + Categorías + CategoriaConfig + Importación masiva desde Excel."""
import io
import re
from decimal import Decimal

from flask import jsonify, request, send_file, Response, current_app
from sqlalchemy import distinct as sql_distinct

from app.extensions import db, limiter
from app.models import (
    Producto, Proyecto, CategoriaConfig, StockPorAlmacen,
    SolicitudMaterial, SolicitudMaterialDetalle,
)

from ._core import (
    bp,
    _require_login, _require_inventario, _require_inventario_admin,
    _parse_or_422,
    CategoriaConfigUpsertSchema,
    CODIGO_REGEX, _IMAGEN_URL_REGEX,
    _audit, _almacen_default_id,
    _INV_ROLES,
)
from app.realtime import emit_to_role


# ─── Proyectos ────────────────────────────────────────────────────────────────

@bp.route('/proyectos/', methods=['GET'])
@_require_login
def get_proyectos():
    proyectos = (
        Proyecto.query
        .filter(Proyecto.activo == True)
        .order_by(Proyecto.numero_proyecto)
        .all()
    )
    return jsonify([
        {'id': p.id, 'numero_proyecto': p.numero_proyecto, 'nombre': p.nombre or ''}
        for p in proyectos
    ])


# ─── Categorías ───────────────────────────────────────────────────────────────

@bp.route('/categorias/', methods=['GET'])
@_require_inventario
def get_categorias():
    """Devuelve la unión de categorías presentes en el catálogo de productos
    y las registradas en `categorias_config` (admin pudo crear categorías sin
    haber capturado aún ningún producto)."""
    prod_rows = (
        db.session.query(sql_distinct(Producto.categoria))
        .filter(Producto.activo == True, Producto.categoria != None, Producto.categoria != '')
        .all()
    )
    cfg_rows = db.session.query(CategoriaConfig.nombre).all()
    nombres = {r[0] for r in prod_rows} | {r[0] for r in cfg_rows}
    return jsonify(sorted(nombres))


@bp.route('/categorias/resumen', methods=['GET'])
@_require_inventario
def get_categorias_resumen():
    """Conteo de productos por categoría (total + cuántos bajo mínimo) para las
    tarjetas del catálogo. Server-side: evita descargar miles de productos al
    cliente solo para contarlos. Incluye categorías de `categorias_config` que
    aún no tienen productos (total 0)."""
    rows = (
        db.session.query(
            Producto.categoria,
            db.func.count(Producto.id),
            db.func.coalesce(
                db.func.sum(
                    db.case((Producto.stock_actual <= Producto.stock_minimo, 1), else_=0)
                ), 0,
            ),
        )
        .filter(Producto.activo == True, Producto.categoria != None, Producto.categoria != '')  # noqa: E711,E712
        .group_by(Producto.categoria)
        .all()
    )
    resumen = {
        nombre: {'nombre': nombre, 'total': int(total or 0), 'bajo_minimo': int(bajos or 0)}
        for nombre, total, bajos in rows
    }
    # Categorías registradas en config pero sin productos todavía.
    for (nombre,) in db.session.query(CategoriaConfig.nombre).all():
        if nombre and nombre not in resumen:
            resumen[nombre] = {'nombre': nombre, 'total': 0, 'bajo_minimo': 0}

    return jsonify(sorted(resumen.values(), key=lambda r: r['nombre'].lower()))


# ─── CategoriaConfig (metadatos visuales por categoría) ──────────────────────

def _categoria_config_to_dict(c: CategoriaConfig) -> dict:
    return {
        'nombre': c.nombre,
        'imagen_url': c.imagen_url,
        'updated_at': c.updated_at.isoformat() if c.updated_at else None,
    }


@bp.route('/categorias-config/', methods=['GET'])
@_require_login
def get_categorias_config():
    """Lista todas las configuraciones (imagen, etc.) por nombre de categoría.
    Lectura abierta a cualquier usuario autenticado: el dashboard de inventario
    también lo consume desde el rol solicitante_material."""
    rows = CategoriaConfig.query.order_by(CategoriaConfig.nombre).all()
    return jsonify([_categoria_config_to_dict(c) for c in rows])


@bp.route('/categorias-config/<string:nombre>', methods=['PUT'])
@_require_inventario_admin
def upsert_categoria_config(nombre: str):
    """Crea o actualiza la config de la categoría con `nombre`. Si imagen_url
    viene null o vacío, persiste null (UI lo trata como "quitar imagen")."""
    nombre = (nombre or '').strip()
    if not nombre or len(nombre) > 100:
        return jsonify({'detail': "Nombre de categoría inválido (1..100 caracteres)"}), 422

    data, err = _parse_or_422(CategoriaConfigUpsertSchema(), request.get_json(silent=True))
    if err: return err

    imagen = (data.get('imagen_url') or '').strip() or None

    cfg = CategoriaConfig.query.filter(CategoriaConfig.nombre == nombre).first()
    if cfg is None:
        cfg = CategoriaConfig(
            nombre=nombre,
            imagen_url=imagen,
            created_by_id=request.current_user.id,
        )
        db.session.add(cfg)
        _audit(request.current_user, f"Categoría '{nombre}' creada/actualizada")
    else:
        cfg.imagen_url = imagen
        _audit(request.current_user, f"Categoría '{nombre}' actualizada")

    db.session.commit()
    db.session.refresh(cfg)
    return jsonify(_categoria_config_to_dict(cfg))


@bp.route('/categorias-config/<string:nombre>', methods=['DELETE'])
@_require_inventario_admin
def delete_categoria_config(nombre: str):
    """Elimina una categoría.

    Por defecto solo borra la fila de config (metadatos visuales) y NO afecta
    productos. Para eliminar también todos los productos de la categoría —el
    flujo destructivo que el SPA debe confirmar con una advertencia— pasar
    `?con_productos=1`. En ese caso:
      - Se hace soft-delete (activo=False) de cada producto, igual que
        `delete_producto`, para preservar el histórico de movimientos y
        solicitudes (no se rompe el kardex ni los folios).
      - Se liberan las reservas pendientes de stock de esos productos para que
        no queden unidades apartadas por solicitudes sobre un producto muerto.
      - Se borra también la config de la categoría.
    """
    nombre = (nombre or '').strip()
    con_productos = (request.args.get('con_productos') or '').lower() in ('1', 'true', 'yes')

    cfg = CategoriaConfig.query.filter(CategoriaConfig.nombre == nombre).first()

    if not con_productos:
        if not cfg:
            return jsonify({'detail': 'Categoría no encontrada en config'}), 404
        db.session.delete(cfg)
        _audit(request.current_user, f"Categoría '{nombre}' config eliminada")
        db.session.commit()
        return Response(status=204)

    # Borrado en cascada: productos + config.
    productos = (
        Producto.query
        .filter(Producto.categoria == nombre, Producto.activo == True)  # noqa: E712
        .all()
    )
    # Si no hay ni productos ni config, la categoría no existe.
    if not productos and not cfg:
        return jsonify({'detail': 'Categoría no encontrada'}), 404

    # Guardia: no borrar productos que tengan entregas pendientes. Una solicitud
    # APROBADA y no entregada apartó stock y el solicitante la espera; si
    # desactiváramos el producto, la entrega posterior generaría una SALIDA
    # sobre un producto muerto y dejaría la solicitud colgada. Bloqueamos con
    # 409 y devolvemos qué solicitudes resolver primero (opción conservadora:
    # el almacenista decide, no cancelamos pedidos ajenos automáticamente).
    prod_ids = [p.id for p in productos]
    if prod_ids:
        # pendiente por línea = base − entregada, con base = aprobada (o
        # solicitada si aún no se tocó la aprobación, caso pre-8b). Mismo
        # criterio que `_reservas_de_solicitud`.
        base = db.func.coalesce(
            db.func.nullif(SolicitudMaterialDetalle.cantidad_aprobada, 0),
            SolicitudMaterialDetalle.cantidad_solicitada,
        )
        pendiente = base - db.func.coalesce(SolicitudMaterialDetalle.cantidad_entregada, 0)
        filas_bloqueo = (
            db.session.query(
                SolicitudMaterial.id,
                Producto.codigo,
                Producto.descripcion,
            )
            .join(SolicitudMaterialDetalle, SolicitudMaterialDetalle.solicitud_id == SolicitudMaterial.id)
            .join(Producto, Producto.id == SolicitudMaterialDetalle.producto_id)
            .filter(
                SolicitudMaterial.estatus == 'APROBADA',
                SolicitudMaterialDetalle.producto_id.in_(prod_ids),
                pendiente > 0,
            )
            .distinct()
            .all()
        )
        if filas_bloqueo:
            solicitudes_ids = sorted({r.id for r in filas_bloqueo})
            return jsonify({
                'detail': (
                    'No se puede eliminar la categoría: tiene productos con entregas '
                    f'pendientes en {len(solicitudes_ids)} solicitud(es) aprobada(s). '
                    'Entrega o rechaza esas solicitudes antes de borrar.'
                ),
                'codigo': 'ENTREGAS_PENDIENTES',
                'solicitudes': [f'SOL-{sid:06d}' for sid in solicitudes_ids],
                'productos': sorted({f'{r.codigo} — {r.descripcion}' for r in filas_bloqueo}),
            }), 409

    eliminados = 0
    for prod in productos:
        prod.activo = False  # Soft delete: conserva histórico (igual que delete_producto)
        # Libera la reserva que esté apartando para que no quede stock fantasma
        # apartado por solicitudes sobre un producto desactivado.
        if prod.stock_reservado:
            prod.stock_reservado = Decimal('0')
        eliminados += 1

    if cfg:
        db.session.delete(cfg)

    _audit(
        request.current_user,
        f"Categoría '{nombre}' eliminada con sus productos ({eliminados} desactivados)",
    )
    db.session.commit()

    # Websockets-first: refresca catálogos abiertos en otras sesiones. El front
    # invalida el namespace completo de productos con un solo emit (igual que la
    # importación masiva) y refresca las tarjetas de categorías.
    emit_to_role(_INV_ROLES, 'producto:changed', {
        'action': 'bulk_delete', 'categoria': nombre, 'count': eliminados,
    })

    return jsonify({
        'categoria': nombre,
        'productos_eliminados': eliminados,
        'config_eliminada': cfg is not None,
    })


# ─── Importar materiales desde Excel ─────────────────────────────────────────

# Encabezados oficiales de la plantilla. El importador acepta también una
# variante en lowercase/sin tildes para no romper si el usuario edita los headers.
PLANTILLA_HEADERS = [
    'Código (SKU)', 'Descripción', 'Categoría', 'Unidad',
    'Stock Inicial', 'Stock Mínimo', 'Precio Unitario', 'URL Imagen (opcional)',
]
PLANTILLA_MAX_FILAS = 5000  # Tope para evitar DoS por archivo gigante


def _norm_header(s: str) -> str:
    """Normaliza un header para comparación: lower, sin acentos, sin espacios extra."""
    import unicodedata
    s = unicodedata.normalize('NFKD', str(s or '')).encode('ascii', 'ignore').decode('ascii')
    return s.strip().lower().replace('  ', ' ')


def _cell_str(value, maxlen=None) -> str:
    """Convierte valor de pandas a str limpio (NaN→'', strip). Trunca si maxlen."""
    import math
    if value is None:
        return ''
    try:
        if isinstance(value, float) and math.isnan(value):
            return ''
    except Exception:
        pass
    s = str(value).strip()
    if s.lower() == 'nan':
        return ''
    if maxlen and len(s) > maxlen:
        s = s[:maxlen]
    return s


def _norm_categoria(s: str) -> str:
    """Clave de comparación case/acento-insensitiva para nombres de categoría.
    Sirve solo como índice: el nombre original (con tildes y mayúsculas) se
    conserva como 'nombre canónico' en la primera ocurrencia."""
    import unicodedata
    s = unicodedata.normalize('NFKD', str(s or '')).encode('ascii', 'ignore').decode('ascii')
    return ' '.join(s.strip().lower().split())


def _cell_number(value, default=0.0):
    """Convierte celda a float. Acepta '1,234.56' (formato MX), devuelve (valor, error_str_o_None)."""
    import math
    if value is None or value == '':
        return default, None
    try:
        if isinstance(value, float) and math.isnan(value):
            return default, None
    except Exception:
        pass
    if isinstance(value, (int, float)):
        return float(value), None
    s = str(value).strip().replace(',', '').replace('$', '').replace(' ', '')
    if not s:
        return default, None
    try:
        return float(s), None
    except ValueError:
        return default, f'no es un número válido ({value!r})'


@bp.route('/productos/plantilla-importar', methods=['GET'])
@_require_inventario
def get_plantilla_materiales():
    """Genera y sirve un Excel de plantilla para carga masiva de productos.
    Incluye instrucciones, validaciones de celda (dropdown unidad, números) y
    columna opcional de URL de imagen."""
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        from openpyxl.utils import get_column_letter
        from openpyxl.worksheet.datavalidation import DataValidation
        from openpyxl.comments import Comment
    except ImportError:
        return jsonify({'detail': 'openpyxl no instalado en el servidor'}), 500

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Materiales'

    # Estilos
    header_fill = PatternFill(start_color='1E40AF', end_color='1E40AF', fill_type='solid')
    header_font = Font(bold=True, color='FFFFFF', size=11)
    title_font = Font(bold=True, color='111827', size=14)
    instr_font = Font(italic=True, color='6B7280', size=10)
    thin = Side(border_style='thin', color='CBD5E1')
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    # Fila 1: título
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(PLANTILLA_HEADERS))
    c = ws.cell(row=1, column=1, value='Plantilla de Importación de Materiales — SKILLED')
    c.font = title_font
    c.alignment = Alignment(horizontal='center', vertical='center')
    ws.row_dimensions[1].height = 28

    # Fila 2: instrucciones
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=len(PLANTILLA_HEADERS))
    c = ws.cell(row=2, column=1,
        value='⚠ No alteres los encabezados de la fila 4. Precio y URL Imagen son opcionales (URL solo HTTPS o /static/...png). Si un SKU ya existe, se ACTUALIZA con los datos de esta fila (el stock NO se toca).')
    c.font = instr_font
    c.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    ws.row_dimensions[2].height = 30

    # Fila 3 vacía como separador
    ws.row_dimensions[3].height = 6

    # Fila 4: encabezados oficiales
    for col, header in enumerate(PLANTILLA_HEADERS, 1):
        cell = ws.cell(row=4, column=col, value=header)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        cell.border = border
        # Anchos diferenciados
        widths = {1: 18, 2: 36, 3: 18, 4: 10, 5: 14, 6: 14, 7: 16, 8: 42}
        ws.column_dimensions[get_column_letter(col)].width = widths.get(col, 18)

    # Comentarios (tooltips) en cada header
    tooltips = {
        1: 'Código único del producto (SKU). Acepta letras, números, -_./',
        2: 'Descripción corta del producto (máx 250 caracteres)',
        3: 'Categoría (ej: Tornillería, Eléctrico, Pinturas)',
        4: 'Unidad de medida: Pza, Kg, Mts, Lts, Caja, Bote, etc.',
        5: 'Cantidad inicial en almacén (número >= 0)',
        6: 'Cantidad mínima antes de alertar de bajo stock (número >= 0)',
        7: 'Precio unitario del material (número >= 0). Se usa para los costos por proyecto.',
        8: 'OPCIONAL — URL HTTPS de la imagen del producto. Ej: https://cdn.miempresa.com/tornillo.jpg',
    }
    for col, txt in tooltips.items():
        ws.cell(row=4, column=col).comment = Comment(txt, 'Plantilla SKILLED')

    ws.row_dimensions[4].height = 36
    ws.freeze_panes = 'A5'

    # Sin filas de ejemplo: la plantilla viene vacía para que el usuario
    # solo capture sus productos reales (no se importen los ejemplos por error).
    # Las instrucciones en filas 1-2 + los tooltips en los headers ya muestran
    # el formato esperado.

    # Data validations (solo aplican a filas 5..1004 para mantener archivo ligero)
    rango = '5:1004'

    # Stock inicial y mínimo: número >= 0
    num_dv = DataValidation(type='decimal', operator='greaterThanOrEqual', formula1=0,
                             showErrorMessage=True,
                             errorTitle='Stock inválido',
                             error='El stock debe ser un número mayor o igual a 0.')
    num_dv.add(f'E5:E1004')
    num_dv.add(f'F5:F1004')
    num_dv.add(f'G5:G1004')  # Precio Unitario
    ws.add_data_validation(num_dv)

    # Longitudes
    desc_dv = DataValidation(type='textLength', operator='between', formula1=1, formula2=250,
                              showErrorMessage=True,
                              errorTitle='Descripción inválida',
                              error='Entre 1 y 250 caracteres.')
    desc_dv.add(f'B5:B1004')
    ws.add_data_validation(desc_dv)

    sku_dv = DataValidation(type='textLength', operator='between', formula1=1, formula2=50,
                              showErrorMessage=True,
                              errorTitle='SKU inválido',
                              error='Entre 1 y 50 caracteres.')
    sku_dv.add(f'A5:A1004')
    ws.add_data_validation(sku_dv)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    return send_file(
        buf,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name='plantilla_materiales.xlsx',
    )


@bp.route('/productos/importar', methods=['POST'])
@_require_inventario_admin
@limiter.limit('5 per minute')
def importar_materiales():
    """Importa productos en masa desde un archivo Excel.

    Validaciones a prueba de tontos:
      - Tamaño max 5 MB.
      - Extensión .xlsx/.xls.
      - Headers tolerantes (acepta variantes sin tildes/lowercase, encuentra la
        fila de headers automáticamente).
      - Trim, normalización y truncado por seguridad.
      - SKU debe pasar CODIGO_REGEX (mismo que crear manual).
      - Descripción, categoría, unidad: validación de longitud.
      - Stocks: parseo tolerante ('1,234.56', '$50', etc.) con error claro.
      - URL imagen pasa por _IMAGEN_URL_REGEX (anti-XSS/SSRF).
      - Duplicados dentro del mismo archivo y contra DB.
      - Filas vacías se ignoran silenciosamente.
      - Tope de PLANTILLA_MAX_FILAS para evitar DoS.
    """
    try:
        import pandas as pd
    except ImportError:
        return jsonify({'detail': 'pandas no instalado en el servidor'}), 500

    file = request.files.get('archivo') or request.files.get('archivo_excel')
    if not file or not file.filename:
        return jsonify({'detail': 'No se envió archivo'}), 400
    if not file.filename.lower().endswith(('.xlsx', '.xls')):
        return jsonify({'detail': 'Formato no válido. Debe ser .xlsx o .xls'}), 400

    # Validar tamaño antes de cargar a pandas (evita DoS).
    file.stream.seek(0, io.SEEK_END)
    size = file.stream.tell()
    file.stream.seek(0)
    MAX_BYTES = 5 * 1024 * 1024
    if size > MAX_BYTES:
        return jsonify({'detail': f'Archivo demasiado grande. Máximo {MAX_BYTES // (1024*1024)} MB.'}), 413
    if size < 100:
        return jsonify({'detail': 'Archivo vacío o corrupto.'}), 400

    # Buscar la fila de encabezados (acepta hasta fila 10 — la plantilla los pone en fila 4).
    # Si los encabezados no se encuentran, fallamos con mensaje claro.
    try:
        raw = pd.read_excel(file, header=None, nrows=PLANTILLA_MAX_FILAS + 20)
    except Exception as e:
        return jsonify({
            'detail': 'No se pudo leer el Excel. Asegúrate de usar la plantilla y guardar como .xlsx.',
            'tecnico': str(e)[:200],
        }), 400

    expected_norm = {_norm_header(h): h for h in PLANTILLA_HEADERS}
    header_row_idx = None
    column_map = {}  # idx_columna_excel -> nombre_oficial
    for ridx in range(min(10, len(raw))):
        row_vals = [_norm_header(v) for v in raw.iloc[ridx].tolist()]
        matches = {expected_norm[h]: i for i, h in enumerate(row_vals) if h in expected_norm}
        # Necesitamos al menos los 4 obligatorios para considerar esa fila como header
        obligatorios = {'Código (SKU)', 'Descripción', 'Categoría', 'Unidad'}
        if obligatorios.issubset(matches.keys()):
            header_row_idx = ridx
            column_map = {matches[h]: h for h in matches}
            break

    if header_row_idx is None:
        return jsonify({
            'detail': 'No se encontraron los encabezados esperados. '
                      'Descarga la plantilla nueva (debe incluir Código (SKU), Descripción, Categoría, Unidad).',
        }), 400

    # Datos = todo lo que está debajo del header
    data_df = raw.iloc[header_row_idx + 1:].reset_index(drop=True)
    if len(data_df) > PLANTILLA_MAX_FILAS:
        return jsonify({
            'detail': f'Demasiadas filas ({len(data_df)}). Máximo {PLANTILLA_MAX_FILAS} por importación.',
        }), 400

    user = request.current_user
    exitosos = 0       # productos nuevos creados
    actualizados = 0   # productos existentes actualizados (upsert por SKU)
    errores = []
    skus_en_archivo = set()  # detectar duplicados intra-archivo

    codigo_re = re.compile(CODIGO_REGEX)
    imagen_re = re.compile(_IMAGEN_URL_REGEX)

    # Pausa 2: bodega default donde caerá el stock inicial de productos nuevos.
    # Si no hay ninguna bodega activa, el stock se importa pero queda
    # huérfano (cache lleno, stock_por_almacen vacío) hasta que se cree una.
    bodega_default_id = _almacen_default_id()

    # Cache de categorías existentes para resolver case/acento-insensitivamente.
    # Unimos Producto.categoria (catálogo real) + CategoriaConfig (metadatos
    # visuales). El usuario puede capturar "tornilleria" en el Excel y, si ya
    # existe "Tornillería", reutilizamos el nombre canónico en vez de crear un
    # duplicado por dedazo. Cargamos una sola vez (evita N+1 consultas).
    cat_canon: dict[str, str] = {}  # clave normalizada -> nombre canónico
    for (nombre,) in db.session.query(sql_distinct(Producto.categoria)).filter(
        Producto.categoria != None, Producto.categoria != ''
    ).all():
        if nombre:
            cat_canon.setdefault(_norm_categoria(nombre), nombre)
    for (nombre,) in db.session.query(CategoriaConfig.nombre).all():
        if nombre:
            cat_canon.setdefault(_norm_categoria(nombre), nombre)

    categorias_creadas: list[str] = []  # nombres de las categorías nuevas (para reportar al SPA)

    def _g(row, header_oficial):
        """Lee la celda por header oficial, no por posición."""
        for col_idx, oficial in column_map.items():
            if oficial == header_oficial:
                return row.iloc[col_idx] if col_idx < len(row) else None
        return None

    for offset, row in data_df.iterrows():
        fila_excel = header_row_idx + offset + 2  # +1 por header + 1 porque Excel es 1-indexed
        try:
            codigo      = _cell_str(_g(row, 'Código (SKU)'), maxlen=50)
            descripcion = _cell_str(_g(row, 'Descripción'), maxlen=250)
            categoria   = _cell_str(_g(row, 'Categoría'), maxlen=100)
            unidad      = _cell_str(_g(row, 'Unidad'), maxlen=50)
            imagen_url  = _cell_str(_g(row, 'URL Imagen (opcional)'), maxlen=500)

            # Fila completamente vacía: ignorar sin reportar
            if not (codigo or descripcion or categoria or unidad):
                continue

            problemas = []
            if not codigo:
                problemas.append('falta SKU')
            elif not codigo_re.match(codigo):
                problemas.append('SKU contiene caracteres no permitidos (usa A-Z 0-9 - _ . /)')
            if not descripcion:
                problemas.append('falta descripción')
            if not categoria:
                problemas.append('falta categoría')
            if not unidad:
                unidad = 'Pza'  # default suave

            stock_actual, err_si = _cell_number(_g(row, 'Stock Inicial'), default=0.0)
            if err_si:
                problemas.append(f'stock inicial {err_si}')
            elif stock_actual < 0:
                problemas.append('stock inicial debe ser >= 0')
            elif stock_actual > 1_000_000:
                problemas.append('stock inicial fuera de rango (máx 1M)')

            stock_minimo, err_sm = _cell_number(_g(row, 'Stock Mínimo'), default=0.0)
            if err_sm:
                problemas.append(f'stock mínimo {err_sm}')
            elif stock_minimo < 0:
                problemas.append('stock mínimo debe ser >= 0')
            elif stock_minimo > 1_000_000:
                problemas.append('stock mínimo fuera de rango (máx 1M)')

            precio, err_pr = _cell_number(_g(row, 'Precio Unitario'), default=0.0)
            if err_pr:
                problemas.append(f'precio {err_pr}')
            elif precio < 0:
                problemas.append('precio debe ser >= 0')
            elif precio > 100_000_000:
                problemas.append('precio fuera de rango (máx 100M)')

            # Validar URL imagen (opcional). Si viene, debe ser HTTPS o /path.png.
            imagen_final = None
            if imagen_url:
                if not imagen_re.match(imagen_url):
                    problemas.append('URL imagen inválida (solo HTTPS o /static/...png)')
                else:
                    imagen_final = imagen_url

            if problemas:
                errores.append(f'Fila {fila_excel}: ' + '; '.join(problemas))
                continue

            # Duplicado intra-archivo
            sku_lower = codigo.lower()
            if sku_lower in skus_en_archivo:
                errores.append(f'Fila {fila_excel}: SKU "{codigo}" duplicado en este archivo')
                continue
            skus_en_archivo.add(sku_lower)

            # Resolver categoría a prueba de tontos (sirve para alta y update):
            #  - Si ya existe una equivalente (case/acento-insensitiva), usar el
            #    nombre canónico para no romper el agrupado del dashboard.
            #  - Si es nueva, registrarla en CategoriaConfig (sin imagen) y
            #    agregarla al cache para que el resto del archivo la reutilice.
            cat_key = _norm_categoria(categoria)
            categoria_canonica = cat_canon.get(cat_key)
            if categoria_canonica is None:
                categoria_canonica = categoria  # primera ocurrencia → este es el canónico
                cat_canon[cat_key] = categoria_canonica
                db.session.add(CategoriaConfig(
                    nombre=categoria_canonica,
                    imagen_url=None,
                    created_by_id=user.id,
                ))
                categorias_creadas.append(categoria_canonica)

            precio_dec = Decimal(str(precio))
            stock_min_dec = Decimal(str(stock_minimo))
            # ¿Vinieron en blanco las celdas opcionales? En un update NO debemos
            # pisar un precio/mínimo existente con 0 solo porque la celda quedó
            # vacía. (En alta sí caen a 0, que es el default correcto.)
            precio_provisto = _cell_str(_g(row, 'Precio Unitario')) != ''
            stock_min_provisto = _cell_str(_g(row, 'Stock Mínimo')) != ''

            # ── UPSERT: si el SKU ya existe, ACTUALIZAR campos provistos ──
            # No tocamos stock_actual ni stock_por_almacen: el inventario real
            # se mueve solo por movimientos/ajustes, nunca por reimportar la
            # plantilla. Reactivamos si estaba soft-deleted.
            existente = Producto.query.filter(Producto.codigo == codigo).first()
            if existente:
                existente.descripcion = descripcion
                existente.categoria = categoria_canonica
                existente.unidad = unidad
                if precio_provisto:
                    existente.precio_unitario = precio_dec
                if stock_min_provisto:
                    existente.stock_minimo = stock_min_dec
                if imagen_final:
                    existente.imagen_url = imagen_final
                if not existente.activo:
                    existente.activo = True
                actualizados += 1
                continue

            stock_inicial_dec = Decimal(str(stock_actual))
            nuevo = Producto(
                codigo=codigo,
                descripcion=descripcion,
                categoria=categoria_canonica,
                unidad=unidad,
                stock_actual=stock_inicial_dec,
                stock_minimo=stock_min_dec,
                precio_unitario=precio_dec,
                imagen_url=imagen_final,
                created_by_id=user.id,
            )
            db.session.add(nuevo)
            db.session.flush()  # asegura nuevo.id antes de crear StockPorAlmacen

            # Pausa 2: depositar stock inicial en bodega default si hay y >0.
            if stock_inicial_dec > 0 and bodega_default_id:
                db.session.add(StockPorAlmacen(
                    producto_id=nuevo.id,
                    almacen_id=bodega_default_id,
                    cantidad=stock_inicial_dec,
                ))

            exitosos += 1

        except Exception as e:
            errores.append(f'Fila {fila_excel}: error inesperado — {str(e)[:80]}')

    try:
        msg = (f'Importación masiva: {exitosos} creados, {actualizados} actualizados, '
               f'{len(errores)} errores')
        if categorias_creadas:
            msg += f', {len(categorias_creadas)} categorías nuevas'
        _audit(user, msg)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception('Error commit importar_materiales')
        return jsonify({'detail': f'Error al guardar en base de datos: {str(e)[:100]}'}), 500

    # Refresca catálogos abiertos en otras sesiones tras la importación masiva.
    # Mandamos un solo emit aunque hayan sido N productos — el front invalida
    # el namespace completo de productos vía `useResource.invalidateOn`.
    if exitosos > 0 or actualizados > 0:
        emit_to_role(_INV_ROLES, 'producto:changed', {
            'action': 'bulk_import', 'count': exitosos + actualizados,
        })

    return jsonify({
        'exitosos': exitosos,
        'actualizados': actualizados,
        'errores': errores,
        'total_procesadas': exitosos + actualizados + len(errores),
        'categorias_creadas': categorias_creadas,
    })
