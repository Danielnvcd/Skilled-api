$(document).ready(function () {

    // --- Filtros de tabla ---
    var filtroDia = 'todos';
    var filtroBusqueda = '';

    // Marcar "Todos" como activo al inicio
    document.querySelector('.filtro-dia[data-dia="todos"]').classList.add('activo');

    document.querySelectorAll('.filtro-dia').forEach(function (btn) {
        btn.addEventListener('click', function () {
            document.querySelectorAll('.filtro-dia').forEach(function (b) { b.classList.remove('activo'); });
            this.classList.add('activo');
            filtroDia = this.dataset.dia;
            aplicarFiltros();
        });
    });

    var inputBusqueda = document.getElementById('buscarTrabajador');
    if (inputBusqueda) {
        inputBusqueda.addEventListener('input', function () {
            filtroBusqueda = this.value.toLowerCase().trim();
            aplicarFiltros();
        });
    }

    function aplicarFiltros() {
        var filas = document.querySelectorAll('.history-table tbody tr[data-dia]');
        var visibles = 0;
        filas.forEach(function (fila) {
            var dia = fila.dataset.dia || '';
            var trabajador = fila.dataset.trabajador || '';
            var coincideDia = filtroDia === 'todos' || dia === filtroDia;
            var coincideBusqueda = filtroBusqueda === '' || trabajador.indexOf(filtroBusqueda) !== -1;
            if (coincideDia && coincideBusqueda) {
                fila.style.display = '';
                visibles++;
            } else {
                fila.style.display = 'none';
            }
        });
        var sinResultados = document.getElementById('sinResultadosFiltro');
        if (sinResultados) {
            sinResultados.style.display = (visibles === 0 && filas.length > 0) ? '' : 'none';
        }
    }

    $('#trabajadorSelect').select2({
        placeholder: "Busca por nombre o num. de empleado...",
        allowClear: true,
        language: {
            noResults: function () {
                return "No se encontró ningún trabajador";
            }
        }
    });

    // --- Viáticos: attach event listeners (CSP no permite inline handlers) ---
    var chkViaticos = document.getElementById('chkViaticos');
    var viaticosPerfilRadio = document.getElementById('viaticos_perfil');
    var viaticosManualRadio = document.getElementById('viaticos_manual');

    if (chkViaticos) {
        chkViaticos.addEventListener('change', toggleViaticosOpciones);
    }
    if (viaticosPerfilRadio) {
        viaticosPerfilRadio.addEventListener('change', toggleMontoManual);
    }
    if (viaticosManualRadio) {
        viaticosManualRadio.addEventListener('change', toggleMontoManual);
    }
});

// --- Viáticos: toggle opciones (Perfil / Manual) ---
function toggleViaticosOpciones() {
    var chk = document.getElementById('chkViaticos');
    var opciones = document.getElementById('viaticosOpciones');
    if (chk.checked) {
        opciones.style.display = 'flex';
    } else {
        opciones.style.display = 'none';
        document.getElementById('viaticos_perfil').checked = true;
        document.getElementById('montoViaticosManual').style.display = 'none';
        document.getElementById('montoViaticosManual').value = '';
    }
}

function toggleMontoManual() {
    var isManual = document.getElementById('viaticos_manual').checked;
    var input = document.getElementById('montoViaticosManual');
    input.style.display = isManual ? 'block' : 'none';
    if (!isManual) input.value = '';
}
