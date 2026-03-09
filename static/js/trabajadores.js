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

        // Reset arrays and UI for new worker
        if (typeof credentialsArray !== 'undefined') {
            credentialsArray = [];
            const credencialesJson = document.getElementById('credencialesJson');
            if (credencialesJson) credencialesJson.value = "[]";
            const credentialsList = document.getElementById('credentialsList');
            if (credentialsList) credentialsList.innerHTML = '';
        }

        if (typeof documentosArray !== 'undefined') {
            documentosArray = [];
            const documentosList = document.getElementById('documentosList');
            if (documentosList) documentosList.innerHTML = '<li style="color: #6B7280; font-size: 0.9rem;">No hay documentos subidos (El trabajador debe guardarse primero).</li>';
            const tabBtnDocumentos = document.getElementById('tabBtnDocumentos');
            if (tabBtnDocumentos) tabBtnDocumentos.style.display = 'none'; // Only show docs on Edit
        }

        modalTitle.textContent = 'Nuevo Trabajador';
        form.action = '/trabajadores/agregar';
        enableFormElements();

        modal.classList.add('active');
        switchTab('laborales');
    }

    function closeModal() {
        modal.classList.remove('active');
        const viewModal = document.getElementById('viewWorkerModal');
        if (viewModal) viewModal.classList.remove('active');
    }

    if (btnNuevoTrabajador) btnNuevoTrabajador.addEventListener('click', openModal);
    if (btnCloseModalTop) btnCloseModalTop.addEventListener('click', closeModal);
    if (btnCloseModalBottom) btnCloseModalBottom.addEventListener('click', closeModal);

    // View Modal specific closes
    const btnCloseViewModal = document.getElementById('btnCloseViewModal');
    const btnCloseViewModalBottom = document.getElementById('btnCloseViewModalBottom');
    if (btnCloseViewModal) btnCloseViewModal.addEventListener('click', closeModal);
    if (btnCloseViewModalBottom) btnCloseViewModalBottom.addEventListener('click', closeModal);

    // Close modal if clicked outside
    modal.addEventListener('click', function (e) {
        if (e.target === this) {
            closeModal();
        }
    });

    // Close view modal if clicked outside
    const viewModal = document.getElementById('viewWorkerModal');
    if (viewModal) {
        viewModal.addEventListener('click', function (e) {
            if (e.target === this) {
                closeModal();
            }
        });
    }

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
    // Search is now handled server-side via form submission in trabajadores.html

    // === CREDENTIALS LOGIC ===
    const selectPlanta = document.getElementById('selectPlanta');
    const customPlantaContainer = document.getElementById('customPlantaContainer');
    const customPlantaName = document.getElementById('customPlantaName');
    const credencialIdInput = document.getElementById('credencialIdInput');
    const credencialCaducidadInput = document.getElementById('credencialCaducidadInput');
    const btnAddCredential = document.getElementById('btnAddCredential');
    const credentialsList = document.getElementById('credentialsList');
    const credencialesJson = document.getElementById('credencialesJson');

    let credentialsArray = [];

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
                    ${isExpired ? 'Caducada' : 'Vigente'} (${cred.fecha_caducidad})
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
                    <strong style="color: #1f2937;">${cred.planta}</strong>
                    <span style="color: #6b7280; font-family: monospace; margin-left: 8px;">ID: ${cred.credencial_id}</span>
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

            // Check max limits just in case
            if (credId.length > 40) {
                alert('El ID de la credencial no puede superar los 40 caracteres.');
                return;
            }

            // Check if plant is already added
            if (credentialsArray.find(c => c.planta === planta)) {
                alert('Ya agregaste una credencial para esta planta.');
                return;
            }

            credentialsArray.push({
                planta: planta,
                credencial_id: credId,
                fecha_caducidad: fechaCaducidad
            });

            // Rest UI
            credencialIdInput.value = '';
            if (credencialCaducidadInput) credencialCaducidadInput.value = '';
            if (selectPlanta.value === 'OTRA') {
                customPlantaName.value = '';
                selectPlanta.value = 'CAET'; // Reset default
                customPlantaContainer.style.display = 'none';
            }

            updateCredentialsUI();
        });
    }

    // === DOCUMENTS LOGIC ===
    const tabBtnDocumentos = document.getElementById('tabBtnDocumentos');
    const fileUploadInput = document.getElementById('fileUploadInput');
    const btnUploadDoc = document.getElementById('btnUploadDoc');
    const documentosList = document.getElementById('documentosList');
    const docTipoDocumento = document.getElementById('docTipoDocumento');
    const docTipoCustom = document.getElementById('docTipoCustom');

    // Toggle custom tipo input
    if (docTipoDocumento) {
        docTipoDocumento.addEventListener('change', function () {
            if (docTipoCustom) {
                docTipoCustom.style.display = this.value === 'Otro' ? 'block' : 'none';
                if (this.value !== 'Otro') docTipoCustom.value = '';
            }
        });
    }

    let currentWorkerId = null;
    let documentosArray = [];

    function updateDocumentosUI() {
        if (!documentosList) return;
        documentosList.innerHTML = '';
        if (documentosArray.length === 0) {
            documentosList.innerHTML = '<li style="color: #6B7280; font-size: 0.9rem;">No hay documentos subidos.</li>';
            return;
        }

        documentosArray.forEach((doc) => {
            const li = document.createElement('li');
            li.style.display = 'flex';
            li.style.justifyContent = 'space-between';
            li.style.alignItems = 'center';
            li.style.padding = '8px 12px';
            li.style.background = '#f3f4f6';
            li.style.borderRadius = '6px';
            li.style.border = '1px solid #e5e7eb';

            let caducadoHtml = '';
            if (doc.fecha_fin) {
                const hoy = new Date();
                hoy.setHours(0, 0, 0, 0);
                const [year, month, day] = doc.fecha_fin.split('-');
                const fin = new Date(year, month - 1, day);

                if (fin < hoy) {
                    caducadoHtml = '<span style="color: #ef4444; font-weight: bold; font-size: 0.75rem; margin-left: 8px; border: 1px solid #ef4444; border-radius: 4px; padding: 1px 4px;">Caducado</span>';
                }
            }

            const tipoHtml = doc.tipo_documento ? `<span style="font-size: 0.7rem; background: #eff6ff; color: #2563eb; padding: 2px 6px; border-radius: 4px; margin-left: 6px;">${doc.tipo_documento}</span>` : '';

            li.innerHTML = `
                <div style="display: flex; align-items: center; gap: 10px; flex-wrap: wrap;">
                    <a href="/trabajadores/documento/${doc.id}" target="_blank" style="color: #2563eb; text-decoration: none; font-weight: 500;">
                        ${doc.nombre_archivo}
                    </a>
                    ${tipoHtml}
                    ${caducadoHtml}
                    <span style="font-size: 0.75rem; color: #9ca3af;">${new Date(doc.fecha_subida).toLocaleDateString()}</span>
                </div>
                <button type="button" class="btn-delete-doc" data-id="${doc.id}" style="background: none; border: none; color: #ef4444; font-weight: bold; cursor: pointer;">X</button>
            `;
            documentosList.appendChild(li);
        });

        // Attach delete handlers for docs
        document.querySelectorAll('.btn-delete-doc').forEach(btn => {
            btn.addEventListener('click', async function () {
                const docId = this.getAttribute('data-id');
                if (confirm('¿Eliminar este documento definitivamente?')) {
                    try {
                        const csrfInput = document.querySelector('input[name="csrf_token"]');
                        const response = await fetch(`/trabajadores/documento/${docId}`, {
                            method: 'DELETE',
                            headers: {
                                'X-CSRFToken': csrfInput ? csrfInput.value : ''
                            }
                        });
                        if (response.ok) {
                            documentosArray = documentosArray.filter(d => d.id != docId);
                            updateDocumentosUI();
                        } else {
                            alert('Error al eliminar el documento.');
                        }
                    } catch (err) {
                        console.error(err);
                        alert('Error de conexión.');
                    }
                }
            });
        });
    }

    if (btnUploadDoc) {
        btnUploadDoc.addEventListener('click', async function () {
            if (!currentWorkerId) return;

            const file = fileUploadInput.files[0];
            if (!file) {
                alert('Por favor selecciona un archivo primero.');
                return;
            }

            const formData = new FormData();
            formData.append('documento', file);

            // Tipo de documento
            if (docTipoDocumento) {
                let tipoVal = docTipoDocumento.value;
                if (tipoVal === 'Otro' && docTipoCustom) {
                    tipoVal = docTipoCustom.value.trim();
                }
                if (tipoVal) {
                    formData.append('tipo_documento', tipoVal);
                }
            }

            const docFechaInicio = document.getElementById('docFechaInicio');
            if (docFechaInicio && docFechaInicio.value) {
                formData.append('fecha_inicio', docFechaInicio.value);
            }

            const docFechaFin = document.getElementById('docFechaFin');
            if (docFechaFin && docFechaFin.value) {
                formData.append('fecha_fin', docFechaFin.value);
            }

            const csrfInput = document.querySelector('input[name="csrf_token"]');
            if (csrfInput) {
                formData.append('csrf_token', csrfInput.value);
            }

            btnUploadDoc.disabled = true;
            btnUploadDoc.textContent = 'Subiendo...';

            try {
                const response = await fetch(`/trabajadores/${currentWorkerId}/documentos`, {
                    method: 'POST',
                    headers: {
                        'X-CSRFToken': csrfInput ? csrfInput.value : ''
                    },
                    body: formData
                });

                if (response.ok) {
                    const newDoc = await response.json();
                    documentosArray.push(newDoc);
                    updateDocumentosUI();
                    fileUploadInput.value = ''; // clear input
                    const docFechaInicio = document.getElementById('docFechaInicio');
                    const docFechaFin = document.getElementById('docFechaFin');
                    if (docFechaInicio) docFechaInicio.value = '';
                    if (docFechaFin) docFechaFin.value = '';
                    if (docTipoDocumento) { docTipoDocumento.value = ''; }
                    if (docTipoCustom) { docTipoCustom.value = ''; docTipoCustom.style.display = 'none'; }
                } else {
                    const err = await response.json();
                    alert(err.error || 'Ocurrió un error al subir el archivo');
                }
            } catch (e) {
                console.error(e);
                alert('Error de red al intentar subir el archivo.');
            } finally {
                btnUploadDoc.disabled = false;
                btnUploadDoc.textContent = 'Subir';
            }
        });
    }

    // === EDIT / DELETE LOGIC ===
    const editBtns = document.querySelectorAll('.edit-btn');
    const deleteBtns = document.querySelectorAll('.delete-btn');
    const viewBtns = document.querySelectorAll('.view-btn');
    const profilePicPreview = document.getElementById('profilePicPreview');
    const defaultAvatarUrl = profilePicPreview ? profilePicPreview.src : '';

    // Create a hidden form for delete
    const deleteForm = document.createElement('form');
    deleteForm.method = 'POST';
    deleteForm.style.display = 'none';
    const csrfInput = document.querySelector('input[name="csrf_token"]');
    if (csrfInput) {
        const clonedCsrf = csrfInput.cloneNode(true);
        deleteForm.appendChild(clonedCsrf);
    }
    document.body.appendChild(deleteForm);

    deleteBtns.forEach(btn => {
        btn.addEventListener('click', function (e) {
            e.stopPropagation();
            const id = this.getAttribute('data-id');
            if (confirm('¿Estás seguro de dar de baja a este trabajador?')) {
                deleteForm.action = `/trabajadores/eliminar/${id}`;
                deleteForm.submit();
            }
        });
    });

    const modalTitle = document.querySelector('.modal-header h3');

    // Make sure 'Nuevo Trabajador' resets to Add mode
    if (btnNuevoTrabajador) {
        btnNuevoTrabajador.addEventListener('click', function () {
            if (modalTitle) modalTitle.textContent = 'Registrar Trabajador';
            form.action = '/trabajadores/agregar'; // Assuming url_for('trabajadores.agregar') evaluates to this or similar relative path
            form.reset();

            // Re-enable everything
            enableFormElements();

            if (profilePicPreview) profilePicPreview.src = defaultAvatarUrl;
            credentialsArray = [];
            documentosArray = [];
            currentWorkerId = null;
            if (tabBtnDocumentos) tabBtnDocumentos.style.display = 'none';
            updateCredentialsUI();
            updateDocumentosUI();
        });
    }

    function disableFormElements() {
        const inputs = form.querySelectorAll('input, select, textarea');
        inputs.forEach(input => input.disabled = true);
        const saveBtn = document.getElementById('btnSaveTrabajador');
        if (saveBtn) saveBtn.style.display = 'none';

        // Hide specific buttons
        if (btnAddCredential) btnAddCredential.style.display = 'none';
        if (btnUploadDoc) btnUploadDoc.style.display = 'none';
        const fpInputLabel = document.querySelector('label[for="fotoPerfilInput"]');
        if (fpInputLabel) fpInputLabel.style.display = 'none';

        // Remove delete doc and cred buttons globally
        const css = document.createElement("style");
        css.id = 'viewOnlyStyles';
        css.innerHTML = `
            .btn-delete-doc, .credentialsList button { display: none !important; }
        `;
        document.head.appendChild(css);
    }

    function enableFormElements() {
        const inputs = form.querySelectorAll('input, select, textarea');
        inputs.forEach(input => input.disabled = false);
        const saveBtn = document.getElementById('btnSaveTrabajador');
        if (saveBtn) saveBtn.style.display = 'block';

        // Show specific buttons
        if (btnAddCredential) btnAddCredential.style.display = 'block';
        if (btnUploadDoc) btnUploadDoc.style.display = 'block';
        const fpInputLabel = document.querySelector('label[for="fotoPerfilInput"]');
        if (fpInputLabel) fpInputLabel.style.display = 'block';

        const css = document.getElementById('viewOnlyStyles');
        if (css) css.remove();
    }

    async function populateModal(id, mode) {
        try {
            const response = await fetch(`/trabajadores/get/${id}`);
            if (!response.ok) {
                const text = await response.text();
                console.error("Fetch failed:", response.status, text);
                alert(`Error HTTP ${response.status}: El servidor no devolvió los datos correctamente. Revisa la consola para más detalles.`);
                return;
            }
            const data = await response.json();

            if (mode === 'view') {
                // Populate Presentation Modal
                const viewModal = document.getElementById('viewWorkerModal');

                if (viewModal) {
                    const safeSetText = (id, text) => {
                        const el = document.getElementById(id);
                        if (el) el.textContent = text;
                    };

                    safeSetText('viewNombreCompleto', data.nombre_apellidos + ' ' + data.nombre);
                    safeSetText('viewPuesto', data.puesto || 'Puesto no asignado');
                    safeSetText('viewArea', data.area || 'Área no asignada');
                    safeSetText('viewNoEmpleado', data.no_empleado);
                    safeSetText('viewCorreo', data.correo || 'No registrado');
                    safeSetText('viewCelular', data.celular || 'No registrado');

                    safeSetText('viewRfc', data.rfc || '---');
                    safeSetText('viewCurp', data.curp || '---');
                    safeSetText('viewNss', data.nss || '---');
                    safeSetText('viewNacimiento', data.fecha_nacimiento || '---');
                    safeSetText('viewSangre', data.tipo_sangre || 'N/A');
                    safeSetText('viewAlergias', data.alergias || 'Ninguna');
                    safeSetText('viewEnfermedades', data.enfermedades_cronicas || 'Ninguna');

                    safeSetText('viewContactoE', data.contacto_emergencia || 'No registrado');
                    safeSetText('viewParentescoE', data.parentesco_contacto || 'No registrado');
                    safeSetText('viewTelefonoE', data.numero_contacto_emerg || 'No registrado');

                    safeSetText('viewIngreso', data.fecha_ingreso || '---');
                    safeSetText('viewNomina', data.tipo_nomina || data.tipo_pago || '---');
                    safeSetText('viewSalario', data.salario_real_pactado_x_sem || '0.00');
                    safeSetText('viewUbicacion', data.ubicacion_actual || '---');
                    safeSetText('viewEstado', data.ubicacion_estado || '---');
                    safeSetText('viewCoodinador', data.coordinadores_actuales || '---');
                } else {
                    console.warn("viewWorkerModal not found in DOM.");
                    alert("Tu navegador está utilizando una versión en caché de la página web que no incluye la nueva ventana de 'Visualización'. Por favor, limpia la caché de tu navegador (Ctrl + Shift + R o borrando el historial).");
                    return;
                }

                // Picture
                const avatarContainer = document.getElementById('viewAvatarContainer');
                if (avatarContainer) {
                    if (data.foto_perfil) {
                        avatarContainer.innerHTML = `<img src="/trabajadores/foto/${id}" style="width: 100%; height: 100%; object-fit: cover;">`;
                        avatarContainer.style.background = 'transparent';
                    } else {
                        const initial = data.nombre ? data.nombre.charAt(0).toUpperCase() : 'U';
                        avatarContainer.innerHTML = `<span id="viewAvatarInitial">${initial}</span>`;
                        avatarContainer.style.background = 'var(--primary-color)';
                    }
                }

                // Credentials
                const credList = document.getElementById('viewCredentialsList');
                if (credList) {
                    credList.innerHTML = '';
                    if (data.credenciales && data.credenciales.length > 0) {
                        data.credenciales.forEach(c => {
                            credList.innerHTML += `<li><i class="fas fa-check-circle" style="color: #10b981; margin-right: 6px;"></i> <strong>${c.planta}</strong> (ID: ${c.credencial_id})</li>`;
                        });
                    } else {
                        credList.innerHTML = '<li style="color: #9ca3af; font-style: italic;">Ninguna registrada.</li>';
                    }
                }

                // Documents
                const docList = document.getElementById('viewDocumentosList');
                if (docList) {
                    docList.innerHTML = '';
                    if (data.documentos && data.documentos.length > 0) {
                        data.documentos.forEach(d => {
                            const ext = d.nombre_archivo.split('.').pop().toLowerCase();
                            let icon = 'fa-file';
                            if (ext === 'pdf') icon = 'fa-file-pdf';
                            else if (['jpg', 'jpeg', 'png', 'heic'].includes(ext)) icon = 'fa-file-image';

                            let caducadoHtml = '';
                            if (d.fecha_fin) {
                                const hoy = new Date();
                                hoy.setHours(0, 0, 0, 0);
                                const [year, month, day] = d.fecha_fin.split('-');
                                const fin = new Date(year, month - 1, day);

                                if (fin < hoy) {
                                    caducadoHtml = '<span style="color: #ef4444; font-weight: bold; font-size: 0.75rem; margin-left: 8px; border: 1px solid #ef4444; border-radius: 4px; padding: 1px 4px;">Caducado</span>';
                                }
                            }

                            docList.innerHTML += `
                                <li style="display: flex; align-items: center;">
                                    <i class="fas ${icon}" style="color: var(--primary-color); width: 20px;"></i> 
                                    <a href="/trabajadores/documento/${d.id}" target="_blank" class="hover-underline" style="color: #2563eb; text-decoration: none; font-weight: 500; font-size: 0.95rem;">
                                        ${d.nombre_original || d.nombre_archivo}
                                    </a>
                                    ${caducadoHtml}
                                </li>`;
                        });
                    } else {
                        docList.innerHTML = '<li style="color: #9ca3af; font-style: italic;">Ningún documento subido.</li>';
                    }
                }

                viewModal.classList.add('active');

            } else {
                // Populate Edit Modal
                if (modalTitle) modalTitle.textContent = `Editar Trabajador (${data.no_empleado})`;
                form.action = `/trabajadores/editar/${id}`;
                enableFormElements();

                for (const key in data) {
                    if (key === 'credenciales' || key === 'id' || key === 'documentos' || key === 'foto_perfil') continue;
                    const input = form.querySelector(`[name="${key}"]`);
                    if (input) input.value = data[key] || '';
                }

                if (profilePicPreview) {
                    profilePicPreview.src = data.foto_perfil ? `/trabajadores/foto/${id}` : defaultAvatarUrl;
                }

                credentialsArray = data.credenciales || [];
                updateCredentialsUI();

                currentWorkerId = id;
                documentosArray = data.documentos || [];
                if (tabBtnDocumentos) tabBtnDocumentos.style.display = 'inline-block';
                updateDocumentosUI();

                modal.classList.add('active');
                switchTab('laborales');
            }

        } catch (err) {
            console.error("Error fetching worker data:", err);
            alert("Error del sistema: " + err.message);
        }
    }

    editBtns.forEach(btn => {
        btn.addEventListener('click', function (e) {
            e.stopPropagation();
            const id = this.getAttribute('data-id');
            populateModal(id, 'edit');

        });
    });

    viewBtns.forEach(btn => {
        btn.addEventListener('click', function (e) {
            e.stopPropagation();
            const id = this.getAttribute('data-id');
            populateModal(id, 'view');
        });
    });

    // Live preview for profile picture upload
    const fotoPerfilInput = document.getElementById('fotoPerfilInput');
    if (fotoPerfilInput && profilePicPreview) {
        fotoPerfilInput.addEventListener('change', function () {
            const file = this.files[0];
            if (file) {
                profilePicPreview.src = URL.createObjectURL(file);
            }
        });
    }

    // Auto-capture pending credentials on save
    form.addEventListener('submit', function (e) {
        const credencialIdInput = document.getElementById('credencialIdInput');
        const credencialCaducidadInput = document.getElementById('credencialCaducidadInput');
        if (credencialIdInput && credencialIdInput.value.trim() !== '') {
            let planta = selectPlanta ? selectPlanta.value : 'CAET';
            if (planta === 'OTRA' && customPlantaName) {
                planta = customPlantaName.value.trim().toUpperCase() || 'OTRA';
            }
            const credId = credencialIdInput.value.trim();
            const fechaCaducidad = (credencialCaducidadInput && credencialCaducidadInput.value) ? credencialCaducidadInput.value : null;

            // Only add if not duplicate plant
            if (!credentialsArray.find(c => c.planta === planta)) {
                credentialsArray.push({
                    planta: planta,
                    credencial_id: credId,
                    fecha_caducidad: fechaCaducidad
                });
                updateCredentialsUI();
            }
        }
    });

});
