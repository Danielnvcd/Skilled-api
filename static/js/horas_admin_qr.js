// horas_admin_qr.js v=2
const csrfToken = document.querySelector('meta[name="csrf-token"]').content;
let pendingRegenId = null;

// ─── Generar / Regenerar QR ──────────────────────────────────────────────────

async function generarQR(trabajadorId) {
    const formData = new FormData();
    formData.append('csrf_token', csrfToken);
    formData.append('trabajador_id', trabajadorId);

    let data;
    try {
        const res = await fetch('/horas/admin/qr/generar', { method: 'POST', body: formData });
        data = await res.json();
    } catch {
        alert('Error de conexión al generar el QR.');
        return;
    }

    if (!data.ok) {
        alert(data.error || 'Error al generar QR');
        return;
    }

    // Actualizar celda de imagen en la tabla
    const qrCell = document.getElementById(`qr-cell-${trabajadorId}`);
    if (qrCell) {
        const img = document.createElement('img');
        img.src = data.imagen_url;
        img.alt = 'QR';
        img.className = 'qr-img-thumb';
        qrCell.innerHTML = '';
        qrCell.appendChild(img);
    }

    // Actualizar celda de acciones (reemplaza "Generar" por "Regenerar" + "Imprimir")
    const accCell = document.getElementById(`acc-cell-${trabajadorId}`);
    if (accCell) {
        const nombre = accCell.dataset.nombre || '';
        accCell.innerHTML = `
            <div class="flex gap-2 flex-wrap">
                <button
                    data-action="regenerar"
                    data-id="${trabajadorId}"
                    data-nombre="${nombre}"
                    class="px-3 py-1.5 text-xs font-semibold bg-amber-50 text-amber-700 rounded-lg hover:bg-amber-100 transition-colors border-0 cursor-pointer">
                    Regenerar
                </button>
                <a href="/horas/admin/qr/print/${trabajadorId}" target="_blank"
                    class="px-3 py-1.5 text-xs font-semibold bg-green-50 text-green-700 rounded-lg hover:bg-green-100 transition-colors no-underline">
                    Imprimir
                </a>
            </div>
        `;
    }

    // Mostrar modal con QR generado
    const modalImg = document.getElementById('modal-qr-img');
    const modalNombre = document.getElementById('modal-qr-nombre');
    modalImg.src = data.imagen_url;
    modalNombre.textContent = accCell ? accCell.dataset.nombre : '';
    document.getElementById('modal-qr-generado').classList.remove('hidden');
}

// ─── Delegación de eventos en la tabla ───────────────────────────────────────

document.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-action]');
    if (!btn) return;

    const action = btn.dataset.action;
    const id = btn.dataset.id;

    if (action === 'generar') {
        generarQR(id);
    } else if (action === 'regenerar') {
        pendingRegenId = id;
        document.getElementById('modal-confirmar-nombre').textContent = btn.dataset.nombre || '';
        document.getElementById('modal-confirmar').classList.remove('hidden');
    }
});

// ─── Modal: Confirmar regenerar ──────────────────────────────────────────────

document.getElementById('btn-cancelar-regen').addEventListener('click', () => {
    pendingRegenId = null;
    document.getElementById('modal-confirmar').classList.add('hidden');
});

document.getElementById('btn-confirmar-regen').addEventListener('click', () => {
    document.getElementById('modal-confirmar').classList.add('hidden');
    if (pendingRegenId) {
        const id = pendingRegenId;
        pendingRegenId = null;
        generarQR(id);
    }
});

// ─── Modal: Cerrar QR generado ───────────────────────────────────────────────

document.getElementById('btn-cerrar-qr-modal').addEventListener('click', () => {
    document.getElementById('modal-qr-generado').classList.add('hidden');
});

// ─── Búsqueda ────────────────────────────────────────────────────────────────

(function () {
    const input = document.getElementById('qr-search');
    const countEl = document.getElementById('search-count');
    if (!input) return;

    // Recopilar datos de cada fila una sola vez para no releer el DOM en cada keystroke
    const rows = Array.from(document.querySelectorAll('tbody tr[id^="row-"]')).map(row => {
        const cells = row.querySelectorAll('td');
        return {
            el: row,
            // No.Emp | Nombre | Área — todo en minúsculas para comparar
            text: Array.from(cells).slice(0, 3).map(td => td.textContent.trim().toLowerCase()).join(' ')
        };
    });

    const total = rows.length;

    input.addEventListener('input', () => {
        const q = input.value.trim().toLowerCase();
        let visible = 0;

        rows.forEach(({ el, text }) => {
            const match = !q || text.includes(q);
            el.style.display = match ? '' : 'none';
            if (match) visible++;
        });

        if (q) {
            countEl.textContent = `${visible} de ${total} trabajadores`;
            countEl.classList.remove('hidden');
        } else {
            countEl.classList.add('hidden');
        }
    });
})();
