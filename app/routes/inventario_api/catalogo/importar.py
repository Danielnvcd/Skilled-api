"""Importación y exportación masiva de productos vía Excel."""
import io
import re
from decimal import Decimal

from flask import jsonify, request, send_file, current_app
from sqlalchemy import distinct as sql_distinct

from app.extensions import db, limiter
from app.models import (
    Almacen, CategoriaConfig, ImportacionCatalogo, ImportacionCatalogoCambio,
    Producto, Proyecto, StockPorAlmacen, StockAlmacenProyecto,
)
from app.realtime import emit_to_role

from .._core import (
    bp,
    _require_inventario, _require_inventario_admin,
    transaccion_de_stock,
    CODIGO_REGEX, _IMAGEN_URL_REGEX,
    _audit, _almacen_default_id,
    _es_categoria_cable, CABLE_UNIDAD,
    _INV_ROLES,
)
from ..productos.consultas import _productos_filtered_query
from ..productos.reglas import _validar_stock_entero
from .plantilla import (
    COL_ALMACEN, COL_PROYECTO,
    EXPORT_HEADERS, PLANTILLA_HEADERS, PLANTILLA_MAX_FILAS, PLANTILLA_SECTION_PREFIX,
    _cell_number, _cell_str, _categoria_similar, _construir_plantilla_workbook,
    _norm_categoria, _norm_header, _producto_export_row,
)


def _clave_proyecto(p: Proyecto) -> str:
    """Texto con el que el importador reconoce un proyecto (`proy_by_key`)."""
    return (p.numero_proyecto or p.nombre or '').strip()


# Cambios que se reportan sin "antes → después" porque el valor es largo o
# binario y no aporta leerlo en una línea.
_CAMBIOS_SIN_VALOR = {'descripción', 'imagen', 'reactivado', 'contacto del proveedor'}


def _mismo_valor(a, b) -> bool:
    """¿Cuentan como el MISMO dato del catálogo?

    Una celda vacía y un campo en NULL son lo mismo, y los números se comparan
    por valor (3.50 == 3.5). Sin esto, reimportar un archivo sin editar
    reportaría cambios falsos por puro formato.
    """
    if isinstance(a, Decimal) or isinstance(b, Decimal):
        return (Decimal(str(a if a is not None else 0))
                == Decimal(str(b if b is not None else 0)))
    return ('' if a is None else a) == ('' if b is None else b)


# Campos numéricos del producto: al deshacer hay que devolverlos como Decimal,
# no como el texto con el que viajaron en el JSON del registro.
_CAMPOS_DECIMAL = {'precio_unitario', 'stock_minimo', 'stock_actual'}
_CAMPOS_BOOL = {'activo'}


def _valor_serializable(v):
    """Valor listo para JSON (Decimal → str, para no perder precisión)."""
    return str(v) if isinstance(v, Decimal) else v


def _valor_restaurado(attr: str, v):
    """Convierte el valor guardado en el registro al tipo real de la columna."""
    if v is None:
        return None
    if attr in _CAMPOS_DECIMAL:
        return Decimal(str(v))
    if attr in _CAMPOS_BOOL:
        return bool(v)
    return v


def _texto_cambio(etiqueta: str, antes, despues) -> str:
    """Línea legible del cambio, para el reporte al SPA."""
    if etiqueta in _CAMBIOS_SIN_VALOR:
        return etiqueta

    def _fmt(v):
        if v is None or v == '':
            return '—'
        return str(float(v)) if isinstance(v, Decimal) else str(v)

    return f'{etiqueta}: {_fmt(antes)} → {_fmt(despues)}'


@bp.route('/productos/plantilla-importar', methods=['GET'])
@_require_inventario
def get_plantilla_materiales():
    """Genera y sirve un Excel de plantilla VACÍA para carga masiva de productos.
    Incluye instrucciones, validaciones de celda y tooltips en cada columna.

    Acepta `almacen_id` y `proyecto_id` (opcionales): el destino elegido al
    descargar baja PRELLENADO en la columna Almacén / Proyecto de cada fila, de
    modo que el stock inicial de todo lo que se capture caiga donde se quiere sin
    teclear nombres. Ambas columnas traen además la lista de bodegas y proyectos
    reales, así que ni el prellenado ni un cambio manual pueden inventar un
    nombre que no existe (que era la fuente de errores al importar).
    """
    almacen_id = request.args.get('almacen_id', type=int)
    proyecto_id = request.args.get('proyecto_id', type=int)

    prellenado = {}
    destino_txt = []
    if almacen_id:
        alm = db.session.get(Almacen, almacen_id)
        if not alm or not alm.activo:
            return jsonify({'detail': 'La bodega seleccionada no existe o está inactiva'}), 400
        prellenado[COL_ALMACEN] = alm.nombre
        destino_txt.append(f'bodega {alm.nombre}')
    if proyecto_id:
        proy = db.session.get(Proyecto, proyecto_id)
        if not proy or not proy.activo:
            return jsonify({'detail': 'El proyecto seleccionado no existe o está inactivo'}), 400
        clave = _clave_proyecto(proy)
        if not clave:
            return jsonify({'detail': 'El proyecto seleccionado no tiene número ni nombre'}), 400
        prellenado[COL_PROYECTO] = clave
        destino_txt.append(f'proyecto {clave}')

    # Listas desplegables con lo que EXISTE hoy (hoja oculta). El proyecto se
    # ofrece por su clave de importación para que coincida exactamente.
    listas = {
        COL_ALMACEN: [
            a.nombre for a in Almacen.query
            .filter(Almacen.activo == True)  # noqa: E712
            .order_by(Almacen.nombre).all()
        ],
        COL_PROYECTO: [
            clave for clave in (
                _clave_proyecto(p) for p in Proyecto.query
                .filter(Proyecto.activo == True)  # noqa: E712
                .order_by(Proyecto.numero_proyecto).all()
            ) if clave
        ],
    }

    instruccion = None
    if destino_txt:
        instruccion = (
            f'⚠ El stock inicial de este archivo entra a: {" · ".join(destino_txt)} — ya viene '
            'lleno en las columnas Almacén/Proyecto (celdas grises). Si una fila va a otro lado, '
            'cámbialo con la listita de la celda. Vacío = bodega predeterminada y General (libre). '
            'No alteres los encabezados de la fila 4. Las columnas de CABLE (ámbar) solo se llenan '
            'si la categoría contiene "cable".'
        )

    try:
        buf = _construir_plantilla_workbook(
            instruccion=instruccion, prellenado=prellenado, listas=listas,
        )
    except ImportError:
        return jsonify({'detail': 'openpyxl no instalado en el servidor'}), 500
    return send_file(
        buf,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name='plantilla_materiales.xlsx',
    )


@bp.route('/productos/exportar', methods=['GET'])
@_require_inventario_admin
def exportar_productos():
    """Exporta TODO el catálogo activo con los productos ya llenados. Sirve para
    editar en Excel y reimportar: el importador detecta y aplica SOLO los campos
    que cambiaron.

    Baja con EXPORT_HEADERS, no con la plantilla completa: aquí los productos YA
    existen, así que solo salen los datos de catálogo (marca, descripción,
    categoría, unidad, cable, precio, imagen). Stock inicial, stock mínimo,
    almacén y proyecto no se exportan porque no se aplican a un producto
    existente — el stock se mueve por Movimientos y el mínimo desde la ficha del
    producto. Como el importador empareja por nombre de columna, el archivo se
    reimporta igual y esos campos quedan intactos.

    Acepta los MISMOS filtros del catálogo (`categoria`, `q`, `stock`, `imagen`,
    `unidad`, `compra`) reusando su query: con miles de productos, bajar todo
    para corregir una categoría es incómodo y arriesgado. Un archivo filtrado se
    reimporta sin problema — el importador solo toca los SKU que vienen en él,
    así que lo que no se exportó no se entera."""
    from itertools import groupby
    productos = (
        _productos_filtered_query()
        .order_by(Producto.categoria.asc(), Producto.codigo.asc())
        .all()
    )
    # Descripción legible de los filtros, para el encabezado y el nombre del
    # archivo: que se vea a simple vista que el Excel es un subconjunto.
    filtros_txt = []
    if (request.args.get('categoria') or '').strip():
        filtros_txt.append(f"categoría {request.args['categoria'].strip()}")
    if (request.args.get('q') or '').strip():
        filtros_txt.append(f"búsqueda “{request.args['q'].strip()}”")
    if (request.args.get('unidad') or '').strip():
        filtros_txt.append(f"unidad {request.args['unidad'].strip()}")
    _etiquetas = {'bajo': 'bajo mínimo', 'sin': 'sin existencias'}
    if (request.args.get('stock') or '').strip().lower() in _etiquetas:
        filtros_txt.append(_etiquetas[request.args['stock'].strip().lower()])
    _img = {'con': 'con imagen', 'sin': 'sin imagen'}
    if (request.args.get('imagen') or '').strip().lower() in _img:
        filtros_txt.append(_img[request.args['imagen'].strip().lower()])
    if (request.args.get('compra') or '').strip().lower() in ('activa', '1', 'true', 'si'):
        filtros_txt.append('con compra en curso')

    # Agrupar por categoría: fila de sección resaltada + productos, con una fila
    # en blanco entre secciones. El importador salta secciones/blancos.
    rows = []
    for cat, grupo in groupby(productos, key=lambda p: p.categoria or 'Sin categoría'):
        grupo = list(grupo)
        if rows:
            rows.append(None)  # separador visual entre secciones
        rows.append({'__section__': cat, 'count': len(grupo)})
        rows.extend(_producto_export_row(p, EXPORT_HEADERS) for p in grupo)

    cabecera_filtro = (
        f'Selección: {" · ".join(filtros_txt)} — {len(productos)} producto(s). '
        'Solo estos se verán afectados al reimportar. '
        if filtros_txt else ''
    )
    instruccion = (
        f'⚠ {cabecera_filtro}Edita los valores y vuelve a subir este archivo en "Importar". '
        'El sistema aplica SOLO lo que cambió; las filas sin cambios se ignoran. Aquí se editan '
        'los datos del catálogo (marca, descripción, categoría, unidad, precio, proveedor, '
        'imagen): el stock se mueve en Movimientos y el stock mínimo en la ficha del producto. '
        'No borres el SKU (columna A) ni las filas de sección grises. Columnas de CABLE en '
        'ámbar: llénalas solo en productos de cable.'
    )
    try:
        buf = _construir_plantilla_workbook(
            rows=rows, instruccion=instruccion, headers=EXPORT_HEADERS,
        )
    except ImportError:
        return jsonify({'detail': 'openpyxl no instalado en el servidor'}), 500

    detalle = f' [{", ".join(filtros_txt)}]' if filtros_txt else ''
    _audit(request.current_user,
           f'Exportó catálogo de materiales ({len(productos)} productos){detalle}')
    db.session.commit()
    return send_file(
        buf,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=('catalogo_filtrado.xlsx' if filtros_txt else 'catalogo_materiales.xlsx'),
    )


@bp.route('/productos/importar', methods=['POST'])
@_require_inventario_admin
# Una importación ahora son DOS pasadas por aquí (previsualizar y aplicar), tres
# si hubo que confirmar categorías. Con el tope viejo de 5/min, dos cargas
# seguidas chocaban con el límite; el guardia real sigue siendo el tope de 5 MB
# y el de filas por archivo.
@limiter.limit('15 per minute')
@transaccion_de_stock
def importar_materiales():
    """Importa productos en masa desde un archivo Excel.

    Con `previsualizar=1` hace exactamente el mismo recorrido pero NO escribe
    nada: devuelve el plan (qué se crearía, qué cambiaría campo por campo, qué
    filas fallan y qué materiales se parecen a uno que ya existe) para que el
    usuario confirme. Es el mismo camino de código, no una simulación aparte:
    si el plan dice algo, aplicar hace justo eso.

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

    previsualizar = (request.form.get('previsualizar') or '').strip().lower() in ('1', 'true', 'si')

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
    actualizados = 0   # productos existentes con al menos un campo cambiado
    sin_cambios = 0    # productos existentes cuya fila era idéntica (se ignoran)
    cambios_detalle = []  # [{codigo, cambios: [str, ...]}] para el reporte al SPA
    errores = []
    skus_en_archivo = set()  # detectar duplicados intra-archivo
    # Solo para la previsualización: qué se daría de alta y qué materiales se
    # parecen a uno que ya existe.
    nuevos_detalle, duplicados = [], []
    # Registro del lote para poder DESHACERLO: por producto, qué se hizo y con
    # qué valores estaba antes.
    lote_cambios = []
    # Pipeline de imágenes → R2: ids de productos cuya imagen es URL externa y
    # hay que descargar/convertir/subir. Se encola al final (no-op si R2 apagado).
    from ..imagenes import marcar_para_sync, encolar_sync
    sync_ids = []

    codigo_re = re.compile(CODIGO_REGEX)
    imagen_re = re.compile(_IMAGEN_URL_REGEX)

    # Pausa 2: bodega default donde caerá el stock inicial de productos nuevos.
    # Si no hay ninguna bodega activa, el stock se importa pero queda
    # huérfano (cache lleno, stock_por_almacen vacío) hasta que se cree una.
    bodega_default_id = _almacen_default_id()

    # Feature stock por proyecto: caches para resolver las columnas opcionales
    # 'Almacén' y 'Proyecto' por fila (case/acento-insensitivo, una query cada
    # uno). El proyecto se resuelve por número o por nombre.
    from app.models import Almacen as _Almacen
    alm_by_name: dict[str, int] = {}
    alm_nombre_por_id: dict[int, str] = {}   # para nombrar el destino en el plan
    for _a in _Almacen.query.filter(_Almacen.activo == True).all():  # noqa: E712
        alm_by_name.setdefault(_norm_categoria(_a.nombre), _a.id)
        alm_nombre_por_id[_a.id] = _a.nombre
    proy_by_key: dict[str, int] = {}
    proy_nombre_por_id: dict[int, str] = {}
    for _p in Proyecto.query.all():
        proy_nombre_por_id[_p.id] = _clave_proyecto(_p)
        if _p.numero_proyecto:
            proy_by_key.setdefault(_norm_categoria(_p.numero_proyecto), _p.id)
        if _p.nombre:
            proy_by_key.setdefault(_norm_categoria(_p.nombre), _p.id)

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

    # ── Confirmación de categorías nuevas parecidas a existentes ──────────────
    # Si el usuario ya decidió qué hacer con cada categoría ambigua, llega el
    # mapeo {nombre_en_archivo: nombre_existente}. Valor vacío/ausente = crear
    # como nueva con ese mismo nombre.
    import json as _json
    mapeo_raw = request.form.get('categoria_mapeo')
    categoria_mapeo: dict[str, str] = {}
    if mapeo_raw:
        try:
            _m = _json.loads(mapeo_raw) or {}
            categoria_mapeo = {str(k): str(v or '') for k, v in _m.items()}
        except (ValueError, TypeError):
            categoria_mapeo = {}

    # Primer intento (sin mapeo): si hay categorías NUEVAS parecidas a alguna
    # existente, no creamos nada — devolvemos la lista para que el SPA pregunte.
    if not categoria_mapeo:
        file_cats: dict[str, int] = {}
        for _, row in data_df.iterrows():
            cat = _cell_str(_g(row, 'Categoría'), maxlen=100)
            cod = _cell_str(_g(row, 'Código (SKU)'), maxlen=50)
            if not cat or cod.startswith(PLANTILLA_SECTION_PREFIX):
                continue
            file_cats[cat] = file_cats.get(cat, 0) + 1
        ambiguas = []
        for cat, n in file_cats.items():
            if _norm_categoria(cat) in cat_canon:
                continue  # ya existe exactamente → no preguntar
            sug = _categoria_similar(cat, cat_canon.values())
            if sug:
                ambiguas.append({'nombre': cat, 'sugerencia': sug, 'productos': n})
        if ambiguas:
            ambiguas.sort(key=lambda a: a['nombre'].lower())
            return jsonify({
                'necesita_confirmacion': True,
                'categorias_ambiguas': ambiguas,
                'categorias_existentes': sorted(set(cat_canon.values()), key=str.lower),
            }), 200

    # ── Precarga de los SKU del archivo (una query por lote, no por fila) ─────
    # Buscar el producto fila por fila era un SELECT por renglón: reimportar el
    # export de 5 000 productos disparaba 5 001 consultas, o sea 5 000 viajes de
    # ida y vuelta a Postgres — segundos de latencia pura y riesgo de que el
    # proxy corte la petición antes de terminar. Se resuelven en lotes.
    # Se traen TODOS, activos e inactivos: un SKU dado de baja se reactiva al
    # reimportarlo, igual que antes.
    codigos_archivo, _vistos = [], set()
    for _, row in data_df.iterrows():
        cod = _cell_str(_g(row, 'Código (SKU)'), maxlen=50)
        if cod and not cod.startswith(PLANTILLA_SECTION_PREFIX) and cod not in _vistos:
            _vistos.add(cod)
            codigos_archivo.append(cod)
    # Columnas que el archivo TRAE. Los campos opcionales de texto solo se
    # aplican si su columna existe: un archivo viejo (o hecho a mano) que no la
    # tenga se leería como celda vacía y BORRARÍA el dato guardado. Con la
    # columna presente, una celda vacía sí significa "límpialo", que es lo que
    # espera quien edita el export.
    columnas_presentes = set(column_map.values())

    # Índice de descripciones del catálogo, solo para avisar de posibles
    # duplicados en la previsualización. Se carga únicamente en ese modo: son
    # dos columnas de todo el catálogo y no vale la pena pagarlo al aplicar.
    desc_existente: dict[str, str] = {}
    if previsualizar:
        for _cod, _desc in db.session.query(Producto.codigo, Producto.descripcion).filter(
            Producto.activo == True  # noqa: E712
        ).all():
            if _desc:
                desc_existente.setdefault(_norm_categoria(_desc), _cod)

    existentes_por_codigo: dict[str, Producto] = {}
    LOTE_SKUS = 900  # holgado para el tope de parámetros de un IN (...)
    for i in range(0, len(codigos_archivo), LOTE_SKUS):
        for p in Producto.query.filter(
            Producto.codigo.in_(codigos_archivo[i:i + LOTE_SKUS])
        ).all():
            existentes_por_codigo[p.codigo] = p

    for offset, row in data_df.iterrows():
        fila_excel = header_row_idx + offset + 2  # +1 por header + 1 porque Excel es 1-indexed
        try:
            codigo      = _cell_str(_g(row, 'Código (SKU)'), maxlen=50)
            descripcion = _cell_str(_g(row, 'Descripción'), maxlen=250)
            marca       = _cell_str(_g(row, 'Marca'), maxlen=100)
            categoria   = _cell_str(_g(row, 'Categoría'), maxlen=100)
            unidad      = _cell_str(_g(row, 'Unidad'), maxlen=50)
            imagen_url  = _cell_str(_g(row, 'URL Imagen (opcional)'), maxlen=500)
            cable_tipo_in    = _cell_str(_g(row, 'Tipo (cable)'), maxlen=60)
            cable_calibre_in = _cell_str(_g(row, 'Tamaño mm²/AWG (cable)'), maxlen=40)
            # Destino del stock inicial (feature stock por proyecto). Solo se usan
            # al crear un producto nuevo con stock > 0; en updates se ignoran.
            almacen_in  = _cell_str(_g(row, 'Almacén'), maxlen=100)
            proyecto_in = _cell_str(_g(row, 'Proyecto'), maxlen=100)
            # Proveedor habitual (para Compras Express).
            prov_nombre   = _cell_str(_g(row, 'Proveedor'), maxlen=150)
            prov_contacto = _cell_str(_g(row, 'Contacto proveedor'), maxlen=150)

            # Aplicar la decisión del usuario: si esta categoría del archivo se
            # mapeó a una existente, la sustituimos antes de procesar.
            if categoria and categoria in categoria_mapeo:
                destino = categoria_mapeo[categoria].strip()
                if destino:
                    categoria = destino[:100]

            # Fila completamente vacía: ignorar sin reportar
            if not (codigo or descripcion or categoria or unidad):
                continue

            # Fila de sección del export (código empieza con '#'): saltar sin
            # error. Un SKU real nunca empieza con '#' (lo prohíbe CODIGO_REGEX).
            if codigo.startswith(PLANTILLA_SECTION_PREFIX):
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

            # Producto existente (upsert por SKU), de la precarga por lotes. Se
            # resuelve aquí para poder reutilizarlo tanto en la validación de
            # cable (heredar Tipo/Tamaño ya guardados) como en el bloque de
            # actualización más abajo.
            existente = existentes_por_codigo.get(codigo)

            # ── Cable a prueba de tontos ──────────────────────────────────────
            # Si la categoría es de cable: Tipo y Tamaño son obligatorios (pueden
            # heredarse de un producto ya existente) y la unidad se fuerza a 'M'.
            # Si NO es cable: se ignoran esas columnas aunque las hayan llenado
            # (no se guardan). Detectamos por el texto crudo de la categoría
            # (mismo criterio, sin importar tildes/mayúsculas).
            es_cable = _es_categoria_cable(categoria)
            cable_tipo_final = (cable_tipo_in or '').strip() or (existente.cable_tipo if existente else None)
            cable_calibre_final = (cable_calibre_in or '').strip() or (existente.cable_calibre if existente else None)
            if es_cable:
                if not cable_tipo_final or not cable_calibre_final:
                    errores.append(
                        f'Fila {fila_excel}: la categoría es de cable; captura Tipo y Tamaño mm²/AWG'
                    )
                    continue
                unidad = CABLE_UNIDAD  # metros, sin importar lo que traiga la celda
            else:
                # No es cable → no arrastrar datos de cable (foolproof).
                cable_tipo_final = None
                cable_calibre_final = None

            # Decimales según la unidad: misma regla que el alta manual y que
            # solicitudes/compras — 'pza' no admite 2.5. Se exige SOLO sobre lo
            # que esta fila va a aplicar: el stock inicial únicamente cuenta en
            # productos nuevos (en existentes se ignora), y el mínimo solo si la
            # celda traía valor. Así un archivo viejo con decimales en una
            # columna que de todos modos no se aplica no se vuelve un error.
            stock_min_provisto = _cell_str(_g(row, 'Stock Mínimo')) != ''
            err_dec = _validar_stock_entero(
                unidad,
                None if existente else stock_actual,
                stock_minimo if stock_min_provisto else None,
            )
            if err_dec:
                errores.append(f'Fila {fila_excel}: {err_dec}')
                continue

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
                if not previsualizar:
                    db.session.add(CategoriaConfig(
                        nombre=categoria_canonica,
                        imagen_url=None,
                        created_by_id=user.id,
                    ))
                categorias_creadas.append(categoria_canonica)

            precio_dec = Decimal(str(precio))
            stock_min_dec = Decimal(str(stock_minimo))
            # ¿Vino en blanco la celda opcional? En un update NO debemos pisar un
            # precio existente con 0 solo porque la celda quedó vacía. (En alta sí
            # cae a 0, que es el default correcto.) `stock_min_provisto` ya se
            # calculó arriba, para la validación de decimales.
            precio_provisto = _cell_str(_g(row, 'Precio Unitario')) != ''

            # ── UPSERT con DETECCIÓN DE CAMBIOS ──────────────────────────────
            # Solo aplicamos (y reportamos) los campos que REALMENTE cambiaron;
            # una fila idéntica a lo guardado se cuenta como "sin cambios" y no
            # se toca. Así el flujo exportar → editar → reimportar aplica solo lo
            # editado. NUNCA tocamos stock_actual/stock_por_almacen: el inventario
            # real se mueve solo por movimientos/ajustes. (`existente` ya se
            # resolvió arriba, en la validación de cable.)
            if existente:
                # Primero se calcula QUÉ cambiaría y hasta el final se aplica.
                # Así el mismo recorrido sirve para previsualizar (sin escribir
                # nada) y para aplicar: es imposible que el plan que ve el
                # usuario y lo que realmente pasa se separen. Los pares
                # (anterior, nuevo) son además el registro que necesita el
                # deshacer.
                #
                # Solo entran las columnas que el archivo TRAE: sin esa guarda,
                # una plantilla vieja sin la columna Marca borraba la marca de
                # todo el catálogo al reimportarla (celda ausente = celda vacía).
                propuestas = [
                    ('descripcion', descripcion, 'descripción'),
                    ('categoria', categoria_canonica, 'categoría'),
                    ('unidad', unidad, 'unidad'),
                    # Cable: None si el producto dejó de ser de cable.
                    ('cable_tipo', cable_tipo_final, 'tipo cable'),
                    ('cable_calibre', cable_calibre_final, 'tamaño cable'),
                ]
                if 'Marca' in columnas_presentes:
                    propuestas.append(('marca', marca or None, 'marca'))
                # Precio y stock mínimo: solo si la celda venía con valor, para no
                # pisar lo guardado con un 0 que en realidad era "no lo toqué".
                if precio_provisto:
                    propuestas.append(('precio_unitario', precio_dec, 'precio'))
                if stock_min_provisto:
                    propuestas.append(('stock_minimo', stock_min_dec, 'stock mín'))
                if 'Proveedor' in columnas_presentes:
                    propuestas.append(('proveedor_default_nombre', prov_nombre or None, 'proveedor'))
                if 'Contacto proveedor' in columnas_presentes:
                    propuestas.append(
                        ('proveedor_default_contacto', prov_contacto or None, 'contacto del proveedor'))
                # Imagen: solo si vino una URL válida (vacío nunca borra la foto).
                if imagen_final:
                    propuestas.append(('imagen_url', imagen_final, 'imagen'))
                # Reactivar si estaba dado de baja.
                if not existente.activo:
                    propuestas.append(('activo', True, 'reactivado'))

                cambios_fila, valores_previos, valores_nuevos = [], {}, {}
                for attr, valor, etiqueta in propuestas:
                    actual = getattr(existente, attr)
                    if _mismo_valor(actual, valor):
                        continue
                    cambios_fila.append(_texto_cambio(etiqueta, actual, valor))
                    valores_previos[attr] = actual
                    valores_nuevos[attr] = valor

                if cambios_fila:
                    actualizados += 1
                    if len(cambios_detalle) < 500:  # cap para no inflar la respuesta
                        cambios_detalle.append({'codigo': existente.codigo, 'cambios': cambios_fila})
                    if not previsualizar:
                        for attr, valor in valores_nuevos.items():
                            setattr(existente, attr, valor)
                        if 'imagen_url' in valores_nuevos and marcar_para_sync(existente, imagen_final):
                            sync_ids.append(existente.id)
                        lote_cambios.append({
                            'producto': existente, 'codigo': existente.codigo,
                            'accion': 'ACTUALIZADO',
                            'antes': {k: _valor_serializable(v) for k, v in valores_previos.items()},
                            'despues': {k: _valor_serializable(v) for k, v in valores_nuevos.items()},
                        })
                else:
                    sin_cambios += 1
                continue

            stock_inicial_dec = Decimal(str(stock_actual))

            # Resolver destino del stock inicial (columnas opcionales Almacén /
            # Proyecto). Vacío → bodega default / bucket general (compat). Si viene
            # un nombre que no existe, error de fila claro para no colocar el stock
            # en el bucket equivocado en silencio.
            almacen_destino_id = bodega_default_id
            proyecto_destino_id = None
            if almacen_in:
                aid = alm_by_name.get(_norm_categoria(almacen_in))
                if not aid:
                    errores.append(f'Fila {fila_excel}: almacén "{almacen_in}" no existe (créalo o deja la celda vacía)')
                    continue
                almacen_destino_id = aid
            if proyecto_in:
                pid = proy_by_key.get(_norm_categoria(proyecto_in))
                if not pid:
                    errores.append(f'Fila {fila_excel}: proyecto "{proyecto_in}" no existe (usa su número o nombre, o deja la celda vacía)')
                    continue
                proyecto_destino_id = pid

            exitosos += 1
            # Aviso de posible duplicado: mismo texto de descripción que un
            # material que YA existe (o que otra fila del archivo) con otro SKU.
            # A miles de productos, el catálogo duplicado es el problema clásico;
            # se avisa, no se bloquea, porque a veces sí son cosas distintas.
            if previsualizar:
                desc_key = _norm_categoria(descripcion)
                gemelo = desc_existente.get(desc_key)
                if gemelo and gemelo != codigo and len(duplicados) < 200:
                    duplicados.append({
                        'fila': fila_excel, 'codigo': codigo,
                        'descripcion': descripcion, 'parecido_a': gemelo,
                    })
                elif not gemelo:
                    desc_existente[desc_key] = codigo
                if len(nuevos_detalle) < 500:
                    nuevos_detalle.append({
                        'fila': fila_excel, 'codigo': codigo, 'descripcion': descripcion,
                        'categoria': categoria_canonica, 'unidad': unidad,
                        'stock_inicial': float(stock_inicial_dec),
                        'almacen': alm_nombre_por_id.get(almacen_destino_id, '—'),
                        'proyecto': proy_nombre_por_id.get(proyecto_destino_id, 'General (libre)'),
                    })
                continue

            nuevo = Producto(
                codigo=codigo,
                descripcion=descripcion,
                marca=(marca or None),
                categoria=categoria_canonica,
                unidad=unidad,
                cable_tipo=cable_tipo_final,
                cable_calibre=cable_calibre_final,
                stock_actual=stock_inicial_dec,
                stock_minimo=stock_min_dec,
                precio_unitario=precio_dec,
                imagen_url=imagen_final,
                proveedor_default_nombre=(prov_nombre or None),
                proveedor_default_contacto=(prov_contacto or None),
                created_by_id=user.id,
            )
            db.session.add(nuevo)
            db.session.flush()  # asegura nuevo.id antes de crear StockPorAlmacen

            # Depositar stock inicial en el bucket resuelto (almacén, proyecto|
            # general) si hay bodega y >0. Feature stock por proyecto: la fuente de
            # verdad es stock_almacen_proyecto; se crea también el cache
            # stock_por_almacen consistente (producto nuevo = un solo bucket).
            #
            # A diferencia del resto del módulo, aquí NO se usan `_depositar()` +
            # `_recalcular_caches()`: esos hacen SELECT ... FOR UPDATE y dos
            # recálculos por producto, o sea ~5 consultas por fila — en una carga
            # de miles de altas serían decenas de miles de viajes a la base. Y no
            # aportan nada: el producto acaba de nacer en ESTA transacción, así
            # que nadie más puede tener su bucket, no hay fila previa que releer
            # y el cache es exactamente lo que se deposita. La vista va envuelta
            # en @transaccion_de_stock, de modo que cualquier fallo hace rollback
            # y los errores de lock salen como 409 igual que en los demás
            # endpoints que mutan stock.
            if stock_inicial_dec > 0 and almacen_destino_id:
                db.session.add(StockAlmacenProyecto(
                    producto_id=nuevo.id,
                    almacen_id=almacen_destino_id,
                    proyecto_id=proyecto_destino_id,
                    cantidad=stock_inicial_dec,
                ))
                db.session.add(StockPorAlmacen(
                    producto_id=nuevo.id,
                    almacen_id=almacen_destino_id,
                    cantidad=stock_inicial_dec,
                ))

            # Registro para deshacer: el alta y el stock que se depositó (con su
            # bucket), que es lo que habría que retirar al revertir.
            deposito = stock_inicial_dec > 0 and almacen_destino_id
            lote_cambios.append({
                'producto': nuevo, 'codigo': codigo, 'accion': 'CREADO',
                'antes': {}, 'despues': {},
                # El stock se registra SIEMPRE, aunque no haya bodega donde
                # depositarlo: al deshacer se compara contra `stock_actual` del
                # producto para saber si alguien lo movió, y si aquí fuera None
                # un alta sin bodega parecería "ya tocada" y no se borraría.
                'stock_inicial': stock_inicial_dec,
                'almacen_id': almacen_destino_id if deposito else None,
                'proyecto_id': proyecto_destino_id if deposito else None,
            })

            # Si la imagen importada es una URL externa, encolar su sync a R2.
            if marcar_para_sync(nuevo, imagen_final):
                sync_ids.append(nuevo.id)

        except Exception as e:
            errores.append(f'Fila {fila_excel}: error inesperado — {str(e)[:80]}')

    # ── Previsualización: se devuelve el plan y NO se escribe nada ────────────
    # El rollback es la garantía dura: aunque algún camino haya tocado la sesión,
    # aquí no queda nada. Es la misma información que verá el resumen final,
    # calculada por el mismo recorrido.
    if previsualizar:
        db.session.rollback()
        return jsonify({
            'previsualizacion': True,
            'exitosos': exitosos,
            'actualizados': actualizados,
            'sin_cambios': sin_cambios,
            'errores': errores,
            'total_procesadas': exitosos + actualizados + sin_cambios + len(errores),
            'categorias_creadas': categorias_creadas,
            'cambios_detalle': cambios_detalle,
            'nuevos': nuevos_detalle,
            'duplicados': duplicados,
        })

    # ── Registro del lote, para poder deshacerlo ─────────────────────────────
    # Se guarda ANTES del commit y en la misma transacción: o queda todo (los
    # productos y su registro) o no queda nada. Un lote sin registro sería una
    # importación que ya no se puede revertir.
    lote = None
    if lote_cambios:
        import json as _json_lote
        lote = ImportacionCatalogo(
            usuario_id=user.id,
            archivo=(file.filename or '')[:250],
            creados=exitosos, actualizados=actualizados,
            sin_cambios=sin_cambios, errores=len(errores),
            estado='APLICADA',
        )
        db.session.add(lote)
        db.session.flush()  # id del lote antes de colgarle los cambios
        for c in lote_cambios:
            db.session.add(ImportacionCatalogoCambio(
                importacion_id=lote.id,
                producto_id=c['producto'].id,
                codigo=c['codigo'],
                accion=c['accion'],
                antes=_json_lote.dumps(c['antes']) if c['antes'] else None,
                despues=_json_lote.dumps(c['despues']) if c['despues'] else None,
                stock_inicial=c.get('stock_inicial'),
                almacen_id=c.get('almacen_id'),
                proyecto_id=c.get('proyecto_id'),
            ))

    try:
        msg = (f'Importación masiva: {exitosos} creados, {actualizados} actualizados, '
               f'{sin_cambios} sin cambios, {len(errores)} errores')
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

    # Encolar la descarga/conversión/subida a R2 de las imágenes externas de
    # esta importación. No-op si R2 está apagado (sync_ids queda vacío).
    imagenes_job = encolar_sync(user.id, sync_ids) if sync_ids else None

    respuesta = {
        'exitosos': exitosos,
        'actualizados': actualizados,
        'sin_cambios': sin_cambios,
        'errores': errores,
        'total_procesadas': exitosos + actualizados + sin_cambios + len(errores),
        'categorias_creadas': categorias_creadas,
        'cambios_detalle': cambios_detalle,
    }
    # Id del lote: con esto el SPA ofrece "Deshacer" justo después de importar.
    if lote is not None:
        respuesta['importacion_id'] = lote.id
    # Clave `imagenes` solo si hay algo que sincronizar → la respuesta queda
    # idéntica a la de antes cuando R2 está apagado (no rompe nada).
    if sync_ids:
        respuesta['imagenes'] = {'pendientes': len(sync_ids), 'job_id': imagenes_job}
    return jsonify(respuesta)
