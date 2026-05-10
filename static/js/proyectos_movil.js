// proyectos_movil.js v=1
document.addEventListener('DOMContentLoaded', () => {

    // ─── Acordeón de trabajadores ─────────────────────────────────────────────
    document.querySelectorAll('.toggle-workers').forEach(btn => {
        btn.addEventListener('click', () => {
            const bodyId = btn.dataset.target;
            const body = document.getElementById(bodyId);
            const chevron = btn.querySelector('i');
            const isOpen = btn.getAttribute('aria-expanded') === 'true';

            btn.setAttribute('aria-expanded', String(!isOpen));
            body.style.display = isOpen ? 'none' : 'block';
            chevron.style.transform = isOpen ? '' : 'rotate(180deg)';
        });
    });

    // ─── Búsqueda ─────────────────────────────────────────────────────────────
    const input = document.getElementById('proy-search');
    if (!input) return;

    const cards = Array.from(document.querySelectorAll('.proy-card'));

    input.addEventListener('input', () => {
        const q = input.value.trim().toLowerCase();
        cards.forEach(card => {
            const texto = (card.dataset.texto || '').toLowerCase();
            card.style.display = !q || texto.includes(q) ? '' : 'none';
        });
    });
});
