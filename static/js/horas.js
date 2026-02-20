document.addEventListener('DOMContentLoaded', function () {
    const modal = document.getElementById('reporteModal');
    const btnNuevo = document.getElementById('btnNuevoReporte');
    const btnCloseTop = document.getElementById('btnCloseModalTop');
    const btnCloseBottom = document.getElementById('btnCloseModalBottom');

    function openModal() { modal.classList.add('active'); }
    function closeModal() { modal.classList.remove('active'); }

    if (btnNuevo) btnNuevo.addEventListener('click', openModal);
    if (btnCloseTop) btnCloseTop.addEventListener('click', closeModal);
    if (btnCloseBottom) btnCloseBottom.addEventListener('click', closeModal);

    if (modal) {
        modal.addEventListener('click', function (e) {
            if (e.target === this) closeModal();
        });
    }
});
