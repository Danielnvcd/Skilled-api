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

    // Date Validation on form submit
    const reporteForm = document.getElementById('reporteForm');
    if (reporteForm) {
        reporteForm.addEventListener('submit', function (e) {
            const startDateStr = document.querySelector('input[name="fecha_inicio_semana"]').value;
            const endDateStr = document.querySelector('input[name="fecha_fin_semana"]').value;

            if (startDateStr && endDateStr) {
                const startDate = new Date(startDateStr);
                const endDate = new Date(endDateStr);

                if (startDate >= endDate) {
                    e.preventDefault();
                    alert("La Fecha de Inicio debe ser mayor a la Fecha de Fin.");
                }
            }
        });
    }
});
