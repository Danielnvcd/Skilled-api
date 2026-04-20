/* logout-confirm.js — CSP-compliant, sin handlers inline */
document.addEventListener('DOMContentLoaded', function () {
    var form = document.getElementById('logout-form');
    if (!form) return;

    form.addEventListener('submit', function (e) {
        e.preventDefault();
        if (window.confirm('¿Seguro que deseas cerrar sesión?')) {
            form.removeEventListener('submit', arguments.callee);
            form.submit();
        }
    });
});
