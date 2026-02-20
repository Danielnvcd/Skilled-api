document.addEventListener('DOMContentLoaded', function () {

    // Modal Elements
    const modal = document.getElementById('trabajadorModal');
    const form = document.getElementById('trabajadorForm');
    const btnNuevoTrabajador = document.getElementById('btnNuevoTrabajador');
    const btnCloseModalTop = document.getElementById('btnCloseModalTop');
    const btnCloseModalBottom = document.getElementById('btnCloseModalBottom');

    // Tab Elements
    const tabBtns = document.querySelectorAll('.tab-btn');
    const tabContents = document.querySelectorAll('.tab-content');

    // Search Element
    const searchInput = document.getElementById('searchInput');

    // === MODAL LOGIC ===
    function openModal() {
        form.reset();
        modal.classList.add('active');
        switchTab('laborales');
    }

    function closeModal() {
        modal.classList.remove('active');
    }

    if (btnNuevoTrabajador) btnNuevoTrabajador.addEventListener('click', openModal);
    if (btnCloseModalTop) btnCloseModalTop.addEventListener('click', closeModal);
    if (btnCloseModalBottom) btnCloseModalBottom.addEventListener('click', closeModal);

    // Close modal if clicked outside
    modal.addEventListener('click', function (e) {
        if (e.target === this) {
            closeModal();
        }
    });

    // === TABS LOGIC ===
    function switchTab(tabId) {
        // Esconder todos los contenidos
        tabContents.forEach(t => t.classList.remove('active'));

        // Quitar active a los botones
        tabBtns.forEach(b => b.classList.remove('active'));

        // Activar el tab actual
        const currentContent = document.getElementById('tab-' + tabId);
        if (currentContent) currentContent.classList.add('active');

        // Encontrar el boton correspondiente y activarlo
        const currentBtn = document.querySelector(`.tab-btn[data-target="${tabId}"]`);
        if (currentBtn) currentBtn.classList.add('active');
    }

    // Attach listener to all tab buttons
    tabBtns.forEach(btn => {
        btn.addEventListener('click', function () {
            const target = this.getAttribute('data-target');
            switchTab(target);
        });
    });

    // === SEARCH / FILTER LOGIC ===
    if (searchInput) {
        searchInput.addEventListener('keyup', function () {
            let filter = this.value.toUpperCase();
            let table = document.getElementById("workersTable");
            let tr = table.getElementsByTagName("tr");

            for (let i = 1; i < tr.length; i++) {
                let txtValue = tr[i].textContent || tr[i].innerText;
                if (txtValue.toUpperCase().indexOf(filter) > -1) {
                    tr[i].style.display = "";
                } else {
                    tr[i].style.display = "none";
                }
            }
        });
    }

});
