// horas_lista_movil.js v=1
const datos = JSON.parse(document.getElementById('data-horas-lista').textContent);

document.addEventListener('DOMContentLoaded', () => {
    const sel = document.getElementById('modal-proyecto-id');
    if (sel && datos.proyectos) {
        datos.proyectos.forEach(p => {
            const opt = document.createElement('option');
            opt.value = p.id;
            opt.textContent = `${p.numero} - ${p.nombre || '(Sin nombre)'}`;
            sel.appendChild(opt);
        });
    }

    const modal = document.getElementById('modal-nueva-semana');
    document.getElementById('btn-nueva-semana').addEventListener('click', () => modal.classList.remove('hidden'));
    document.getElementById('btn-cancelar-modal').addEventListener('click', () => modal.classList.add('hidden'));
    modal.addEventListener('click', e => { if (e.target === modal) modal.classList.add('hidden'); });

    document.querySelectorAll('.flash-item').forEach(el => {
        setTimeout(() => { el.style.opacity = '0'; }, 3000);
        setTimeout(() => el.remove(), 3500);
    });
});
