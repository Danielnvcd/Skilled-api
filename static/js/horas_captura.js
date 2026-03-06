$(document).ready(function () {
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
