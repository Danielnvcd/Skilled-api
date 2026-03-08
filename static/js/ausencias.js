/* js/ausencias.js - CSP-compliant (no inline handlers) */

document.addEventListener('DOMContentLoaded', function () {

    // ===== TABS =====
    var tabs = document.querySelectorAll('.tab-item');
    var panes = document.querySelectorAll('.tab-pane');

    tabs.forEach(function (tab) {
        tab.addEventListener('click', function () {
            var target = tab.getAttribute('data-tab');
            tabs.forEach(function (t) { t.classList.remove('active'); });
            panes.forEach(function (p) { p.classList.remove('active'); });
            tab.classList.add('active');
            document.getElementById('tab-' + target).classList.add('active');
        });
    });

    // Hash anchor for saldos
    if (window.location.hash === '#saldos-section') {
        var saldoTab = document.querySelector('.tab-item[data-tab="saldos"]');
        if (saldoTab) saldoTab.click();
    }

    // Link "Ver saldos detallados" dentro de la tarjeta
    var linkSaldos = document.querySelector('a[href="#saldos-section"]');
    if (linkSaldos) {
        linkSaldos.addEventListener('click', function (e) {
            e.preventDefault();
            var saldoTab = document.querySelector('.tab-item[data-tab="saldos"]');
            if (saldoTab) saldoTab.click();
        });
    }

    // ===== MODAL: SOLICITUD =====
    var modalSolicitud = document.getElementById('modalSolicitud');
    var formAusencia = document.getElementById('formAusencia');

    var btnAbrir = document.getElementById('btnAbrirSolicitud');
    if (btnAbrir) {
        btnAbrir.addEventListener('click', function () {
            formAusencia.reset();
            var infoDiv = document.getElementById('saldoInfoModal');
            if (infoDiv) infoDiv.style.display = 'none';
            modalSolicitud.classList.add('active');
        });
    }

    // Close solicitud modal
    var btnCerrarSolicitud = document.getElementById('btnCerrarSolicitud');
    var btnCancelarSolicitud = document.getElementById('btnCancelarSolicitud');

    function cerrarSolicitud() {
        modalSolicitud.classList.remove('active');
    }

    if (btnCerrarSolicitud) btnCerrarSolicitud.addEventListener('click', cerrarSolicitud);
    if (btnCancelarSolicitud) btnCancelarSolicitud.addEventListener('click', cerrarSolicitud);

    // ===== MODAL: AJUSTE SALDO =====
    var modalAjuste = document.getElementById('modalAjuste');

    document.querySelectorAll('.btn-ajustar-saldo').forEach(function (btn) {
        btn.addEventListener('click', function () {
            var id = btn.getAttribute('data-id');
            var nombre = btn.getAttribute('data-nombre');
            var asignados = btn.getAttribute('data-asignados');
            var disfrutados = btn.getAttribute('data-disfrutados');

            document.getElementById('ajusteNombre').textContent = nombre;
            document.getElementById('ajusteAsignados').value = asignados;
            document.getElementById('ajusteDisfrutados').value = disfrutados;

            var form = document.getElementById('formAjusteSaldo');
            form.action = '/ausencias/actualizar_saldo/' + id;

            modalAjuste.classList.add('active');
        });
    });

    // Close ajuste modal
    var btnCerrarAjuste = document.getElementById('btnCerrarAjuste');
    var btnCancelarAjuste = document.getElementById('btnCancelarAjuste');

    function cerrarAjuste() {
        modalAjuste.classList.remove('active');
    }

    if (btnCerrarAjuste) btnCerrarAjuste.addEventListener('click', cerrarAjuste);
    if (btnCancelarAjuste) btnCancelarAjuste.addEventListener('click', cerrarAjuste);

    // ===== FORMS: CANCELAR AUSENCIA (confirm) =====
    document.querySelectorAll('.form-cancelar-ausencia').forEach(function (form) {
        form.addEventListener('submit', function (e) {
            if (!confirm('¿Seguro que deseas cancelar esta ausencia?')) {
                e.preventDefault();
            }
        });
    });

    // ===== SELECT CHANGES: Verificar Saldo =====
    var selectTrabajador = document.getElementById('selectTrabajador');
    var selectTipoAusencia = document.getElementById('selectTipoAusencia');

    if (selectTrabajador) selectTrabajador.addEventListener('change', verificarSaldoModal);
    if (selectTipoAusencia) selectTipoAusencia.addEventListener('change', verificarSaldoModal);

    // ===== DATE CHANGES: Calcular Dias =====
    var fechaInicio = document.getElementById('fechaInicio');
    var fechaFin = document.getElementById('fechaFin');

    if (fechaInicio) fechaInicio.addEventListener('change', calcularDias);
    if (fechaFin) fechaFin.addEventListener('change', calcularDias);

    // ===== PAGINACIÓN + BUSCADOR SALDOS (20 por página) =====
    var POR_PAGINA = 20;
    var paginaActualSaldos = 1;
    var buscarSaldo = document.getElementById('buscarSaldo');
    var tablaSaldos = document.getElementById('tablaSaldos');
    var paginacionDiv = document.getElementById('paginacionSaldos');

    function obtenerFilasSaldos() {
        if (!tablaSaldos) return [];
        return Array.from(tablaSaldos.querySelectorAll('tbody tr'));
    }

    function filtrarYPaginar() {
        var filas = obtenerFilasSaldos();
        var filtro = buscarSaldo ? buscarSaldo.value.toLowerCase() : '';
        var filasFiltradas = filas.filter(function (f) {
            return f.textContent.toLowerCase().includes(filtro);
        });
        filas.forEach(function (f) { f.style.display = 'none'; });
        var totalPag = Math.max(1, Math.ceil(filasFiltradas.length / POR_PAGINA));
        if (paginaActualSaldos > totalPag) paginaActualSaldos = totalPag;
        var inicio = (paginaActualSaldos - 1) * POR_PAGINA;
        for (var i = inicio; i < inicio + POR_PAGINA && i < filasFiltradas.length; i++) {
            filasFiltradas[i].style.display = '';
        }
        renderPaginacion(totalPag, filasFiltradas.length);
    }

    function renderPaginacion(totalPag, total) {
        if (!paginacionDiv) return;
        paginacionDiv.innerHTML = '';
        if (total === 0) { paginacionDiv.innerHTML = '<span style="color:#94a3b8;font-size:0.85rem;">Sin resultados</span>'; return; }
        var info = document.createElement('span');
        info.style.cssText = 'font-size:0.85rem;color:#64748b;margin-right:1rem;';
        info.textContent = 'Mostrando ' + (((paginaActualSaldos - 1) * POR_PAGINA) + 1) + '-' + Math.min(paginaActualSaldos * POR_PAGINA, total) + ' de ' + total;
        paginacionDiv.appendChild(info);

        var btnAnt = document.createElement('button');
        btnAnt.textContent = '\u2190 Anterior';
        btnAnt.className = 'btn btn-sm btn-outline';
        btnAnt.disabled = paginaActualSaldos <= 1;
        btnAnt.addEventListener('click', function () { paginaActualSaldos--; filtrarYPaginar(); });
        paginacionDiv.appendChild(btnAnt);

        for (var p = 1; p <= totalPag; p++) {
            (function (num) {
                var b = document.createElement('button');
                b.textContent = num;
                b.className = 'btn btn-sm ' + (num === paginaActualSaldos ? 'btn-primary' : 'btn-outline');
                b.addEventListener('click', function () { paginaActualSaldos = num; filtrarYPaginar(); });
                paginacionDiv.appendChild(b);
            })(p);
        }

        var btnSig = document.createElement('button');
        btnSig.textContent = 'Siguiente \u2192';
        btnSig.className = 'btn btn-sm btn-outline';
        btnSig.disabled = paginaActualSaldos >= totalPag;
        btnSig.addEventListener('click', function () { paginaActualSaldos++; filtrarYPaginar(); });
        paginacionDiv.appendChild(btnSig);
    }

    if (buscarSaldo) {
        buscarSaldo.addEventListener('input', function () {
            paginaActualSaldos = 1;
            filtrarYPaginar();
        });
    }
    filtrarYPaginar();

});

// ===== LOGIC: Check Vacation Balance =====
function verificarSaldoModal() {
    var trabajadorId = document.getElementById('selectTrabajador').value;
    var tipoAusencia = document.getElementById('selectTipoAusencia').value;
    var infoDiv = document.getElementById('saldoInfoModal');
    var spanDias = document.getElementById('spanDiasPendientes');

    if (trabajadorId && tipoAusencia === 'VACACIONES') {
        fetch('/ausencias/balance/' + trabajadorId)
            .then(function (response) { return response.json(); })
            .then(function (data) {
                if (data.success) {
                    spanDias.textContent = data.dias_pendientes;
                    infoDiv.style.display = 'block';
                    if (data.dias_pendientes <= 0) {
                        infoDiv.className = 'alert alert-danger';
                    } else {
                        infoDiv.className = 'alert alert-info';
                    }
                }
            })
            .catch(function (error) {
                console.error('Error fetching balance:', error);
                infoDiv.style.display = 'none';
            });
    } else {
        infoDiv.style.display = 'none';
    }
}

// ===== LOGIC: Calc days requested dynamically =====
function calcularDias() {
    var fInicio = document.getElementById('fechaInicio').value;
    var fFin = document.getElementById('fechaFin').value;

    if (fInicio && fFin) {
        var dInicio = new Date(fInicio);
        var dFin = new Date(fFin);

        if (dFin >= dInicio) {
            var diffTime = Math.abs(dFin - dInicio);
            var diffDays = Math.ceil(diffTime / (1000 * 60 * 60 * 24)) + 1;
            document.getElementById('diasSolicitados').value = diffDays;
        }
    }
}
