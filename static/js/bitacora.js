document.addEventListener('click', async (e) => {
    // Manejar click en boton "Ver"
    const btn = e.target.closest('.btn-ver-detalle');
    if (btn) {
        e.preventDefault();
        const logId = btn.getAttribute('data-log-id');
        if (!logId) return;

        const modal = document.getElementById('bitacoraModal');
        if (!modal) {
            console.error("No se encontró el modal #bitacoraModal en el DOM");
            return;
        }

        const contentData = document.getElementById('bitacoraModalData');
        const contentLoading = document.getElementById('bitacoraModalLoading');
        const contentError = document.getElementById('bitacoraModalError');

        // Reset state
        modal.classList.add('active');
        contentData.style.display = 'none';
        contentError.style.display = 'none';
        contentLoading.style.display = 'block';

        try {
            const res = await fetch('/bitacora/api/log/' + logId);
            if (!res.ok) throw new Error('Error en backend');
            
            const data = await res.json();
            
            document.getElementById('modalAction').textContent = data.action;
            document.getElementById('modalUser').textContent = data.user;
            document.getElementById('modalDate').textContent = data.date;
            document.getElementById('modalIp').textContent = data.ip;
            document.getElementById('modalLocation').textContent = data.location;
            
            contentLoading.style.display = 'none';
            contentData.style.display = 'block';
        } catch (err) {
            console.error("Error fetching log detail:", err);
            contentLoading.style.display = 'none';
            contentError.style.display = 'block';
        }
        return;
    }

    // Manejar cerrar modal con boton X
    const closeBtn = e.target.closest('.bitacora-modal-close');
    if (closeBtn) {
        const modal = closeBtn.closest('.bitacora-modal-overlay');
        if (modal) modal.classList.remove('active');
        return;
    }

    // Manejar cerrar modal dando click afuera
    if (e.target.classList.contains('bitacora-modal-overlay')) {
        e.target.classList.remove('active');
    }
});
