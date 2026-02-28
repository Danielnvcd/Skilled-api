/**
 * Desactiva el doble clic en toda la aplicación.
 * Se usa la fase de captura (capture: true) para interceptar
 * el evento antes de que llegue a cualquier otro handler.
 */
document.addEventListener('dblclick', function (e) {
    e.preventDefault();
    e.stopPropagation();
}, true);
