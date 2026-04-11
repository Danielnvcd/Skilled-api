/* ─── inventario_web.js v31 ─── */
document.addEventListener('DOMContentLoaded', async () => {
    // Lee la categoría desde data attribute (CSP-safe, sin inline script)
    const CATEGORIA_ACTUAL = document.getElementById('page-data')?.dataset?.categoria || null;

    // ─── CATEGORÍAS ─────────────────────────────────────────────
    const CATEGORIAS = ['Tornillería','Tuercas','Rondanas','Pijas','Abrazaderas','Soportería','Tubería/Accesorios'];

    // ─── CONFIG VISUAL POR CATEGORÍA ────────────────────────────
    const CATEGORIA_CONFIG = {
        'Tornillería': {
            color: '#4F46E5', bg: '#EEF2FF',
            svg: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/></svg>`
        },
        'Tuercas': {
            color: '#B45309', bg: '#FFFBEB',
            svg: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polygon points="12 2 22 8.5 22 15.5 12 22 2 15.5 2 8.5"/><circle cx="12" cy="12" r="3"/></svg>`
        },
        'Rondanas': {
            color: '#0891B2', bg: '#ECFEFF',
            svg: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="3.5"/></svg>`
        },
        'Pijas': {
            color: '#7C3AED', bg: '#F5F3FF',
            svg: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="2" x2="12" y2="20"/><path d="M8 6h8"/><path d="M9 10h6"/><path d="M10 14h4"/><path d="M11 18h2"/></svg>`
        },
        'Abrazaderas': {
            color: '#059669', bg: '#ECFDF5',
            svg: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>`
        },
        'Soportería': {
            color: '#DC2626', bg: '#FEF2F2',
            svg: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="4" rx="1"/><rect x="2" y="10" width="20" height="4" rx="1"/><rect x="2" y="17" width="20" height="4" rx="1"/></svg>`
        },
        'Tubería/Accesorios': {
            color: '#0284C7', bg: '#F0F9FF',
            svg: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><rect x="1" y="6" width="22" height="12" rx="5"/><path d="M6 12h12"/></svg>`
        }
    };
    const CAT_DEFAULT = {
        color: '#6B7280', bg: '#F3F4F6',
        svg: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/></svg>`
    };

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

    // ─── MODALES ────────────────────────────────────────────────
    const modalProducto      = document.getElementById('modalProducto');
    const modalDeleteProducto= document.getElementById('modalDeleteProducto');
    const modalAlmacen       = document.getElementById('modalAlmacen');
    const modalEstante       = document.getElementById('modalEstante');
    const modalDeleteEstante = document.getElementById('modalDeleteEstante');

    // Cerrar con backdrop click
    [modalProducto, modalDeleteProducto, modalAlmacen, modalEstante, modalDeleteEstante].forEach(m => {
        if (m) m.addEventListener('click', (e) => { if (e.target === m) closeModal(m); });
    });

    const bindClose = (btnId, modal) => {
        const btn = document.getElementById(btnId);
        if (btn && modal) btn.addEventListener('click', () => closeModal(modal));
    };

    bindClose('btnCloseModalProducto', modalProducto);
    bindClose('btnCancelProducto', modalProducto);
    bindClose('btnCloseModalAlmacen', modalAlmacen);
    bindClose('btnCancelAlmacen', modalAlmacen);
    bindClose('btnCloseModalEstante', modalEstante);
    bindClose('btnCancelEstante', modalEstante);
    bindClose('btnCancelDeleteProducto', modalDeleteProducto);
    bindClose('btnCancelDeleteEstante', modalDeleteEstante);

    // ─── CATÁLOGO: Cargar Productos ──────────────────────────────
    let todosProductos = [];
    const productsList = document.getElementById('productos-list');

    async function cargarProductos() {
        if (!productsList) return;
        try {
            const res = await fetch('/api/v1/productos/?limit=9999');
            todosProductos = await res.json();
            renderizarProductos(todosProductos);
            const countLabel = document.getElementById('tab-count-catalogo');
            if (countLabel) countLabel.textContent = todosProductos.length;
        } catch (e) {
            console.error("Error cargando productos:", e);
        }
    }


    const searchProd = document.getElementById('searchProducto');
    const filterCat = document.getElementById('filterCategoria');
    if (searchProd) searchProd.addEventListener('input', aplicarFiltros);
    if (filterCat) filterCat.addEventListener('change', aplicarFiltros);

    function renderizarProductos(productos) {
        if (!productsList) return;
        if (!productos.length) {
            productsList.innerHTML = `
            <div class="flex flex-col items-center py-16 text-gray-300">
                <svg width="56" height="56" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" class="mb-4"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/></svg>
                <p class="text-sm font-semibold">No hay productos que mostrar</p>
            </div>`;
            return;
        }

        const btnGenHtml = (p) => `
            <div class="product-item-actions">
                <button class="action-btn action-btn-edit" data-id="${p.id}" data-action="edit-prod" title="Editar">
                    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
                </button>
                <button class="action-btn action-btn-delete" data-id="${p.id}" data-nombre="${p.descripcion}" data-action="delete-prod" title="Eliminar">
                    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/></svg>
                </button>
            </div>`;

        // ── MODO 1: Vista Detallada de Categoría ──────────
        if (CATEGORIA_ACTUAL) {
            const cat = CATEGORIA_ACTUAL;
            const dictCfg = CATEGORIA_CONFIG[cat] || CAT_DEFAULT;
            const prods = productos.filter(p => p.categoria === cat);
            
            if (!prods.length) {
                productsList.innerHTML = `<div class="text-center p-8 text-gray-400">No hay materiales en ${cat}</div>`;
                return;
            }

            const itemsHTML = prods.map(p => {
                const stock   = parseFloat(p.stock_actual);
                const minimo  = parseFloat(p.stock_minimo);
                const bajo    = stock <= minimo;
                const pillCls = bajo ? 'stock-pill-low' : 'stock-pill-ok';
                return `
                <div class="product-card">
                    <div class="product-card-icon" style="background:${dictCfg.bg}; color:${dictCfg.color};">
                        ${dictCfg.svg}
                    </div>
                    <div class="product-card-body">
                        <p class="product-card-name">${p.descripcion}</p>
                        <div class="product-card-meta">
                            <span class="sku-tag">${p.codigo}</span>
                            <span class="cat-tag" style="color:${dictCfg.color}; background:${dictCfg.bg};">${p.unidad}</span>
                        </div>
                    </div>
                    <div class="product-card-side">
                        <span class="stock-pill ${pillCls}">${bajo ? '⚠' : '✔'} ${stock.toFixed(0)} ${p.unidad}</span>
                        ${btnGenHtml(p)}
                    </div>
                </div>`;
            }).join('');

            productsList.innerHTML = `<div class="seccion-productos-grid">${itemsHTML}</div>`;
            bindAcciones(productsList);
            return;
        }

        // ── MODO 2: Búsqueda o filtro de categoría activo ──────────
        const posBusqueda = (searchProd && searchProd.value.trim() !== '') || (filterCat && filterCat.value !== '');

        if (posBusqueda) {
            // Si ests buscando algo desde el dashboard principal, listar productos que coincidan
            const itemsHTML = productos.map(p => {
                const cfg = CATEGORIA_CONFIG[p.categoria] || CAT_DEFAULT;
                const stock   = parseFloat(p.stock_actual);
                const minimo  = parseFloat(p.stock_minimo);
                const bajo    = stock <= minimo;
                const pillCls = bajo ? 'stock-pill-low' : 'stock-pill-ok';
                return `
                <div class="product-card">
                    <div class="product-card-icon" style="background:${cfg.bg}; color:${cfg.color};">
                        ${cfg.svg}
                    </div>
                    <div class="product-card-body">
                        <p class="product-card-name">${p.descripcion}</p>
                        <div class="product-card-meta">
                            <span class="sku-tag">${p.codigo}</span>
                            <span class="cat-tag" style="color:${cfg.color}; background:${cfg.bg};">${p.categoria}</span>
                        </div>
                    </div>
                    <div class="product-card-side">
                        <span class="stock-pill ${pillCls}">${bajo ? '⚠' : '✔'} ${stock.toFixed(0)} ${p.unidad}</span>
                        ${btnGenHtml(p)}
                    </div>
                </div>`;
            }).join('');
            productsList.innerHTML = `<div class="seccion-productos-grid">${itemsHTML}</div>`;
            bindAcciones(productsList);
            return;
        }

        // --- DIBUJAR CAJAS DE DASHBOARD ---
        const grupos = {};
        const ordenCats = CATEGORIAS.filter(c => productos.some(p => p.categoria === c));
        productos.forEach(p => {
            const cat = p.categoria || 'Sin categoría';
            if (!grupos[cat]) grupos[cat] = [];
            grupos[cat].push(p);
        });
        
        const catOrdenadas = [
            ...ordenCats,
            ...Object.keys(grupos).filter(c => !CATEGORIAS.includes(c))
        ];

        const linkBase = '/inventario/catalogo/';
        const dashHtml = `<div class="dashboard-cats-grid">` + catOrdenadas.map(cat => {
            const prods  = grupos[cat] || [];
            if(prods.length === 0) return '';
            const cfg    = CATEGORIA_CONFIG[cat] || CAT_DEFAULT;
            const bajos  = prods.filter(p => parseFloat(p.stock_actual) <= parseFloat(p.stock_minimo)).length;
            const ok     = prods.length - bajos;
            
            return `
            <a href="${linkBase}${encodeURIComponent(cat)}" class="dash-cat-card" style="--base-color:${cfg.color};">
                <div class="dash-cat-bg-icon">
                    ${cfg.svg}
                </div>
                <div class="dash-cat-content">
                    <h3>${cat}</h3>
                    <div class="dash-cat-metrics">
                        ${prods.length} <span class="text-sm font-normal opacity-80">Items</span>
                    </div>
                </div>
                <div class="dash-cat-footer">
                    ${ok > 0 ? `<span class="stat-badge ok">✔ ${ok} estables</span>` : ''}
                    ${bajos > 0 ? `<span class="stat-badge danger">⚠ ${bajos} por reponer</span>` : ''}
                </div>
            </a>`;
        }).join('') + `</div>`;

        productsList.innerHTML = dashHtml;
    }

    function bindAcciones(container) {
        container.querySelectorAll('[data-action="edit-prod"]').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.preventDefault();
                e.stopPropagation();
                const p = todosProductos.find(x => x.id == btn.dataset.id);
                if (p) abrirEditarProducto(p);
            });
        });
        container.querySelectorAll('[data-action="delete-prod"]').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.preventDefault();
                e.stopPropagation();
                const inputId   = document.getElementById('deleteProductoId');
                const labelName = document.getElementById('deleteProductoNombre');
                if (inputId)   inputId.value = btn.dataset.id;
                if (labelName) labelName.textContent = '"' + btn.dataset.nombre + '" será eliminado permanentemente.';
                openModal(modalDeleteProducto);
            });
        });
    }

    function aplicarFiltros() {
        const q   = searchProd ? searchProd.value.toLowerCase() : '';
        const cat = filterCat ? filterCat.value : '';
        const filtrado = todosProductos.filter(p => {
            const matchQ   = !q   || p.descripcion.toLowerCase().includes(q) || p.codigo.toLowerCase().includes(q);
            const matchCat = !cat || p.categoria === cat;
            return matchQ && matchCat;
        });
        renderizarProductos(filtrado);
    }



    // ─── Nuevo/Editar Producto ─────────────────────────────────────────
    const btnOpenProd = document.getElementById('btnOpenModalProducto');
    const formProducto = document.getElementById('formProducto');

    if (btnOpenProd) {
        btnOpenProd.addEventListener('click', () => {
            if (formProducto) formProducto.reset();
            const inputId = document.getElementById('prodId');
            const title = document.getElementById('titleModalProducto');
            const btnSubmit = document.getElementById('btnSubmitProducto');
            if (inputId) inputId.value = '';
            if (title) title.textContent = 'Registrar Nuevo Producto';
            if (btnSubmit) btnSubmit.textContent = 'Guardar Producto';
            openModal(modalProducto);
        });
    }

    function abrirEditarProducto(p) {
        const fields = {
            'prodId': p.id,
            'prodCodigo': p.codigo,
            'prodCat': p.categoria || '',
            'prodDesc': p.descripcion,
            'prodUnidad': p.unidad,
            'prodStock': p.stock_actual,
            'prodMin': p.stock_minimo
        };
        Object.entries(fields).forEach(([id, val]) => {
            const el = document.getElementById(id);
            if (el) el.value = val;
        });
        const title = document.getElementById('titleModalProducto');
        const btnSubmit = document.getElementById('btnSubmitProducto');
        if (title) title.textContent = 'Editar Producto';
        if (btnSubmit) btnSubmit.textContent = 'Guardar Cambios';
        openModal(modalProducto);
    }

    if (formProducto) {
        formProducto.addEventListener('submit', async (e) => {
            e.preventDefault();
            const btn = e.submitter;
            btn.disabled = true;
            const id = document.getElementById('prodId').value;
            const payload = {
                codigo:      document.getElementById('prodCodigo').value.trim(),
                descripcion: document.getElementById('prodDesc').value.trim(),
                categoria:   document.getElementById('prodCat').value || 'General',
                unidad:      document.getElementById('prodUnidad').value.trim(),
                stock_actual: parseFloat(document.getElementById('prodStock').value) || 0,
                stock_minimo: parseFloat(document.getElementById('prodMin').value) || 0
            };
            try {
                const url    = id ? `/api/v1/productos/${id}` : '/api/v1/productos/';
                const method = id ? 'PUT' : 'POST';
                const res = await fetch(url, { method, headers: {'Content-Type':'application/json'}, body: JSON.stringify(payload) });
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

    const btnConfirmDeleteProd = document.getElementById('btnConfirmDeleteProducto');
    if (btnConfirmDeleteProd) {
        btnConfirmDeleteProd.addEventListener('click', async () => {
            const id = document.getElementById('deleteProductoId').value;
            const res = await fetch(`/api/v1/productos/${id}`, { method: 'DELETE' });
            closeModal(modalDeleteProducto);
            if (res.ok) {
                await cargarProductos();
                showToast('Producto eliminado ✓');
            } else {
                showToast('No se pudo eliminar', 'error');
            }
        });
    }

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
        } catch (e) {
            console.error("Error cargando almacenes:", e);
        }
    }

    // Paleta de colores para bodegas (cíclico)
    const BODEGA_COLORS = [
        { gradient: 'from-emerald-500 to-teal-600', icon: '#059669', bg: '#ECFDF5', light: 'rgba(16,185,129,0.08)' },
        { gradient: 'from-indigo-500 to-violet-600', icon: '#4F46E5', bg: '#EEF2FF', light: 'rgba(79,70,229,0.08)' },
        { gradient: 'from-amber-500 to-orange-600', icon: '#D97706', bg: '#FFFBEB', light: 'rgba(217,119,6,0.08)' },
        { gradient: 'from-sky-500 to-blue-600', icon: '#0284C7', bg: '#F0F9FF', light: 'rgba(2,132,199,0.08)' },
        { gradient: 'from-rose-500 to-pink-600', icon: '#DC2626', bg: '#FEF2F2', light: 'rgba(220,38,38,0.08)' },
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
            const estantes  = estantesMap[alm.id] || [];
            const pal       = BODEGA_COLORS[idx % BODEGA_COLORS.length];
            const estantesHTML = estantes.length
                ? estantes.map(est => {
                    const catCfg = CATEGORIA_CONFIG[est.descripcion] || CAT_DEFAULT;
                    return `
                    <div class="shelf-item-enhanced">
                        <div class="shelf-item-icon" style="background:${catCfg.bg}; color:${catCfg.color};">
                            ${catCfg.svg}
                        </div>
                        <div class="min-w-0 flex-1 pr-3">
                            <p class="text-[13px] font-bold text-gray-800 leading-tight">${est.nombre}</p>
                            ${est.descripcion ? `<span class="text-[10px] font-semibold px-1.5 py-0.5 rounded-full" style="color:${catCfg.color}; background:${catCfg.bg};">${est.descripcion}</span>` : ''}
                        </div>
                        <div class="flex items-center gap-1.5 shrink-0">
                            <a href="/inventario/qr/estante/${est.id}" target="_blank"
                                class="inline-flex items-center gap-1 text-[10px] font-bold px-2.5 py-1.5 rounded-lg bg-indigo-50 text-indigo-600 hover:bg-indigo-600 hover:text-white transition-all">
                                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><path d="M3 7V5a2 2 0 0 1 2-2h2"/><path d="M17 3h2a2 2 0 0 1 2 2v2"/><path d="M21 17v2a2 2 0 0 1-2 2h-2"/><path d="M7 21H5a2 2 0 0 1-2-2v-2"/><line x1="7" y1="12" x2="17" y2="12"/></svg>
                                QR
                            </a>
                            <div class="shelf-item-actions">
                                <button class="action-btn action-btn-edit" data-id="${est.id}" data-nombre="${est.nombre}" data-desc="${est.descripcion || ''}" data-almacen="${alm.id}" data-action="edit-estante" title="Editar">
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
                    <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="#D1D5DB" stroke-width="1.5" class="mx-auto mb-2"><rect x="2" y="3" width="20" height="5" rx="1"/><rect x="2" y="10" width="20" height="5" rx="1"/><rect x="2" y="17" width="20" height="5" rx="1"/></svg>
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
                        <button class="action-btn action-btn-edit bodega-edit-btn" data-id="${alm.id}" data-nombre="${alm.nombre}" data-ubicacion="${alm.ubicacion || ''}" data-action="edit-almacen" title="Editar bodega">
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
                    <div class="grid grid-cols-1 md:grid-cols-2 gap-2.5 mt-3">
                        ${estantesHTML}
                    </div>
                </div>
            </div>`;
        }).join('');

        // Eventos Estantes: Añadir
        almacenesList.querySelectorAll('.btn-add-estante').forEach(btn => {
            btn.addEventListener('click', () => {
                const inputEstId = document.getElementById('estanteId');
                const inputAlmId = document.getElementById('estanteAlmacenId');
                const inputName = document.getElementById('estanteNombre');
                const inputDesc = document.getElementById('estanteDesc');
                const title = document.getElementById('titleModalEstante');
                const btnSub = document.getElementById('btnSubmitEstante');
                if (inputEstId) inputEstId.value = '';
                if (inputAlmId) inputAlmId.value = btn.dataset.almacenId;
                if (inputName) inputName.value = '';
                if (inputDesc) inputDesc.value = '';
                if (title) title.textContent = `Agregar Estante en: ${btn.dataset.almacenNombre}`;
                if (btnSub) btnSub.textContent = 'Guardar + QR';
                openModal(modalEstante);
            });
        });

        // Eventos Estantes: Editar
        almacenesList.querySelectorAll('[data-action="edit-estante"]').forEach(btn => {
            btn.addEventListener('click', () => {
                const inputEstId = document.getElementById('estanteId');
                const inputAlmId = document.getElementById('estanteAlmacenId');
                const inputName = document.getElementById('estanteNombre');
                const inputDesc = document.getElementById('estanteDesc');
                const title = document.getElementById('titleModalEstante');
                const btnSub = document.getElementById('btnSubmitEstante');
                if (inputEstId) inputEstId.value = btn.dataset.id;
                if (inputAlmId) inputAlmId.value = btn.dataset.almacen;
                if (inputName) inputName.value = btn.dataset.nombre;
                if (inputDesc) inputDesc.value = btn.dataset.desc;
                if (title) title.textContent = `Editar Estante: ${btn.dataset.nombre}`;
                if (btnSub) btnSub.textContent = 'Guardar Cambios';
                openModal(modalEstante);
            });
        });

        // Eventos: Editar Almacén
        almacenesList.querySelectorAll('[data-action="edit-almacen"]').forEach(btn => {
            btn.addEventListener('click', () => {
                const inputId = document.getElementById('almId');
                const inputName = document.getElementById('almNombre');
                const inputUbic = document.getElementById('almUbicacion');
                const title = document.getElementById('titleModalAlmacen');
                const btnSub = document.getElementById('btnSubmitAlmacen');
                if (inputId) inputId.value = btn.dataset.id;
                if (inputName) inputName.value = btn.dataset.nombre;
                if (inputUbic) inputUbic.value = btn.dataset.ubicacion;
                if (title) title.textContent = 'Editar Bodega';
                if (btnSub) btnSub.textContent = 'Guardar Cambios';
                openModal(modalAlmacen);
            });
        });

        // Eventos: Eliminar (Estante o Bodega)
        almacenesList.querySelectorAll('[data-action="delete-shelf"]').forEach(btn => {
            btn.addEventListener('click', () => {
                const inputId = document.getElementById('deleteEstanteId');
                const inputTipo = document.getElementById('deleteEstanteTipo');
                const title = document.getElementById('titleDeleteEstante');
                const labelName = document.getElementById('deleteEstanteNombre');
                if (inputId) inputId.value = btn.dataset.id;
                if (inputTipo) inputTipo.value = btn.dataset.tipo;
                if (title) title.textContent = btn.dataset.tipo === 'bodega' ? '¿Eliminar Bodega?' : '¿Eliminar Estante?';
                if (labelName) labelName.textContent = `"${btn.dataset.nombre}" será eliminado permanentemente.`;
                openModal(modalDeleteEstante);
            });
        });
    }

    // ─── FORM: New/Edit Almacén ────────────────────────────────
    const btnOpenAlm = document.getElementById('btnOpenModalAlmacen');
    const formAlmacen = document.getElementById('formAlmacen');
    if (btnOpenAlm) {
        btnOpenAlm.addEventListener('click', () => {
            if (formAlmacen) formAlmacen.reset();
            const inputId = document.getElementById('almId');
            const title = document.getElementById('titleModalAlmacen');
            const btnSub = document.getElementById('btnSubmitAlmacen');
            if (inputId) inputId.value = '';
            if (title) title.textContent = 'Nueva Bodega';
            if (btnSub) btnSub.textContent = 'Registrar Bodega';
            openModal(modalAlmacen);
        });
    }

    if (formAlmacen) {
        formAlmacen.addEventListener('submit', async (e) => {
            e.preventDefault();
            const btn = e.submitter;
            btn.disabled = true;
            const id = document.getElementById('almId').value;
            const payload = {
                nombre:    document.getElementById('almNombre').value.trim(),
                ubicacion: document.getElementById('almUbicacion').value.trim() || null,
                activo: true
            };
            try {
                const url    = id ? `/api/v1/almacenes/${id}` : '/api/v1/almacenes/';
                const method = id ? 'PUT' : 'POST';
                const res = await fetch(url, { method, headers: {'Content-Type':'application/json'}, body: JSON.stringify(payload) });
                if (res.ok) {
                    closeModal(modalAlmacen);
                    e.target.reset();
                    await cargarAlmacenes();
                    showToast(id ? 'Bodega actualizada ✓' : 'Bodega registrada ✓');
                } else {
                    const err = await res.json();
                    showToast('Error: ' + (err.detail || 'No se pudo guardar'), 'error');
                }
            } finally { btn.disabled = false; }
        });
    }

    // ─── FORM: New/Edit Estante ────────────────────────────────
    const formEstante = document.getElementById('formEstante');
    if (formEstante) {
        formEstante.addEventListener('submit', async (e) => {
            e.preventDefault();
            const btn = e.submitter;
            btn.disabled = true;
            const id = document.getElementById('estanteId').value;
            const payload = {
                nombre:      document.getElementById('estanteNombre').value.trim(),
                descripcion: document.getElementById('estanteDesc').value.trim() || null,
                almacen_id:  parseInt(document.getElementById('estanteAlmacenId').value)
            };
            try {
                const url    = id ? `/api/v1/estantes/${id}` : '/api/v1/estantes/';
                const method = id ? 'PUT' : 'POST';
                const res = await fetch(url, { method, headers: {'Content-Type':'application/json'}, body: JSON.stringify(payload) });
                if (res.ok) {
                    const estante = await res.json();
                    closeModal(modalEstante);
                    e.target.reset();
                    await cargarAlmacenes();
                    showToast(id ? 'Estante actualizado ✓' : 'Estante guardado ✓');
                    if (!id) window.location.href = `/inventario/qr/estante/${estante.id}`;
                } else {
                    const err = await res.json();
                    showToast('Error: ' + (err.detail || 'No se pudo crear el estante'), 'error');
                }
            } finally { btn.disabled = false; }
        });
    }

    const btnConfirmDeleteEst = document.getElementById('btnConfirmDeleteEstante');
    if (btnConfirmDeleteEst) {
        btnConfirmDeleteEst.addEventListener('click', async () => {
            const id   = document.getElementById('deleteEstanteId').value;
            const tipo = document.getElementById('deleteEstanteTipo').value;
            const url  = tipo === 'bodega' ? `/api/v1/almacenes/${id}` : `/api/v1/estantes/${id}`;
            const res  = await fetch(url, { method: 'DELETE' });
            closeModal(modalDeleteEstante);
            if (res.ok) {
                await cargarAlmacenes();
                showToast(`${tipo === 'bodega' ? 'Bodega' : 'Estante'} eliminado ✓`);
            } else {
                showToast('No se pudo eliminar', 'error');
            }
        });
    }

    // ─── INIT ────────────────────────────────────────────────────
    await cargarProductos();
    await cargarAlmacenes();
});
