document.addEventListener('DOMContentLoaded', function () {
    const contactLink = document.getElementById('contact-admin');

    if (contactLink) {
        contactLink.addEventListener('click', function (e) {
            e.preventDefault();
            alert('Por favor contacte al administrador del sistema al correo: shojai.anzures@skilled.mx');
        });
    }
});
