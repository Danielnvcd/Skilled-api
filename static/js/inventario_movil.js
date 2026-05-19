document.addEventListener("DOMContentLoaded", () => {

    const CSRF_TOKEN = document.querySelector('meta[name="csrf-token"]').getAttribute('content');

    // ─── State ───────────────────────────────────────────────────
    let html5Qrcode = null;
    let selectedTipo = 'ENTRADA';
    let currentEstante = null;          // objeto completo {id, nombre, descripcion, almacen_id, ...}
    let globalProductos = [];
    let productosEstante = [];          // productos filtrados por categoría del estante actual

    // ─── DOM Refs ─────────────────────────────────────────────────
    const viewScanner   = document.getElementById('view-scanner');
    const viewMenu      = document.getElementById('view-menu');
    const viewProducts  = document.getElementById('view-products');
    const viewForm      = document.getElementById('view-form');
    const toast         = document.getElementById('toast');
    const loadingOv     = document.getElementById('loading-overlay');

    const btnScan        = document.getElementById('btn-scan');
    const btnStop        = document.getElementById('btn-stop-scan');
    const btnCloseScan   = document.getElementById('btn-close-scanner');
    const modalScanner   = document.getElementById('modal-scanner');
    const qrReaderDiv    = document.getElementById('qr-reader');
    const btnBack        = document.getElementById('btn-back');
    const btnMenuBack    = document.getElementById('btn-menu-back');
    const btnProductsBack = document.getElementById('btn-products-back');
    const btnSave        = document.getElementById('btn-save');
    const btnScanAnother = document.getElementById('btn-scan-another');
    const btnMenuScanOther = document.getElementById('btn-menu-scan-other');
    const btnRefresh     = document.getElementById('btn-refresh');

    // Form view
    const estanteNombreEl = document.getElementById('almacen-nombre');
    const estanteBadgeEl  = document.getElementById('almacen-badge');
    const prodSelect      = document.getElementById('producto_id');
    const cantidadInput   = document.getElementById('cantidad');
    const tipoHidden      = document.getElementById('tipo');
    const tipoChips       = document.querySelectorAll('.tipo-chip');

    // Menu view
    const menuEstanteNombre = document.getElementById('menu-estante-nombre');
    const menuEstanteDesc   = document.getElementById('menu-estante-desc');
    const menuAlmacenBadge  = document.getElementById('menu-almacen-badge');
    const menuCountProd     = document.getElementById('menu-count-productos');
    const menuCards         = document.querySelectorAll('.menu-card');

    // Products view
    const productsList      = document.getElementById('products-list');
    const productsEmpty     = document.getElementById('products-empty');
    const productsSearch    = document.getElementById('products-search');
    const productsHeader    = document.getElementById('products-header-title');

    // Imagen placeholder cuando no hay imagen_url
    const IMG_PLACEHOLDER = "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64' fill='none' stroke='%239ca3af' stroke-width='2'><rect x='8' y='12' width='48' height='40' rx='4'/><path d='M8 40l12-12 16 16 8-8 12 12'/><circle cx='22' cy='24' r='4'/></svg>";

    // ─── Toast / Loading ─────────────────────────────────────────
    function showToast(msg, type = 'info') {
        toast.textContent = msg;
        toast.className = `toast ${type} show`;
        setTimeout(() => { toast.className = 'toast'; }, 3000);
    }
    function setLoading(on) { loadingOv.classList.toggle('hidden', !on); }

    // ─── Views ────────────────────────────────────────────────────
    function showView(view) {
        [viewScanner, viewMenu, viewProducts, viewForm].forEach(v => v.classList.remove('active'));
        view.classList.add('active');
        window.scrollTo({ top: 0 });
    }

    // ─── Tipo chips ───────────────────────────────────────────────
    tipoChips.forEach(chip => {
        chip.addEventListener('click', () => {
            tipoChips.forEach(c => c.className = 'tipo-chip');
            selectedTipo = chip.dataset.tipo;
            tipoHidden.value = selectedTipo;
            if (selectedTipo === 'ENTRADA') chip.classList.add('active-entrada');
            else if (selectedTipo === 'SALIDA') chip.classList.add('active-salida');
            else chip.classList.add('active-ajuste');
        });
    });

    // ─── Load Products ────────────────────────────────────────────
    async function loadProducts() {
        try {
            const res = await fetch('/api/v1/productos/');
            if (res.ok) {
                globalProductos = await res.json();
            }
        } catch (e) {
            console.error('Error cargando productos:', e);
        }
    }

    // ─── Scanner ──────────────────────────────────────────────────
    async function startScanner() {
        modalScanner.classList.remove('hidden');
        qrReaderDiv.classList.remove('hidden');
        btnStop.classList.remove('hidden');
        document.body.style.overflow = 'hidden';

        if (!html5Qrcode) {
            html5Qrcode = new Html5Qrcode("qr-reader");
        }

        const config = { fps: 10, qrbox: { width: 250, height: 250 } };

        try {
            await html5Qrcode.start(
                { facingMode: "environment" },
                config,
                onScanSuccess,
                (_) => {}
            );
        } catch (err) {
            console.error(err);
            showToast('Esperando acceso a la cámara...', 'info');
        }
    }

    async function stopScanner() {
        if (html5Qrcode && html5Qrcode.isScanning) {
            try { await html5Qrcode.stop(); } catch (_) {}
        }
        modalScanner.classList.add('hidden');
        document.body.style.overflow = '';
    }

    async function onScanSuccess(decodedText) {
        await stopScanner();
        validarEstante(decodedText);
    }

    async function validarEstante(qr_code) {
        setLoading(true);
        try {
            const res = await fetch(`/api/v1/estantes/${qr_code}/validar`);
            if (res.ok) {
                const estante = await res.json();
                currentEstante = estante;

                // Filtrar productos del estante usando la descripción como categoría
                const catLocal = estante.descripcion;
                productosEstante = catLocal
                    ? globalProductos.filter(p => p.categoria === catLocal)
                    : globalProductos.slice();

                // Llenar la vista de menú
                menuEstanteNombre.textContent = estante.nombre;
                menuAlmacenBadge.textContent = estante.almacen_id ? `Almacén #${estante.almacen_id}` : 'Estante';
                menuEstanteDesc.textContent = catLocal ? `Categoría: ${catLocal}` : 'Catálogo general';
                menuCountProd.textContent = `${productosEstante.length} ${productosEstante.length === 1 ? 'producto' : 'productos'}`;

                showView(viewMenu);
            } else {
                showToast('QR no válido. Escanea un estante del sistema.', 'error');
            }
        } catch (e) {
            showToast('Error de conexión', 'error');
        } finally {
            setLoading(false);
        }
    }

    // ─── Menu actions ─────────────────────────────────────────────
    menuCards.forEach(card => {
        card.addEventListener('click', () => {
            const action = card.dataset.action;
            if (action === 'ver-productos') {
                renderProductsList();
                showView(viewProducts);
            } else {
                // ENTRADA / SALIDA / AJUSTE → abrir form preconfigurado
                openMovementForm(action);
            }
        });
    });

    // ─── Form view ────────────────────────────────────────────────
    function openMovementForm(tipo, preselectProductoId = null) {
        if (!currentEstante) return;

        estanteNombreEl.textContent = currentEstante.nombre;
        estanteBadgeEl.textContent = currentEstante.almacen_id
            ? `Almacén #${currentEstante.almacen_id}`
            : 'Estante';

        // Llenar select de productos
        if (!productosEstante.length) {
            prodSelect.innerHTML = `<option value="">Sin productos disponibles</option>`;
        } else {
            prodSelect.innerHTML = productosEstante.map(p =>
                `<option value="${p.id}">[${p.codigo}] ${p.descripcion} — Stock: ${parseFloat(p.stock_actual).toFixed(1)} ${p.unidad}</option>`
            ).join('');
        }

        if (preselectProductoId) prodSelect.value = preselectProductoId;

        cantidadInput.value = '';

        // Set tipo
        tipoChips.forEach(c => c.className = 'tipo-chip');
        const chipToActivate = Array.from(tipoChips).find(c => c.dataset.tipo === tipo);
        if (chipToActivate) {
            if (tipo === 'ENTRADA') chipToActivate.classList.add('active-entrada');
            else if (tipo === 'SALIDA') chipToActivate.classList.add('active-salida');
            else chipToActivate.classList.add('active-ajuste');
        }
        selectedTipo = tipo;
        tipoHidden.value = tipo;

        showView(viewForm);
    }

    // ─── Products list rendering ─────────────────────────────────
    function renderProductsList(filterText = '') {
        productsHeader.textContent = currentEstante ? currentEstante.nombre : 'Productos';

        const q = filterText.trim().toLowerCase();
        const list = q
            ? productosEstante.filter(p =>
                (p.codigo || '').toLowerCase().includes(q) ||
                (p.descripcion || '').toLowerCase().includes(q))
            : productosEstante;

        if (!list.length) {
            productsList.innerHTML = '';
            productsEmpty.classList.remove('hidden');
            return;
        }
        productsEmpty.classList.add('hidden');

        productsList.innerHTML = list.map(p => {
            const stockActual = parseFloat(p.stock_actual) || 0;
            const stockMin = parseFloat(p.stock_minimo) || 0;
            const isLow = stockActual <= stockMin;
            const stockClass = isLow ? 'stock-low' : 'stock-ok';
            const imgSrc = p.imagen_url || IMG_PLACEHOLDER;
            return `
                <button type="button" class="product-item" data-id="${p.id}">
                    <div class="product-img-wrap">
                        <img src="${imgSrc}" alt="${p.descripcion}" loading="lazy"
                             onerror="this.src='${IMG_PLACEHOLDER}'">
                    </div>
                    <div class="product-info">
                        <span class="product-codigo">${p.codigo || '—'}</span>
                        <span class="product-desc">${p.descripcion || '—'}</span>
                        <span class="product-stock ${stockClass}">
                            ${stockActual.toFixed(1)} ${p.unidad || ''}
                            ${isLow ? '<span class="stock-badge">BAJO</span>' : ''}
                        </span>
                    </div>
                    <svg class="product-chevron" width="20" height="20" viewBox="0 0 24 24" fill="none"
                        stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <polyline points="9 18 15 12 9 6"/>
                    </svg>
                </button>
            `;
        }).join('');

        // Tap en producto → abrir form con producto preseleccionado y tipo ENTRADA por defecto
        productsList.querySelectorAll('.product-item').forEach(item => {
            item.addEventListener('click', () => {
                const id = parseInt(item.dataset.id);
                openMovementForm('ENTRADA', id);
            });
        });
    }

    // ─── Save Movement ────────────────────────────────────────────
    async function saveMovimiento() {
        const producto_id = parseInt(prodSelect.value);
        const cantidad    = parseFloat(cantidadInput.value);
        const tipo        = tipoHidden.value;

        if (!producto_id) { showToast('Selecciona un producto', 'error'); return; }
        if (!cantidad || cantidad <= 0) { showToast('Ingresa una cantidad válida', 'error'); return; }

        const payload = {
            tipo,
            producto_id,
            cantidad,
            estante_id: currentEstante ? currentEstante.id : null,
            motivo: `Escaneo móvil — Estante #${currentEstante ? currentEstante.id : '—'}`
        };

        setLoading(true);
        try {
            const res = await fetch('/api/v1/movimientos/', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': CSRF_TOKEN },
                body: JSON.stringify(payload)
            });
            if (res.ok) {
                showToast('✔ Movimiento registrado', 'success');
                // Refrescar el stock local del producto afectado para que se vea actualizado
                await loadProducts();
                productosEstante = currentEstante && currentEstante.descripcion
                    ? globalProductos.filter(p => p.categoria === currentEstante.descripcion)
                    : globalProductos.slice();

                cantidadInput.value = '';
                prodSelect.value = '';
            } else {
                const data = await res.json();
                showToast('Error: ' + (data.detail || 'No se pudo registrar'), 'error');
            }
        } catch (e) {
            showToast('Error de conexión', 'error');
        } finally {
            setLoading(false);
        }
    }

    function backToScanner() {
        currentEstante = null;
        productosEstante = [];
        showView(viewScanner);
    }

    function backToMenu() {
        if (currentEstante) showView(viewMenu);
        else backToScanner();
    }

    // ─── Event Listeners ─────────────────────────────────────────
    btnScan.addEventListener('click', startScanner);
    btnStop.addEventListener('click', stopScanner);
    btnCloseScan.addEventListener('click', stopScanner);

    btnBack.addEventListener('click', backToMenu);            // form → menu
    btnMenuBack.addEventListener('click', backToScanner);     // menu → scanner
    btnProductsBack.addEventListener('click', backToMenu);    // products → menu

    btnScanAnother.addEventListener('click', () => {
        backToScanner();
        startScanner();
    });
    btnMenuScanOther.addEventListener('click', () => {
        backToScanner();
        startScanner();
    });

    btnSave.addEventListener('click', saveMovimiento);
    if (btnRefresh) {
        btnRefresh.addEventListener('click', () => window.location.reload());
    }

    if (productsSearch) {
        let searchTimer = null;
        productsSearch.addEventListener('input', (e) => {
            clearTimeout(searchTimer);
            searchTimer = setTimeout(() => renderProductsList(e.target.value), 150);
        });
    }

    // ─── Init ─────────────────────────────────────────────────────
    loadProducts();
});
