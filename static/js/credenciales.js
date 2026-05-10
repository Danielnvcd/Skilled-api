document.addEventListener('DOMContentLoaded', function () {
    // Modal Elements
    const modal = document.getElementById('credencialesModal');
    const form = document.getElementById('credencialesForm');
    const btnCloseModalTop = document.getElementById('btnCloseModalTop');
    const btnCloseModalBottom = document.getElementById('btnCloseModalBottom');
    const workerNameSpan = document.getElementById('modalWorkerName');
    const searchInput = document.getElementById('searchInput');
    const alertMessage = document.getElementById('alertMessage');

    // Credentials Logic Elements
    const selectPlanta = document.getElementById('selectPlanta');
    const customPlantaContainer = document.getElementById('customPlantaContainer');
    const customPlantaName = document.getElementById('customPlantaName');
    const credencialIdInput = document.getElementById('credencialIdInput');
    const credencialCaducidadInput = document.getElementById('credencialCaducidadInput');
    const btnAddCredential = document.getElementById('btnAddCredential');
    const credentialsList = document.getElementById('credentialsList');
    const credencialesJson = document.getElementById('credencialesJson');
    const observacionesInput = document.getElementById('observacionesInput');

    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    let currentWorkerId = null;
    let credentialsArray = [];

    // === VIEW FICHA MODAL LOGIC ===
    const viewFichaModal = document.getElementById('viewFichaModal');
    const btnCloseFicha = document.getElementById('btnCloseFicha');

    function closeFichaModal() {
        if (viewFichaModal) viewFichaModal.classList.remove('active');
    }

    if (btnCloseFicha) btnCloseFicha.addEventListener('click', closeFichaModal);
    if (viewFichaModal) {
        viewFichaModal.addEventListener('click', function (e) {
            if (e.target === viewFichaModal) closeFichaModal();
        });
    }

    document.querySelectorAll('.btn-view-ficha').forEach(function (btn) {
        btn.addEventListener('click', function () {
            var name = btn.getAttribute('data-name') || '-';
            var empno = btn.getAttribute('data-empno') || '-';
            var puesto = btn.getAttribute('data-puesto') || '-';
            var area = btn.getAttribute('data-area') || '-';
            var tipopago = btn.getAttribute('data-tipopago') || '-';
            var coord = btn.getAttribute('data-coord') || '-';
            var ubicacion = btn.getAttribute('data-ubicacion') || '-';
            var celular = btn.getAttribute('data-celular') || '-';
            var estado = btn.getAttribute('data-estado') || '-';
            var observaciones = btn.getAttribute('data-observaciones') || '';
            var foto = btn.getAttribute('data-foto') || '';
            var credIds = {};

            try { credIds = JSON.parse(btn.getAttribute('data-credids') || '{}'); } catch (e) { }

            // Populate modal
            var avatarDiv = document.getElementById('fichaAvatar');
            if (foto) {
                var img = document.createElement('img');
                img.src = foto;
                img.style.cssText = 'width:100%; height:100%; border-radius:50%; object-fit:cover;';
                avatarDiv.innerHTML = '';
                avatarDiv.appendChild(img);
                avatarDiv.style.background = 'transparent';
                avatarDiv.style.border = '3px solid rgba(255, 255, 255, 0.3)';
            } else {
                avatarDiv.innerHTML = '';
                avatarDiv.textContent = name.charAt(0).toUpperCase();
                avatarDiv.style.background = 'white';
            }

            document.getElementById('fichaName').textContent = name;
            document.getElementById('fichaEmpNo').textContent = 'No. ' + empno;
            document.getElementById('fichaPuesto').textContent = puesto;
            document.getElementById('fichaArea').textContent = area;
            document.getElementById('fichaTipoPago').textContent = tipopago;
            document.getElementById('fichaCoord').textContent = coord;
            document.getElementById('fichaUbicacion').textContent = ubicacion;
            document.getElementById('fichaEstado').textContent = estado;
            document.getElementById('fichaCelular').textContent = celular;

            var obsRow = document.getElementById('fichaObsRow');
            if (observaciones) {
                obsRow.style.display = '';
                document.getElementById('fichaObs').textContent = observaciones;
            } else {
                obsRow.style.display = 'none';
            }

            // Build credential chips
            var chipsContainer = document.getElementById('fichaCredChips');
            var plants = Object.keys(credIds);
            if (plants.length > 0) {
                chipsContainer.innerHTML = '';
                var today = new Date();
                today.setHours(0, 0, 0, 0);

                plants.forEach(function (planta) {
                    var credData = credIds[planta];
                    // Handle backward compatibility where credIds[planta] might just be a string ID
                    var cid = typeof credData === 'object' ? credData.id : credData;
                    var caducidad = typeof credData === 'object' ? credData.caducidad : null;

                    var isExpired = false;
                    var expText = '';
                    if (caducidad) {
                        var expDate = new Date(caducidad + 'T00:00:00'); // Ensure local timezone parsing
                        if (expDate < today) {
                            isExpired = true;
                        }
                        expText = ' <span style="font-size: 0.7rem; color: ' + (isExpired ? '#ef4444' : '#6b7280') + '; margin-left: 4px;">(' + (isExpired ? 'Caducada: ' : 'Vence: ') + esc(caducidad) + ')</span>';
                    }

                    var chipColor = isExpired ? '#fee2e2' : '#ecfdf5';
                    var borderColor = isExpired ? '#fca5a5' : '#a7f3d0';
                    var iconColor = isExpired ? '#ef4444' : '#10b981';
                    var iconClass = isExpired ? 'fa-times-circle' : 'fa-check-circle';

                    var chip = document.createElement('div');
                    chip.className = 'cred-chip cred-active';
                    chip.style.background = chipColor;
                    chip.style.borderColor = borderColor;
                    chip.innerHTML = '<i class="fas ' + iconClass + '" style="color: ' + iconColor + '; margin-right: 4px;"></i>' +
                        '<strong>' + esc(planta) + '</strong>' +
                        '<span class="cred-id">' + esc(cid) + '</span>' + expText;
                    chipsContainer.appendChild(chip);
                });
            } else {
                chipsContainer.innerHTML = '<p style="color: #9ca3af; font-size: 0.85rem; margin: 0;">Sin credenciales registradas</p>';
            }

            viewFichaModal.classList.add('active');
        });
    });

    // === SEARCH / FILTER LOGIC ===
    // Search is now handled server-side via form submission in credenciales.html

    // === MODAL LOGIC ===
    function closeModal() {
        modal.classList.remove('active');
        currentWorkerId = null;
    }

    if (btnCloseModalTop) btnCloseModalTop.addEventListener('click', closeModal);
    if (btnCloseModalBottom) btnCloseModalBottom.addEventListener('click', closeModal);

    // Close modal if clicked outside
    window.addEventListener('click', function (e) {
        if (e.target === modal) {
            closeModal();
        }
    });

    // Toggle custom planta input
    if (selectPlanta) {
        selectPlanta.addEventListener('change', function () {
            if (this.value === 'OTRA') {
                customPlantaContainer.style.display = 'block';
            } else {
                customPlantaContainer.style.display = 'none';
                customPlantaName.value = '';
            }
        });
    }

    function updateCredentialsUI() {
        if (!credentialsList) return;
        credentialsList.innerHTML = '';
        var today = new Date();
        today.setHours(0, 0, 0, 0);

        credentialsArray.forEach((cred, index) => {
            let isExpired = false;
            let statusBadge = '';

            if (cred.fecha_caducidad) {
                let expDate = new Date(cred.fecha_caducidad + 'T00:00:00');
                if (expDate < today) {
                    isExpired = true;
                }
                statusBadge = `<span style="font-size: 0.75rem; padding: 2px 6px; border-radius: 4px; background: ${isExpired ? '#fee2e2' : '#d1fae5'}; color: ${isExpired ? '#991b1b' : '#065f46'}; margin-left: 8px;">
                    ${isExpired ? 'Caducada' : 'Vigente'} (${esc(cred.fecha_caducidad)})
                </span>`;
            }

            const li = document.createElement('li');
            li.style.display = 'flex';
            li.style.justifyContent = 'space-between';
            li.style.alignItems = 'center';
            li.style.padding = '8px 12px';
            li.style.background = isExpired ? '#fef2f2' : '#f3f4f6';
            li.style.borderRadius = '6px';
            li.style.border = isExpired ? '1px solid #fecaca' : '1px solid #e5e7eb';

            li.innerHTML = `
                <div>
                    <strong style="color: #1f2937;">${esc(cred.planta)}</strong>
                    <span style="color: #6b7280; font-family: monospace; margin-left: 8px;">ID: ${esc(cred.credencial_id)}</span>
                    ${statusBadge}
                </div>
                <button type="button" class="btn-remove-cred" data-index="${index}" style="background: none; border: none; color: #ef4444; font-weight: bold; cursor: pointer;">X</button>
            `;
            credentialsList.appendChild(li);
        });

        // Attach event listeners safely
        document.querySelectorAll('.btn-remove-cred').forEach(btn => {
            btn.addEventListener('click', function () {
                const idx = parseInt(this.getAttribute('data-index'), 10);
                removeCredential(idx);
            });
        });

        credencialesJson.value = JSON.stringify(credentialsArray);
    }

    function removeCredential(index) {
        credentialsArray.splice(index, 1);
        updateCredentialsUI();
    }

    if (btnAddCredential) {
        btnAddCredential.addEventListener('click', function () {
            let planta = selectPlanta.value;
            if (planta === 'OTRA') {
                planta = customPlantaName.value.trim().toUpperCase();
                if (!planta) {
                    alert('Por favor especifica el nombre de la planta.');
                    return;
                }
            }

            const credId = credencialIdInput.value.trim();
            const fechaCaducidad = (credencialCaducidadInput && credencialCaducidadInput.value) ? credencialCaducidadInput.value : null;

            if (credId.length > 40) {
                alert('El ID de la credencial no puede superar los 40 caracteres.');
                return;
            }

            if (credentialsArray.find(c => c.planta === planta)) {
                alert('Ya agregaste una credencial para esta planta.');
                return;
            }

            credentialsArray.push({
                planta: planta,
                credencial_id: credId,
                fecha_caducidad: fechaCaducidad
            });

            // Reset UI
            credencialIdInput.value = '';
            if (credencialCaducidadInput) credencialCaducidadInput.value = '';
            if (selectPlanta.value === 'OTRA') {
                customPlantaName.value = '';
                selectPlanta.value = 'CAET';
                customPlantaContainer.style.display = 'none';
            }

            updateCredentialsUI();
        });
    }

    // Load data when edit is clicked
    const editBtns = document.querySelectorAll('.edit-btn');
    editBtns.forEach(btn => {
        btn.addEventListener('click', async function () {
            const id = this.getAttribute('data-id');
            const row = this.closest('tr');
            const name = row.querySelector('td:nth-child(2) div').innerText;
            workerNameSpan.textContent = name;
            currentWorkerId = id;

            // Optional: reset message
            alertMessage.style.display = 'none';
            alertMessage.textContent = '';

            // Clean specific inputs
            credencialIdInput.value = '';
            if (credencialCaducidadInput) credencialCaducidadInput.value = '';
            selectPlanta.value = 'CAET';
            customPlantaContainer.style.display = 'none';
            customPlantaName.value = '';
            if (observacionesInput) observacionesInput.value = '';

            try {
                const response = await fetch(`/trabajadores/get/${id}`);
                const data = await response.json();
                credentialsArray = data.credenciales || [];
                if (observacionesInput) observacionesInput.value = data.observaciones || '';
                updateCredentialsUI();
                modal.classList.add('active');
            } catch (err) {
                console.error("Error fetching credentials:", err);
                alert("Error al cargar credenciales del trabajador.");
            }
        });
    });

    // Handle form submit via AJAX
    if (form) {
        form.addEventListener('submit', async function (e) {
            e.preventDefault();

            // Auto-capture pending credentials
            if (credencialIdInput.value.trim() !== '') {
                let planta = selectPlanta.value;
                if (planta === 'OTRA' && customPlantaName) {
                    planta = customPlantaName.value.trim().toUpperCase() || 'OTRA';
                }
                const credId = credencialIdInput.value.trim();
                const fechaCaducidad = (credencialCaducidadInput && credencialCaducidadInput.value) ? credencialCaducidadInput.value : null;

                if (!credentialsArray.find(c => c.planta === planta)) {
                    credentialsArray.push({
                        planta: planta,
                        credencial_id: credId,
                        fecha_caducidad: fechaCaducidad
                    });
                    updateCredentialsUI();
                }
            }

            const submitBtn = document.getElementById('btnSaveCredenciales');
            submitBtn.disabled = true;
            submitBtn.textContent = 'Guardando...';

            const formData = new FormData(form);
            const csrfInput = document.querySelector('input[name="csrf_token"]');
            if (csrfInput) {
                formData.append('csrf_token', csrfInput.value);
            }

            try {
                const response = await fetch(`/trabajadores/credenciales/${currentWorkerId}`, {
                    method: 'POST',
                    body: formData,
                    headers: {
                        'X-CSRFToken': csrfInput ? csrfInput.value : ''
                    }
                });

                const result = await response.json();

                if (result.success) {
                    // Update table UI
                    alertMessage.textContent = result.message;
                    alertMessage.style.backgroundColor = '#d1fae5';
                    alertMessage.style.color = '#065f46';
                    alertMessage.style.display = 'block';

                    setTimeout(() => {
                        window.location.reload(); // Reload to see changes reflect on the table simply
                    }, 1000);
                } else {
                    alertMessage.textContent = result.error || 'Error al guardar';
                    alertMessage.style.backgroundColor = '#fee2e2';
                    alertMessage.style.color = '#991b1b';
                    alertMessage.style.display = 'block';
                    submitBtn.disabled = false;
                    submitBtn.textContent = 'Guardar Credenciales';
                }
            } catch (err) {
                console.error(err);
                alertMessage.textContent = 'Error de conexión';
                alertMessage.style.backgroundColor = '#fee2e2';
                alertMessage.style.color = '#991b1b';
                alertMessage.style.display = 'block';
                submitBtn.disabled = false;
                submitBtn.textContent = 'Guardar Credenciales';
            }
        });
    }
});
