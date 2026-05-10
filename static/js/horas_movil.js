// horas_movil.js v=3
const csrfToken = document.querySelector('meta[name="csrf-token"]').content;
const datos = JSON.parse(document.getElementById('data-movil').textContent);

let html5QrCode = null;
let selectedReporteId = null;
let isProcessing = false;
let overlayTimer = null;

// ─── Vistas ──────────────────────────────────────────────────────────────────

function showView(id) {
    document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
    document.getElementById(id).classList.add('active');
    window.scrollTo(0, 0);
}

// ─── Toast ───────────────────────────────────────────────────────────────────

function showToast(msg) {
    const t = document.getElementById('toast');
    t.textContent = msg;
    t.classList.add('show');
    setTimeout(() => t.classList.remove('show'), 3000);
}

// ─── Loading ─────────────────────────────────────────────────────────────────

function setLoading(on) {
    document.getElementById('loading-overlay').classList.toggle('hidden', !on);
}

// ─── Sheet de selección de reporte ───────────────────────────────────────────

function abrirSheet() {
    const lista = document.getElementById('sheet-reportes-lista');
    lista.innerHTML = '';

    if (datos.reportes.length === 0) {
        lista.innerHTML = `
            <div style="text-align:center; padding:24px 0; color:#94a3b8;">
                <i class="fas fa-folder-open" style="font-size:2rem; display:block; margin-bottom:10px;"></i>
                <p style="font-size:0.9rem; font-weight:500;">Sin reportes abiertos</p>
                <p style="font-size:0.8rem; margin-top:4px;">Crea uno en <strong>Reporte de Horas</strong>.</p>
            </div>`;
    } else {
        const conHoy = datos.reportes.filter(r => r.hoy);
        const sinHoy = datos.reportes.filter(r => !r.hoy);

        if (conHoy.length === 0) {
            const aviso = document.createElement('div');
            aviso.style.cssText = 'background:#fef9c3; color:#92400e; border-radius:10px; padding:10px 14px; font-size:0.82rem; margin-bottom:12px;';
            aviso.innerHTML = '<strong>⚠ Ningún reporte cubre la fecha de hoy.</strong> Crea uno en Reporte de Horas.';
            lista.appendChild(aviso);
        }

        // Reportes de esta semana primero, habilitados
        conHoy.forEach(r => {
            const btn = document.createElement('button');
            btn.className = 'reporte-sheet-item';
            btn.innerHTML = `
                <div style="display:flex; align-items:center; justify-content:space-between; gap:8px;">
                    <span class="reporte-sheet-numero">${r.numero}</span>
                    <span style="background:#dcfce7; color:#166534; font-size:0.68rem; font-weight:700; padding:2px 8px; border-radius:20px;">ESTA SEMANA</span>
                </div>
                <span class="reporte-sheet-nombre">${r.nombre || '(Sin nombre)'}</span>
                <span class="reporte-sheet-fecha">${r.inicio} &mdash; ${r.fin}</span>`;
            btn.addEventListener('click', () => seleccionarReporte(r));
            lista.appendChild(btn);
        });

        // Reportes de otras semanas: visibles pero deshabilitados
        if (sinHoy.length > 0) {
            const sep = document.createElement('p');
            sep.style.cssText = 'font-size:0.73rem; color:#94a3b8; font-weight:600; text-transform:uppercase; letter-spacing:0.05em; margin:12px 0 6px;';
            sep.textContent = 'Otras semanas (solo lectura)';
            lista.appendChild(sep);

            sinHoy.forEach(r => {
                const btn = document.createElement('button');
                btn.className = 'reporte-sheet-item';
                btn.disabled = true;
                btn.style.opacity = '0.5';
                btn.style.cursor = 'default';
                btn.innerHTML = `
                    <div style="display:flex; align-items:center; justify-content:space-between; gap:8px;">
                        <span class="reporte-sheet-numero">${r.numero}</span>
                    </div>
                    <span class="reporte-sheet-nombre">${r.nombre || '(Sin nombre)'}</span>
                    <span class="reporte-sheet-fecha">${r.inicio} &mdash; ${r.fin}</span>`;
                lista.appendChild(btn);
            });
        }
    }

    document.getElementById('sheet-reporte').classList.remove('hidden');
}

function cerrarSheet() {
    document.getElementById('sheet-reporte').classList.add('hidden');
}

function seleccionarReporte(r) {
    selectedReporteId = r.id;
    document.getElementById('scan-proyecto-nombre').textContent = `${r.numero} · ${r.nombre || '(Sin nombre)'}`;
    document.getElementById('scan-semana-label').textContent = `${r.inicio} — ${r.fin}`;
    cerrarSheet();
    showView('view-scanner');
    iniciarCamara();
}

// ─── Cámara ──────────────────────────────────────────────────────────────────

function iniciarCamara() {
    if (!html5QrCode) {
        html5QrCode = new Html5Qrcode('qr-reader');
    }
    html5QrCode.start(
        { facingMode: 'environment' },
        { fps: 10, qrbox: { width: 240, height: 240 } },
        (decodedText) => {
            if (!isProcessing) {
                isProcessing = true;
                procesarQR(decodedText);
            }
        },
        () => {}
    ).catch(() => {
        showToast('No se pudo acceder a la cámara. Verifica los permisos.');
        detenerYVolver();
    });
}

function detenerCamara() {
    if (html5QrCode) {
        const inst = html5QrCode;
        html5QrCode = null;
        inst.stop().catch(() => {});
    }
}

function detenerYVolver() {
    clearTimeout(overlayTimer);
    isProcessing = false;
    detenerCamara();
    document.getElementById('scan-overlay').classList.add('hidden');
    showView('view-menu');
}

// ─── QR Check ────────────────────────────────────────────────────────────────

async function procesarQR(decodedText) {
    setLoading(true);
    try {
        const body = { qr_code: decodedText };
        if (selectedReporteId) body.reporte_id = selectedReporteId;

        const res = await fetch('/horas/qr_check', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken,
                'X-Requested-With': 'XMLHttpRequest'
            },
            body: JSON.stringify(body)
        });
        const data = await res.json();
        mostrarOverlay(res.status, data);
    } catch {
        mostrarOverlay(500, { ok: false, error: 'Error de conexión. Intenta de nuevo.' });
    } finally {
        setLoading(false);
    }
}

// ─── Overlay de resultado ────────────────────────────────────────────────────

function mostrarOverlay(status, data) {
    const overlay  = document.getElementById('scan-overlay');
    const icon     = document.getElementById('overlay-icon');
    const tipo     = document.getElementById('overlay-tipo');
    const nombre   = document.getElementById('overlay-nombre');
    const hora     = document.getElementById('overlay-hora');
    const errorEl  = document.getElementById('overlay-error');

    icon.className = 'fas scan-overlay-icon';
    tipo.className = 'scan-overlay-tipo';
    errorEl.textContent = '';
    hora.textContent = '';
    nombre.textContent = '';

    if (data.ok) {
        if (data.tipo === 'ENTRADA') {
            icon.classList.add('fa-sign-in-alt', 'entrada');
            tipo.classList.add('tipo-entrada');
        } else {
            icon.classList.add('fa-sign-out-alt', 'salida');
            tipo.classList.add('tipo-salida');
        }
        tipo.textContent = data.tipo;
        nombre.textContent = data.nombre;
        hora.textContent = data.hora;
    } else {
        icon.classList.add('fa-times-circle', 'error');
        tipo.classList.add('tipo-error');
        tipo.textContent = status === 409 ? 'YA REGISTRADO' : 'ERROR';
        errorEl.textContent = data.error || 'No se pudo registrar.';
    }

    overlay.classList.remove('hidden');
    clearTimeout(overlayTimer);
    // Después de 2s ocultar overlay y permitir siguiente escaneo
    overlayTimer = setTimeout(() => {
        overlay.classList.add('hidden');
        isProcessing = false;
    }, 2000);
}

// ─── Eventos ─────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
    // Recargar página
    document.getElementById('btn-refresh').addEventListener('click', () => window.location.reload());

    // Abrir selección de reporte
    document.getElementById('btn-abrir-scanner').addEventListener('click', abrirSheet);

    // Cerrar sheet (botón o fondo)
    document.getElementById('btn-cancelar-reporte').addEventListener('click', cerrarSheet);
    document.getElementById('sheet-reporte').addEventListener('click', e => {
        if (e.target === document.getElementById('sheet-reporte')) cerrarSheet();
    });

    // Cambiar reporte desde scanner (detiene cámara, abre sheet)
    document.getElementById('btn-cambiar-reporte').addEventListener('click', () => {
        clearTimeout(overlayTimer);
        isProcessing = false;
        detenerCamara();
        document.getElementById('scan-overlay').classList.add('hidden');
        showView('view-menu');
        abrirSheet();
    });

    // Volver al menú (flecha)
    document.getElementById('btn-back-menu').addEventListener('click', detenerYVolver);

    // Detener escaneo (botón rojo)
    document.getElementById('btn-detener').addEventListener('click', detenerYVolver);
});
