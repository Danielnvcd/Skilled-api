/* ─── inventario_solicitar.js v5 ─── */
document.addEventListener('DOMContentLoaded', () => {
    const CSRF_TOKEN = document.querySelector('meta[name="csrf-token"]').getAttribute('content');

    let products       = [];
    let basket         = [];
    let selectedProduct = null;
    let activeCategory = 'Todas';

    // ─── CATEGORIA CONFIG (iconos SVG por categoría) ───────────
    const CAT_CFG = {
        'Tornillería':        { color:'#4F46E5', bg:'#EEF2FF', svg:`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/></svg>` },
        'Tuercas':            { color:'#B45309', bg:'#FFFBEB', svg:`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polygon points="12 2 22 8.5 22 15.5 12 22 2 15.5 2 8.5"/><circle cx="12" cy="12" r="3"/></svg>` },
        'Rondanas':           { color:'#0891B2', bg:'#ECFEFF', svg:`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="3.5"/></svg>` },
        'Pijas':              { color:'#7C3AED', bg:'#F5F3FF', svg:`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="2" x2="12" y2="20"/><path d="M8 6h8"/><path d="M9 10h6"/><path d="M10 14h4"/><path d="M11 18h2"/></svg>` },
        'Abrazaderas':        { color:'#059669', bg:'#ECFDF5', svg:`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>` },
        'Soportería':         { color:'#DC2626', bg:'#FEF2F2', svg:`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="4" rx="1"/><rect x="2" y="10" width="20" height="4" rx="1"/><rect x="2" y="17" width="20" height="4" rx="1"/></svg>` },
        'Tubería/Accesorios': { color:'#0284C7', bg:'#F0F9FF', svg:`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><rect x="1" y="6" width="22" height="12" rx="5"/><path d="M6 12h12"/></svg>` },
    };
    const CAT_DEFAULT = { color:'#6B7280', bg:'#F3F4F6', svg:`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/></svg>` };

    // ─── ELEMENTOS DOM ─────────────────────────────────────────
    const productList   = document.getElementById('productList');
    const basketItems   = document.getElementById('basketItems');
    const basketCount   = document.getElementById('basketCount');
    const prodCount     = document.getElementById('prodCount');
    const searchInput   = document.getElementById('searchProduct');
    const btnSubmit     = document.getElementById('btnSubmitRequest');
    const btnPrint      = document.getElementById('btnPrint');
    const catChipsEl    = document.getElementById('catChips');
    const modalQty      = document.getElementById('modalQty');

    // ─── TOAST ─────────────────────────────────────────────────
    function showToast(msg, type = 'success') {
        const t = document.createElement('div');
        t.className = 'sol-toast';
        t.textContent = msg;
        t.style.background = type === 'error' ? '#EF4444' : type === 'info' ? '#6366F1' : '#10B981';
        document.body.appendChild(t);
        setTimeout(() => t.remove(), 3500);
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
.notes-box{background:#F9FAFB;border:1px solid #E5E7EB;border-radius:7px;padding:12px 16px;margin-bottom:20px}
.notes-label{font-size:9px;font-weight:900;color:#888;text-transform:uppercase;letter-spacing:.08em;margin-bottom:6px}
.notes-text{font-size:12px;color:#374151;line-height:1.5}
.status-box{display:inline-block;padding:4px 14px;border-radius:4px;font-size:10px;font-weight:900;text-transform:uppercase;letter-spacing:.07em;margin-top:6px;border:1.5px solid #111}
.status-pendiente{color:#92400E;border-color:#D97706;background:#FFFBEB}
.status-aprobada{color:#065F46;border-color:#10B981;background:#ECFDF5}
.status-rechazada{color:#991B1B;border-color:#EF4444;background:#FEF2F2}
.status-entregada{color:#1E40AF;border-color:#3B82F6;background:#EFF6FF}
.section-title{font-size:10px;font-weight:900;color:#6B7280;text-transform:uppercase;letter-spacing:.1em;margin:0 0 10px}
.table-wrap{border:1px solid #E5E7EB;border-radius:8px;overflow:hidden;margin-bottom:28px}
table{width:100%;border-collapse:collapse;font-size:12px}
thead tr{background:#111827}
thead th{color:#fff;padding:10px 14px;text-align:left;font-size:9px;text-transform:uppercase;letter-spacing:.07em;font-weight:800}
tbody tr:nth-child(even) td{background:#F9FAFB}
tbody td{padding:10px 14px;border-bottom:1px solid #F1F5F9;vertical-align:middle}
tbody tr:last-child td{border-bottom:none}
.c{text-align:center}.r{text-align:right}.bold{font-weight:800;color:#4F46E5}
.ac{font-size:15px}.muted{color:#9CA3AF;font-size:11px;font-weight:600}
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

    // ─── INIT ──────────────────────────────────────────────────
    async function init() {
        await Promise.all([loadProducts(), loadProjects()]);
    }

    async function loadProducts() {
        try {
            const res = await fetch('/api/v1/productos/');
            products = await res.json();
            renderCategoryChips();
            renderProducts(products);
        } catch(e) {
            productList.innerHTML = '<p class="col-span-full text-center text-red-400 py-8">Error cargando productos</p>';
        }
    }

    async function loadProjects() {
        const sel = document.getElementById('selProyecto');
        try {
            const res = await fetch('/api/v1/proyectos/');
            if (!res.ok) throw new Error('HTTP ' + res.status);
            const proyectos = await res.json();
            proyectos.forEach(p => {
                const o = document.createElement('option');
                o.value = `${p.numero_proyecto} — ${p.nombre}`;
                o.textContent = `${p.numero_proyecto} — ${p.nombre}`;
                sel.appendChild(o);
            });
        } catch(e) {
            console.warn('No se pudieron cargar proyectos desde la BD:', e);
        }
    }

    // ─── CHIPS DE CATEGORÍA ────────────────────────────────────
    function renderCategoryChips() {
        const cats = ['Todas', ...new Set(products.map(p => p.categoria).filter(Boolean))];
        catChipsEl.innerHTML = cats.map(cat => {
            const cfg = cat === 'Todas' ? null : (CAT_CFG[cat] || CAT_DEFAULT);
            const isActive = cat === activeCategory;
            const style = cfg
                ? `color:${isActive ? 'white' : cfg.color}; background:${isActive ? cfg.color : cfg.bg}; border-color:${isActive ? cfg.color : cfg.bg};`
                : `color:${isActive ? 'white' : '#374151'}; background:${isActive ? '#374151' : '#F3F4F6'}; border-color:${isActive ? '#374151' : '#E5E7EB'};`;
            const icon = cfg
                ? `<span style="width:13px;height:13px;display:inline-flex;align-items:center;justify-content:center;">${cfg.svg}</span>`
                : `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="16"/><line x1="8" y1="12" x2="16" y2="12"/></svg>`;
            return `<button class="cat-chip${isActive ? ' active' : ''}" style="${style}" data-cat="${cat}">${icon}${cat}</button>`;
        }).join('');

        catChipsEl.querySelectorAll('.cat-chip').forEach(btn => {
            btn.addEventListener('click', () => {
                activeCategory = btn.dataset.cat;
                renderCategoryChips();
                applyFilters();
            });
        });
    }

    // ─── RENDER PRODUCTOS ──────────────────────────────────────
    function applyFilters() {
        const q = searchInput.value.toLowerCase();
        let filtered = products.filter(p =>
            (activeCategory === 'Todas' || p.categoria === activeCategory) &&
            (!q || p.descripcion.toLowerCase().includes(q) || p.codigo.toLowerCase().includes(q))
        );
        renderProducts(filtered);
    }

    function renderProducts(list) {
        prodCount.textContent = `${list.length} materiales`;
        if (!list.length) {
            productList.innerHTML = `
            <div class="col-span-full flex flex-col items-center py-14 text-gray-300">
                <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" class="mb-3"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg>
                <p class="text-sm font-semibold">No se encontraron materiales</p>
            </div>`;
            return;
        }
        productList.innerHTML = list.map(p => {
            const cfg   = CAT_CFG[p.categoria] || CAT_DEFAULT;
            const stock = parseFloat(p.stock_actual);
            const bajo  = stock <= parseFloat(p.stock_minimo);
            const enBasket = basket.find(b => b.id === p.id);
            return `
            <div class="prod-card" data-id="${p.id}">
                <div class="prod-card-icon" style="background:${cfg.bg}; color:${cfg.color};">${cfg.svg}</div>
                <div class="min-w-0 flex-1">
                    <p class="text-[12px] font-bold text-gray-800 leading-tight truncate">${p.descripcion}</p>
                    <div class="flex items-center gap-1.5 mt-0.5 flex-wrap">
                        <span class="text-[9px] font-black bg-gray-100 text-gray-500 px-1.5 py-0.5 rounded uppercase">${p.codigo}</span>
                        <span class="text-[9px] font-semibold ${bajo ? 'text-rose-500' : 'text-emerald-500'}">${stock.toFixed(0)} ${p.unidad}</span>
                    </div>
                </div>
                <button class="prod-card-add" data-id="${p.id}" title="Agregar a lista">
                    ${enBasket
                        ? `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><polyline points="20 6 9 17 4 12"/></svg>`
                        : `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>`
                    }
                </button>
            </div>`;
        }).join('');

        // Eventos de clic
        productList.querySelectorAll('.prod-card').forEach(card => {
            card.addEventListener('click', () => openQtyModal(parseInt(card.dataset.id)));
        });
        productList.querySelectorAll('.prod-card-add').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                openQtyModal(parseInt(btn.dataset.id));
            });
        });
    }

    searchInput.addEventListener('input', applyFilters);

    // ─── MODAL CANTIDAD ────────────────────────────────────────
    function openQtyModal(id) {
        selectedProduct = products.find(p => p.id === id);
        if (!selectedProduct) return;
        const enBasket = basket.find(b => b.id === id);
        document.getElementById('modalProdName').textContent = selectedProduct.descripcion;
        document.getElementById('modalProdSku').textContent  = selectedProduct.codigo;
        document.getElementById('modalProdUnit').textContent = selectedProduct.unidad;
        document.getElementById('modalStock').textContent    = `Stock disponible: ${selectedProduct.stock_actual} ${selectedProduct.unidad}`;
        document.getElementById('inputQty').value = enBasket ? enBasket.cantidad : 1;
        modalQty.classList.remove('hidden');
        setTimeout(() => document.getElementById('inputQty').select(), 50);
    }

    document.getElementById('btnCancelQty').addEventListener('click', () => modalQty.classList.add('hidden'));
    modalQty.addEventListener('click', (e) => { if (e.target === modalQty) modalQty.classList.add('hidden'); });

    document.getElementById('btnQtyMinus').addEventListener('click', () => {
        const input = document.getElementById('inputQty');
        const v = parseFloat(input.value) || 1;
        input.value = Math.max(0.1, Math.round((v - 1) * 10) / 10);
    });
    document.getElementById('btnQtyPlus').addEventListener('click', () => {
        const input = document.getElementById('inputQty');
        const v = parseFloat(input.value) || 0;
        input.value = Math.round((v + 1) * 10) / 10;
    });

    document.getElementById('btnAddConfirm').addEventListener('click', () => {
        const qty = parseFloat(document.getElementById('inputQty').value);
        if (!qty || qty <= 0) return;
        const exists = basket.find(b => b.id === selectedProduct.id);
        if (exists) {
            exists.cantidad = qty;
        } else {
            basket.push({
                id:          selectedProduct.id,
                codigo:      selectedProduct.codigo,
                descripcion: selectedProduct.descripcion,
                unidad:      selectedProduct.unidad,
                categoria:   selectedProduct.categoria,
                cantidad:    qty
            });
        }
        modalQty.classList.add('hidden');
        updateBasket();
        applyFilters(); // Refresca el check en el catálogo
    });

    // ─── BASKET ────────────────────────────────────────────────
    function updateBasket() {
        basketCount.textContent = `${basket.length} ${basket.length === 1 ? 'ítem' : 'ítems'}`;
        btnSubmit.disabled = basket.length === 0;
        btnPrint.disabled  = basket.length === 0;

        if (!basket.length) {
            basketItems.innerHTML = `
            <div class="text-center py-8 border-2 border-dashed border-gray-100 rounded-2xl">
                <svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="#D1D5DB" stroke-width="1.5" class="mx-auto mb-2"><path d="M6 2 3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6l-3-4Z"/><path d="M3 6h18"/></svg>
                <p class="text-gray-400 text-xs font-medium">Agrega materiales del catálogo</p>
            </div>`;
            return;
        }

        basketItems.innerHTML = basket.map((item, idx) => {
            const cfg = CAT_CFG[item.categoria] || CAT_DEFAULT;
            return `
            <div class="basket-item flex items-center gap-2 p-2.5 bg-gray-50/70 rounded-xl border border-gray-100">
                <div class="w-8 h-8 rounded-lg flex-shrink-0 flex items-center justify-center p-1.5" style="background:${cfg.bg}; color:${cfg.color};">${cfg.svg}</div>
                <div class="min-w-0 flex-1">
                    <p class="text-[11px] font-bold text-gray-800 truncate leading-tight">${item.descripcion}</p>
                    <p class="text-[9px] font-black text-gray-400 uppercase">${item.codigo}</p>
                </div>
                <div class="qty-ctrl shrink-0">
                    <button class="qty-btn btn-qty-minus" data-idx="${idx}">−</button>
                    <span class="qty-val">${item.cantidad}</span>
                    <button class="qty-btn btn-qty-plus" data-idx="${idx}">+</button>
                    <span class="text-[9px] font-bold text-gray-400 uppercase ml-0.5">${item.unidad}</span>
                </div>
                <button class="btn-remove-basket text-gray-300 hover:text-rose-500 transition-colors p-1 flex-shrink-0" data-idx="${idx}">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><path d="M18 6 6 18M6 6l12 12"/></svg>
                </button>
            </div>`;
        }).join('');

        // Eventos +/-/eliminar
        basketItems.querySelectorAll('.btn-qty-minus').forEach(btn => {
            btn.addEventListener('click', () => {
                const i = parseInt(btn.dataset.idx);
                if (basket[i].cantidad > 1) { basket[i].cantidad = Math.round((basket[i].cantidad - 1) * 10) / 10; }
                else { basket.splice(i, 1); }
                updateBasket(); applyFilters();
            });
        });
        basketItems.querySelectorAll('.btn-qty-plus').forEach(btn => {
            btn.addEventListener('click', () => {
                const i = parseInt(btn.dataset.idx);
                basket[i].cantidad = Math.round((basket[i].cantidad + 1) * 10) / 10;
                updateBasket();
            });
        });
        basketItems.querySelectorAll('.btn-remove-basket').forEach(btn => {
            btn.addEventListener('click', () => {
                basket.splice(parseInt(btn.dataset.idx), 1);
                updateBasket(); applyFilters();
            });
        });
    }

    // ─── IMPRIMIR (nueva ventana PDF) ─────────────────────────
    btnPrint.addEventListener('click', () => {
        if (!basket.length) return;
        const proyecto = document.getElementById('selProyecto').value || '—';
        const notas    = document.getElementById('txtNotes').value.trim() || '';
        const fecha    = new Date().toLocaleDateString('es-MX', { day:'2-digit', month:'long', year:'numeric', hour:'2-digit', minute:'2-digit' });
        const folio    = 'SOL-' + Date.now().toString().slice(-6);

        const filas = basket.map((item, i) => `
            <tr>
                <td class="c">${i + 1}</td>
                <td>${item.descripcion}</td>
                <td><span class="sku">${item.codigo}</span></td>
                <td class="r bold ac">${item.cantidad}</td>
                <td class="muted">${item.unidad}</td>
            </tr>`).join('');

        const notasHTML = notas
            ? `<div class="notes-box"><div class="notes-label">Observaciones</div><div class="notes-text">${notas}</div></div>`
            : '';

        abrirVentanaPDF(`Solicitud ${folio}`, `
        <div class="header">
            <div>
                <div class="logo">SKIL<span>LED</span></div>
                <div class="logo-sub">Solicitud de Materiales</div>
            </div>
            <div class="folio-box">
                <div class="folio-num">${folio}</div>
                <div class="folio-date">${fecha}</div>
            </div>
        </div>
        <div class="divider"></div>
        <div class="info-grid">
            <div class="info-field"><label>Proyecto</label><span>${proyecto}</span></div>
            <div class="info-field"><label>Total de materiales</label><span>${basket.length} línea${basket.length !== 1 ? 's' : ''}</span></div>
        </div>
        ${notasHTML}
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
            <div class="sig"><div class="sig-line">Jefe de Almacén</div></div>
            <div class="sig"><div class="sig-line">Autorizado por</div></div>
        </div>`);
    });

    // ─── ENVIAR SOLICITUD ──────────────────────────────────────
    btnSubmit.addEventListener('click', async () => {
        const proyecto = document.getElementById('selProyecto').value;
        if (!proyecto) { showToast('Selecciona un proyecto antes de enviar.', 'error'); return; }

        const data = {
            proyecto: proyecto,
            detalles: basket.map(item => ({
                producto_id:         item.id,
                cantidad_solicitada: item.cantidad
            }))
        };

        try {
            btnSubmit.disabled = true;
            btnSubmit.innerHTML = `<span class="inline-block animate-spin mr-2">↻</span> Enviando...`;
            const res = await fetch('/api/v1/solicitudes/', {
                method:  'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': CSRF_TOKEN },
                body:    JSON.stringify(data)
            });

            if (res.ok) {
                showToast(`¡Solicitud enviada con éxito! (${basket.length} materiales)`);
                basket = [];
                updateBasket();
                applyFilters();
                document.getElementById('selProyecto').value = '';
                document.getElementById('txtNotes').value = '';
            } else {
                const err = await res.json();
                showToast('Error: ' + (err.detail || 'No se pudo enviar'), 'error');
            }
        } catch(e) {
            showToast('Error de red. Intenta de nuevo.', 'error');
        } finally {
            btnSubmit.disabled = basket.length === 0;
            btnSubmit.innerHTML = `<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg> Enviar Solicitud`;
        }
    });

    init();
});
