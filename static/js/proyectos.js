document.addEventListener('DOMContentLoaded', function () {
    const modal = document.getElementById('proyectoModal');
    const form = document.getElementById('proyectoForm');
    const btnNuevo = document.getElementById('btnNuevoProyecto');
    const btnCloseTop = document.getElementById('btnCloseModalTop');
    const btnCloseBottom = document.getElementById('btnCloseModalBottom');

    const searchInput = document.getElementById('searchInput');
    const searchParticipantes = document.getElementById('searchParticipantes');

    // --- BÚSQUEDA EN TABLA ---
    if (searchInput) {
        searchInput.addEventListener('input', function (e) {
            const term = e.target.value.toLowerCase();
            const rows = document.querySelectorAll('.proyecto-row');
            rows.forEach(row => {
                const text = row.innerText.toLowerCase();
                if (text.includes(term)) {
                    row.style.display = '';
                } else {
                    row.style.display = 'none';
                }
            });
        });
    }

    // --- FILTRAR PARTICIPANTES EN MODAL ---
    if (searchParticipantes) {
        searchParticipantes.addEventListener('input', function (e) {
            const term = e.target.value.toLowerCase();
            const items = document.querySelectorAll('.participant-item');
            items.forEach(item => {
                const name = item.getAttribute('data-name') || '';
                if (name.includes(term)) {
                    item.style.display = 'flex';
                } else {
                    item.style.display = 'none';
                }
            });
        });
    }

    // --- MODAL logic ---
    function openModal() {
        form.reset();

        // Clear all checkboxes
        document.querySelectorAll('.participant-check').forEach(chk => chk.checked = false);

        // Reset Search
        if (searchParticipantes) {
            searchParticipantes.value = '';
            // trigger event
            searchParticipantes.dispatchEvent(new Event('input'));
        }

        const modalHeader = document.querySelector('.modal-header h3');
        if (modalHeader) modalHeader.textContent = 'Registrar Proyecto';
        form.action = '/proyectos/agregar';

        // Show Checkbox for Activo but check it by default
        const checkActivo = document.getElementById('checkActivo');
        if (checkActivo) {
            checkActivo.checked = true;
            checkActivo.disabled = false;
        }

        modal.classList.add('active');
    }

    function closeModal() {
        modal.classList.remove('active');
    }

    if (btnNuevo) btnNuevo.addEventListener('click', openModal);
    if (btnCloseTop) btnCloseTop.addEventListener('click', closeModal);
    if (btnCloseBottom) btnCloseBottom.addEventListener('click', closeModal);

    // Close clicking outside
    modal.addEventListener('click', function (e) {
        if (e.target === modal) {
            closeModal();
        }
    });

    // --- EDIT logic ---
    document.querySelectorAll('.view-btn').forEach(btn => {
        btn.addEventListener('click', async function () {
            const id = this.getAttribute('data-id');
            try {
                const response = await fetch(`/proyectos/get/${id}`);
                const data = await response.json();

                openModal();
                const modalHeader = document.querySelector('.modal-header h3');
                if (modalHeader) modalHeader.textContent = 'Editar Proyecto';
                form.action = `/proyectos/editar/${id}`;

                // Set text fields
                form.querySelector('input[name="numero_proyecto"]').value = data.numero_proyecto;
                form.querySelector('input[name="nombre"]').value = data.nombre;

                // Set select
                if (data.coordinador_id) {
                    form.querySelector('select[name="coordinador_id"]').value = data.coordinador_id;
                }

                // Set activo
                const checkActivo = document.getElementById('checkActivo');
                if (checkActivo) {
                    checkActivo.checked = data.activo;
                }

                // Set Participants Checkboxes
                if (data.participantes_ids && data.participantes_ids.length > 0) {
                    data.participantes_ids.forEach(pid => {
                        const chk = document.getElementById('part_' + pid);
                        if (chk) chk.checked = true;
                    });
                }

            } catch (error) {
                console.error('Error fetching proyecto data:', error);
                alert('No se pudo cargar la información del proyecto.');
            }
        });
    });

    // --- BEFORE SUBMIT ---
    form.addEventListener('submit', function (e) {
        // Collect checked participants
        const checkedBoxes = document.querySelectorAll('.participant-check:checked');
        const pArray = Array.from(checkedBoxes).map(chk => parseInt(chk.value));

        const hidInput = document.getElementById('participantesJson');
        if (hidInput) {
            hidInput.value = JSON.stringify(pArray);
        }
    });
});
