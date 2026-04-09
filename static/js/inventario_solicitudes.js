document.addEventListener('DOMContentLoaded', () => {
    const CSRF_TOKEN = document.querySelector('meta[name="csrf-token"]').getAttribute('content');
    let currentRequests = [];
    let activeRequest = null;

    // Elementos DOM
    const requestsContainer = document.getElementById('requestsContainer');
    const modalDetail = document.getElementById('modalDetail');
    const btnCloseDetail = document.getElementById('btnCloseDetail');
    const btnApprove = document.getElementById('btnApprove');
    const btnReject = document.getElementById('btnReject');
    const btnRefresh = document.querySelector('.header-section button'); // El botón de Actualizar

    async function loadRequests() {
        if (!requestsContainer) return;
        try {
            const res = await fetch('/api/v1/solicitudes/');
            currentRequests = await res.json();
            renderRequests(currentRequests);
        } catch(e) {
            requestsContainer.innerHTML = '<p class="text-center text-rose-500 py-10">Error cargando datos</p>';
        }
    }

    function renderRequests(list) {
        if(!list.length) {
            requestsContainer.innerHTML = '<div class="text-center py-20 text-gray-400"><p class="text-lg mb-1">No hay solicitudes registradas.</p><p class="text-xs">Las nuevas peticiones aparecerán aquí automáticamente.</p></div>';
            return;
        }

        requestsContainer.innerHTML = list.map(s => {
            const date = new Date(s.fecha_creacion).toLocaleDateString('es-MX', { day:'2-digit', month:'short', hour:'2-digit', minute:'2-digit' });
            const statusClass = `status-${s.estatus.toLowerCase()}`;
            
            return `
            <div class="request-card flex items-center justify-between p-5 bg-white/40 border border-gray-100/50 rounded-2xl cursor-pointer" data-id="${s.id}">
                <div class="flex items-center gap-4">
                    <div class="w-12 h-12 rounded-xl bg-gradient-to-tr from-gray-100 to-gray-50 flex items-center justify-center text-gray-400">
                        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/></svg>
                    </div>
                    <div>
                        <p class="text-sm font-black text-gray-900 leading-tight">Solicitud #${s.id}</p>
                        <p class="text-[11px] font-bold text-gray-500 uppercase tracking-tight mt-0.5">${s.solicitante_nombre} · Proyecto: ${s.proyecto || 'General'}</p>
                    </div>
                </div>
                <div class="flex items-center gap-6">
                    <div class="text-right">
                        <p class="text-[11px] font-bold text-gray-400 capitalize">${date}</p>
                        <p class="text-[10px] font-black text-indigo-500 uppercase tracking-widest mt-0.5">${s.detalles.length} MATERIALES</p>
                    </div>
                    <span class="status-badge ${statusClass}">${s.estatus}</span>
                    <button class="w-8 h-8 rounded-full hover:bg-white flex items-center justify-center text-gray-300 hover:text-indigo-600 transition-all">
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><polyline points="9 18 15 12 9 6"/></svg>
                    </button>
                </div>
            </div>`;
        }).join('');

        // Agregar eventos de clic (CSP Compliance)
        requestsContainer.querySelectorAll('.request-card').forEach(card => {
            card.addEventListener('click', () => {
                const id = parseInt(card.getAttribute('data-id'));
                openDetail(id);
            });
        });
    }

    function openDetail(id) {
        activeRequest = currentRequests.find(s => s.id === id);
        if(!activeRequest) return;

        document.getElementById('detailTitle').textContent = `Solicitud #${activeRequest.id} de ${activeRequest.solicitante_nombre}`;
        document.getElementById('detailSubtitle').textContent = `ID: #${activeRequest.id} · PROYECTO: ${activeRequest.proyecto || 'GENERAL'}`;
        
        const tbody = document.getElementById('detailTableBody');
        tbody.innerHTML = activeRequest.detalles.map(d => `
            <tr>
                <td class="px-5 py-4">
                    <p class="text-sm font-bold text-gray-800">${d.producto_descripcion}</p>
                    <p class="text-[10px] font-black text-gray-400 uppercase mt-0.5">${d.producto_codigo}</p>
                </td>
                <td class="px-5 py-4 text-right">
                    <span class="text-lg font-black text-indigo-600">${d.cantidad_solicitada}</span>
                    <span class="text-[10px] font-bold text-gray-400 uppercase ml-1">unidades</span>
                </td>
            </tr>
        `).join('');

        const isPending = activeRequest.estatus === 'PENDIENTE';
        document.getElementById('actionButtons').classList.toggle('hidden', !isPending);
        document.getElementById('doneMessage').classList.toggle('hidden', isPending);

        modalDetail.classList.remove('hidden');
    }

    async function updateStatus(newStatus) {
        if(!activeRequest) return;
        
        try {
            btnApprove.disabled = btnReject.disabled = true;
            const res = await fetch(`/api/v1/solicitudes/${activeRequest.id}/estado`, {
                method: 'PATCH',
                headers: { 
                    'Content-Type': 'application/json',
                    'X-CSRF-Token': CSRF_TOKEN
                },
                body: JSON.stringify({ estatus: newStatus })
            });

            if(res.ok) {
                modalDetail.classList.add('hidden');
                loadRequests();
            } else {
                alert("No se pudo actualizar el estado");
            }
        } catch(e) {
            alert("Error de conexión");
        } finally {
            btnApprove.disabled = btnReject.disabled = false;
        }
    }

    // Event Listeners (CSP Compliance)
    if(btnRefresh) btnRefresh.addEventListener('click', loadRequests);
    if(btnCloseDetail) btnCloseDetail.onclick = () => modalDetail.classList.add('hidden');
    if(btnApprove) btnApprove.onclick = () => updateStatus('APROBADA');
    if(btnReject) btnReject.onclick = () => updateStatus('RECHAZADA');

    loadRequests();
});
