document.addEventListener('DOMContentLoaded', function () {

    // ── Toggle password visibility ──
    const btn = document.getElementById('toggle-pwd');
    const pwd = document.getElementById('password');
    const eye = document.getElementById('eye-icon');

    if (btn && pwd && eye) {
        btn.addEventListener('click', () => {
            const show = pwd.type === 'password';
            pwd.type = show ? 'text' : 'password';
            eye.className = show ? 'fa-regular fa-eye-slash' : 'fa-regular fa-eye';
        });
    }

    // ── ¿Olvidaste tu contraseña? ──
    const contactLink = document.getElementById('contact-admin');

    if (contactLink) {
        contactLink.addEventListener('click', function (e) {
            e.preventDefault();
            alert('Por favor contacte al administrador del sistema al correo: shojai.anzures@skilled.mx');
        });
    }

});

// ── PWA Service Worker ──
if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
        navigator.serviceWorker.register('/static/sw.js')
            .then(r => console.log('SW ok', r))
            .catch(e => console.log('SW fail', e));
    });
}
