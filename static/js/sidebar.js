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
        var oneYear = 365 * 24 * 3600;
        var expires = new Date(Date.now() + oneYear * 1000).toUTCString();
        // SameSite=Lax: más compatible que Strict — algunos navegadores y proxies
        // no persisten cookies Strict de forma fiable entre sesiones.
        // max-age + expires: doble cinto, max-age para navegadores modernos, expires
        // como fallback para los viejos.
        document.cookie = 'sidebar_collapsed=' + val
            + '; expires=' + expires
            + '; max-age=' + oneYear
            + '; path=/; SameSite=Lax';
        try { localStorage.setItem('sidebar-collapsed', String(collapsed)); } catch (_) {}
    }

    // Restore desde localStorage si la cookie se perdió pero el estado local persiste.
    // Esto cubre casos donde el navegador limpia cookies pero respeta localStorage.
    try {
        var lsState = localStorage.getItem('sidebar-collapsed');
        if (lsState === 'true' || lsState === 'false') {
            var wantCollapsed = (lsState === 'true');
            var isCollapsedNow = sidebar.classList.contains('collapsed');
            if (wantCollapsed !== isCollapsedNow) {
                // Render del servidor no coincide con la preferencia local → sincronizar
                // y reescribir la cookie para los siguientes requests.
                sidebar.classList.toggle('collapsed', wantCollapsed);
                saveState(wantCollapsed);
            }
        }
    } catch (_) {}

    toggleBtn.addEventListener('click', function () {
        var isCollapsed = sidebar.classList.toggle('collapsed');
        saveState(isCollapsed);
    });

    // ── Persistencia del scroll del menú ──────────────────────────
    // Evita el "brinco" al cambiar de página: la posición del scroll del
    // menú se guarda en sessionStorage y se restaura en cada navegación.
    // Solo si no hay posición guardada (primera visita en la sesión),
    // centramos el ítem activo.
    var menu       = document.getElementById('sidebar-menu');
    var activeItem = document.querySelector('#sidebar-menu [data-active="true"]');
    var SCROLL_KEY = 'sidebar-menu-scroll';

    if (menu) {
        // Restaurar inmediatamente y de forma síncrona (antes del primer paint
        // útil) para que el usuario no vea el menú en posición 0 y luego saltar.
        var savedScroll = null;
        try { savedScroll = sessionStorage.getItem(SCROLL_KEY); } catch (_) {}

        if (savedScroll !== null) {
            menu.scrollTop = parseInt(savedScroll, 10) || 0;
        } else if (activeItem) {
            // Primera visita: solo scrollear si el activo está fuera del viewport visible.
            // Usamos scrollIntoView con block:'nearest' que no mueve si ya está visible.
            // Lo hacemos en el próximo frame para que el navegador haya pintado layout.
            requestAnimationFrame(function () {
                var aTop    = activeItem.offsetTop;
                var aBottom = aTop + activeItem.clientHeight;
                var vTop    = menu.scrollTop;
                var vBottom = vTop + menu.clientHeight;
                if (aTop < vTop || aBottom > vBottom) {
                    activeItem.scrollIntoView({ block: 'center', behavior: 'auto' });
                }
            });
        }

        // Persistir scroll: cada vez que el usuario rueda el menú,
        // guardamos la posición (con throttle para no saturar).
        var scrollTimer = null;
        menu.addEventListener('scroll', function () {
            if (scrollTimer) return;
            scrollTimer = setTimeout(function () {
                try { sessionStorage.setItem(SCROLL_KEY, String(menu.scrollTop)); } catch (_) {}
                scrollTimer = null;
            }, 80);
        });
    }
})();
