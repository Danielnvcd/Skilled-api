// ficha_movil.js v=1
document.addEventListener('DOMContentLoaded', () => {

    // ─── Acordeón ────────────────────────────────────────────────────────────
    document.querySelectorAll('.ficha-toggle').forEach(btn => {
        btn.addEventListener('click', () => {
            const bodyId = btn.dataset.target;
            const body = document.getElementById(bodyId);
            const chevron = btn.querySelector('.ficha-chevron');
            const isOpen = btn.getAttribute('aria-expanded') === 'true';

            btn.setAttribute('aria-expanded', String(!isOpen));
            body.style.display = isOpen ? 'none' : 'block';
            chevron.style.transform = isOpen ? '' : 'rotate(180deg)';
        });
    });

    // ─── Búsqueda ─────────────────────────────────────────────────────────────
    const input = document.getElementById('ficha-search');
    const countEl = document.getElementById('ficha-count');
    if (!input) return;

    const items = Array.from(document.querySelectorAll('.ficha-item')).map(el => ({
        el,
        text: (el.dataset.nombre + ' ' + el.dataset.num)
    }));
    const total = items.length;

    input.addEventListener('input', () => {
        const q = input.value.trim().toLowerCase();
        let vis = 0;
        items.forEach(({ el, text }) => {
            const match = !q || text.includes(q);
            el.style.display = match ? '' : 'none';
            if (match) vis++;
        });
        if (q) {
            countEl.textContent = `${vis} de ${total} empleados`;
            countEl.style.display = 'block';
        } else {
            countEl.style.display = 'none';
        }
    });
});
