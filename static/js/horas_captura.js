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
});
