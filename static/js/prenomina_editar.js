document.addEventListener('DOMContentLoaded', function () {
    var csrfToken = document.querySelector('meta[name="csrf-token"]');
    csrfToken = csrfToken ? csrfToken.getAttribute('content') : '';

    function getHeaders() {
        var headers = { 'Content-Type': 'application/json' };
        if (csrfToken) headers['X-CSRFToken'] = csrfToken;
        return headers;
    }

    // === Accordion Toggle ===
    document.querySelectorAll('[data-toggle-id]').forEach(function (header) {
        header.addEventListener('click', function () {
            var bodyId = this.getAttribute('data-toggle-id');
            var body = document.getElementById(bodyId);
            var chevron = this.querySelector('.fa-chevron-down');
            if (body.style.display === 'none') {
                body.style.display = 'block';
                if (chevron) chevron.style.transform = 'rotate(180deg)';
            } else {
                body.style.display = 'none';
                if (chevron) chevron.style.transform = 'rotate(0deg)';
            }
        });
    });

    // === Helper to update totals in DOM ===
    function updateTotals(prenominaId, data) {
        if (data.total_deducciones !== undefined) {
            var dedEl = document.getElementById('deducciones-' + prenominaId);
            if (dedEl) dedEl.textContent = '-$' + parseFloat(data.total_deducciones).toFixed(2);
        }
        if (data.total_percepciones !== undefined) {
            var perEl = document.getElementById('percepciones-' + prenominaId);
            if (perEl) perEl.textContent = '$' + parseFloat(data.total_percepciones).toFixed(2);
        }
        if (data.total_a_pagar !== undefined) {
            var netoEl = document.getElementById('neto-' + prenominaId);
            if (netoEl) netoEl.textContent = '$' + parseFloat(data.total_a_pagar).toFixed(2);
        }
    }

    // === Aplicar Incidencia como Descuento ===
    document.querySelectorAll('.btn-aplicar-incidencia').forEach(function (btn) {
        btn.addEventListener('click', function (e) {
            e.stopPropagation();
            var prenominaId = this.getAttribute('data-prenomina-id');
            var concepto = this.getAttribute('data-concepto');
            var fecha = this.getAttribute('data-fecha');
            var monto = prompt('Ingresa el monto a descontar por: ' + concepto);
            if (!monto || isNaN(parseFloat(monto))) return;

            var self = this;
            fetch('/prenomina/api/descuento', {
                method: 'POST',
                headers: getHeaders(),
                body: JSON.stringify({
                    prenomina_id: prenominaId,
                    tipo: 'INCIDENCIA',
                    concepto: concepto,
                    monto: parseFloat(monto),
                    fecha_incidencia: fecha
                })
            })
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    if (data.success) {
                        // Add to descuentos list
                        var listEl = document.getElementById('descuentos-list-' + prenominaId);
                        var newDiv = document.createElement('div');
                        newDiv.className = 'descuento-item';
                        newDiv.id = 'descuento-item-' + data.id;
                        newDiv.style.cssText = 'display:flex;justify-content:space-between;align-items:center;padding:8px;background:#FEF2F2;border-radius:6px;margin-bottom:4px;font-size:0.85rem;';
                        newDiv.innerHTML = '<span><strong>INCIDENCIA:</strong> ' + concepto + '</span>' +
                            '<span style="display:flex;align-items:center;gap:8px;">' +
                            '<strong style="color:#DC2626;">-$' + parseFloat(monto).toFixed(2) + '</strong>' +
                            '<button class="btn-eliminar-descuento" data-id="' + data.id + '" data-prenomina-id="' + prenominaId + '" style="background:none;border:none;color:#DC2626;cursor:pointer;font-size:0.9rem;">' +
                            '<i class="fa-solid fa-trash"></i></button></span>';
                        listEl.appendChild(newDiv);
                        bindEliminarDescuento(newDiv.querySelector('.btn-eliminar-descuento'));
                        updateTotals(prenominaId, data);
                        self.disabled = true;
                        self.textContent = 'Aplicado';
                        self.style.background = '#9CA3AF';
                    } else {
                        alert('Error: ' + data.message);
                    }
                });
        });
    });

    // === Agregar Descuento Manual ===
    document.querySelectorAll('.btn-agregar-descuento').forEach(function (btn) {
        btn.addEventListener('click', function (e) {
            e.stopPropagation();
            var prenominaId = this.getAttribute('data-prenomina-id');
            var conceptoEl = document.querySelector('.desc-concepto[data-prenomina-id="' + prenominaId + '"]');
            var montoEl = document.querySelector('.desc-monto[data-prenomina-id="' + prenominaId + '"]');
            var concepto = conceptoEl.value.trim();
            var monto = parseFloat(montoEl.value);

            if (!concepto || !monto) {
                alert('Completa concepto y monto.');
                return;
            }

            fetch('/prenomina/api/descuento', {
                method: 'POST',
                headers: getHeaders(),
                body: JSON.stringify({
                    prenomina_id: prenominaId,
                    tipo: 'MANUAL',
                    concepto: concepto,
                    monto: monto
                })
            })
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    if (data.success) {
                        var listEl = document.getElementById('descuentos-list-' + prenominaId);
                        var newDiv = document.createElement('div');
                        newDiv.className = 'descuento-item';
                        newDiv.id = 'descuento-item-' + data.id;
                        newDiv.style.cssText = 'display:flex;justify-content:space-between;align-items:center;padding:8px;background:#FEF2F2;border-radius:6px;margin-bottom:4px;font-size:0.85rem;';
                        newDiv.innerHTML = '<span><strong>MANUAL:</strong> ' + concepto + '</span>' +
                            '<span style="display:flex;align-items:center;gap:8px;">' +
                            '<strong style="color:#DC2626;">-$' + monto.toFixed(2) + '</strong>' +
                            '<button class="btn-eliminar-descuento" data-id="' + data.id + '" data-prenomina-id="' + prenominaId + '" style="background:none;border:none;color:#DC2626;cursor:pointer;font-size:0.9rem;">' +
                            '<i class="fa-solid fa-trash"></i></button></span>';
                        listEl.appendChild(newDiv);
                        bindEliminarDescuento(newDiv.querySelector('.btn-eliminar-descuento'));
                        updateTotals(prenominaId, data);
                        conceptoEl.value = '';
                        montoEl.value = '';
                    } else {
                        alert('Error: ' + data.message);
                    }
                });
        });
    });

    // === Eliminar Descuento ===
    function bindEliminarDescuento(btn) {
        btn.addEventListener('click', function (e) {
            e.stopPropagation();
            var descId = this.getAttribute('data-id');
            var prenominaId = this.getAttribute('data-prenomina-id');
            if (!confirm('¿Eliminar este descuento?')) return;

            fetch('/prenomina/api/descuento/' + descId, {
                method: 'DELETE',
                headers: getHeaders()
            })
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    if (data.success) {
                        var item = document.getElementById('descuento-item-' + descId);
                        if (item) item.remove();
                        updateTotals(prenominaId, data);
                    } else {
                        alert('Error: ' + data.message);
                    }
                });
        });
    }
    document.querySelectorAll('.btn-eliminar-descuento').forEach(bindEliminarDescuento);

    // === Agregar Depósito ===
    document.querySelectorAll('.btn-agregar-deposito').forEach(function (btn) {
        btn.addEventListener('click', function (e) {
            e.stopPropagation();
            var prenominaId = this.getAttribute('data-prenomina-id');
            var conceptoEl = document.querySelector('.dep-concepto[data-prenomina-id="' + prenominaId + '"]');
            var montoEl = document.querySelector('.dep-monto[data-prenomina-id="' + prenominaId + '"]');
            var concepto = conceptoEl.value.trim();
            var monto = parseFloat(montoEl.value);

            if (!concepto || !monto) {
                alert('Completa concepto y monto.');
                return;
            }

            fetch('/prenomina/api/deposito', {
                method: 'POST',
                headers: getHeaders(),
                body: JSON.stringify({
                    prenomina_id: prenominaId,
                    monto: monto,
                    concepto: concepto
                })
            })
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    if (data.success) {
                        var listEl = document.getElementById('depositos-list-' + prenominaId);
                        var newDiv = document.createElement('div');
                        newDiv.className = 'deposito-item';
                        newDiv.id = 'deposito-item-' + data.id;
                        newDiv.style.cssText = 'display:flex;justify-content:space-between;align-items:center;padding:8px;background:#F0FDF4;border-radius:6px;margin-bottom:4px;font-size:0.85rem;';
                        newDiv.innerHTML = '<span>' + concepto + '</span>' +
                            '<span style="display:flex;align-items:center;gap:8px;">' +
                            '<strong style="color:#059669;">+$' + monto.toFixed(2) + '</strong>' +
                            '<button class="btn-eliminar-deposito" data-id="' + data.id + '" data-prenomina-id="' + prenominaId + '" style="background:none;border:none;color:#DC2626;cursor:pointer;font-size:0.9rem;">' +
                            '<i class="fa-solid fa-trash"></i></button></span>';
                        listEl.appendChild(newDiv);
                        bindEliminarDeposito(newDiv.querySelector('.btn-eliminar-deposito'));
                        updateTotals(prenominaId, data);
                        conceptoEl.value = '';
                        montoEl.value = '';
                    } else {
                        alert('Error: ' + data.message);
                    }
                });
        });
    });

    // === Eliminar Depósito ===
    function bindEliminarDeposito(btn) {
        btn.addEventListener('click', function (e) {
            e.stopPropagation();
            var depId = this.getAttribute('data-id');
            var prenominaId = this.getAttribute('data-prenomina-id');
            if (!confirm('¿Eliminar este depósito?')) return;

            fetch('/prenomina/api/deposito/' + depId, {
                method: 'DELETE',
                headers: getHeaders()
            })
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    if (data.success) {
                        var item = document.getElementById('deposito-item-' + depId);
                        if (item) item.remove();
                        updateTotals(prenominaId, data);
                    } else {
                        alert('Error: ' + data.message);
                    }
                });
        });
    }
    document.querySelectorAll('.btn-eliminar-deposito').forEach(bindEliminarDeposito);

    // === Viáticos ===
    document.querySelectorAll('.btn-editar-viaticos').forEach(function (btn) {
        btn.addEventListener('click', function (e) {
            e.stopPropagation();
            var prenominaId = this.getAttribute('data-prenomina-id');
            var formEl = document.getElementById('viaticos-form-' + prenominaId);
            if (formEl) formEl.style.display = 'block';
        });
    });

    document.querySelectorAll('.btn-cancelar-viaticos').forEach(function (btn) {
        btn.addEventListener('click', function (e) {
            e.stopPropagation();
            var prenominaId = this.getAttribute('data-prenomina-id');
            var formEl = document.getElementById('viaticos-form-' + prenominaId);
            if (formEl) formEl.style.display = 'none';
        });
    });

    document.querySelectorAll('.btn-guardar-viaticos').forEach(function (btn) {
        btn.addEventListener('click', function (e) {
            e.stopPropagation();
            var prenominaId = this.getAttribute('data-prenomina-id');
            var inputEl = document.querySelector('.viaticos-input[data-prenomina-id="' + prenominaId + '"]');
            var monto = parseFloat(inputEl.value);
            if (isNaN(monto) || monto < 0) {
                alert('Ingresa un monto válido.');
                return;
            }

            var self = this;
            self.disabled = true;
            fetch('/prenomina/api/viaticos', {
                method: 'PATCH',
                headers: getHeaders(),
                body: JSON.stringify({ prenomina_id: prenominaId, monto_viaticos: monto })
            })
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    self.disabled = false;
                    if (data.success) {
                        var displayEl = document.getElementById('viaticos-display-' + prenominaId);
                        if (displayEl) displayEl.textContent = '$' + parseFloat(data.pago_viaticos).toFixed(2);
                        updateTotals(prenominaId, data);
                        var formEl = document.getElementById('viaticos-form-' + prenominaId);
                        if (formEl) formEl.style.display = 'none';
                    } else {
                        alert('Error: ' + data.message);
                    }
                })
                .catch(function () {
                    self.disabled = false;
                    alert('Error de red.');
                });
        });
    });

    // === Festivos ===
    document.querySelectorAll('.btn-editar-festivos').forEach(function (btn) {
        btn.addEventListener('click', function (e) {
            e.stopPropagation();
            var prenominaId = this.getAttribute('data-prenomina-id');
            var formEl = document.getElementById('festivos-form-' + prenominaId);
            if (formEl) formEl.style.display = 'block';
        });
    });

    document.querySelectorAll('.btn-cancelar-festivos').forEach(function (btn) {
        btn.addEventListener('click', function (e) {
            e.stopPropagation();
            var prenominaId = this.getAttribute('data-prenomina-id');
            var formEl = document.getElementById('festivos-form-' + prenominaId);
            if (formEl) formEl.style.display = 'none';
        });
    });

    document.querySelectorAll('.btn-guardar-festivos').forEach(function (btn) {
        btn.addEventListener('click', function (e) {
            e.stopPropagation();
            var prenominaId = this.getAttribute('data-prenomina-id');
            var inputEl = document.querySelector('.festivos-input[data-prenomina-id="' + prenominaId + '"]');
            var monto = parseFloat(inputEl.value);
            if (isNaN(monto) || monto < 0) {
                alert('Ingresa un monto válido.');
                return;
            }

            var self = this;
            self.disabled = true;
            fetch('/prenomina/api/festivos', {
                method: 'PATCH',
                headers: getHeaders(),
                body: JSON.stringify({ prenomina_id: prenominaId, monto_festivos: monto })
            })
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    self.disabled = false;
                    if (data.success) {
                        var displayEl = document.getElementById('festivos-display-' + prenominaId);
                        if (displayEl) displayEl.textContent = '$' + parseFloat(data.pago_festivos).toFixed(2);
                        updateTotals(prenominaId, data);
                        var formEl = document.getElementById('festivos-form-' + prenominaId);
                        if (formEl) formEl.style.display = 'none';
                    } else {
                        alert('Error: ' + data.message);
                    }
                })
                .catch(function () {
                    self.disabled = false;
                    alert('Error de red.');
                });
        });
    });

    // === Cerrar Nómina ===
    var btnCerrar = document.getElementById('btnCerrarNomina');
    if (btnCerrar) {
        btnCerrar.addEventListener('click', function () {
            var fecha = this.getAttribute('data-fecha');
            if (!confirm('⚠️ ATENCIÓN: ¿Cerrar esta nómina global?\n\nUna vez cerrada NO se podrán realizar más cambios (descuentos, depósitos, ni préstamos).\n\nEsta operación es definitiva.')) return;

            btnCerrar.disabled = true;
            btnCerrar.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Cerrando...';

            fetch('/prenomina/cerrar/' + fecha, {
                method: 'POST',
                headers: getHeaders()
            })
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    if (data.success) {
                        alert('✅ ' + data.message);
                        window.location.reload();
                    } else {
                        alert('Error: ' + data.message);
                        btnCerrar.disabled = false;
                        btnCerrar.innerHTML = '<i class="fa-solid fa-lock"></i> Cerrar Nómina Global';
                    }
                })
                .catch(function (err) {
                    console.error(err);
                    alert('Error de red.');
                    btnCerrar.disabled = false;
                    btnCerrar.innerHTML = '<i class="fa-solid fa-lock"></i> Cerrar Nómina Global';
                });
        });
    }
});
