/**
 * Filtros para Prenómina
 * - Filtra cards por fecha (input type="date")
 * - Filtra por estado (segmented: all / pending / done)
 * - Oculta separadores de mes que queden sin cards visibles
 */
(function () {
    var input = document.getElementById('searchFechaPrenomina');
    var clearBtn = document.getElementById('clearSearchPrenomina');
    var noResults = document.getElementById('noResultsPrenomina');
    var segmented = document.getElementById('pnSegmented');
    var cards = document.querySelectorAll('.rep-card');
    var monthSeps = document.querySelectorAll('.pn-month-sep');

    if (cards.length === 0) return;

    var currentStatus = 'all';

    function dateMatch(card, selectedDate) {
        if (!selectedDate) return true;
        var inicio = card.getAttribute('data-fecha-inicio');
        var fin = card.getAttribute('data-fecha-fin');
        if (!inicio) return false;
        if (selectedDate >= inicio && (!fin || selectedDate <= fin)) return true;
        if (selectedDate === inicio) return true;
        return false;
    }

    function statusMatch(card) {
        if (currentStatus === 'all') return true;
        return card.getAttribute('data-status') === currentStatus;
    }

    function applyFilters() {
        var selectedDate = input ? input.value : '';
        if (clearBtn) clearBtn.style.display = selectedDate ? 'block' : 'none';

        var visibleCount = 0;
        cards.forEach(function (card) {
            var visible = dateMatch(card, selectedDate) && statusMatch(card);
            card.style.display = visible ? '' : 'none';
            if (visible) visibleCount++;
        });

        // Hide month separators with no visible cards underneath
        monthSeps.forEach(function (sep) {
            var hasVisible = false;
            var node = sep.nextElementSibling;
            while (node && !node.classList.contains('pn-month-sep')) {
                if (node.classList.contains('rep-card') && node.style.display !== 'none') {
                    hasVisible = true;
                    break;
                }
                node = node.nextElementSibling;
            }
            sep.style.display = hasVisible ? '' : 'none';
        });

        if (noResults) {
            noResults.style.display = (visibleCount === 0 && (selectedDate || currentStatus !== 'all')) ? 'block' : 'none';
        }
    }

    if (input) input.addEventListener('change', applyFilters);
    if (clearBtn) clearBtn.addEventListener('click', function () { input.value = ''; applyFilters(); });

    if (segmented) {
        segmented.addEventListener('click', function (e) {
            var btn = e.target.closest('.pn-seg-btn');
            if (!btn) return;
            segmented.querySelectorAll('.pn-seg-btn').forEach(function (b) { b.classList.remove('active'); });
            btn.classList.add('active');
            currentStatus = btn.getAttribute('data-status') || 'all';
            applyFilters();
        });
    }
})();
