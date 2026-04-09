/* ─── inventario_web.js — Soporte para páginas separadas ─── */
document.addEventListener('DOMContentLoaded', async () => {

    // ─── CATEGORÍAS ─────────────────────────────────────────────
    const CATEGORIAS = ['Tornillería','Tuercas','Rondanas','Pijas','Abrazaderas','Soportería','Tubería/Accesorios'];

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
            const res = await fetch('/api/v1/productos/');
            todosProductos = await res.json();
            renderizarProductos(todosProductos);
            const countLabel = document.getElementById('tab-count-catalogo');
            if (countLabel) countLabel.textContent = todosProductos.length;
        } catch (e) {
            console.error("Error cargando productos:", e);
        }
    }

    function renderizarProductos(productos) {
        if (!productsList) return;
        if (!productos.length) {
            productsList.innerHTML = '<div class="col-span-full text-center py-12 text-gray-400"><p class="text-sm">No hay productos registrados.</p></div>';
            return;
        }
        productsList.innerHTML = productos.map(p => {
            const stock    = parseFloat(p.stock_actual);
            const minimo   = parseFloat(p.stock_minimo);
            const bajo     = stock <= minimo;
            const pillCls  = bajo ? 'stock-pill-low' : 'stock-pill-ok';
            const pillIcon = bajo ? '⚠' : '✔';
            return `
            <div class="product-item">
                <div class="min-w-0 flex-1 pr-2">
                    <p class="font-bold text-gray-900 text-sm mb-0.5 leading-tight truncate">${p.descripcion}</p>
                    <div class="flex items-center gap-2 flex-wrap">
                        <span class="text-[10px] font-black bg-gray-100 text-gray-500 px-1.5 py-0.5 rounded uppercase tracking-tighter">${p.codigo}</span>
                        <span class="text-[11px] text-gray-400 font-medium">${p.categoria || 'Sin categoría'}</span>
                    </div>
                </div>
                <div class="flex items-center gap-2 shrink-0">
                    <span class="stock-pill ${pillCls}">${pillIcon} ${stock.toFixed(0)} ${p.unidad}</span>
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
        }).join('');

        // Eventos de acciones
        productsList.querySelectorAll('[data-action="edit-prod"]').forEach(btn => {
            btn.addEventListener('click', () => {
                const p = todosProductos.find(x => x.id == btn.dataset.id);
                if (p) abrirEditarProducto(p);
            });
        });
        productsList.querySelectorAll('[data-action="delete-prod"]').forEach(btn => {
            btn.addEventListener('click', () => {
                const inputId = document.getElementById('deleteProductoId');
                const labelName = document.getElementById('deleteProductoNombre');
                if (inputId) inputId.value = btn.dataset.id;
                if (labelName) labelName.textContent = `"${btn.dataset.nombre}" será eliminado permanentemente.`;
                openModal(modalDeleteProducto);
            });
        });
    }

    // Filtros en tiempo real
    const searchProd = document.getElementById('searchProducto');
    const filterCat = document.getElementById('filterCategoria');
    if (searchProd) searchProd.addEventListener('input', aplicarFiltros);
    if (filterCat) filterCat.addEventListener('change', aplicarFiltros);

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

    async function renderizarAlmacenes(almacenes) {
        if (!almacenesList) return;
        if (!almacenes.length) {
            almacenesList.innerHTML = '<div class="text-center py-12 text-gray-400"><p class="text-sm">No hay bodegas registradas.</p></div>';
            return;
        }

        const estantesMap = {};
        await Promise.all(almacenes.map(async (alm) => {
            const r = await fetch(`/api/v1/almacenes/${alm.id}/estantes`);
            if (r.ok) estantesMap[alm.id] = await r.json();
        }));

        almacenesList.innerHTML = almacenes.map(alm => {
            const estantes = estantesMap[alm.id] || [];
            const estantesHTML = estantes.length
                ? estantes.map(est => `
                    <div class="shelf-item">
                        <div class="min-w-0 pr-4">
                            <p class="text-[13px] font-bold text-gray-700 leading-tight">${est.nombre}</p>
                            ${est.descripcion ? `<p class="text-[11px] text-gray-400 font-medium truncate mt-0.5">${est.descripcion}</p>` : ''}
                        </div>
                        <div class="flex items-center gap-2 shrink-0">
                            <a href="/inventario/qr/estante/${est.id}" target="_blank"
                                class="inline-flex items-center gap-1.5 text-[11px] font-bold px-3 py-1.5 rounded-lg bg-indigo-50 text-indigo-600 hover:bg-indigo-600 hover:text-white transition-all">
                                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><path d="M3 7V5a2 2 0 0 1 2-2h2"/><path d="M17 3h2a2 2 0 0 1 2 2v2"/><path d="M21 17v2a2 2 0 0 1-2 2h-2"/><path d="M7 21H5a2 2 0 0 1-2-2v-2"/><line x1="7" y1="12" x2="17" y2="12"/></svg>
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
                    </div>`).join('')
                : '<div class="py-4 text-center border-2 border-dashed border-gray-100 rounded-xl"><p class="text-xs text-gray-400 font-medium">Sin estantes registrados</p></div>';

            return `
            <div class="border border-gray-100/50 rounded-2xl overflow-hidden bg-white/40 shadow-sm transition-all hover:bg-white/60">
                <div class="flex items-center justify-between px-5 py-4 bg-gradient-to-r from-gray-50/80 to-transparent border-b border-gray-100/40">
                    <div>
                        <h3 class="font-bold text-gray-900 leading-tight">${alm.nombre}</h3>
                        ${alm.ubicacion ? `<p class="text-[11px] font-semibold text-gray-400 uppercase tracking-tighter mt-0.5">${alm.ubicacion}</p>` : ''}
                    </div>
                    <div class="almacen-header-actions">
                        <button class="action-btn action-btn-edit" data-id="${alm.id}" data-nombre="${alm.nombre}" data-ubicacion="${alm.ubicacion || ''}" data-action="edit-almacen" title="Editar bodega">
                            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
                        </button>
                        <button class="action-btn action-btn-delete" data-id="${alm.id}" data-nombre="${alm.nombre}" data-tipo="bodega" data-action="delete-shelf" title="Eliminar bodega">
                            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/></svg>
                        </button>
                        <button class="btn-add-estante inline-flex items-center gap-1.5 text-[11px] font-black px-3 py-2 rounded-lg bg-emerald-50 text-emerald-600 hover:bg-emerald-500 hover:text-white transition-all active:scale-95"
                            data-almacen-id="${alm.id}" data-almacen-nombre="${alm.nombre}">
                            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.5"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
                            NUEVO ESTANTE
                        </button>
                    </div>
                </div>
                <div class="p-4 grid grid-cols-1 md:grid-cols-2 gap-3">
                    ${estantesHTML}
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
