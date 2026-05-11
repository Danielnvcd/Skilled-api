/**
 * sidebar.js  v3
 * Controla la sidebar colapsable de Skilled.
 *
 * Estrategia anti-flash:
 *   1. El servidor lee la cookie "sidebar_collapsed" y aplica la clase
 *      "collapsed" en el HTML inicial — sin parpadeo.
 *   2. Este script solo gestiona el toggle interactivo y sincroniza
 *      la cookie para el siguiente request.
 *   3. La clase "sidebar-pre-collapsed" en <body> desactiva las
 *      transiciones CSS en el primer render; la quitamos aquí para
 *      habilitarlas después.
 */
(function () {
    'use strict';

    var sidebar   = document.getElementById('sidebar');
    var toggleBtn = document.getElementById('sidebar-toggle');
    if (!sidebar || !toggleBtn) return;

    // Quita la clase que bloquea transiciones (puesta por Jinja al cargar)
    document.body.classList.remove('sidebar-pre-collapsed');

    /** Guarda el estado en cookie (para el servidor) y localStorage (respaldo). */
    function saveState(collapsed) {
        var val = collapsed ? '1' : '0';
        // Cookie de 1 año, path raíz, SameSite=Strict
        var expires = new Date(Date.now() + 365 * 24 * 3600 * 1000).toUTCString();
        document.cookie = 'sidebar_collapsed=' + val
            + '; expires=' + expires
            + '; path=/; SameSite=Strict';
        try { localStorage.setItem('sidebar-collapsed', String(collapsed)); } catch (_) {}
    }

    toggleBtn.addEventListener('click', function () {
        var isCollapsed = sidebar.classList.toggle('collapsed');
        saveState(isCollapsed);
    });

    // Scroll automático al ítem activo
    var activeItem = document.querySelector('#sidebar-menu [data-active="true"]');
    var menu       = document.getElementById('sidebar-menu');
    if (activeItem && menu) {
        var attempts = 0;
        var id = setInterval(function () {
            attempts++;
            if (menu.scrollHeight > menu.clientHeight || attempts > 30) {
                clearInterval(id);
                var top = activeItem.offsetTop;
                var h   = menu.clientHeight;
                var ih  = activeItem.clientHeight;
                if (top > h / 2) {
                    menu.scrollTop = top - h / 2 + ih / 2;
                }
            }
        }, 20);
    }
})();
