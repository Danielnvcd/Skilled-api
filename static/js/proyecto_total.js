// proyecto_total.js — Toggle expand/collapse for project cards

document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('.project-toggle').forEach(function (header) {
        header.addEventListener('click', function () {
            const chevron = header.querySelector('.chevron');
            const weeksDiv = header.closest('.project-card').querySelector('.project-weeks');

            if (chevron.classList.contains('open')) {
                chevron.classList.remove('open');
                weeksDiv.style.maxHeight = '0';
            } else {
                chevron.classList.add('open');
                weeksDiv.style.maxHeight = weeksDiv.scrollHeight + 'px';
            }
        });
    });

    // Filtro de proyectos
    const buscador = document.getElementById('buscadorProyectos');
    if (buscador) {
        buscador.addEventListener('input', function () {
            const term = this.value.toLowerCase().trim();
            const cards = document.querySelectorAll('.project-card');

            cards.forEach(card => {
                // Obtenemos el texto del nombre del proyecto (incluye el número, nombre y coordinador)
                const headerText = card.querySelector('.project-header').textContent.toLowerCase();

                if (term === '' || headerText.includes(term)) {
                    card.style.display = 'block';
                } else {
                    card.style.display = 'none';
                }
            });
        });
    }
});
