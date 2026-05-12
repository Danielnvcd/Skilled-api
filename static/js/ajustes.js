$(document).ready(function () {
    var totalMeta = 0;
    var trabajadoresAgregados = {};

    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    $('#selectorTrabajador').select2({
        placeholder: "Busca por nombre o num. de empleado...",
        allowClear: true,
        language: { noResults: function () { return "No se encontró"; } }
    });

    document.getElementById('btnAgregarTrabajador').addEventListener('click', function () {
        var select = document.getElementById('selectorTrabajador');
        var metaInput = document.getElementById('inputMetaTrabajador');
        var tId = select.value;
        var monto = parseFloat(metaInput.value);

        if (!tId || isNaN(monto) || monto <= 0) {
            alert('Selecciona un trabajador y pon un monto mayor a $0.00');
            return;
        }

        if (trabajadoresAgregados[tId]) {
            alert('Este trabajador ya fue agregado.');
            return;
        }

        var option = select.options[select.selectedIndex];
        var nombre = option.getAttribute('data-nombre');
        var noEmp = option.getAttribute('data-noemp');

        trabajadoresAgregados[tId] = monto;
        totalMeta += monto;

        var tbody = document.getElementById('tablaTrabajadores');
        var tr = document.createElement('tr');
        tr.id = 'row-' + tId;
        tr.innerHTML =
            '<td>' + esc(noEmp) + '</td>' +
            '<td>' + esc(nombre) + '</td>' +
            '<td class="col-meta">$' + monto.toFixed(2) + '</td>' +
            '<td class="col-action"><button type="button" class="delete-btn" data-tid="' + esc(tId) + '" data-monto="' + monto + '" title="Eliminar"><i class="fa-solid fa-xmark"></i></button>' +
            '<input type="hidden" name="trabajador_ids" value="' + esc(tId) + '">' +
            '<input type="hidden" name="montos_meta" value="' + monto.toFixed(2) + '">' +
            '</td>';
        tbody.appendChild(tr);

        document.getElementById('totalMeta').textContent = '$' + totalMeta.toFixed(2);
        document.getElementById('tablaTrabajadoresWrapper').classList.remove('hidden');

        // Limpiar
        $('#selectorTrabajador').val(null).trigger('change');
        metaInput.value = '';
    });

    // Eliminar trabajador de la tabla
    document.getElementById('tablaTrabajadores').addEventListener('click', function (e) {
        var btn = e.target.closest('.delete-btn');
        if (!btn) return;
        var tid = btn.getAttribute('data-tid');
        var monto = parseFloat(btn.getAttribute('data-monto'));
        totalMeta -= monto;
        delete trabajadoresAgregados[tid];
        document.getElementById('row-' + tid).remove();
        document.getElementById('totalMeta').textContent = '$' + totalMeta.toFixed(2);
        if (Object.keys(trabajadoresAgregados).length === 0) {
            document.getElementById('tablaTrabajadoresWrapper').classList.add('hidden');
        }
    });
});
