document.addEventListener("DOMContentLoaded", () => {

    // ─── State ───────────────────────────────────────────────────
    let html5Qrcode = null;
    let selectedTipo = 'ENTRADA';
    let currentEstanteId = null;
    let globalProductos = [];

    // ─── DOM Refs ─────────────────────────────────────────────────
    const viewScanner = document.getElementById('view-scanner');
    const viewForm    = document.getElementById('view-form');
    const toast       = document.getElementById('toast');
    const loadingOv   = document.getElementById('loading-overlay');

    const btnScan       = document.getElementById('btn-scan');
    const btnStop       = document.getElementById('btn-stop-scan');
    const btnCloseScan  = document.getElementById('btn-close-scanner'); // Nuevo botón X
    const modalScanner  = document.getElementById('modal-scanner');   // Nuevo modal
    const qrReaderDiv   = document.getElementById('qr-reader');
    const btnBack       = document.getElementById('btn-back');
    const btnSave       = document.getElementById('btn-save');
    const btnScanAnother = document.getElementById('btn-scan-another');
    const btnRefresh    = document.getElementById('btn-refresh');

    const estanteNombreEl = document.getElementById('almacen-nombre');
    const estanteBadgeEl  = document.getElementById('almacen-badge');
    const prodSelect      = document.getElementById('producto_id');
    const cantidadInput   = document.getElementById('cantidad');
    const tipoHidden      = document.getElementById('tipo');
    const tipoChips       = document.querySelectorAll('.tipo-chip');

    // ─── Toast / Loading ─────────────────────────────────────────
    function showToast(msg, type = 'info') {
        toast.textContent = msg;
        toast.className = `toast ${type} show`;
        setTimeout(() => { toast.className = 'toast'; }, 3000);
    }
    function setLoading(on) { loadingOv.classList.toggle('hidden', !on); }

    // ─── Views ────────────────────────────────────────────────────
    function showView(view) {
        viewScanner.classList.remove('active');
        viewForm.classList.remove('active');
        view.classList.add('active');
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

    // ─── Scanner — Reutilizar misma instancia
    async function startScanner() {
        modalScanner.classList.remove('hidden');
        qrReaderDiv.classList.remove('hidden');
        btnStop.classList.remove('hidden');
        document.body.style.overflow = 'hidden'; // Bloquear scroll del fondo

        if (!html5Qrcode) {
            html5Qrcode = new Html5Qrcode("qr-reader");
        }

        const config = { fps: 10, qrbox: { width: 250, height: 250 } };

        try {
            await html5Qrcode.start(
                { facingMode: "environment" }, // Cámara trasera
                config,
                onScanSuccess,
                (_) => {} // Error silencioso por frame
            );
        } catch (err) {
            console.error(err);
            showToast('Esperando acceso a la cámara...', 'info');
        }
    }

    async function stopScanner() {
        if (html5Qrcode && html5Qrcode.isScanning) {
            try { await html5Qrcode.stop(); } catch(_) {}
        }
        modalScanner.classList.add('hidden');
        document.body.style.overflow = ''; // Restaurar scroll
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
                currentEstanteId = estante.id;
                estanteNombreEl.textContent = estante.nombre;
                estanteBadgeEl.textContent = `${estante.almacen_id ? 'Almacén #' + estante.almacen_id : 'Estante'}`;
                
                const catLocal = estante.descripcion;
                const filteredProductos = catLocal ? globalProductos.filter(p => p.categoria === catLocal) : globalProductos;
                
                if (!filteredProductos.length) {
                    prodSelect.innerHTML = `<option value="">Sin productos en categoría: ${catLocal || 'General'}</option>`;
                } else {
                    prodSelect.innerHTML = filteredProductos.map(p =>
                        `<option value="${p.id}">[${p.codigo}] ${p.descripcion} — Stock: ${parseFloat(p.stock_actual).toFixed(1)} ${p.unidad}</option>`
                    ).join('');
                }

                cantidadInput.value = '';
                // Reset tipo chips
                tipoChips.forEach(c => c.className = 'tipo-chip');
                tipoChips[0].classList.add('active-entrada');
                selectedTipo = 'ENTRADA';
                tipoHidden.value = 'ENTRADA';
                showView(viewForm);
            } else {
                showToast('QR no válido. Escanea un estante del sistema.', 'error');
                btnScan.classList.remove('hidden');
            }
        } catch (e) {
            showToast('Error de conexión', 'error');
            btnScan.classList.remove('hidden');
        } finally {
            setLoading(false);
        }
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
            estante_id: currentEstanteId,
            motivo: `Escaneo móvil — Estante #${currentEstanteId}`
        };

        setLoading(true);
        try {
            const res = await fetch('/api/v1/movimientos/', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            if (res.ok) {
                showToast('✔ Movimiento registrado', 'success');
                // Permanecer en la misma pantalla de estante y solo limpiar entradas
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
        currentEstanteId = null;
        showView(viewScanner);
    }

    // ─── Event Listeners ─────────────────────────────────────────
    btnScan.addEventListener('click', startScanner);
    btnStop.addEventListener('click', stopScanner);
    btnCloseScan.addEventListener('click', stopScanner); // Listener para la X
    btnBack.addEventListener('click', backToScanner);
    btnScanAnother.addEventListener('click', () => {
        backToScanner();
        startScanner(); // Re-abrir cámara automáticamente
    });
    btnSave.addEventListener('click', saveMovimiento);
    if (btnRefresh) {
        btnRefresh.addEventListener('click', () => window.location.reload());
    }

    // ─── Init ─────────────────────────────────────────────────────
    loadProducts();
});
