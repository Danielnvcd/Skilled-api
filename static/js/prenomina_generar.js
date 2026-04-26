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
                        window.location.href = '/prenomina/editar/' + fechaStr;
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

    const btnDescargasIndividuales = document.querySelectorAll('.btn-descarga-individual');
    btnDescargasIndividuales.forEach(btn => {
        btn.addEventListener('click', function (e) {
            const nombre = this.getAttribute('data-nombre');
            if (!confirm(`¿Estás seguro que deseas generar y descargar de forma individual el recibo en PDF para: ${nombre}?`)) {
                e.preventDefault();
            }
        });
    });

    const csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content');

    const modalCorreos = document.getElementById('modalCorreosTodos');
    const btnEnviarTodos = document.getElementById('btnEnviarTodos');
    const btnCerrarModal = document.getElementById('btnCerrarModalCorreos');

    const modalIndividual = document.getElementById('modalCorreoIndividual');
    const btnCerrarModalInd = document.getElementById('btnCerrarModalIndividual');
    const btnCerrarModalIndFooter = document.getElementById('btnCerrarModalIndividualFooter');

    function abrirModalIndividual() {
        document.getElementById('modalIndividualCargando').style.display = 'block';
        document.getElementById('modalIndividualResultado').style.display = 'none';
        btnCerrarModalIndFooter.style.display = 'none';
        modalIndividual.style.display = 'flex';
    }

    function mostrarResultadoIndividual(data) {
        document.getElementById('modalIndividualCargando').style.display = 'none';
        const mensaje = document.getElementById('modalIndividualMensaje');
        const spamAviso = document.getElementById('modalIndividualSpamAviso');
        if (data.success) {
            mensaje.innerHTML = `
                <div style="display:flex;align-items:center;gap:0.75rem;background:#dcfce7;border-radius:8px;padding:1rem;">
                    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#16a34a" stroke-width="2.5"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>
                    <span style="color:#15803d;font-weight:600;">${data.message}</span>
                </div>`;
            spamAviso.style.display = 'flex';
        } else {
            mensaje.innerHTML = `
                <div style="display:flex;align-items:center;gap:0.75rem;background:#fee2e2;border-radius:8px;padding:1rem;">
                    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#dc2626" stroke-width="2.5"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>
                    <span style="color:#b91c1c;font-weight:600;">${data.message}</span>
                </div>`;
            spamAviso.style.display = 'none';
        }
        document.getElementById('modalIndividualResultado').style.display = 'block';
        btnCerrarModalIndFooter.style.display = 'inline-block';
    }

    if (btnCerrarModalInd) {
        btnCerrarModalInd.addEventListener('click', () => { modalIndividual.style.display = 'none'; });
    }
    if (btnCerrarModalIndFooter) {
        btnCerrarModalIndFooter.addEventListener('click', () => { modalIndividual.style.display = 'none'; });
    }
    if (modalIndividual) {
        modalIndividual.addEventListener('click', function (e) {
            if (e.target === this) this.style.display = 'none';
        });
    }

    function abrirModal() {
        document.getElementById('modalCorreosCargando').style.display = 'block';
        document.getElementById('modalCorreosResultados').style.display = 'none';
        modalCorreos.style.display = 'flex';
    }

    function mostrarResultados(data) {
        document.getElementById('modalCorreosCargando').style.display = 'none';

        const resumen = document.getElementById('modalCorreosResumen');
        resumen.innerHTML = `
            <div style="background:#dcfce7; border-radius:8px; padding:0.75rem 1.25rem; flex:1; min-width:120px; text-align:center;">
                <div style="font-size:1.5rem; font-weight:700; color:#16a34a;">${data.enviados}</div>
                <div style="font-size:0.8rem; color:#15803d;">Enviados</div>
            </div>
            <div style="background:#fef9c3; border-radius:8px; padding:0.75rem 1.25rem; flex:1; min-width:120px; text-align:center;">
                <div style="font-size:1.5rem; font-weight:700; color:#ca8a04;">${data.sin_correo}</div>
                <div style="font-size:0.8rem; color:#a16207;">Sin correo</div>
            </div>
            <div style="background:#fee2e2; border-radius:8px; padding:0.75rem 1.25rem; flex:1; min-width:120px; text-align:center;">
                <div style="font-size:1.5rem; font-weight:700; color:#dc2626;">${data.errores}</div>
                <div style="font-size:0.8rem; color:#b91c1c;">Errores</div>
            </div>`;

        const tbody = document.getElementById('modalCorreosTabla');
        tbody.innerHTML = '';
        data.resultados.forEach(r => {
            const iconos = {
                enviado:    '<span style="color:#16a34a; font-weight:600;">✓ Enviado</span>',
                sin_correo: '<span style="color:#ca8a04; font-weight:600;">⚠ Sin correo</span>',
                error:      `<span style="color:#dc2626; font-weight:600;" title="${r.detalle || ''}">✗ Error</span>`
            };
            const bg = r.estado === 'enviado' ? '' : r.estado === 'sin_correo' ? 'background:#fffbeb;' : 'background:#fff1f2;';
            tbody.innerHTML += `
                <tr style="border-bottom:1px solid #f3f4f6; ${bg}">
                    <td style="padding:0.55rem 0.75rem;">${r.nombre}</td>
                    <td style="padding:0.55rem 0.75rem; color:#6b7280;">${r.correo}</td>
                    <td style="padding:0.55rem 0.75rem; text-align:center;">${iconos[r.estado] || r.estado}</td>
                </tr>`;
        });

        document.getElementById('modalCorreosResultados').style.display = 'block';
    }

    if (btnEnviarTodos) {
        btnEnviarTodos.addEventListener('click', async function () {
            if (!confirm('¿Enviar recibo por correo a todos los trabajadores con correo registrado?')) return;
            abrirModal();
            btnEnviarTodos.disabled = true;
            try {
                const resp = await fetch(this.dataset.url, {
                    method: 'POST',
                    headers: { 'X-CSRFToken': csrfToken || '', 'Content-Type': 'application/json' }
                });
                const data = await resp.json();
                mostrarResultados(data);
            } catch (e) {
                document.getElementById('modalCorreosCargando').innerHTML = '<p style="color:#dc2626; text-align:center;">Error de red al enviar los correos.</p>';
            } finally {
                btnEnviarTodos.disabled = false;
            }
        });
    }

    if (btnCerrarModal) {
        btnCerrarModal.addEventListener('click', () => { modalCorreos.style.display = 'none'; });
    }
    if (modalCorreos) {
        modalCorreos.addEventListener('click', function (e) {
            if (e.target === this) this.style.display = 'none';
        });
    }

    document.querySelectorAll('.btn-enviar-correo').forEach(btn => {
        btn.addEventListener('click', async function () {
            const url = this.dataset.url;
            const nombre = this.dataset.nombre;
            if (!confirm(`¿Enviar recibo por correo a ${nombre}?`)) return;

            abrirModalIndividual();
            this.disabled = true;
            const textoOriginal = this.innerHTML;
            this.textContent = 'Enviando...';

            try {
                const resp = await fetch(url, {
                    method: 'POST',
                    headers: {
                        'X-CSRFToken': csrfToken || '',
                        'Content-Type': 'application/json'
                    }
                });
                const data = await resp.json();
                mostrarResultadoIndividual(data);
            } catch (e) {
                mostrarResultadoIndividual({ success: false, message: 'Error de red al enviar el correo.' });
            } finally {
                this.disabled = false;
                this.innerHTML = textoOriginal;
            }
        });
    });
});
