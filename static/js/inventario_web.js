/* ─── inventario_web.js v32 ─── */
document.addEventListener('DOMContentLoaded', async () => {
    const CSRF_TOKEN = document.querySelector('meta[name="csrf-token"]').getAttribute('content');
    const CATEGORIA_ACTUAL = document.getElementById('page-data')?.dataset?.categoria || null;

    // ─── CATEGORÍAS (base + custom en localStorage) ──────────────
    const CATS_BASE = ['Tornillería','Tuercas','Rondanas','Pijas','Abrazaderas','Soportería','Tubería/Accesorios'];

    const ICONOS_PICKER = [
        { id:'tornillo',  svg:`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/></svg>` },
        { id:'hexagon',  svg:`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polygon points="12 2 22 8.5 22 15.5 12 22 2 15.5 2 8.5"/><circle cx="12" cy="12" r="3"/></svg>` },
        { id:'circle',   svg:`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="3.5"/></svg>` },
        { id:'link',     svg:`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>` },
        { id:'shelves',  svg:`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="4" rx="1"/><rect x="2" y="10" width="20" height="4" rx="1"/><rect x="2" y="17" width="20" height="4" rx="1"/></svg>` },
        { id:'pipe',     svg:`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><rect x="1" y="6" width="22" height="12" rx="5"/><path d="M6 12h12"/></svg>` },
        { id:'box',      svg:`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/></svg>` },
        { id:'wrench',   svg:`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3-3a6 6 0 0 1-8 8l-6 6a2 2 0 0 1-3-3l6-6a6 6 0 0 1 8-8l-3.1 3.1z"/></svg>` },
        { id:'zap',      svg:`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>` },
        { id:'hammer',   svg:`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="m15 12-8.5 8.5a2.12 2.12 0 0 1-3-3L12 9"/><path d="M17.64 15 22 10.64"/><path d="m20.91 11.7-1.25-1.25c.16-.88.03-1.82-.5-2.6l-2.17-3.22a5 5 0 0 0-8.09-.76l2.26 2.26c.29.3.29.77 0 1.06L9.69 9.09c-.3.3-.77.3-1.06 0L6.37 6.83a5 5 0 0 0 .76 8.09l3.22 2.17c.78.53 1.72.66 2.6.5l1.25 1.25c.51.51 1.34.51 1.86 0l4.84-4.84c.52-.52.52-1.35 0-1.86z"/></svg>` },
        { id:'layers',   svg:`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/></svg>` },
        { id:'tag',      svg:`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"/><line x1="7" y1="7" x2="7.01" y2="7"/></svg>` },
        { id:'shield',   svg:`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>` },
        { id:'droplet',  svg:`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2.69l5.66 5.66a8 8 0 1 1-11.31 0z"/></svg>` },
        { id:'cpu',      svg:`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6"/><line x1="9" y1="1" x2="9" y2="4"/><line x1="15" y1="1" x2="15" y2="4"/><line x1="9" y1="20" x2="9" y2="23"/><line x1="15" y1="20" x2="15" y2="23"/><line x1="20" y1="9" x2="23" y2="9"/><line x1="20" y1="14" x2="23" y2="14"/><line x1="1" y1="9" x2="4" y2="9"/><line x1="1" y1="14" x2="4" y2="14"/></svg>` },
    ];

    const COLORES_PICKER = [
        { color:'#0b5fb4', bg:'#dbeafe', label:'Azul' },
        { color:'#dc2626', bg:'#fee2e2', label:'Rojo' },
        { color:'#64748b', bg:'#f1f5f9', label:'Gris' },
    ];

    const CATEGORIA_CONFIG_BASE = {
        'Tornillería':       { color:'#0b5fb4', bg:'#dbeafe',  iconId:'tornillo' },
        'Tuercas':           { color:'#dc2626', bg:'#fee2e2',  iconId:'hexagon'  },
        'Rondanas':          { color:'#64748b', bg:'#f1f5f9',  iconId:'circle'   },
        'Pijas':             { color:'#0b5fb4', bg:'#dbeafe',  iconId:'tornillo' },
        'Abrazaderas':       { color:'#64748b', bg:'#f1f5f9',  iconId:'link'     },
        'Soportería':        { color:'#dc2626', bg:'#fee2e2',  iconId:'shelves'  },
        'Tubería/Accesorios':{ color:'#0b5fb4', bg:'#dbeafe',  iconId:'pipe'     },
    };

    function getSvgById(id) {
        return (ICONOS_PICKER.find(i => i.id === id) || ICONOS_PICKER[6]).svg;
    }

    function buildCatCfg(cat, raw) {
        const iconId = raw.iconId || 'box';
        return { color: raw.color, bg: raw.bg, svg: getSvgById(iconId), iconId };
    }

    function loadCustomCats() {
        try { return JSON.parse(localStorage.getItem('inv_custom_cats') || '[]'); } catch { return []; }
    }
    function saveCustomCats(arr) {
        localStorage.setItem('inv_custom_cats', JSON.stringify(arr));
    }

    function getAllCatConfig() {
        const cfg = {};
        // Base
        Object.entries(CATEGORIA_CONFIG_BASE).forEach(([cat, raw]) => {
            cfg[cat] = buildCatCfg(cat, raw);
        });
        // Custom (sobrescriben si hay nombre igual)
        loadCustomCats().forEach(c => {
            cfg[c.nombre] = buildCatCfg(c.nombre, { color: c.color, bg: c.bg, iconId: c.iconId });
        });
        return cfg;
    }

    function getAllCats() {
        const base = [...CATS_BASE];
        const custom = loadCustomCats().map(c => c.nombre);
        return [...new Set([...base, ...custom])];
    }

    const CAT_DEFAULT = {
        color: '#6B7280', bg: '#F3F4F6',
        svg: getSvgById('box'), iconId: 'box'
    };

    function getCatCfg(cat) {
        return getAllCatConfig()[cat] || CAT_DEFAULT;
    }

    // ─── UTILS ──────────────────────────────────────────────────
    const openModal  = (m) => m && m.classList.remove('hidden');
    const closeModal = (m) => m && m.classList.add('hidden');

    function showToast(msg, type = 'success') {
        const t = document.createElement('div');
        t.textContent = msg;
        t.style.cssText = `position:fixed;bottom:1.5rem;right:1.5rem;z-index:9999;padding:.85rem 1.5rem;border-radius:12px;font-weight:700;font-size:.9rem;color:white;box-shadow:0 8px 24px rgba(0,0,0,.15);animation:fadeIn .3s ease;`;
        t.style.background = type === 'error' ? '#EF4444' : type === 'info' ? '#6366F1' : '#10B981';
        document.body.appendChild(t);
        setTimeout(() => t.remove(), 3000);
    }

    // ─── LLENAR SELECTS DE CATEGORÍA ──────────────────────────────
    function llenarSelectsCat() {
        const cats = getAllCats();
        const selects = document.querySelectorAll('.select-categoria');
        selects.forEach(sel => {
            const current = sel.value;
            sel.innerHTML = `<option value="">Seleccione Categoría</option>`;
            cats.forEach(c => {
                const opt = document.createElement('option');
                opt.value = c; opt.textContent = c;
                if (c === current) opt.selected = true;
                sel.appendChild(opt);
            });
        });
        // filterCategoria select en catálogo
        const filterSel = document.getElementById('filterCategoria');
        if (filterSel) {
            const fCurrent = filterSel.value;
            filterSel.innerHTML = `<option value="">Todas las categorías</option>`;
            cats.forEach(c => {
                const opt = document.createElement('option');
                opt.value = c; opt.textContent = c;
                if (c === fCurrent) opt.selected = true;
                filterSel.appendChild(opt);
            });
        }
    }

    // ─── MODALES ────────────────────────────────────────────────
    const modalProducto       = document.getElementById('modalProducto');
    const modalDeleteProducto = document.getElementById('modalDeleteProducto');
    const modalAlmacen        = document.getElementById('modalAlmacen');
    const modalEstante        = document.getElementById('modalEstante');
    const modalDeleteEstante  = document.getElementById('modalDeleteEstante');
    const modalCategoria      = document.getElementById('modalCategoria');

    [modalProducto, modalDeleteProducto, modalAlmacen, modalEstante, modalDeleteEstante, modalCategoria].forEach(m => {
        if (m) m.addEventListener('click', (e) => { if (e.target === m) closeModal(m); });
    });

    const bindClose = (btnId, modal) => {
        const btn = document.getElementById(btnId);
        if (btn && modal) btn.addEventListener('click', () => closeModal(modal));
    };

    bindClose('btnCloseModalProducto',  modalProducto);
    bindClose('btnCancelProducto',      modalProducto);
    bindClose('btnCloseModalAlmacen',   modalAlmacen);
    bindClose('btnCancelAlmacen',       modalAlmacen);
    bindClose('btnCloseModalEstante',   modalEstante);
    bindClose('btnCancelEstante',       modalEstante);
    bindClose('btnCancelDeleteProducto',modalDeleteProducto);
    bindClose('btnCancelDeleteEstante', modalDeleteEstante);
    bindClose('btnCloseModalCategoria', modalCategoria);
    bindClose('btnCancelCategoria',     modalCategoria);

    // ─── MODAL NUEVA CATEGORÍA ─────────────────────────────────
    let _catSelectedIcon  = 'box';
    let _catSelectedColor = COLORES_PICKER[0];

    function buildCatModal() {
        if (!modalCategoria) return;

        const iconGrid   = document.getElementById('catIconGrid');
        const colorGrid  = document.getElementById('catColorGrid');
        if (!iconGrid || !colorGrid) return;

        // Icono picker
        iconGrid.innerHTML = ICONOS_PICKER.map(ic => `
            <button type="button" class="icon-pick-btn ${ic.id === _catSelectedIcon ? 'selected' : ''}"
                data-icon-id="${ic.id}" title="${ic.id}">
                ${ic.svg}
            </button>`).join('');

        iconGrid.querySelectorAll('.icon-pick-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                _catSelectedIcon = btn.dataset.iconId;
                iconGrid.querySelectorAll('.icon-pick-btn').forEach(b => b.classList.remove('selected'));
                btn.classList.add('selected');
                updateCatPreview();
            });
        });

        // Color picker
        colorGrid.innerHTML = COLORES_PICKER.map(c => `
            <button type="button" class="color-pick-btn ${c.color === _catSelectedColor.color ? 'selected' : ''}"
                data-color="${c.color}" data-bg="${c.bg}" title="${c.label}"
                style="background:${c.color};">
            </button>`).join('');

        colorGrid.querySelectorAll('.color-pick-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                _catSelectedColor = { color: btn.dataset.color, bg: btn.dataset.bg };
                colorGrid.querySelectorAll('.color-pick-btn').forEach(b => b.classList.remove('selected'));
                btn.classList.add('selected');
                updateCatPreview();
            });
        });

        updateCatPreview();
    }

    function updateCatPreview() {
        const preview = document.getElementById('catPreview');
        const nombre  = document.getElementById('catNombre')?.value || 'Nueva Categoría';
        if (!preview) return;
        const iconSvg = getSvgById(_catSelectedIcon);
        preview.innerHTML = `
            <div class="dash-cat-card" style="--base-color:${_catSelectedColor.color}; max-width:200px; pointer-events:none; min-height:110px;">
                <div class="dash-cat-bg-icon">${iconSvg}</div>
                <div class="dash-cat-content">
                    <h3 style="font-size:1rem;">${nombre}</h3>
                    <div class="dash-cat-metrics" style="font-size:1.4rem;">0 <span class="text-sm font-normal opacity-80">Items</span></div>
                </div>
            </div>`;
    }

    document.getElementById('catNombre')?.addEventListener('input', updateCatPreview);

    const btnOpenCat = document.getElementById('btnOpenModalCategoria');
    if (btnOpenCat) {
        btnOpenCat.addEventListener('click', () => {
            _catSelectedIcon  = 'box';
            _catSelectedColor = COLORES_PICKER[0];
            const inputNom = document.getElementById('catNombre');
            if (inputNom) inputNom.value = '';
            buildCatModal();
            openModal(modalCategoria);
        });
    }

    const formCategoria = document.getElementById('formCategoria');
    if (formCategoria) {
        formCategoria.addEventListener('submit', (e) => {
            e.preventDefault();
            const nombre = document.getElementById('catNombre')?.value.trim();
            if (!nombre) return;
            const custom = loadCustomCats();
            if (getAllCats().map(c=>c.toLowerCase()).includes(nombre.toLowerCase())) {
                showToast('Esa categoría ya existe', 'error'); return;
            }
            custom.push({ nombre, color: _catSelectedColor.color, bg: _catSelectedColor.bg, iconId: _catSelectedIcon });
            saveCustomCats(custom);
            closeModal(modalCategoria);
            llenarSelectsCat();
            showToast(`Categoría "${nombre}" creada ✓`);
            // Re-render dashboard si estamos en el catálogo
            if (todosProductos.length > 0) renderizarProductos(todosProductos);
        });
    }

    // ─── CATÁLOGO: Cargar Productos ──────────────────────────────
    let todosProductos = [];
    const productsList = document.getElementById('productos-list');

    async function cargarProductos() {
        if (!productsList) return;
        try {
            const res = await fetch('/api/v1/productos/?limit=1000');
            todosProductos = await res.json();
            renderizarProductos(todosProductos);
            const countLabel = document.getElementById('tab-count-catalogo');
            if (countLabel) countLabel.textContent = todosProductos.length;
        } catch (e) {
            console.error("Error cargando productos:", e);
        }
    }

    const searchProd = document.getElementById('searchProducto');
    const filterCat  = document.getElementById('filterCategoria');
    if (searchProd) searchProd.addEventListener('input', aplicarFiltros);
    if (filterCat)  filterCat.addEventListener('change', aplicarFiltros);

    // ── Tarjeta de producto (lista y foto) ───────────────────────
    function prodCardHtml(p, showCat = false) {
        const cfg     = getCatCfg(p.categoria);
        const stock   = parseFloat(p.stock_actual);
        const minimo  = parseFloat(p.stock_minimo);
        const bajo    = stock <= minimo;
        const pillCls = bajo ? 'stock-pill-low' : 'stock-pill-ok';
        const catLabel = showCat ? `<span class="cat-tag" style="color:${cfg.color};background:${cfg.bg};">${p.categoria}</span>` : `<span class="cat-tag" style="color:${cfg.color};background:${cfg.bg};">${p.unidad}</span>`;

        // Icono o imagen
        const thumb = p.imagen_url
            ? `<div class="product-card-img" data-cat="${p.categoria}"><img src="${p.imagen_url}" alt="${p.descripcion}" loading="lazy"></div>`
            : `<div class="product-card-icon" style="background:${cfg.bg};color:${cfg.color};">${cfg.svg}</div>`;

        return `
        <div class="product-card">
            ${thumb}
            <div class="product-card-body">
                <p class="product-card-name">${p.descripcion}</p>
                <div class="product-card-meta">
                    <span class="sku-tag">${p.codigo}</span>
                    ${catLabel}
                </div>
            </div>
            <div class="product-card-side">
                <span class="stock-pill ${pillCls}">${bajo ? '⚠' : '✔'} ${stock.toFixed(0)} ${p.unidad}</span>
                <div class="product-item-actions">
                    <button class="action-btn action-btn-edit" data-id="${p.id}" data-action="edit-prod" title="Editar">
                        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
                    </button>
                    <button class="action-btn action-btn-delete" data-id="${p.id}" data-nombre="${p.descripcion}" data-action="delete-prod" title="Eliminar">
                        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/></svg>
                    </button>
                </div>
            </div>
        </div>`;
    }

    function renderizarProductos(productos) {
        if (!productsList) return;
        // Con tabla vacía igual mostramos el dashboard de categorías base

        // ── MODO 1: Vista Detallada de Categoría ──────────────────
        if (CATEGORIA_ACTUAL) {
            const cat     = CATEGORIA_ACTUAL;
            const prods   = productos.filter(p => p.categoria === cat);

            if (!prods.length) {
                productsList.innerHTML = `<div class="text-center p-8 text-gray-400">No hay materiales en ${cat}</div>`;
                return;
            }

            const itemsHTML = prods.map(p => prodCardHtml(p, false)).join('');
            productsList.innerHTML = `<div class="seccion-productos-grid">${itemsHTML}</div>`;
            bindAcciones(productsList);
            bindImgFallback(productsList);
            return;
        }

        // ── MODO 2: Búsqueda o filtro activo ─────────────────────
        const hayBusqueda = (searchProd?.value.trim() !== '') || (filterCat?.value !== '');
        if (hayBusqueda) {
            if (!productos.length) {
                productsList.innerHTML = `<div class="text-center p-8 text-gray-400 text-sm font-semibold">No se encontraron productos</div>`;
                return;
            }
            const itemsHTML = productos.map(p => prodCardHtml(p, true)).join('');
            productsList.innerHTML = `<div class="seccion-productos-grid">${itemsHTML}</div>`;
            bindAcciones(productsList);
            bindImgFallback(productsList);
            return;
        }

        // ── MODO 3: Dashboard de categorías ──────────────────────
        const grupos = {};
        productos.forEach(p => {
            const cat = p.categoria || 'Sin categoría';
            if (!grupos[cat]) grupos[cat] = [];
            grupos[cat].push(p);
        });

        const allCats = getAllCats();
        const catOrdenadas = [
            ...allCats,                                                  // todas las base+custom siempre
            ...Object.keys(grupos).filter(c => !allCats.includes(c)),   // productos en cats desconocidas
        ];

        const linkBase = '/inventario/catalogo/';
        let dashHtml = `<div class="dashboard-cats-grid">`;

        catOrdenadas.forEach(cat => {
            const prods  = grupos[cat] || [];
            const cfg    = getCatCfg(cat);
            const bajos  = prods.filter(p => parseFloat(p.stock_actual) <= parseFloat(p.stock_minimo)).length;
            const ok     = prods.length - bajos;

            const esVacia = prods.length === 0;
            dashHtml += `
            <a href="${linkBase}${encodeURIComponent(cat)}" class="dash-cat-card ${esVacia ? 'dash-cat-card-empty' : ''}" style="--base-color:${cfg.color};">
                <div class="dash-cat-bg-icon">${cfg.svg}</div>
                <div class="dash-cat-content">
                    <h3>${cat}</h3>
                    <div class="dash-cat-metrics">${prods.length} <span class="text-sm font-normal opacity-80">Items</span></div>
                </div>
                <div class="dash-cat-footer">
                    ${esVacia
                        ? `<span class="stat-badge" style="background:rgba(255,255,255,.2);border-color:rgba(255,255,255,.2);">+ Agregar productos</span>`
                        : `${ok > 0  ? `<span class="stat-badge ok">✔ ${ok} estables</span>` : ''}
                           ${bajos>0 ? `<span class="stat-badge danger">⚠ ${bajos} reponer</span>` : ''}`
                    }
                </div>
            </a>`;
        });

        // Botón "Nueva Categoría" al final del grid (si estamos en el catálogo)
        if (document.getElementById('btnOpenModalCategoria')) {
            dashHtml += `
            <button id="dashAddCat" class="dash-cat-card dash-add-cat-card" type="button">
                <div class="dash-add-cat-inner">
                    <div class="dash-add-cat-icon">
                        <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
                    </div>
                    <p class="dash-add-cat-label">Nueva Categoría</p>
                </div>
            </button>`;
        }

        dashHtml += `</div>`;
        productsList.innerHTML = dashHtml;

        document.getElementById('dashAddCat')?.addEventListener('click', () => {
            document.getElementById('btnOpenModalCategoria')?.click();
        });
    }

    function bindImgFallback(container) {
        container.querySelectorAll('.product-card-img img').forEach(img => {
            img.addEventListener('error', () => {
                const wrap = img.parentElement;
                const cfg  = getCatCfg(wrap.dataset.cat || '');
                wrap.className = 'product-card-icon';
                wrap.style.background = cfg.bg;
                wrap.style.color      = cfg.color;
                wrap.removeAttribute('data-cat');
                wrap.innerHTML = cfg.svg;
            });
        });
    }

    function bindAcciones(container) {
        container.querySelectorAll('[data-action="edit-prod"]').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.preventDefault(); e.stopPropagation();
                const p = todosProductos.find(x => x.id == btn.dataset.id);
                if (p) abrirEditarProducto(p);
            });
        });
        container.querySelectorAll('[data-action="delete-prod"]').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.preventDefault(); e.stopPropagation();
                document.getElementById('deleteProductoId').value     = btn.dataset.id;
                document.getElementById('deleteProductoNombre').textContent = '"' + btn.dataset.nombre + '" será eliminado permanentemente.';
                openModal(modalDeleteProducto);
            });
        });
    }

    function aplicarFiltros() {
        const q   = searchProd ? searchProd.value.toLowerCase() : '';
        const cat = filterCat  ? filterCat.value : '';
        const filtrado = todosProductos.filter(p => {
            const matchQ   = !q   || p.descripcion.toLowerCase().includes(q) || p.codigo.toLowerCase().includes(q);
            const matchCat = !cat || p.categoria === cat;
            return matchQ && matchCat;
        });
        renderizarProductos(filtrado);
    }

    // ── Preview imagen en el modal de producto ────────────────────
    const prodImgUrl = document.getElementById('prodImagenUrl');
    const prodImgPrev= document.getElementById('prodImgPreview');
    if (prodImgUrl && prodImgPrev) {
        const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
        const mostrarPreview = (url) => {
            if (!url) { prodImgPrev.innerHTML = ''; return; }
            prodImgPrev.innerHTML = '';
            const img = document.createElement('img');
            img.className = 'prod-img-preview-thumb';
            img.alt = 'Preview';
            img.addEventListener('error', () => {
                prodImgPrev.innerHTML = '<p style="font-size:.75rem;color:#EF4444;margin:.25rem 0 0;">No se pudo cargar la imagen — verifica la URL</p>';
            });
            img.src = url;
            prodImgPrev.appendChild(img);
        };
        prodImgUrl.addEventListener('input', debounce(() => mostrarPreview(prodImgUrl.value.trim()), 500));
    }

    // ─── Nuevo/Editar Producto ────────────────────────────────────
    const btnOpenProd  = document.getElementById('btnOpenModalProducto');
    const formProducto = document.getElementById('formProducto');

    if (btnOpenProd) {
        btnOpenProd.addEventListener('click', () => {
            if (formProducto) formProducto.reset();
            document.getElementById('prodId').value = '';
            document.getElementById('titleModalProducto').textContent  = 'Registrar Nuevo Producto';
            document.getElementById('btnSubmitProducto').textContent   = 'Guardar Producto';
            if (prodImgPrev) prodImgPrev.innerHTML = '';
            // Pre-seleccionar categoría actual si estamos en página de categoría
            if (CATEGORIA_ACTUAL) {
                const sel = document.getElementById('prodCat');
                if (sel) sel.value = CATEGORIA_ACTUAL;
            }
            openModal(modalProducto);
        });
    }

    function abrirEditarProducto(p) {
        const fields = { 'prodId': p.id, 'prodCodigo': p.codigo, 'prodCat': p.categoria||'', 'prodDesc': p.descripcion, 'prodUnidad': p.unidad, 'prodStock': p.stock_actual, 'prodMin': p.stock_minimo, 'prodImagenUrl': p.imagen_url || '' };
        Object.entries(fields).forEach(([id, val]) => { const el = document.getElementById(id); if(el) el.value = val; });
        document.getElementById('titleModalProducto').textContent = 'Editar Producto';
        document.getElementById('btnSubmitProducto').textContent  = 'Guardar Cambios';
        // Preview imagen
        if (prodImgPrev) {
            prodImgPrev.innerHTML = '';
            if (p.imagen_url) {
                const img = document.createElement('img');
                img.className = 'prod-img-preview-thumb';
                img.alt = 'Preview';
                img.addEventListener('error', () => { prodImgPrev.innerHTML = ''; });
                img.src = p.imagen_url;
                prodImgPrev.appendChild(img);
            }
        }
        openModal(modalProducto);
    }

    if (formProducto) {
        formProducto.addEventListener('submit', async (e) => {
            e.preventDefault();
            const btn = e.submitter; btn.disabled = true;
            const id  = document.getElementById('prodId').value;
            const imgVal = (document.getElementById('prodImagenUrl')?.value || '').trim();
            const payload = {
                codigo:      document.getElementById('prodCodigo').value.trim(),
                descripcion: document.getElementById('prodDesc').value.trim(),
                categoria:   document.getElementById('prodCat').value || 'General',
                unidad:      document.getElementById('prodUnidad').value.trim(),
                stock_actual: parseFloat(document.getElementById('prodStock').value) || 0,
                stock_minimo: parseFloat(document.getElementById('prodMin').value)   || 0,
                imagen_url:   imgVal || null
            };
            try {
                const url    = id ? `/api/v1/productos/${id}` : '/api/v1/productos/';
                const method = id ? 'PUT' : 'POST';
                const res = await fetch(url, { method, headers:{'Content-Type':'application/json', 'X-CSRF-Token': CSRF_TOKEN}, body: JSON.stringify(payload) });
                if (res.ok) {
                    closeModal(modalProducto);
                    e.target.reset();
                    await cargarProductos();
                    showToast(id ? 'Producto actualizado ✓' : 'Producto registrado ✓');
                } else {
                    const err = await res.json();
                    showToast('Error: ' + (err.detail || 'No se pudo guardar'), 'error');
                }
            } finally { btn.disabled = false; }
        });
    }

    document.getElementById('btnConfirmDeleteProducto')?.addEventListener('click', async () => {
        const id  = document.getElementById('deleteProductoId').value;
        const res = await fetch(`/api/v1/productos/${id}`, { method:'DELETE', headers:{'X-CSRF-Token': CSRF_TOKEN} });
        closeModal(modalDeleteProducto);
        if (res.ok) { await cargarProductos(); showToast('Producto eliminado ✓'); }
        else showToast('No se pudo eliminar', 'error');
    });

    // ─── ALMACENES Y ESTANTES ────────────────────────────────────
    const almacenesList = document.getElementById('almacenes-list');

    async function cargarAlmacenes() {
        if (!almacenesList) return;
        try {
            const res = await fetch('/api/v1/almacenes/');
            const almacenes = await res.json();
            await renderizarAlmacenes(almacenes);
            const countLabel = document.getElementById('tab-count-estantes');
            if (countLabel) countLabel.textContent = almacenes.length;
        } catch (e) { console.error("Error cargando almacenes:", e); }
    }

    const BODEGA_COLORS = [
        { gradient:'from-emerald-500 to-teal-600'   },
        { gradient:'from-indigo-500 to-violet-600'  },
        { gradient:'from-amber-500 to-orange-600'   },
        { gradient:'from-sky-500 to-blue-600'       },
        { gradient:'from-rose-500 to-pink-600'      },
    ];

    async function renderizarAlmacenes(almacenes) {
        if (!almacenesList) return;
        if (!almacenes.length) {
            almacenesList.innerHTML = `
            <div class="flex flex-col items-center py-16 text-gray-300">
                <svg width="56" height="56" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" class="mb-4"><path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/></svg>
                <p class="text-sm font-semibold">No hay bodegas registradas</p>
            </div>`;
            return;
        }

        const estantesMap = {};
        await Promise.all(almacenes.map(async (alm) => {
            const r = await fetch(`/api/v1/almacenes/${alm.id}/estantes`);
            if (r.ok) estantesMap[alm.id] = await r.json();
        }));

        almacenesList.innerHTML = almacenes.map((alm, idx) => {
            const estantes = estantesMap[alm.id] || [];
            const pal      = BODEGA_COLORS[idx % BODEGA_COLORS.length];
            const estantesHTML = estantes.length
                ? estantes.map(est => {
                    const catCfg = getCatCfg(est.descripcion);
                    return `
                    <div class="shelf-item-enhanced">
                        <div class="shelf-item-icon" style="background:${catCfg.bg};color:${catCfg.color};">${catCfg.svg}</div>
                        <div class="min-w-0 flex-1 pr-3">
                            <p class="text-[13px] font-bold text-gray-800 leading-tight">${est.nombre}</p>
                            ${est.descripcion ? `<span class="text-[10px] font-semibold px-1.5 py-0.5 rounded-full" style="color:${catCfg.color};background:${catCfg.bg};">${est.descripcion}</span>` : ''}
                        </div>
                        <div class="flex items-center gap-1.5 shrink-0">
                            <a href="/inventario/qr/estante/${est.id}" target="_blank"
                                class="inline-flex items-center gap-1 text-[10px] font-bold px-2.5 py-1.5 rounded-lg bg-indigo-50 text-indigo-600 hover:bg-indigo-600 hover:text-white transition-all">
                                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><path d="M3 7V5a2 2 0 0 1 2-2h2"/><path d="M17 3h2a2 2 0 0 1 2 2v2"/><path d="M21 17v2a2 2 0 0 1-2 2h-2"/><path d="M7 21H5a2 2 0 0 1-2-2v-2"/><line x1="7" y1="12" x2="17" y2="12"/></svg>
                                QR
                            </a>
                            <div class="shelf-item-actions">
                                <button class="action-btn action-btn-edit" data-id="${est.id}" data-nombre="${est.nombre}" data-desc="${est.descripcion||''}" data-almacen="${alm.id}" data-action="edit-estante" title="Editar">
                                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
                                </button>
                                <button class="action-btn action-btn-delete" data-id="${est.id}" data-nombre="${est.nombre}" data-tipo="estante" data-action="delete-shelf" title="Eliminar">
                                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/></svg>
                                </button>
                            </div>
                        </div>
                    </div>`;
                }).join('')
                : `<div class="col-span-full py-6 text-center border-2 border-dashed border-gray-100 rounded-xl">
                    <p class="text-xs text-gray-400 font-medium">Sin estantes registrados</p>
                </div>`;

            return `
            <div class="bodega-card">
                <div class="bodega-card-header bg-gradient-to-r ${pal.gradient}">
                    <div class="bodega-card-header-icon">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                            <path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>
                            <polyline points="9 22 9 12 15 12 15 22"/>
                        </svg>
                    </div>
                    <div class="bodega-card-header-info">
                        <h3 class="bodega-card-title">${alm.nombre}</h3>
                        ${alm.ubicacion ? `<p class="bodega-card-location"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/></svg>${alm.ubicacion}</p>` : ''}
                    </div>
                    <div class="almacen-header-actions">
                        <button class="action-btn action-btn-edit bodega-edit-btn" data-id="${alm.id}" data-nombre="${alm.nombre}" data-ubicacion="${alm.ubicacion||''}" data-action="edit-almacen" title="Editar bodega">
                            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
                        </button>
                        <button class="action-btn action-btn-delete bodega-del-btn" data-id="${alm.id}" data-nombre="${alm.nombre}" data-tipo="bodega" data-action="delete-shelf" title="Eliminar bodega">
                            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/></svg>
                        </button>
                        <button class="btn-add-estante" data-almacen-id="${alm.id}" data-almacen-nombre="${alm.nombre}">
                            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.5"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
                            NUEVO ESTANTE
                        </button>
                    </div>
                </div>
                <div class="bodega-card-shelves">
                    <div class="bodega-shelves-count">
                        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><rect x="2" y="3" width="20" height="5" rx="1"/><rect x="2" y="10" width="20" height="5" rx="1"/><rect x="2" y="17" width="20" height="5" rx="1"/></svg>
                        ${estantes.length} ${estantes.length === 1 ? 'estante' : 'estantes'}
                    </div>
                    <div class="grid grid-cols-1 md:grid-cols-2 gap-2.5 mt-3">${estantesHTML}</div>
                </div>
            </div>`;
        }).join('');

        almacenesList.querySelectorAll('.btn-add-estante').forEach(btn => {
            btn.addEventListener('click', () => {
                document.getElementById('estanteId').value        = '';
                document.getElementById('estanteAlmacenId').value = btn.dataset.almacenId;
                document.getElementById('estanteNombre').value    = '';
                document.getElementById('estanteDesc').value      = '';
                document.getElementById('titleModalEstante').textContent = `Agregar Estante en: ${btn.dataset.almacenNombre}`;
                document.getElementById('btnSubmitEstante').textContent  = 'Guardar + QR';
                openModal(modalEstante);
            });
        });

        almacenesList.querySelectorAll('[data-action="edit-estante"]').forEach(btn => {
            btn.addEventListener('click', () => {
                document.getElementById('estanteId').value        = btn.dataset.id;
                document.getElementById('estanteAlmacenId').value = btn.dataset.almacen;
                document.getElementById('estanteNombre').value    = btn.dataset.nombre;
                document.getElementById('estanteDesc').value      = btn.dataset.desc;
                document.getElementById('titleModalEstante').textContent = `Editar Estante: ${btn.dataset.nombre}`;
                document.getElementById('btnSubmitEstante').textContent  = 'Guardar Cambios';
                openModal(modalEstante);
            });
        });

        almacenesList.querySelectorAll('[data-action="edit-almacen"]').forEach(btn => {
            btn.addEventListener('click', () => {
                document.getElementById('almId').value        = btn.dataset.id;
                document.getElementById('almNombre').value    = btn.dataset.nombre;
                document.getElementById('almUbicacion').value = btn.dataset.ubicacion;
                document.getElementById('titleModalAlmacen').textContent = 'Editar Bodega';
                document.getElementById('btnSubmitAlmacen').textContent  = 'Guardar Cambios';
                openModal(modalAlmacen);
            });
        });

        almacenesList.querySelectorAll('[data-action="delete-shelf"]').forEach(btn => {
            btn.addEventListener('click', () => {
                document.getElementById('deleteEstanteId').value     = btn.dataset.id;
                document.getElementById('deleteEstanteTipo').value   = btn.dataset.tipo;
                document.getElementById('titleDeleteEstante').textContent  = btn.dataset.tipo === 'bodega' ? '¿Eliminar Bodega?' : '¿Eliminar Estante?';
                document.getElementById('deleteEstanteNombre').textContent = `"${btn.dataset.nombre}" será eliminado permanentemente.`;
                openModal(modalDeleteEstante);
            });
        });
    }

    // ─── FORM: New/Edit Almacén ─────────────────────────────────
    const btnOpenAlm  = document.getElementById('btnOpenModalAlmacen');
    const formAlmacen = document.getElementById('formAlmacen');
    if (btnOpenAlm) {
        btnOpenAlm.addEventListener('click', () => {
            if (formAlmacen) formAlmacen.reset();
            document.getElementById('almId').value = '';
            document.getElementById('titleModalAlmacen').textContent = 'Nueva Bodega';
            document.getElementById('btnSubmitAlmacen').textContent  = 'Registrar Bodega';
            openModal(modalAlmacen);
        });
    }
    if (formAlmacen) {
        formAlmacen.addEventListener('submit', async (e) => {
            e.preventDefault();
            const btn = e.submitter; btn.disabled = true;
            const id  = document.getElementById('almId').value;
            const payload = { nombre: document.getElementById('almNombre').value.trim(), ubicacion: document.getElementById('almUbicacion').value.trim() || null, activo: true };
            try {
                const res = await fetch(id ? `/api/v1/almacenes/${id}` : '/api/v1/almacenes/', { method: id?'PUT':'POST', headers:{'Content-Type':'application/json', 'X-CSRF-Token': CSRF_TOKEN}, body: JSON.stringify(payload) });
                if (res.ok) { closeModal(modalAlmacen); e.target.reset(); await cargarAlmacenes(); showToast(id ? 'Bodega actualizada ✓' : 'Bodega registrada ✓'); }
                else { const err = await res.json(); showToast('Error: ' + (err.detail||'No se pudo guardar'), 'error'); }
            } finally { btn.disabled = false; }
        });
    }

    // ─── FORM: New/Edit Estante ─────────────────────────────────
    const formEstante = document.getElementById('formEstante');
    if (formEstante) {
        formEstante.addEventListener('submit', async (e) => {
            e.preventDefault();
            const btn = e.submitter; btn.disabled = true;
            const id  = document.getElementById('estanteId').value;
            const payload = { nombre: document.getElementById('estanteNombre').value.trim(), descripcion: document.getElementById('estanteDesc').value.trim() || null, almacen_id: parseInt(document.getElementById('estanteAlmacenId').value) };
            try {
                const res = await fetch(id ? `/api/v1/estantes/${id}` : '/api/v1/estantes/', { method: id?'PUT':'POST', headers:{'Content-Type':'application/json', 'X-CSRF-Token': CSRF_TOKEN}, body: JSON.stringify(payload) });
                if (res.ok) {
                    const estante = await res.json();
                    closeModal(modalEstante); e.target.reset(); await cargarAlmacenes();
                    showToast(id ? 'Estante actualizado ✓' : 'Estante guardado ✓');
                    if (!id) window.location.href = `/inventario/qr/estante/${estante.id}`;
                } else { const err = await res.json(); showToast('Error: ' + (err.detail||'No se pudo crear'), 'error'); }
            } finally { btn.disabled = false; }
        });
    }

    document.getElementById('btnConfirmDeleteEstante')?.addEventListener('click', async () => {
        const id   = document.getElementById('deleteEstanteId').value;
        const tipo = document.getElementById('deleteEstanteTipo').value;
        const url  = tipo === 'bodega' ? `/api/v1/almacenes/${id}` : `/api/v1/estantes/${id}`;
        const res  = await fetch(url, { method:'DELETE', headers:{'X-CSRF-Token': CSRF_TOKEN} });
        closeModal(modalDeleteEstante);
        if (res.ok) { await cargarAlmacenes(); showToast(`${tipo==='bodega'?'Bodega':'Estante'} eliminado ✓`); }
        else showToast('No se pudo eliminar', 'error');
    });

    // ─── TAB SWITCHING ────────────────────────────────────────────
    document.querySelectorAll('.tab-btn[data-tab]').forEach(btn => {
        btn.addEventListener('click', () => {
            const tab = btn.dataset.tab;
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            document.querySelectorAll('.tab-panel').forEach(p => p.classList.add('hidden'));
            document.getElementById(`tab-${tab}`)?.classList.remove('hidden');
            // Mostrar/ocultar botones de header
            document.querySelectorAll('[class*="tab-btn-"]').forEach(b => b.classList.add('hidden'));
            document.querySelectorAll(`.tab-btn-${tab}`).forEach(b => b.classList.remove('hidden'));
        });
    });

    // ─── INIT ─────────────────────────────────────────────────────
    llenarSelectsCat();
    await cargarProductos();
    await cargarAlmacenes();
});
