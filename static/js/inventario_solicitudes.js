/* ─── inventario_solicitudes.js v2 ─── */
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
    const btnRefresh        = document.getElementById('btnRefresh');
    const statsRow          = document.getElementById('statsRow');
    const filterTabsEl      = document.getElementById('filterTabs');
    const searchInput       = document.getElementById('searchSolicitudes');

    // ─── TOAST ─────────────────────────────────────────────────
    function showToast(msg, type = 'success') {
        const t = document.createElement('div');
        t.className = 'sol-toast';
        t.textContent = msg;
        t.style.background = type === 'error' ? '#EF4444' : type === 'info' ? '#6366F1' : '#10B981';
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
        { key:'total',     label:'Total',      bg:'#F1F5F9', color:'#475569', dotCls:'' },
        { key:'pendiente', label:'Pendientes',  bg:'#FFFBEB', color:'#D97706', dotCls:'dot-pendiente' },
        { key:'aprobada',  label:'Aprobadas',   bg:'#ECFDF5', color:'#059669', dotCls:'dot-aprobada' },
        { key:'rechazada', label:'Rechazadas',  bg:'#FEF2F2', color:'#DC2626', dotCls:'dot-rechazada' },
        { key:'entregada', label:'Entregadas',  bg:'#EFF6FF', color:'#2563EB', dotCls:'dot-entregada' },
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
        <div class="stat-card" style="border-left: 4px solid ${d.color}; border-top: 1px solid #F1F5F9; border-right: 1px solid #F1F5F9; border-bottom: 1px solid #F1F5F9;">
            <div class="stat-icon" style="background:${d.bg};">
                ${d.key === 'total'
                    ? `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="${d.color}" stroke-width="2.5"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>`
                    : `<span class="status-dot ${d.dotCls}" style="width:14px; height:14px;"></span>`
                }
            </div>
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

    function renderFilterTabs() {
        filterTabsEl.innerHTML = TABS.map(tab => {
            const isActive = tab === activeFilter;
            const count    = tab === 'Todas' ? allRequests.length : allRequests.filter(s => s.estatus === tab).length;
            const dot      = tab !== 'Todas' ? `<span class="status-dot tab-dot ${TAB_DOTS[tab]}"></span>` : '';
            return `
            <button class="filter-tab${isActive ? ' active' : ''}" data-filter="${tab}">
                ${dot}${TAB_LABELS[tab]}
                <span class="text-[10px] ${isActive ? 'text-white/70' : 'text-gray-400'} font-black ml-0.5">${count}</span>
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
            const fecha     = new Date(s.fecha_creacion).toLocaleDateString('es-MX', { day:'2-digit', month:'short', year:'numeric', hour:'2-digit', minute:'2-digit' });
            const initiales = (s.solicitante_nombre || 'S').split(' ').map(w => w[0]).join('').toUpperCase().slice(0,2);

            return `
            <div class="request-card border-${cls}" data-id="${s.id}">
                <!-- Avatar -->
                <div class="flex items-center gap-4 min-w-0 flex-1">
                    <div class="w-11 h-11 rounded-xl bg-gradient-to-br from-indigo-100 to-indigo-50 flex items-center justify-center font-black text-indigo-600 text-sm flex-shrink-0">
                        ${initiales}
                    </div>
                    <div class="min-w-0">
                        <p class="text-sm font-black text-gray-900 leading-tight truncate">${s.solicitante_nombre || 'Sin nombre'}</p>
                        <p class="text-[11px] font-semibold text-gray-400 mt-0.5 truncate">
                            <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" class="inline mr-0.5"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18"/></svg>
                            ${s.proyecto || 'Sin proyecto'} &nbsp;·&nbsp; ${s.detalles.length} material${s.detalles.length !== 1 ? 'es' : ''}
                        </p>
                    </div>
                </div>
                <!-- Meta -->
                <div class="flex items-center gap-4 flex-shrink-0">
                    <div class="text-right hidden sm:block">
                        <p class="text-[11px] font-bold text-gray-400">${fecha}</p>
                        <p class="text-[10px] font-black text-indigo-400 uppercase tracking-wider">ID #${s.id}</p>
                    </div>
                    <span class="status-badge status-${cls}">
                        <span class="status-dot ${dot}"></span>${estatus}
                    </span>
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#CBD5E1" stroke-width="2.5"><polyline points="9 18 15 12 9 6"/></svg>
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
        <div>
            <p class="text-[10px] font-black text-gray-400 uppercase tracking-widest mb-0.5">Proyecto</p>
            <p class="text-sm font-bold text-gray-800">${s.proyecto || 'General'}</p>
        </div>
        <div>
            <p class="text-[10px] font-black text-gray-400 uppercase tracking-widest mb-0.5">Materiales</p>
            <p class="text-sm font-bold text-gray-800">${s.detalles.length} líneas</p>
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

        // Botones de acción
        const isPending = estatus === 'PENDIENTE';
        document.getElementById('actionButtons').classList.toggle('hidden', !isPending);
        document.getElementById('doneMessage').classList.toggle('hidden', isPending);

        modalDetail.classList.remove('hidden');
    }

    // ─── ABRIR VENTANA PDF ─────────────────────────────────────
    function abrirVentanaPDF(titulo, bodyHTML) {
        const win = window.open('', '_blank', 'width=860,height=1100,menubar=no,toolbar=no,location=no');
        if (!win) { showToast('Activa las ventanas emergentes para ver el PDF.', 'info'); return; }
        win.document.write(`<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<title>${titulo}</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Segoe UI',Arial,sans-serif;color:#111;background:#fff;padding:44px 52px;font-size:13px}
.header{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:0}
.logo{font-size:28px;font-weight:900;letter-spacing:-.03em;color:#111}
.logo span{color:#4F46E5}
.logo-sub{font-size:10px;color:#888;text-transform:uppercase;letter-spacing:.08em;font-weight:700;margin-top:5px}
.folio-box{text-align:right}
.folio-num{font-size:18px;font-weight:900;color:#111}
.folio-date{font-size:11px;color:#888;margin-top:4px}
.divider{border:none;border-top:3px solid #111;margin:16px 0 20px}
.info-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px 40px;margin-bottom:20px}
.info-field label{font-size:9px;font-weight:900;color:#888;text-transform:uppercase;letter-spacing:.08em;display:block;margin-bottom:3px}
.info-field span{font-size:13px;font-weight:600;color:#111;display:block;padding-bottom:5px;border-bottom:1px solid #E5E7EB}
.status-box{display:inline-block;padding:4px 14px;border-radius:4px;font-size:10px;font-weight:900;text-transform:uppercase;letter-spacing:.07em;margin-top:6px;border:1.5px solid #111}
.status-PENDIENTE{color:#92400E;border-color:#D97706;background:#FFFBEB}
.status-APROBADA{color:#065F46;border-color:#10B981;background:#ECFDF5}
.status-RECHAZADA{color:#991B1B;border-color:#EF4444;background:#FEF2F2}
.status-ENTREGADA{color:#1E40AF;border-color:#3B82F6;background:#EFF6FF}
.section-title{font-size:10px;font-weight:900;color:#6B7280;text-transform:uppercase;letter-spacing:.1em;margin:0 0 10px}
.table-wrap{border:1px solid #E5E7EB;border-radius:8px;overflow:hidden;margin-bottom:24px}
table{width:100%;border-collapse:collapse;font-size:12px}
thead tr{background:#111827}
thead th{color:#fff;padding:10px 14px;text-align:left;font-size:9px;text-transform:uppercase;letter-spacing:.07em;font-weight:800}
tbody tr:nth-child(even) td{background:#F9FAFB}
tbody td{padding:10px 14px;border-bottom:1px solid #F1F5F9;vertical-align:middle}
tbody tr:last-child td{border-bottom:none}
.c{text-align:center}.r{text-align:right}.bold{font-weight:800;color:#4F46E5}.ac{font-size:15px}.muted{color:#9CA3AF;font-size:11px;font-weight:600}
.sku{display:inline-block;background:#F3F4F6;color:#6B7280;padding:2px 6px;border-radius:4px;font-size:10px;font-weight:800;text-transform:uppercase;letter-spacing:.04em}
.check-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:28px}
.check-item{border:1.5px solid #E5E7EB;border-radius:6px;padding:10px 14px;font-size:12px;font-weight:700;display:flex;align-items:center;gap:8px;color:#374151}
.check-box{width:16px;height:16px;border:2px solid #9CA3AF;border-radius:3px;flex-shrink:0}
.sig-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:40px;margin-top:48px}
.sig{text-align:center}
.sig-line{border-top:1.5px solid #374151;padding-top:8px;font-size:10px;color:#6B7280;font-weight:700;text-transform:uppercase;letter-spacing:.05em}
@media print{@page{margin:1.5cm 2cm;size:letter}body{padding:0}}
</style>
</head>
<body>
${bodyHTML}
<script>window.onload=function(){window.focus();window.print();}<\/script>
</body>
</html>`);
        win.document.close();
    }

    // ─── IMPRIMIR SOLICITUD (nueva ventana PDF) ────────────────
    document.getElementById('btnPrintDetail').addEventListener('click', () => {
        if (!activeRequest) return;
        const s      = activeRequest;
        const fecha  = new Date(s.fecha_creacion).toLocaleDateString('es-MX', { day:'2-digit', month:'long', year:'numeric', hour:'2-digit', minute:'2-digit' });
        const estatus = s.estatus || 'PENDIENTE';

        const filas = s.detalles.map((d, i) => `
            <tr>
                <td class="c">${i + 1}</td>
                <td>${d.producto_descripcion}</td>
                <td><span class="sku">${d.producto_codigo}</span></td>
                <td class="r bold ac">${d.cantidad_solicitada}</td>
                <td class="muted">${d.producto_unidad || 'pza'}</td>
            </tr>`).join('');

        abrirVentanaPDF(`Solicitud #${s.id}`, `
        <div class="header">
            <div>
                <div class="logo">SKIL<span>LED</span></div>
                <div class="logo-sub">Solicitud de Materiales · Detalle</div>
            </div>
            <div class="folio-box">
                <div class="folio-num">Solicitud #${s.id}</div>
                <div class="folio-date">${fecha}</div>
                <div class="status-box status-${estatus}">${estatus}</div>
            </div>
        </div>
        <div class="divider"></div>
        <div class="info-grid">
            <div class="info-field"><label>Solicitante</label><span>${s.solicitante_nombre || '—'}</span></div>
            <div class="info-field"><label>Proyecto</label><span>${s.proyecto || 'General'}</span></div>
            <div class="info-field"><label>Total de materiales</label><span>${s.detalles.length} línea${s.detalles.length !== 1 ? 's' : ''}</span></div>
            <div class="info-field"><label>Estado</label><span>${estatus}</span></div>
        </div>
        <div class="section-title">Materiales Solicitados</div>
        <div class="table-wrap">
            <table>
                <thead><tr>
                    <th class="c" style="width:36px">#</th>
                    <th>Descripción del Material</th>
                    <th>Código (SKU)</th>
                    <th class="r" style="width:72px">Cant.</th>
                    <th style="width:56px">Unidad</th>
                </tr></thead>
                <tbody>${filas}</tbody>
            </table>
        </div>
        <div class="section-title">Resolución</div>
        <div class="check-grid">
            <div class="check-item"><div class="check-box"></div> Aprobado</div>
            <div class="check-item"><div class="check-box"></div> Rechazado</div>
            <div class="check-item"><div class="check-box"></div> Entregado</div>
        </div>
        <div class="sig-grid">
            <div class="sig"><div class="sig-line">Solicitante</div></div>
            <div class="sig"><div class="sig-line">Responsable de Almacén</div></div>
            <div class="sig"><div class="sig-line">Autorizado por</div></div>
        </div>`);
    });

    // ─── APROBAR / RECHAZAR ────────────────────────────────────
    async function updateStatus(newStatus) {
        if (!activeRequest) return;
        try {
            btnApprove.disabled = btnReject.disabled = true;
            const res = await fetch(`/api/v1/solicitudes/${activeRequest.id}/estado`, {
                method:  'PATCH',
                headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': CSRF_TOKEN },
                body:    JSON.stringify({ estatus: newStatus })
            });
            if (res.ok) {
                modalDetail.classList.add('hidden');
                showToast(`Solicitud #${activeRequest.id} ${newStatus === 'APROBADA' ? 'aprobada ✓' : 'rechazada'}`);
                await loadRequests();
            } else {
                showToast('No se pudo actualizar el estado.', 'error');
            }
        } catch(e) {
            showToast('Error de conexión.', 'error');
        } finally {
            btnApprove.disabled = btnReject.disabled = false;
        }
    }

    // ─── EVENT LISTENERS ───────────────────────────────────────
    if (btnRefresh)     btnRefresh.addEventListener('click', loadRequests);
    if (btnCloseDetail) btnCloseDetail.addEventListener('click', () => modalDetail.classList.add('hidden'));
    if (btnApprove)     btnApprove.addEventListener('click', () => updateStatus('APROBADA'));
    if (btnReject)      btnReject.addEventListener('click',  () => updateStatus('RECHAZADA'));
    modalDetail.addEventListener('click', (e) => { if (e.target === modalDetail) modalDetail.classList.add('hidden'); });

    // ─── INIT ──────────────────────────────────────────────────
    loadRequests();
});
