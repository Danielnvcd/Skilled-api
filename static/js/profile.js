document.addEventListener('DOMContentLoaded', function () {
    const form = document.querySelector('form[action*="profile"]');
    if (form) {
        form.addEventListener('submit', function (e) {
            const newPass = form.querySelector('input[name="new_password"]').value;
            const confirmPass = form.querySelector('input[name="confirm_password"]').value;

            if (newPass !== confirmPass) {
                e.preventDefault();
                alert('Las contraseñas no coinciden.');
            } else if (newPass.length < 6) {
                e.preventDefault();
                alert('La contraseña debe tener al menos 6 caracteres.');
            }
        });
    }
});
