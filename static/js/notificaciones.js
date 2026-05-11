/* notificaciones.js v1 — panel de notificaciones in-app para admin */
(function () {
    'use strict';

    const CSRF = () => document.querySelector('meta[name="csrf-token"]')?.content || '';
    let panelOpen = false;
    let panel = null;

    // ── Tipos de notificación ────────────────────────────────────────────────
    const TIPO_CFG = {
        REPORTE_CERRADO: { icon: 'fa-file-alt', color: '#3b82f6', bg: '#eff6ff' },
        PRENOMINA_CERRADA: { icon: 'fa-check-circle', color: '#10b981', bg: '#ecfdf5' },
        ACTUALIZACION: { icon: 'fa-star', color: '#f59e0b', bg: '#fffbeb' },
    };
    const TIPO_DEFAULT = { icon: 'fa-bell', color: '#6b7280', bg: '#f9fafb' };

    // ── Crear panel (una sola vez) ───────────────────────────────────────────
    function buildPanel() {
        const el = document.createElement('div');
        el.id = 'notif-panel';
        Object.assign(el.style, {
            position: 'fixed',
            top: '0',
            left: '-400px',          // fuera de pantalla al inicio
            width: '360px',
            maxHeight: '100vh',
            background: 'white',
            borderRight: '1px solid #e5e7eb',
            borderBottom: '1px solid #e5e7eb',
            borderRadius: '0 0 16px 0',
            boxShadow: '6px 0 24px rgba(0,0,0,0.10)',
            zIndex: '9998',
            display: 'flex',
            flexDirection: 'column',
            overflow: 'hidden',
            transition: 'left 0.25s cubic-bezier(0.4,0,0.2,1)',
        });

        el.innerHTML = `
          <div style="padding:14px 18px;border-bottom:1px solid #f3f4f6;display:flex;align-items:center;justify-content:space-between;background:linear-gradient(to right,#eff6ff,white);">
            <div style="display:flex;align-items:center;gap:8px;">
              <i class="fas fa-bell" style="color:#3b82f6;font-size:1rem;"></i>
              <span style="font-weight:700;font-size:0.97rem;color:#1e3a5f;">Notificaciones</span>
            </div>
            <div style="display:flex;gap:6px;align-items:center;">
              <button id="notif-mark-all"
                style="font-size:0.75rem;color:#6b7280;background:none;border:none;cursor:pointer;padding:4px 8px;border-radius:6px;transition:background 0.15s;"
                onmouseover="this.style.background='#f3f4f6'" onmouseout="this.style.background='none'">
                Marcar todas como leídas
              </button>
              <button id="notif-close-btn"
                style="background:none;border:none;cursor:pointer;color:#9ca3af;font-size:1rem;width:28px;height:28px;border-radius:6px;display:flex;align-items:center;justify-content:center;transition:all 0.15s;"
                onmouseover="this.style.background='#f3f4f6'" onmouseout="this.style.background='none'">
                <i class="fas fa-times"></i>
              </button>
            </div>
          </div>
          <div id="notif-list" style="overflow-y:auto;flex:1;max-height:calc(100vh - 58px);"></div>
        `;
        document.body.appendChild(el);

        el.querySelector('#notif-close-btn').addEventListener('click', closePanel);
        el.querySelector('#notif-mark-all').addEventListener('click', markAllRead);
        return el;
    }

    // ── Abrir / cerrar ───────────────────────────────────────────────────────
    function getSidebarWidth() {
        var sb = document.getElementById('sidebar');
        return sb ? sb.offsetWidth : 270;
    }

    function openPanel() {
        if (!panel) panel = buildPanel();
        panelOpen = true;
        // Se posiciona pegado al sidebar sin importar si está expandido o colapsado
        panel.style.left = getSidebarWidth() + 'px';
        fetchNotifications();
    }

    function closePanel() {
        panelOpen = false;
        if (panel) panel.style.left = '-400px';
    }

    function togglePanel() {
        if (panelOpen) closePanel(); else openPanel();
    }

    // ── Renderizar lista ─────────────────────────────────────────────────────
    function renderItems(items) {
        const list = document.getElementById('notif-list');
        if (!list) return;

        if (!items.length) {
            list.innerHTML = `
              <div style="text-align:center;padding:48px 20px;color:#9ca3af;">
                <i class="fas fa-bell-slash" style="font-size:2.5rem;margin-bottom:14px;display:block;opacity:0.4;"></i>
                <p style="margin:0;font-size:0.9rem;">Sin notificaciones por ahora</p>
              </div>`;
            return;
        }

        list.innerHTML = items.map(n => {
            const cfg = TIPO_CFG[n.tipo] || TIPO_DEFAULT;
            const bg = n.leida ? 'white' : '#f5f9ff';
            return `
              <div class="nitem" data-id="${n.id}" data-url="${n.url}"
                   style="display:flex;gap:12px;padding:13px 18px;cursor:pointer;background:${bg};border-bottom:1px solid #f3f4f6;transition:background 0.15s;"
                   onmouseover="this.style.background='#f0f4f8'" onmouseout="this.style.background='${bg}'">
                <div style="flex-shrink:0;width:36px;height:36px;border-radius:10px;background:${cfg.bg};display:flex;align-items:center;justify-content:center;">
                  <i class="fas ${cfg.icon}" style="color:${cfg.color};font-size:0.9rem;"></i>
                </div>
                <div style="flex:1;min-width:0;">
                  <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:6px;margin-bottom:2px;">
                    <p style="margin:0;font-weight:${n.leida ? '500' : '700'};font-size:0.86rem;color:#1f2937;line-height:1.3;">${escHtml(n.titulo)}</p>
                    ${!n.leida ? '<span style="flex-shrink:0;width:7px;height:7px;border-radius:50%;background:#3b82f6;margin-top:4px;display:inline-block;"></span>' : ''}
                  </div>
                  <p style="margin:0 0 3px 0;font-size:0.78rem;color:#6b7280;line-height:1.4;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;">${escHtml(n.mensaje)}</p>
                  <p style="margin:0;font-size:0.72rem;color:#9ca3af;">${n.tiempo}</p>
                </div>
              </div>`;
        }).join('');

        list.querySelectorAll('.nitem').forEach(el => {
            el.addEventListener('click', () => {
                const id = +el.dataset.id;
                const url = el.dataset.url;
                markOneRead(id, () => { if (url) window.location.href = url; });
            });
        });
    }

    // ── Badge ────────────────────────────────────────────────────────────────
    function updateBadge(count) {
        const badge = document.getElementById('notif-badge');
        if (!badge) return;
        if (count > 0) {
            badge.textContent = count > 99 ? '99+' : count;
            badge.style.display = 'inline-flex';
        } else {
            badge.style.display = 'none';
        }
    }

    // ── API calls ────────────────────────────────────────────────────────────
    function fetchNotifications() {
        fetch('/notificaciones/api/resumen', {
            headers: { 'X-Requested-With': 'XMLHttpRequest' },
        })
            .then(r => r.ok ? r.json() : null)
            .then(data => {
                if (!data) return;
                updateBadge(data.no_leidas);
                if (panelOpen) renderItems(data.items);
            })
            .catch(() => { });
    }

    function markOneRead(id, cb) {
        fetch(`/notificaciones/api/marcar_leida/${id}`, {
            method: 'POST',
            headers: {
                'X-CSRFToken': CSRF(),
                'X-Requested-With': 'XMLHttpRequest',
            },
        })
            .then(() => { fetchNotifications(); if (cb) cb(); })
            .catch(() => { if (cb) cb(); });
    }

    function markAllRead() {
        fetch('/notificaciones/api/marcar_todas', {
            method: 'POST',
            headers: {
                'X-CSRFToken': CSRF(),
                'X-Requested-With': 'XMLHttpRequest',
            },
        })
            .then(() => fetchNotifications())
            .catch(() => { });
    }

    // ── Utilidad: escapar HTML ───────────────────────────────────────────────
    function escHtml(s) {
        return String(s)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    // ── Init ─────────────────────────────────────────────────────────────────
    document.addEventListener('DOMContentLoaded', () => {
        const btn = document.getElementById('notif-btn');
        if (!btn) return;

        btn.addEventListener('click', (e) => { e.stopPropagation(); togglePanel(); });

        document.addEventListener('click', (e) => {
            if (panelOpen && !e.target.closest('#notif-panel') && !e.target.closest('#notif-btn')) {
                closePanel();
            }
        });

        fetchNotifications();
        setInterval(fetchNotifications, 45000);
    });
})();
