$(document).ready(function () {
    // Select2 para seleccionar trabajador en creación de periodo
    $('#selectorTrabajador').select2({
        placeholder: "Busca por nombre o num. de empleado...",
        allowClear: true,
        language: { noResults: function () { return "No se encontró"; } }
    });

    // Select2 para agregar descuento en detalle
    $('#trabajadorSelectAjuste').select2({
        placeholder: "Selecciona un trabajador...",
        allowClear: true,
        language: { noResults: function () { return "No se encontró"; } }
    });

    // Confirmar cierre de periodo
    var formCerrar = document.getElementById('formCerrarPeriodo');
    if (formCerrar) {
        formCerrar.addEventListener('submit', function (e) {
            if (!confirm('¿Cerrar este periodo? Ya no se podrán agregar ni eliminar descuentos.')) {
                e.preventDefault();
            }
        });
    }

    // Confirmar eliminar descuento
    document.querySelectorAll('[id^="formEliminarDesc"]').forEach(function (form) {
        form.addEventListener('submit', function (e) {
            if (!confirm('¿Eliminar este descuento?')) {
                e.preventDefault();
            }
        });
    });
});
