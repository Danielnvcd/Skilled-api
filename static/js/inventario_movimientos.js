/* inventario_movimientos.js v=2 */
'use strict';

const API = '/api/v1';

let PRODUCTOS_MAP = {};
let _productosPromise = null;

function loadProductos() {
    if (!_productosPromise) {
        _productosPromise = apiFetch(`${API}/productos/?limit=500`).then(ps => {
            ps.forEach(p => {
                PRODUCTOS_MAP[p.id] = { descripcion: p.descripcion, codigo: p.codigo, unidad: p.unidad, stock: parseFloat(p.stock_actual) };
            });
            return ps;
        });
    }
    return _productosPromise;
}

// ── Tab switching ──────────────────────────────────────────────────────────
document.querySelectorAll('.mv-tab').forEach(btn => {
    btn.addEventListener('click', () => {
        document.querySelectorAll('.mv-tab').forEach(t => t.classList.remove('active'));
        document.querySelectorAll('.mv-panel').forEach(p => p.classList.remove('active'));
        btn.classList.add('active');
        const panel = document.getElementById('panel-' + btn.dataset.tab);
        if (panel) panel.classList.add('active');
        if (btn.dataset.tab === 'bajo-minimo') loadBajoMinimo();
    });
});

// ── Helpers ────────────────────────────────────────────────────────────────
function fmtFecha(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    return d.toLocaleDateString('es-MX', { day: '2-digit', month: '2-digit', year: 'numeric' })
        + ' ' + d.toLocaleTimeString('es-MX', { hour: '2-digit', minute: '2-digit' });
}

function fmtNum(n) {
    const v = parseFloat(n);
    return isNaN(v) ? '0' : (Number.isInteger(v) ? v.toString() : v.toFixed(2).replace(/\.?0+$/, ''));
}

function todayISO() {
    return new Date().toISOString().slice(0, 10);
}

function tipoBadge(tipo) {
    return `<span class="tipo-badge tipo-${tipo}">${tipo}</span>`;
}

async function apiFetch(url) {
    const r = await fetch(url, { credentials: 'include' });
    if (!r.ok) throw new Error(await r.text());
    return r.json();
}

async function apiPost(url, body) {
    const r = await fetch(url, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
    });
    if (!r.ok) {
        const err = await r.json().catch(() => ({ detail: 'Error desconocido' }));
        throw new Error(err.detail || JSON.stringify(err));
    }
    return r.json();
}

function showToast(msg, type = 'success') {
    const t = document.createElement('div');
    t.textContent = msg;
    Object.assign(t.style, {
        position: 'fixed', bottom: '1.5rem', right: '1.5rem', zIndex: 9999,
        padding: '0.75rem 1.25rem', borderRadius: '10px',
        fontWeight: '600', fontSize: '0.9rem',
        background: type === 'success' ? '#16a34a' : '#dc2626',
        color: 'white', boxShadow: '0 4px 18px rgba(0,0,0,0.2)',
        transition: 'opacity 0.4s'
    });
    document.body.appendChild(t);
    setTimeout(() => { t.style.opacity = '0'; setTimeout(() => t.remove(), 400); }, 2800);
}

// ── KPIs ───────────────────────────────────────────────────────────────────
async function loadKPIs() {
    try {
        const [productos, movHoy, bajoMin] = await Promise.all([
            loadProductos(),
            apiFetch(`${API}/movimientos/?limit=1000`),
            apiFetch(`${API}/productos/bajo-minimo/`)
        ]);

        document.getElementById('kpiTotal').textContent = productos.length;

        const hoy = todayISO();
        let entradas = 0, salidas = 0;
        movHoy.forEach(m => {
            const d = (m.fecha || '').slice(0, 10);
            if (d === hoy) {
                if (m.tipo === 'ENTRADA') entradas++;
                if (m.tipo === 'SALIDA') salidas++;
            }
        });
        document.getElementById('kpiEntradas').textContent = entradas;
        document.getElementById('kpiSalidas').textContent = salidas;
        document.getElementById('kpiBajoMinimo').textContent = bajoMin.length;

        const badge = document.getElementById('badgeBajoMinimo');
        if (bajoMin.length > 0) {
            badge.textContent = bajoMin.length;
            badge.style.display = 'inline-block';
        }
    } catch (e) {
        console.error('KPIs error', e);
    }
}

// ── Historial ──────────────────────────────────────────────────────────────
async function loadHistorial() {
    const tipo = document.getElementById('filtroTipo').value;
    const prod = document.getElementById('filtroProducto').value;
    const tbody = document.getElementById('tbodyHistorial');
    tbody.innerHTML = `<tr><td colspan="7" style="text-align:center;padding:2rem;color:var(--text-muted);">
        <i class="fas fa-spinner fa-spin"></i> Cargando...</td></tr>`;

    let url = `${API}/movimientos/?limit=300`;
    if (tipo) url += `&tipo=${tipo}`;
    if (prod) url += `&producto_id=${prod}`;

    try {
        await loadProductos();
        const data = await apiFetch(url);
        if (!data.length) {
            tbody.innerHTML = `<tr><td colspan="7" style="text-align:center;padding:2rem;color:var(--text-muted);">Sin movimientos para los filtros seleccionados.</td></tr>`;
            return;
        }
        tbody.innerHTML = data.map((m, i) => {
            const prod = PRODUCTOS_MAP[m.producto_id] || {};
            const cant = parseFloat(m.cantidad);
            const cantStr = (cant > 0 ? '+' : '') + fmtNum(cant) + ' ' + (prod.unidad || '');
            const cantColor = cant >= 0 ? '#15803d' : '#b91c1c';
            return `<tr>
                <td style="color:var(--text-muted)">${i + 1}</td>
                <td>${fmtFecha(m.fecha)}</td>
                <td>${tipoBadge(m.tipo)}</td>
                <td>
                    <div style="font-weight:600;">${prod.descripcion || '—'}</div>
                    <div style="font-size:0.72rem;color:var(--text-muted);">${prod.codigo || ''}</div>
                </td>
                <td style="font-weight:700;color:${cantColor};font-variant-numeric:tabular-nums;">${cantStr}</td>
                <td style="color:var(--text-muted);max-width:200px;overflow:hidden;text-overflow:ellipsis;">${m.motivo || '—'}</td>
                <td style="color:var(--text-muted);">Usr #${m.usuario_id}</td>
            </tr>`;
        }).join('');
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="7" style="text-align:center;padding:1.5rem;color:#dc2626;">Error al cargar historial.</td></tr>`;
    }
}

document.getElementById('filtroTipo').addEventListener('change', loadHistorial);
document.getElementById('filtroProducto').addEventListener('change', loadHistorial);
document.getElementById('btnRefreshHistorial').addEventListener('click', () => { loadHistorial(); loadKPIs(); });

// ── Entrada stock preview ──────────────────────────────────────────────────
function updateEntradaPreview() {
    const sel = document.getElementById('entradaProducto');
    const opt = sel.options[sel.selectedIndex];
    const cant = parseFloat(document.getElementById('entradaCantidad').value) || 0;
    const preview = document.getElementById('entradaStockPreview');
    if (!opt.value || cant <= 0) { preview.style.display = 'none'; return; }
    const stock = parseFloat(opt.dataset.stock) || 0;
    const unidad = opt.dataset.unidad || '';
    const nuevo = stock + cant;
    preview.style.display = 'block';
    preview.innerHTML = `<i class="fas fa-info-circle"></i> Stock actual: <strong>${fmtNum(stock)} ${unidad}</strong> → Nuevo stock: <strong>${fmtNum(nuevo)} ${unidad}</strong>`;
}

document.getElementById('entradaProducto').addEventListener('change', updateEntradaPreview);
document.getElementById('entradaCantidad').addEventListener('input', updateEntradaPreview);

// ── Ajuste resultado preview ───────────────────────────────────────────────
function updateAjusteResultado() {
    const sel = document.getElementById('ajusteProducto');
    const opt = sel.options[sel.selectedIndex];
    const delta = parseFloat(document.getElementById('ajusteCantidad').value);
    const res = document.getElementById('ajusteResultado');
    if (!opt.value || isNaN(delta)) { res.textContent = '—'; res.style.color = 'var(--text-muted)'; return; }
    const stock = parseFloat(opt.dataset.stock) || 0;
    const unidad = opt.dataset.unidad || '';
    const nuevo = stock + delta;
    res.textContent = fmtNum(nuevo) + ' ' + unidad;
    res.style.color = nuevo < 0 ? '#dc2626' : nuevo === 0 ? '#d97706' : '#15803d';
    res.style.fontWeight = '700';
}

document.getElementById('ajusteProducto').addEventListener('change', updateAjusteResultado);
document.getElementById('ajusteCantidad').addEventListener('input', updateAjusteResultado);

// ── Form: Entrada ──────────────────────────────────────────────────────────
document.getElementById('formEntrada').addEventListener('submit', async e => {
    e.preventDefault();
    const btn = e.target.querySelector('[type=submit]');
    const productoId = parseInt(document.getElementById('entradaProducto').value);
    const cantidad = parseFloat(document.getElementById('entradaCantidad').value);
    const almacenId = parseInt(document.getElementById('entradaAlmacen').value) || null;
    const motivo = document.getElementById('entradaMotivo').value.trim() || null;

    if (!productoId || !(cantidad > 0)) return;

    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Guardando...';
    try {
        await apiPost(`${API}/movimientos/`, {
            tipo: 'ENTRADA',
            producto_id: productoId,
            cantidad: cantidad,
            almacen_destino_id: almacenId,
            motivo: motivo
        });
        showToast('Entrada registrada correctamente');
        e.target.reset();
        document.getElementById('entradaStockPreview').style.display = 'none';
        loadKPIs();
        loadHistorial();
    } catch (err) {
        showToast(err.message, 'error');
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="fas fa-plus"></i> Registrar Entrada';
    }
});

// ── Form: Ajuste ───────────────────────────────────────────────────────────
document.getElementById('formAjuste').addEventListener('submit', async e => {
    e.preventDefault();
    const btn = e.target.querySelector('[type=submit]');
    const productoId = parseInt(document.getElementById('ajusteProducto').value);
    const cantidad = parseFloat(document.getElementById('ajusteCantidad').value);
    const motivo = document.getElementById('ajusteMotivo').value.trim();

    if (!productoId || isNaN(cantidad) || !motivo) return;

    const opt = document.getElementById('ajusteProducto').options[document.getElementById('ajusteProducto').selectedIndex];
    const stockActual = parseFloat(opt.dataset.stock) || 0;
    if (stockActual + cantidad < 0) {
        showToast('El ajuste dejaría stock negativo', 'error');
        return;
    }

    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Aplicando...';
    try {
        await apiPost(`${API}/movimientos/`, {
            tipo: 'AJUSTE',
            producto_id: productoId,
            cantidad: cantidad,
            motivo: motivo
        });
        showToast('Ajuste aplicado correctamente');
        e.target.reset();
        document.getElementById('ajusteResultado').textContent = '—';
        document.getElementById('ajusteResultado').style.color = 'var(--text-muted)';
        loadKPIs();
        loadHistorial();
    } catch (err) {
        showToast(err.message, 'error');
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="fas fa-check"></i> Aplicar Ajuste';
    }
});

// ── Stock Bajo ─────────────────────────────────────────────────────────────
async function loadBajoMinimo() {
    const container = document.getElementById('bajominimoList');
    try {
        const data = await apiFetch(`${API}/productos/bajo-minimo/`);
        if (!data.length) {
            container.innerHTML = `<div style="text-align:center;padding:3rem;color:var(--text-muted);">
                <i class="fas fa-check-circle" style="font-size:2rem;display:block;margin-bottom:0.75rem;color:#16a34a;"></i>
                <strong>Todo en orden</strong><br>Ningún producto está por debajo del stock mínimo.
            </div>`;
            return;
        }
        container.innerHTML = data.map(p => {
            const pct = p.stock_minimo > 0 ? Math.min(100, Math.round((p.stock_actual / p.stock_minimo) * 100)) : 0;
            const warn = pct > 50;
            return `<div class="stock-bajo-card">
                <div style="min-width:0; flex:1;">
                    <div style="font-weight:700;font-size:0.92rem;">${p.descripcion}</div>
                    <div style="font-size:0.75rem;color:var(--text-muted);margin-top:1px;">${p.codigo} · ${p.unidad}</div>
                    <div style="margin-top:8px; display:flex; align-items:center; gap:8px;">
                        <div class="stock-bar-bg" style="flex:1;">
                            <div class="stock-bar-fill${warn ? ' warn' : ''}" style="width:${pct}%;"></div>
                        </div>
                        <span style="font-size:0.78rem;color:var(--text-muted);white-space:nowrap;">${pct}%</span>
                    </div>
                </div>
                <div style="text-align:right;flex-shrink:0;">
                    <div style="font-size:0.72rem;color:var(--text-muted);text-transform:uppercase;font-weight:600;">Stock</div>
                    <div style="font-size:1.1rem;font-weight:800;color:#dc2626;">${fmtNum(p.stock_actual)}</div>
                    <div style="font-size:0.72rem;color:var(--text-muted);">mín: ${fmtNum(p.stock_minimo)}</div>
                </div>
            </div>`;
        }).join('');
    } catch (e) {
        container.innerHTML = `<div style="color:#dc2626;padding:1rem;">Error al cargar stock bajo.</div>`;
    }
}

// ── Init ───────────────────────────────────────────────────────────────────
loadKPIs();
loadHistorial();
