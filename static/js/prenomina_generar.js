document.addEventListener('DOMContentLoaded', function () {
    console.log("Módulo de Prenómina (Generación) cargado.");

    const btnGuardar = document.getElementById('btnGuardarNominas');
    if (btnGuardar) {
        btnGuardar.addEventListener('click', function (e) {
            e.preventDefault();

            if (!confirm("¿Estás seguro que deseas guardar y asentar estas nóminas?\nEsta acción marcará la semana como 'CERRADA' y generará el registro contable final.")) {
                return;
            }

            const fechaStr = this.getAttribute('data-fecha-str');
            const url = `/prenomina/guardar/${fechaStr}`;

            // CSRF token is usually in a meta tag if we set it up, or we can just send the POST
            // We should get CSRF from meta tag if available
            const csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content');

            const headers = {
                'Content-Type': 'application/json'
            };

            if (csrfToken) {
                headers['X-CSRFToken'] = csrfToken;
            }

            // Cambiar estado visual del botón
            const originalText = btnGuardar.innerHTML;
            btnGuardar.innerHTML = 'Guardando...';
            btnGuardar.disabled = true;

            fetch(url, {
                method: 'POST',
                headers: headers
            })
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        alert('¡Éxito! ' + data.message);
                        window.location.href = '/prenomina/';
                    } else {
                        alert('Error: ' + data.message);
                        btnGuardar.innerHTML = originalText;
                        btnGuardar.disabled = false;
                    }
                })
                .catch(error => {
                    console.error('Error:', error);
                    alert('Ocurrió un error de red al intentar guardar.');
                    btnGuardar.innerHTML = originalText;
                    btnGuardar.disabled = false;
                });
        });
    }
});
