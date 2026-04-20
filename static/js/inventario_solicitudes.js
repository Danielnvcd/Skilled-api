/* ─── inventario_solicitudes.js v3 ─── */
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
    const TOAST_ICONS = {
        success: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>`,
        error:   `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>`,
        info:    `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>`,
    };
    function showToast(msg, type = 'success') {
        const t = document.createElement('div');
        t.className = 'sol-toast';
        t.innerHTML = (TOAST_ICONS[type] || TOAST_ICONS.success) + `<span>${msg}</span>`;
        t.style.background = type === 'error' ? '#DC2626' : type === 'info' ? '#4F46E5' : '#059669';
        document.body.appendChild(t);
        setTimeout(() => t.remove(), 3500);
    }

    // ─── CARGA DE SOLICITUDES ──────────────────────────────────
    async function loadRequests() {
        requestsContainer.innerHTML = `
        <div class="text-center py-16 text-gray-400">
            <div class="animate-spin rounded-full h-10 w-10 border-b-2 border-indigo-600 mx-auto mb-4"></div>
            <p class="font-medium text-sm">Actualizando...</p>
        </div>`;
        try {
            const res = await fetch('/api/v1/solicitudes/');
            allRequests = await res.json();
            renderStats();
            renderFilterTabs();
            applyFilters();
        } catch(e) {
            requestsContainer.innerHTML = '<p class="text-center text-rose-500 py-10 font-semibold">Error cargando datos del servidor.</p>';
        }
    }

    // ─── STATS ─────────────────────────────────────────────────
    const STAT_DEFS = [
        { key:'total',     label:'Total Pedidos', bg:'#F1F5F9', color:'#334155', glow:'rgba(51,65,85,0.08)',
          icon:`<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 5H7a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2"/><rect x="9" y="3" width="6" height="4" rx="1"/><line x1="9" y1="12" x2="15" y2="12"/><line x1="9" y1="16" x2="13" y2="16"/></svg>` },
        { key:'pendiente', label:'Pendientes',    bg:'#FEF3C7', color:'#92400E', glow:'rgba(245,158,11,0.15)',
          icon:`<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>` },
        { key:'aprobada',  label:'Aprobadas',     bg:'#D1FAE5', color:'#065F46', glow:'rgba(16,185,129,0.15)',
          icon:`<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>` },
        { key:'rechazada', label:'Rechazadas',    bg:'#FEE2E2', color:'#991B1B', glow:'rgba(239,68,68,0.15)',
          icon:`<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>` },
        { key:'entregada', label:'Entregadas',    bg:'#DBEAFE', color:'#1E40AF', glow:'rgba(59,130,246,0.15)',
          icon:`<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M5 12h14"/><path d="M15 5l7 7-7 7"/><rect x="2" y="7" width="5" height="10" rx="1"/></svg>` },
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
        <div class="stat-card" style="border-left:4px solid ${d.color}; --stat-glow:${d.glow};">
            <div class="stat-icon" style="background:${d.bg}; color:${d.color};">${d.icon}</div>
            <div>
                <div class="stat-val" style="color:${d.color};">${counts[d.key]}</div>
                <div class="stat-lbl">${d.label}</div>
            </div>
        </div>`).join('');
    }

    // ─── FILTER TABS ───────────────────────────────────────────
    const TABS = ['Todas','PENDIENTE','APROBADA','RECHAZADA','ENTREGADA'];
    const TAB_LABELS = { 'Todas':'Todas','PENDIENTE':'Pendientes','APROBADA':'Aprobadas','RECHAZADA':'Rechazadas','ENTREGADA':'Entregadas' };
    const TAB_DOTS   = { 'PENDIENTE':'dot-pendiente','APROBADA':'dot-aprobada','RECHAZADA':'dot-rechazada','ENTREGADA':'dot-entregada' };
    const TAB_ICONS  = {
        'Todas':     `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></svg>`,
        'PENDIENTE': `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>`,
        'APROBADA':  `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>`,
        'RECHAZADA': `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>`,
        'ENTREGADA': `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M5 12h14"/><path d="M15 5l7 7-7 7"/></svg>`,
    };

    function renderFilterTabs() {
        filterTabsEl.innerHTML = TABS.map(tab => {
            const isActive = tab === activeFilter;
            const count    = tab === 'Todas' ? allRequests.length : allRequests.filter(s => s.estatus === tab).length;
            const cls      = tab === 'Todas' ? '' : ` tab-${tab.toLowerCase()}`;
            return `
            <button class="filter-tab${cls}${isActive ? ' active' : ''}" data-filter="${tab}">
                ${TAB_ICONS[tab] || ''}
                ${TAB_LABELS[tab]}
                <span class="tab-count" style="font-size:0.68rem; font-weight:800; margin-left:2px; color:${isActive ? 'rgba(255,255,255,0.65)' : '#9CA3AF'};">${count}</span>
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
            <div class="text-center py-20 text-gray-300">
                <svg width="52" height="52" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" class="mx-auto mb-4"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
                <p class="text-sm font-semibold">No hay solicitudes${activeFilter !== 'Todas' ? ` con estado "${TAB_LABELS[activeFilter]}"` : ''}.</p>
                <p class="text-xs mt-1">Las nuevas peticiones aparecerán aquí.</p>
            </div>`;
            return;
        }

        requestsContainer.innerHTML = list.map(s => {
            const estatus   = s.estatus || 'PENDIENTE';
            const cls       = estatus.toLowerCase();
            const dot       = TAB_DOTS[estatus] || '';
            const fecha     = new Date(s.fecha_creacion).toLocaleDateString('es-MX', { day:'2-digit', month:'short', year:'numeric' });
            const hora      = new Date(s.fecha_creacion).toLocaleTimeString('es-MX', { hour:'2-digit', minute:'2-digit' });
            const initials  = (s.solicitante_nombre || 'S').split(' ').map(w => w[0]).join('').toUpperCase().slice(0,2);
            const matIcon   = `<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/></svg>`;
            const projIcon  = `<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>`;

            return `
            <div class="request-card border-${cls}" data-id="${s.id}">
                <div style="display:flex; align-items:center; gap:1rem; min-width:0; flex:1;">
                    <div class="req-avatar av-${cls}">${initials}</div>
                    <div style="min-width:0;">
                        <p style="font-size:0.9rem; font-weight:800; color:#0F172A; line-height:1.2; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; max-width:220px;">${s.solicitante_nombre || 'Sin nombre'}</p>
                        <div style="display:flex; align-items:center; gap:0.5rem; margin-top:0.35rem; flex-wrap:wrap;">
                            <span class="req-items-badge">${projIcon} ${s.proyecto || 'Sin proyecto'}</span>
                            <span class="req-items-badge">${matIcon} ${s.detalles.length} material${s.detalles.length !== 1 ? 'es' : ''}</span>
                        </div>
                    </div>
                </div>
                <div style="display:flex; align-items:center; gap:1rem; flex-shrink:0;">
                    <div style="text-align:right; display:none;" class="sm-show">
                        <p style="font-size:0.72rem; font-weight:600; color:#94A3B8;">${fecha}</p>
                        <p style="font-size:0.68rem; font-weight:700; color:#CBD5E1;">${hora}</p>
                        <p style="font-size:0.65rem; font-weight:800; color:#C7D2FE; text-transform:uppercase; letter-spacing:0.05em;">ID #${s.id}</p>
                    </div>
                    <span class="status-badge status-${cls}">
                        <span class="status-dot ${dot}"></span>${estatus}
                    </span>
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#CBD5E1" stroke-width="2.5" style="flex-shrink:0;"><polyline points="9 18 15 12 9 6"/></svg>
                </div>
            </div>`;
        }).join('');

        requestsContainer.querySelectorAll('.request-card').forEach(card => {
            card.addEventListener('click', () => openDetail(parseInt(card.dataset.id)));
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
        const dot     = TAB_DOTS[estatus] || '';
        const fecha   = new Date(s.fecha_creacion).toLocaleDateString('es-MX', { day:'2-digit', month:'long', year:'numeric', hour:'2-digit', minute:'2-digit' });

        // Badge de estatus
        document.getElementById('detailStatusBadge').className = `status-badge status-${cls}`;
        document.getElementById('detailStatusBadge').innerHTML = `<span class="status-dot ${dot}"></span>${estatus}`;

        // Título
        document.getElementById('detailTitle').textContent    = `Solicitud #${s.id} — ${s.solicitante_nombre || ''}`;
        document.getElementById('detailSubtitle').textContent = `Enviada el ${fecha}`;

        // Grid de info
        document.getElementById('detailInfoGrid').innerHTML = `
        <div class="detail-info-item">
            <div class="detail-info-label">
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>
                Proyecto
            </div>
            <div class="detail-info-value">${s.proyecto || 'General'}</div>
        </div>
        <div class="detail-info-item">
            <div class="detail-info-label">
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/></svg>
                Total de Materiales
            </div>
            <div class="detail-info-value">${s.detalles.length} material${s.detalles.length !== 1 ? 'es' : ''}</div>
        </div>
        <div class="detail-info-item">
            <div class="detail-info-label">
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
                Fecha de Solicitud
            </div>
            <div class="detail-info-value" style="font-size:0.85rem;">${fecha}</div>
        </div>
        <div class="detail-info-item">
            <div class="detail-info-label">
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
                Solicitante
            </div>
            <div class="detail-info-value" style="font-size:0.85rem;">${s.solicitante_nombre || '—'}</div>
        </div>`;

        // Tabla de materiales
        document.getElementById('detailTableBody').innerHTML = s.detalles.map((d, i) => `
        <tr>
            <td class="px-4 py-3 text-xs font-black text-gray-400">${i + 1}</td>
            <td class="px-4 py-3">
                <p class="text-sm font-bold text-gray-800">${d.producto_descripcion}</p>
            </td>
            <td class="px-4 py-3">
                <span class="text-[10px] font-black bg-gray-100 text-gray-500 px-1.5 py-0.5 rounded uppercase">${d.producto_codigo}</span>
            </td>
            <td class="px-4 py-3 text-right">
                <span class="text-base font-black text-indigo-600">${d.cantidad_solicitada}</span>
                <span class="text-[10px] font-bold text-gray-400 uppercase ml-0.5">uds</span>
            </td>
        </tr>`).join('');

        // Botones de acción según estado
        const isPending  = estatus === 'PENDIENTE';
        const isAprobada = estatus === 'APROBADA';
        const isDone     = estatus === 'RECHAZADA' || estatus === 'ENTREGADA';
        const actionEl   = document.getElementById('actionButtons');
        const doneEl     = document.getElementById('doneMessage');
        actionEl.style.display  = isPending  ? 'flex'   : 'none';
        deliverButton.style.display = isAprobada ? 'block' : 'none';
        doneEl.style.display    = isDone     ? 'flex'   : 'none';

        modalDetail.classList.remove('hidden');
    }

    // ─── DESCARGAR PDF (servidor) ───────────────────────────────
    document.getElementById('btnPrintDetail').addEventListener('click', () => {
        if (!activeRequest) return;
        const btn = document.getElementById('btnPrintDetail');
        const orig = btn.innerHTML;
        btn.disabled = true;
        btn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg> Generando…';
        window.location.href = `/inventario/solicitudes/${activeRequest.id}/pdf`;
        setTimeout(() => { btn.disabled = false; btn.innerHTML = orig; }, 2000);
    });

    // ─── APROBAR / RECHAZAR ────────────────────────────────────
    const STATUS_TOAST = {
        'APROBADA':  'aprobada ✓',
        'RECHAZADA': 'rechazada',
        'ENTREGADA': 'marcada como entregada ✓',
    };

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
                modalDetail.classList.add('hidden');
                showToast(`Solicitud #${activeRequest.id} ${STATUS_TOAST[newStatus] || newStatus}`);
                await loadRequests();
            } else {
                showToast('No se pudo actualizar el estado.', 'error');
            }
        } catch(e) {
            showToast('Error de conexión.', 'error');
        } finally {
            btnApprove.disabled = btnReject.disabled = btnDeliver.disabled = false;
        }
    }

    // ─── EVENT LISTENERS ───────────────────────────────────────
    if (btnRefresh)     btnRefresh.addEventListener('click', loadRequests);
    if (btnCloseDetail) btnCloseDetail.addEventListener('click', () => modalDetail.classList.add('hidden'));
    if (btnApprove)     btnApprove.addEventListener('click',  () => updateStatus('APROBADA'));
    if (btnReject)      btnReject.addEventListener('click',   () => updateStatus('RECHAZADA'));
    if (btnDeliver)     btnDeliver.addEventListener('click',  () => updateStatus('ENTREGADA'));
    modalDetail.addEventListener('click', (e) => { if (e.target === modalDetail) modalDetail.classList.add('hidden'); });

    // ─── INIT ──────────────────────────────────────────────────
    loadRequests();
});
