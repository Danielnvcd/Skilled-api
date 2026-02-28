// Ficha Técnica Javascript

document.addEventListener('DOMContentLoaded', () => {
    const searchInput = document.getElementById('busquedaFicha');
    const fichas = document.querySelectorAll('.ficha-card');

    if (searchInput) {
        searchInput.addEventListener('input', function () {
            const filter = this.value.toLowerCase();

            fichas.forEach(ficha => {
                const nombreItem = ficha.querySelector('.ficha-title h3');
                const numItem = ficha.querySelector('.ficha-title p');

                const nombre = nombreItem ? nombreItem.textContent.toLowerCase() : '';
                const num = numItem ? numItem.textContent.toLowerCase() : '';

                if (nombre.includes(filter) || num.includes(filter)) {
                    ficha.style.display = '';
                } else {
                    ficha.style.display = 'none';
                }
            });
        });
    }
});
