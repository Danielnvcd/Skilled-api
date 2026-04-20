/* ─── inventario_mis_pedidos.js v1 ─── */
document.addEventListener('DOMContentLoaded', () => {

    let allPedidos   = [];
    let activeFilter = 'Todas';
    let activeDetail = null;

    const TABS   = ['Todas','PENDIENTE','APROBADA','RECHAZADA','ENTREGADA'];
    const LABELS = { 'Todas':'Todas','PENDIENTE':'Pendientes','APROBADA':'Aprobadas','RECHAZADA':'Rechazadas','ENTREGADA':'Entregadas' };
    const DOTS   = { 'PENDIENTE':'dot-pendiente','APROBADA':'dot-aprobada','RECHAZADA':'dot-rechazada','ENTREGADA':'dot-entregada' };
    const COLORS = { 'PENDIENTE':'#D97706','APROBADA':'#059669','RECHAZADA':'#DC2626','ENTREGADA':'#2563EB' };
    const STAT_DEFS = [
        { key:'pendiente', label:'Pendientes', bg:'#FFFBEB', color:'#D97706', dot:'dot-pendiente' },
        { key:'aprobada',  label:'Aprobadas',  bg:'#ECFDF5', color:'#059669', dot:'dot-aprobada'  },
        { key:'rechazada', label:'Rechazadas', bg:'#FEF2F2', color:'#DC2626', dot:'dot-rechazada' },
        { key:'entregada', label:'Entregadas', bg:'#EFF6FF', color:'#2563EB', dot:'dot-entregada' },
    ];

    // ─── TOAST ─────────────────────────────────────────────────
    function showToast(msg, type = 'info') {
        const t = document.createElement('div');
        t.style.cssText = 'position:fixed;bottom:1.5rem;right:1.5rem;z-index:9999;padding:.85rem 1.5rem;border-radius:12px;font-weight:700;font-size:.9rem;color:white;box-shadow:0 8px 24px rgba(0,0,0,.15);animation:fadeIn .3s ease';
        t.style.background = type === 'error' ? '#EF4444' : '#6366F1';
        t.textContent = msg;
        document.body.appendChild(t);
        setTimeout(() => t.remove(), 3500);
    }

    // ─── CARGAR ────────────────────────────────────────────────
    async function loadPedidos() {
        document.getElementById('pedidosContainer').innerHTML = `
        <div class="text-center py-14 text-gray-300">
            <div class="animate-spin rounded-full h-9 w-9 border-b-2 border-indigo-400 mx-auto mb-3"></div>
            <p class="text-sm font-medium">Actualizando...</p>
        </div>`;
        try {
            const res = await fetch('/api/v1/solicitudes/');
            if (!res.ok) throw new Error('HTTP ' + res.status);
            allPedidos = await res.json();
            renderStats();
            renderFilterTabs();
            applyFilters();
        } catch(e) {
            document.getElementById('pedidosContainer').innerHTML =
                '<p class="text-center text-rose-400 py-10 text-sm font-semibold">No se pudo cargar el historial.</p>';
        }
    }

    // ─── STATS ─────────────────────────────────────────────────
    function renderStats() {
        const counts = {
            pendiente: allPedidos.filter(s => s.estatus === 'PENDIENTE').length,
            aprobada:  allPedidos.filter(s => s.estatus === 'APROBADA').length,
            rechazada: allPedidos.filter(s => s.estatus === 'RECHAZADA').length,
            entregada: allPedidos.filter(s => s.estatus === 'ENTREGADA').length,
        };
        document.getElementById('statsRow').innerHTML = STAT_DEFS.map(d => `
        <div class="stat-pill" style="background:${d.bg};color:${d.color};border-color:${d.bg};">
            <span class="status-dot ${d.dot}" style="width:9px;height:9px;"></span>
            <span class="font-black text-base" style="color:${d.color};">${counts[d.key]}</span>
            <span style="font-size:.7rem;font-weight:700;opacity:.8;">${d.label}</span>
        </div>`).join('');
    }

    // ─── FILTROS ───────────────────────────────────────────────
    function renderFilterTabs() {
        const el = document.getElementById('filterTabs');
        el.innerHTML = TABS.map(tab => {
            const isActive = tab === activeFilter;
            const count    = tab === 'Todas' ? allPedidos.length : allPedidos.filter(s => s.estatus === tab).length;
            const dot      = tab !== 'Todas' ? `<span class="status-dot tab-dot ${DOTS[tab]}"></span>` : '';
            return `<button class="hist-tab${isActive ? ' active' : ''}" data-tab="${tab}">${dot}${LABELS[tab]}<span style="font-size:.65rem;font-weight:900;margin-left:.25rem;opacity:.6;">${count}</span></button>`;
        }).join('');

        el.querySelectorAll('.hist-tab').forEach(btn => {
            btn.addEventListener('click', () => {
                activeFilter = btn.dataset.tab;
                renderFilterTabs();
                applyFilters();
            });
        });
    }

    // ─── RENDER LISTA ──────────────────────────────────────────
    function applyFilters() {
        const list = activeFilter === 'Todas' ? allPedidos : allPedidos.filter(s => s.estatus === activeFilter);
        const container = document.getElementById('pedidosContainer');

        if (!list.length) {
            container.innerHTML = `
            <div class="text-center py-16 text-gray-300">
                <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" class="mx-auto mb-3"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
                <p class="text-sm font-semibold">No tienes pedidos${activeFilter !== 'Todas' ? ` con estado "${LABELS[activeFilter]}"` : ''}.</p>
                <p class="text-xs mt-1">Las nuevas solicitudes aparecerán aquí.</p>
            </div>`;
            return;
        }

        container.innerHTML = list.map(s => {
            const est   = s.estatus || 'PENDIENTE';
            const cls   = est.toLowerCase();
            const dot   = DOTS[est] || '';
            const color = COLORS[est] || '#6B7280';
            const fecha = new Date(s.fecha_creacion).toLocaleDateString('es-MX', { day:'2-digit', month:'short', year:'numeric' });
            const hora  = new Date(s.fecha_creacion).toLocaleTimeString('es-MX', { hour:'2-digit', minute:'2-digit' });
            return `
            <div class="hist-row" data-id="${s.id}" style="border-left:3.5px solid ${color};">
                <div class="flex items-center gap-3 min-w-0 flex-1">
                    <div class="flex-shrink-0 w-10 h-10 rounded-xl flex items-center justify-center" style="background:${color}18;">
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="${color}" stroke-width="2.5"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
                    </div>
                    <div class="min-w-0">
                        <p class="text-[13px] font-black text-gray-800 leading-tight">Solicitud #${s.id}</p>
                        <p class="text-[11px] font-semibold text-gray-400 truncate mt-0.5">${s.proyecto || 'Sin proyecto'} &nbsp;·&nbsp; ${s.detalles.length} material${s.detalles.length !== 1 ? 'es' : ''}</p>
                    </div>
                </div>
                <div class="flex items-center gap-3 flex-shrink-0">
                    <div class="text-right hidden sm:block">
                        <p class="text-[11px] font-bold text-gray-400">${fecha}</p>
                        <p class="text-[10px] font-semibold text-gray-300">${hora}</p>
                    </div>
                    <span class="status-badge status-${cls}"><span class="status-dot ${dot}"></span>${est}</span>
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#CBD5E1" stroke-width="2.5"><polyline points="9 18 15 12 9 6"/></svg>
                </div>
            </div>`;
        }).join('');

        container.querySelectorAll('.hist-row').forEach(row => {
            row.addEventListener('click', () => openDetail(parseInt(row.dataset.id)));
        });
    }

    // ─── MODAL DETALLE ─────────────────────────────────────────
    function openDetail(id) {
        activeDetail = allPedidos.find(s => s.id === id);
        if (!activeDetail) return;
        const s   = activeDetail;
        const est = s.estatus || 'PENDIENTE';
        const cls = est.toLowerCase();
        const dot = DOTS[est] || '';
        const fecha = new Date(s.fecha_creacion).toLocaleDateString('es-MX', { day:'2-digit', month:'long', year:'numeric', hour:'2-digit', minute:'2-digit' });

        const badge = document.getElementById('detailBadge');
        badge.className = `status-badge status-${cls} mb-1.5 inline-flex`;
        badge.innerHTML = `<span class="status-dot ${dot}"></span>${est}`;

        document.getElementById('detailTitle').textContent = `Solicitud #${s.id}`;
        document.getElementById('detailDate').textContent  = `Enviada el ${fecha}`;

        document.getElementById('detailInfo').innerHTML = `
        <div>
            <p class="text-[10px] font-black text-gray-400 uppercase tracking-widest mb-0.5">Proyecto</p>
            <p class="text-sm font-bold text-gray-800">${s.proyecto || 'General'}</p>
        </div>
        <div>
            <p class="text-[10px] font-black text-gray-400 uppercase tracking-widest mb-0.5">Materiales</p>
            <p class="text-sm font-bold text-gray-800">${s.detalles.length} línea${s.detalles.length !== 1 ? 's' : ''}</p>
        </div>`;

        document.getElementById('detailTableBody').innerHTML = s.detalles.map((d, i) => `
        <tr>
            <td class="px-3 py-2.5 text-xs font-black text-gray-400">${i + 1}</td>
            <td class="px-3 py-2.5 text-sm font-bold text-gray-800">${d.producto_descripcion}</td>
            <td class="px-3 py-2.5"><span class="text-[10px] font-black bg-gray-100 text-gray-500 px-1.5 py-0.5 rounded uppercase">${d.producto_codigo}</span></td>
            <td class="px-3 py-2.5 text-right">
                <span class="text-base font-black text-indigo-600">${d.cantidad_solicitada}</span>
                <span class="text-[10px] font-bold text-gray-400 uppercase ml-0.5">${d.producto_unidad || 'pza'}</span>
            </td>
        </tr>`).join('');

        document.getElementById('modalDetail').classList.remove('hidden');
    }

    // ─── CERRAR MODAL ──────────────────────────────────────────
    document.getElementById('btnCloseDetail').addEventListener('click', () => {
        document.getElementById('modalDetail').classList.add('hidden');
    });
    document.getElementById('modalDetail').addEventListener('click', (e) => {
        if (e.target === document.getElementById('modalDetail'))
            document.getElementById('modalDetail').classList.add('hidden');
    });

    // ─── PDF ───────────────────────────────────────────────────
    document.getElementById('btnPDF').addEventListener('click', () => {
        if (!activeDetail) return;
        const s     = activeDetail;
        const est   = s.estatus || 'PENDIENTE';
        const fecha = new Date(s.fecha_creacion).toLocaleDateString('es-MX', { day:'2-digit', month:'long', year:'numeric', hour:'2-digit', minute:'2-digit' });

        const filas = s.detalles.map((d, i) => `
            <tr>
                <td class="c">${i + 1}</td>
                <td>${d.producto_descripcion}</td>
                <td><span class="sku">${d.producto_codigo}</span></td>
                <td class="r bold ac">${d.cantidad_solicitada}</td>
                <td class="muted">${d.producto_unidad || 'pza'}</td>
            </tr>`).join('');

        const win = window.open('', '_blank', 'width=860,height=1100,menubar=no,toolbar=no,location=no');
        if (!win) { showToast('Activa las ventanas emergentes para ver el PDF.'); return; }
        win.document.write(`<!DOCTYPE html>
<html lang="es"><head><meta charset="UTF-8"><title>Solicitud #${s.id}</title>
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
.table-wrap{border:1px solid #E5E7EB;border-radius:8px;overflow:hidden;margin-bottom:28px}
table{width:100%;border-collapse:collapse;font-size:12px}
thead tr{background:#111827}
thead th{color:#fff;padding:10px 14px;text-align:left;font-size:9px;text-transform:uppercase;letter-spacing:.07em;font-weight:800}
tbody tr:nth-child(even) td{background:#F9FAFB}
tbody td{padding:10px 14px;border-bottom:1px solid #F1F5F9;vertical-align:middle}
tbody tr:last-child td{border-bottom:none}
.c{text-align:center}.r{text-align:right}.bold{font-weight:800;color:#4F46E5}.ac{font-size:15px}.muted{color:#9CA3AF;font-size:11px;font-weight:600}
.sku{display:inline-block;background:#F3F4F6;color:#6B7280;padding:2px 6px;border-radius:4px;font-size:10px;font-weight:800;text-transform:uppercase;letter-spacing:.04em}
.sig-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:40px;margin-top:48px}
.sig{text-align:center}
.sig-line{border-top:1.5px solid #374151;padding-top:8px;font-size:10px;color:#6B7280;font-weight:700;text-transform:uppercase;letter-spacing:.05em}
@media print{@page{margin:1.5cm 2cm;size:letter}body{padding:0}}
</style></head><body>
<div class="header">
    <div>
        <div class="logo">SKIL<span>LED</span></div>
        <div class="logo-sub">Solicitud de Materiales · Detalle</div>
    </div>
    <div class="folio-box">
        <div class="folio-num">Solicitud #${s.id}</div>
        <div class="folio-date">${fecha}</div>
        <div class="status-box status-${est}">${est}</div>
    </div>
</div>
<div class="divider"></div>
<div class="info-grid">
    <div class="info-field"><label>Proyecto</label><span>${s.proyecto || 'General'}</span></div>
    <div class="info-field"><label>Total de materiales</label><span>${s.detalles.length} línea${s.detalles.length !== 1 ? 's' : ''}</span></div>
    <div class="info-field"><label>Estado</label><span>${est}</span></div>
    <div class="info-field"><label>Fecha de solicitud</label><span>${fecha}</span></div>
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
<div class="sig-grid">
    <div class="sig"><div class="sig-line">Solicitante</div></div>
    <div class="sig"><div class="sig-line">Responsable de Almacén</div></div>
    <div class="sig"><div class="sig-line">Autorizado por</div></div>
</div>
<script>window.onload=function(){window.focus();window.print();}<\/script>
</body></html>`);
        win.document.close();
    });

    // ─── EVENTOS ───────────────────────────────────────────────
    document.getElementById('btnRefresh').addEventListener('click', loadPedidos);

    // ─── INIT ──────────────────────────────────────────────────
    loadPedidos();
});
