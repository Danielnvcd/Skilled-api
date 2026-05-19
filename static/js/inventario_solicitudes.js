document.addEventListener('DOMContentLoaded', () => {
    const CSRF_TOKEN = document.querySelector('meta[name="csrf-token"]').getAttribute('content');

    let allRequests    = [];
    let activeRequest  = null;
    let activeFilter   = 'Todas';
    let searchQuery    = '';

    // ─── ELEMENTOS DOM ─────────────────────────────────────────
    const requestsContainer = document.getElementById('requestsContainer');
    const modalDetail       = document.getElementById('modalDetail');
    const btnCloseDetail    = document.getElementById('btnCloseDetail');
    const btnApprove        = document.getElementById('btnApprove');
    const btnReject         = document.getElementById('btnReject');
    const btnDeliver        = document.getElementById('btnDeliver');
    const deliverButton     = document.getElementById('deliverButton');
    const btnRefresh        = document.getElementById('btnRefresh');
    const statsRow          = document.getElementById('statsRow');
    const filterTabsEl      = document.getElementById('filterTabs');
    const searchInput       = document.getElementById('searchSolicitudes');

    // ─── TOAST ─────────────────────────────────────────────────
    function showToast(msg, type = 'success') {
        const t = document.createElement('div');
        t.style.position = 'fixed'; t.style.bottom = '1.5rem'; t.style.right = '1.5rem'; t.style.zIndex = '9999';
        t.style.padding = '0.8rem 1.25rem'; t.style.borderRadius = '8px'; t.style.color = 'white';
        t.style.fontWeight = '600'; t.style.boxShadow = '0 4px 12px rgba(0,0,0,0.15)';
        t.style.background = type === 'error' ? '#ef4444' : '#10b981';
        t.innerHTML = `<span>${msg}</span>`;
        document.body.appendChild(t);
        setTimeout(() => t.remove(), 3500);
    }

    // ─── CARGA DE SOLICITUDES ──────────────────────────────────
    async function loadRequests() {
        requestsContainer.innerHTML = `
        <tr><td colspan="7">
            <div class="empty-state">
                <div class="empty-icon"><i class="fas fa-spinner fa-spin"></i></div>
                <div class="empty-title">Cargando solicitudes...</div>
            </div>
        </td></tr>`;
        try {
            const res = await fetch('/api/v1/solicitudes/');
            allRequests = await res.json();
            renderStats();
            renderFilterTabs();
            applyFilters();
        } catch(e) {
            requestsContainer.innerHTML = '<tr><td colspan="7"><div class="empty-state"><div class="empty-icon" style="color:#ef4444;"><i class="fas fa-exclamation-circle"></i></div><div class="empty-title">Error cargando datos.</div></div></td></tr>';
        }
    }

    // ─── STATS ─────────────────────────────────────────────────
    const STAT_DEFS = [
        { key:'total',     label:'Total', bg:'#f1f5f9', color:'#475569', icon:'<i class="fas fa-boxes"></i>', border:'#e2e8f0' },
        { key:'pendiente', label:'Pendientes', bg:'#fef3c7', color:'#d97706', icon:'<i class="fas fa-clock"></i>', border:'#fde68a' },
        { key:'aprobada',  label:'Aprobadas', bg:'#dcfce7', color:'#15803d', icon:'<i class="fas fa-check-double"></i>', border:'#bbf7d0' },
        { key:'rechazada', label:'Rechazadas', bg:'#fee2e2', color:'#b91c1c', icon:'<i class="fas fa-times-circle"></i>', border:'#fecaca' },
        { key:'entregada', label:'Entregadas', bg:'#dbeafe', color:'#1d4ed8', icon:'<i class="fas fa-box-open"></i>', border:'#bfdbfe' },
    ];

    function renderStats() {
        const counts = {
            total:     allRequests.length,
            pendiente: allRequests.filter(s => s.estatus === 'PENDIENTE').length,
            aprobada:  allRequests.filter(s => s.estatus === 'APROBADA').length,
            rechazada: allRequests.filter(s => s.estatus === 'RECHAZADA').length,
            entregada: allRequests.filter(s => s.estatus === 'ENTREGADA').length,
        };
        statsRow.innerHTML = STAT_DEFS.map(d => `
        <div class="stat-card" style="border-left-color: ${d.color};">
            <div class="stat-icon" style="background:${d.bg}; color:${d.color};">${d.icon}</div>
            <div>
                <div class="stat-val">${counts[d.key]}</div>
                <div class="stat-lbl">${d.label}</div>
            </div>
        </div>`).join('');
    }

    // ─── FILTER TABS ───────────────────────────────────────────
    const TABS = ['Todas','PENDIENTE','APROBADA','RECHAZADA','ENTREGADA'];
    const TAB_LABELS = { 'Todas':'Todas','PENDIENTE':'Pendientes','APROBADA':'Aprobadas','RECHAZADA':'Rechazadas','ENTREGADA':'Entregadas' };
    const TAB_ICONS = {
        'Todas': '<i class="fas fa-list"></i>', 'PENDIENTE': '<i class="fas fa-clock"></i>', 'APROBADA': '<i class="fas fa-check-double"></i>', 'RECHAZADA': '<i class="fas fa-times"></i>', 'ENTREGADA': '<i class="fas fa-box-open"></i>'
    };

    function renderFilterTabs() {
        filterTabsEl.innerHTML = TABS.map(tab => {
            const isActive = tab === activeFilter;
            const count    = tab === 'Todas' ? allRequests.length : allRequests.filter(s => s.estatus === tab).length;
            return `
            <button class="filter-tab ${isActive ? 'active' : ''}" data-filter="${tab}">
                ${TAB_ICONS[tab]} ${TAB_LABELS[tab]}
                <span class="tab-count" style="color:${isActive ? 'rgba(255,255,255,0.8)' : '#6b7280'};">${count}</span>
            </button>`;
        }).join('');

        filterTabsEl.querySelectorAll('.filter-tab').forEach(btn => {
            btn.addEventListener('click', () => {
                activeFilter = btn.dataset.filter;
                renderFilterTabs();
                applyFilters();
            });
        });
    }

    // ─── FILTRAR Y RENDERIZAR ──────────────────────────────────
    function applyFilters() {
        let list = allRequests;
        if (activeFilter !== 'Todas') list = list.filter(s => s.estatus === activeFilter);
        if (searchQuery) {
            const q = searchQuery.toLowerCase();
            list = list.filter(s =>
                (s.solicitante_nombre || '').toLowerCase().includes(q) ||
                (s.proyecto || '').toLowerCase().includes(q)
            );
        }
        renderRequests(list);
    }

    function renderRequests(list) {
        if (!list.length) {
            requestsContainer.innerHTML = `
            <tr><td colspan="7">
                <div class="empty-state">
                    <div class="empty-icon"><i class="fas fa-inbox"></i></div>
                    <div class="empty-title">No hay solicitudes</div>
                    <div class="empty-sub text-sm mt-1">Intenta con otro filtro o búsqueda.</div>
                </div>
            </td></tr>`;
            return;
        }

        requestsContainer.innerHTML = list.map(s => {
            const estatus   = s.estatus || 'PENDIENTE';
            const cls       = estatus.toLowerCase();
            const fecha     = new Date(s.fecha_creacion).toLocaleDateString('es-MX', { day:'2-digit', month:'short', year:'numeric' });
            const hora      = new Date(s.fecha_creacion).toLocaleTimeString('es-MX', { hour:'2-digit', minute:'2-digit' });
            const initials  = (s.solicitante_nombre || 'S').split(' ').map(w => w[0]).join('').toUpperCase().slice(0,2);
            
            return `
            <tr data-id="${s.id}">
                <td><span class="req-id-pill">#${s.id}</span></td>
                <td>
                    <div class="emp-cell">
                        <span class="emp-avatar">${initials}</span>
                        <div class="emp-info">
                            <span class="emp-name">${s.solicitante_nombre || 'Sin nombre'}</span>
                        </div>
                    </div>
                </td>
                <td>
                    <div class="area-stack">
                        <span class="badge"><i class="fas fa-hard-hat" style="margin-right:4px; opacity:0.7;"></i> ${s.proyecto || 'General'}</span>
                    </div>
                </td>
                <td>
                    <span style="font-weight:600; color:#4b5563;">${s.detalles.length}</span> <span style="font-size:0.75rem; color:#9ca3af;">items</span>
                </td>
                <td>
                    <div style="display:flex; flex-direction:column;">
                        <span style="font-weight:500; font-size:0.85rem; color:#374151;">${fecha}</span>
                        <span style="font-size:0.72rem; color:#6b7280;">${hora}</span>
                    </div>
                </td>
                <td>
                    <span class="status-pill status-${cls}">
                        ${estatus}
                    </span>
                </td>
                <td>
                    <div class="actions-cell">
                        <button class="btn-icon-action btn-view" title="Ver Detalle">
                            <i class="fas fa-eye"></i>
                        </button>
                    </div>
                </td>
            </tr>`;
        }).join('');

        requestsContainer.querySelectorAll('tr[data-id]').forEach(tr => {
            tr.addEventListener('click', () => openDetail(parseInt(tr.dataset.id)));
        });
    }

    if (searchInput) {
        searchInput.addEventListener('input', () => {
            searchQuery = searchInput.value;
            applyFilters();
        });
    }

    // ─── DETALLE ───────────────────────────────────────────────
    function openDetail(id) {
        activeRequest = allRequests.find(s => s.id === id);
        if (!activeRequest) return;

        const s       = activeRequest;
        const estatus = s.estatus || 'PENDIENTE';
        const cls     = estatus.toLowerCase();
        const fecha   = new Date(s.fecha_creacion).toLocaleDateString('es-MX', { day:'2-digit', month:'long', year:'numeric', hour:'2-digit', minute:'2-digit' });

        document.getElementById('detailStatusBadge').className = `status-pill status-${cls}`;
        document.getElementById('detailStatusBadge').innerHTML = estatus;

        document.getElementById('detailTitle').textContent    = `Solicitud #${s.id} — ${s.solicitante_nombre || ''}`;
        document.getElementById('detailSubtitle').textContent = `Enviada el ${fecha}`;

        document.getElementById('detailInfoGrid').innerHTML = `
        <div class="detail-info-item">
            <div class="detail-info-label"><i class="fas fa-hard-hat"></i> Proyecto</div>
            <div class="detail-info-value">${s.proyecto || 'General'}</div>
        </div>
        <div class="detail-info-item">
            <div class="detail-info-label"><i class="fas fa-boxes"></i> Total de Materiales</div>
            <div class="detail-info-value">${s.detalles.length} items</div>
        </div>`;

        document.getElementById('detailTableBody').innerHTML = s.detalles.map((d, i) => `
        <tr>
            <td style="font-size:0.8rem; color:#6b7280; font-weight:700;">${i + 1}</td>
            <td>
                <p style="font-weight:600; font-size:0.9rem; color:#111827; margin:0;">${d.producto_descripcion}</p>
            </td>
            <td>
                <span style="font-size:0.75rem; background:#f3f4f6; color:#4b5563; padding:2px 6px; border-radius:4px; font-weight:600;">${d.producto_codigo}</span>
            </td>
            <td style="text-align:right;">
                <span style="font-weight:800; color:#0b5fb4; font-size:1rem;">${d.cantidad_solicitada}</span>
            </td>
        </tr>`).join('');

        const isPending  = estatus === 'PENDIENTE';
        const isAprobada = estatus === 'APROBADA';
        const isDone     = estatus === 'RECHAZADA' || estatus === 'ENTREGADA';
        
        const actionEl   = document.getElementById('actionButtons');
        const doneEl     = document.getElementById('doneMessage');
        const deliverBtn = document.getElementById('deliverButton');
        
        actionEl.style.display   = isPending  ? 'flex'   : 'none';
        deliverBtn.style.display = isAprobada ? 'block' : 'none';
        doneEl.style.display     = isDone     ? 'flex'   : 'none';

        modalDetail.classList.add('active');
    }

    // ─── DESCARGAR PDF ───────────────────────────────────────────
    document.getElementById('btnPrintDetail').addEventListener('click', () => {
        if (!activeRequest) return;
        const btn = document.getElementById('btnPrintDetail');
        const orig = btn.innerHTML;
        btn.disabled = true;
        btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Generando…';
        window.location.href = `/inventario/solicitudes/${activeRequest.id}/pdf`;
        setTimeout(() => { btn.disabled = false; btn.innerHTML = orig; }, 2000);
    });

    // ─── APROBAR / RECHAZAR ────────────────────────────────────
    async function updateStatus(newStatus) {
        if (!activeRequest) return;
        try {
            btnApprove.disabled = btnReject.disabled = btnDeliver.disabled = true;
            const res = await fetch(`/api/v1/solicitudes/${activeRequest.id}/estado`, {
                method:  'PATCH',
                headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': CSRF_TOKEN },
                body:    JSON.stringify({ estatus: newStatus })
            });
            if (res.ok) {
                modalDetail.classList.remove('active');
                showToast(`Solicitud actualizada con éxito`);
                await loadRequests();
            } else {
                showToast('Error al actualizar el estado.', 'error');
            }
        } catch(e) {
            showToast('Error de conexión.', 'error');
        } finally {
            btnApprove.disabled = btnReject.disabled = btnDeliver.disabled = false;
        }
    }

    // ─── EVENT LISTENERS ───────────────────────────────────────
    if (btnRefresh)     btnRefresh.addEventListener('click', loadRequests);
    if (btnCloseDetail) btnCloseDetail.addEventListener('click', () => modalDetail.classList.remove('active'));
    if (btnApprove)     btnApprove.addEventListener('click',  () => updateStatus('APROBADA'));
    if (btnReject)      btnReject.addEventListener('click',   () => updateStatus('RECHAZADA'));
    if (btnDeliver)     btnDeliver.addEventListener('click',  () => updateStatus('ENTREGADA'));
    modalDetail.addEventListener('click', (e) => { if (e.target === modalDetail) modalDetail.classList.remove('active'); });

    // ─── INIT ──────────────────────────────────────────────────
    loadRequests();
});
