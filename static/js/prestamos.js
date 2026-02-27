document.addEventListener('DOMContentLoaded', function () {
    var csrfToken = document.querySelector('meta[name="csrf-token"]');
    csrfToken = csrfToken ? csrfToken.getAttribute('content') : '';

    function getHeaders() {
        var headers = { 'Content-Type': 'application/json' };
        if (csrfToken) headers['X-CSRFToken'] = csrfToken;
        return headers;
    }

    // === Modal: Nuevo Préstamo ===
    var modalPrestamo = document.getElementById('modalNuevoPrestamo');
    var btnNuevo = document.getElementById('btnNuevoPrestamo');
    var closeModal = document.getElementById('closeModalPrestamo');
    var cancelModal = document.getElementById('cancelModalPrestamo');

    function openModal() {
        modalPrestamo.classList.add('active');
    }
    function closeModalFn() {
        modalPrestamo.classList.remove('active');
        resetForm();
    }
    function resetForm() {
        document.getElementById('prestamoEditId').value = '';
        document.getElementById('prestamoTrabajador').value = '';
        document.getElementById('prestamoMonto').value = '';
        document.getElementById('prestamoPlazo').value = '';
        document.getElementById('prestamoDescuento').value = '';
        document.getElementById('prestamoFrecuencia').value = 'semanal';
        document.getElementById('prestamoFechaInicio').value = '';
        document.getElementById('prestamoMotivo').value = '';
        document.getElementById('modalPrestamoTitle').textContent = 'Nuevo Préstamo';
        document.getElementById('prestamoTrabajador').disabled = false;
    }

    if (btnNuevo) btnNuevo.addEventListener('click', openModal);
    if (closeModal) closeModal.addEventListener('click', closeModalFn);
    if (cancelModal) cancelModal.addEventListener('click', closeModalFn);

    // Auto-calculate descuento when monto and plazo change
    var montoInput = document.getElementById('prestamoMonto');
    var plazoInput = document.getElementById('prestamoPlazo');
    var descuentoInput = document.getElementById('prestamoDescuento');

    function autoCalcDescuento() {
        var monto = parseFloat(montoInput.value) || 0;
        var plazo = parseInt(plazoInput.value) || 0;
        if (monto > 0 && plazo > 0 && !descuentoInput._userEdited) {
            descuentoInput.value = (monto / plazo).toFixed(2);
        }
    }
    if (montoInput) montoInput.addEventListener('input', autoCalcDescuento);
    if (plazoInput) plazoInput.addEventListener('input', autoCalcDescuento);
    if (descuentoInput) {
        descuentoInput.addEventListener('input', function () {
            descuentoInput._userEdited = true;
        });
    }

    // === Save Préstamo (Create or Edit) ===
    var btnGuardar = document.getElementById('btnGuardarPrestamo');
    if (btnGuardar) {
        btnGuardar.addEventListener('click', function () {
            var editId = document.getElementById('prestamoEditId').value;
            var trabajadorId = document.getElementById('prestamoTrabajador').value;
            var monto = document.getElementById('prestamoMonto').value;
            var plazo = document.getElementById('prestamoPlazo').value;
            var descuento = document.getElementById('prestamoDescuento').value;
            var frecuencia = document.getElementById('prestamoFrecuencia').value;
            var fechaInicio = document.getElementById('prestamoFechaInicio').value;
            var motivo = document.getElementById('prestamoMotivo').value;

            if (!editId && !trabajadorId) {
                alert('Selecciona un trabajador.');
                return;
            }
            if (!monto || !plazo || !descuento) {
                alert('Completa monto, plazo y descuento.');
                return;
            }

            var url = editId ? '/prestamos/editar/' + editId : '/prestamos/crear';
            var body = {
                trabajador_id: trabajadorId,
                monto_total: monto,
                plazo_semanas: plazo,
                descuento_semanal: descuento,
                frecuencia: frecuencia,
                fecha_inicio: fechaInicio,
                motivo: motivo
            };

            btnGuardar.disabled = true;
            btnGuardar.textContent = 'Guardando...';

            fetch(url, {
                method: 'POST',
                headers: getHeaders(),
                body: JSON.stringify(body)
            })
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    if (data.success) {
                        alert(data.message);
                        window.location.reload();
                    } else {
                        alert('Error: ' + data.message);
                        btnGuardar.disabled = false;
                        btnGuardar.textContent = 'Guardar Préstamo';
                    }
                })
                .catch(function (err) {
                    console.error(err);
                    alert('Error de red al guardar.');
                    btnGuardar.disabled = false;
                    btnGuardar.textContent = 'Guardar Préstamo';
                });
        });
    }

    // === Edit buttons ===
    document.querySelectorAll('.btn-editar-prestamo').forEach(function (btn) {
        btn.addEventListener('click', function () {
            document.getElementById('prestamoEditId').value = this.getAttribute('data-id');
            document.getElementById('prestamoMonto').value = this.getAttribute('data-monto');
            document.getElementById('prestamoPlazo').value = this.getAttribute('data-plazo');
            document.getElementById('prestamoDescuento').value = this.getAttribute('data-descuento');
            document.getElementById('prestamoMotivo').value = this.getAttribute('data-motivo');
            document.getElementById('prestamoFrecuencia').value = this.getAttribute('data-frecuencia');
            document.getElementById('prestamoFechaInicio').value = this.getAttribute('data-fecha');
            document.getElementById('modalPrestamoTitle').textContent = 'Editar Préstamo';
            document.getElementById('prestamoTrabajador').disabled = true;
            openModal();
        });
    });

    // === Modal: Abono ===
    var modalAbono = document.getElementById('modalAbono');
    var closeAbono = document.getElementById('closeModalAbono');
    var cancelAbono = document.getElementById('cancelModalAbono');

    function closeAbonoFn() { modalAbono.classList.remove('active'); }
    if (closeAbono) closeAbono.addEventListener('click', closeAbonoFn);
    if (cancelAbono) cancelAbono.addEventListener('click', closeAbonoFn);

    document.querySelectorAll('.btn-abonar').forEach(function (btn) {
        btn.addEventListener('click', function () {
            document.getElementById('abonoPrestamoId').value = this.getAttribute('data-id');
            document.getElementById('abonoMonto').value = '';
            modalAbono.classList.add('active');
        });
    });

    var btnAplicarAbono = document.getElementById('btnAplicarAbono');
    if (btnAplicarAbono) {
        btnAplicarAbono.addEventListener('click', function () {
            var prestamoId = document.getElementById('abonoPrestamoId').value;
            var monto = document.getElementById('abonoMonto').value;

            if (!monto || parseFloat(monto) <= 0) {
                alert('Ingresa un monto válido.');
                return;
            }

            btnAplicarAbono.disabled = true;
            btnAplicarAbono.textContent = 'Aplicando...';

            fetch('/prestamos/abonar/' + prestamoId, {
                method: 'POST',
                headers: getHeaders(),
                body: JSON.stringify({ monto: monto })
            })
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    if (data.success) {
                        alert(data.message);
                        window.location.reload();
                    } else {
                        alert('Error: ' + data.message);
                        btnAplicarAbono.disabled = false;
                        btnAplicarAbono.textContent = 'Aplicar Abono';
                    }
                })
                .catch(function (err) {
                    console.error(err);
                    alert('Error de red.');
                    btnAplicarAbono.disabled = false;
                    btnAplicarAbono.textContent = 'Aplicar Abono';
                });
        });
    }

    // === Liquidar ===
    document.querySelectorAll('.btn-liquidar').forEach(function (btn) {
        btn.addEventListener('click', function () {
            var prestamoId = this.getAttribute('data-id');
            if (!confirm('¿Estás seguro de liquidar este préstamo? El saldo restante se marcará como $0.00')) return;

            fetch('/prestamos/liquidar/' + prestamoId, {
                method: 'POST',
                headers: getHeaders()
            })
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    if (data.success) {
                        alert(data.message);
                        window.location.reload();
                    } else {
                        alert('Error: ' + data.message);
                    }
                })
                .catch(function (err) {
                    console.error(err);
                    alert('Error de red.');
                });
        });
    });

    // === Detalles ===
    var modalDetalles = document.getElementById('modalDetallesPrestamo');
    var closeDetalles = document.getElementById('closeModalDetalles');
    var cancelDetalles = document.getElementById('cancelModalDetalles');

    function closeDetallesFn() { modalDetalles.classList.remove('active'); }
    if (closeDetalles) closeDetalles.addEventListener('click', closeDetallesFn);
    if (cancelDetalles) cancelDetalles.addEventListener('click', closeDetallesFn);

    document.querySelectorAll('.btn-detalles').forEach(function (btn) {
        btn.addEventListener('click', function () {
            var prestamoId = this.getAttribute('data-id');

            fetch('/prestamos/get/' + prestamoId, {
                headers: getHeaders()
            })
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    if (data.success) {
                        document.getElementById('detallesMontoTotal').textContent = '$' + data.monto_total.toFixed(2);
                        document.getElementById('detallesMontoRestante').textContent = '$' + data.monto_restante.toFixed(2);

                        if (data.monto_restante <= 0) {
                            document.getElementById('detallesMontoRestante').style.color = '#059669'; // Green if paid
                        } else {
                            document.getElementById('detallesMontoRestante').style.color = '#DC2626'; // Red
                        }

                        var tbody = document.getElementById('listaAbonos');
                        tbody.innerHTML = '';

                        if (data.abonos && data.abonos.length > 0) {
                            data.abonos.forEach(function (abono) {
                                var tr = document.createElement('tr');
                                var badgeClass = abono.tipo.toLowerCase() === 'nomina' ? 'badge-activo' : 'badge-liquidado';
                                tr.innerHTML = `
                                <td style="padding: 0.5rem; border-bottom: 1px solid var(--border);">${abono.fecha_abono}</td>
                                <td style="padding: 0.5rem; border-bottom: 1px solid var(--border);"><span class="${badgeClass}" style="font-size: 0.7rem; padding: 2px 6px;">${abono.tipo}</span></td>
                                <td style="padding: 0.5rem; border-bottom: 1px solid var(--border); font-weight: 600; color: #059669;">$${abono.monto.toFixed(2)}</td>
                                <td style="padding: 0.5rem; border-bottom: 1px solid var(--border); font-size: 0.85rem; color: var(--text-muted);">${abono.notas}</td>
                            `;
                                tbody.appendChild(tr);
                            });
                        } else {
                            tbody.innerHTML = '<tr><td colspan="4" style="text-align: center; padding: 1rem; color: var(--text-muted);">No hay pagos registrados aún.</td></tr>';
                        }

                        modalDetalles.classList.add('active');
                    } else {
                        alert('Error al obtener detalles: ' + data.message);
                    }
                })
                .catch(function (err) {
                    console.error(err);
                    alert('Error de red al cargar detalles.');
                });
        });
    });

});
