// Modal Logic for Users Page

document.addEventListener('DOMContentLoaded', function () {
    // Add User Modal Logic
    const addUserModal = document.getElementById('addUserModal');
    const btnAddUser = document.getElementById('btnAddUser');
    const closeAddUser = document.getElementById('closeAddUser');

    if (btnAddUser) {
        btnAddUser.addEventListener('click', function () {
            addUserModal.style.display = "block";
        });
    }

    if (closeAddUser) {
        closeAddUser.addEventListener('click', function () {
            addUserModal.style.display = "none";
        });
    }

    // Change Password Modal Logic
    const changePasswordModal = document.getElementById('changePasswordModal');
    const closeChangePassword = document.getElementById('closeChangePassword');
    const display = document.getElementById('passwordUserDisplay');
    const form = document.getElementById('changePasswordForm');

    if (closeChangePassword) {
        closeChangePassword.addEventListener('click', function () {
            changePasswordModal.style.display = "none";
        });
    }

    // Handle "Change Password" buttons
    const changePasswordButtons = document.querySelectorAll('.btn-change-password');
    changePasswordButtons.forEach(button => {
        button.addEventListener('click', function () {
            const userId = this.getAttribute('data-id');
            const username = this.getAttribute('data-username');

            display.innerText = "Usuario: " + username;
            form.action = "/users/update_password/" + userId;
            changePasswordModal.style.display = "block";
        });
    });

    // Handle "Delete" forms
    const deleteForms = document.querySelectorAll('.form-delete-user');
    deleteForms.forEach(form => {
        form.addEventListener('submit', function (event) {
            const username = this.getAttribute('data-username');
            if (!confirm("¿Estás seguro de que quieres eliminar al usuario " + username + "? Esta acción no se puede deshacer.")) {
                event.preventDefault();
            }
        });
    });

    // Close modals when clicking outside
    window.addEventListener('click', function (event) {
        if (event.target == addUserModal) {
            addUserModal.style.display = "none";
        }
        if (event.target == changePasswordModal) {
            changePasswordModal.style.display = "none";
        }
    });
});
