/**
 * Filtro de fechas para Histórico de Nóminas
 * Filtra las tarjetas .hist-card por fecha usando input type="date"
 * Muestra cards cuya fecha_inicio coincida con la fecha seleccionada.
 */
(function () {
    var input = document.getElementById('searchFechaHistorico');
    var clearBtn = document.getElementById('clearSearchHistorico');
    var noResults = document.getElementById('noResultsHistorico');
    var cards = document.querySelectorAll('.hist-card');

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
                // Histórico solo tiene fecha_inicio, mostrar si coincide
                if (inicio && selectedDate === inicio) {
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
