/**
 * Filtro de fechas para Reporte de Horas
 * Filtra las tarjetas .reporte-card por rango de fecha usando input type="date"
 * Muestra cards cuyo rango [fecha_inicio, fecha_fin] incluya la fecha seleccionada.
 */
(function () {
    var input = document.getElementById('searchFechaHoras');
    var clearBtn = document.getElementById('clearSearchHoras');
    var noResults = document.getElementById('noResultsHoras');
    var cards = document.querySelectorAll('.reporte-card');

    if (!input || cards.length === 0) return;

    function filterCards() {
        var selectedDate = input.value;
        clearBtn.style.display = selectedDate ? 'block' : 'none';

        var visibleCount = 0;
        for (var i = 0; i < cards.length; i++) {
            var card = cards[i];
            if (!selectedDate) {
                card.style.display = '';
                visibleCount++;
            } else {
                var inicio = card.getAttribute('data-fecha-inicio');
                var fin = card.getAttribute('data-fecha-fin');
                if (inicio && selectedDate >= inicio && (!fin || selectedDate <= fin)) {
                    card.style.display = '';
                    visibleCount++;
                } else if (inicio && selectedDate === inicio) {
                    card.style.display = '';
                    visibleCount++;
                } else {
                    card.style.display = 'none';
                }
            }
        }
        noResults.style.display = (selectedDate && visibleCount === 0) ? 'block' : 'none';
    }

    input.addEventListener('change', filterCards);
    clearBtn.addEventListener('click', function () { input.value = ''; filterCards(); });
})();
