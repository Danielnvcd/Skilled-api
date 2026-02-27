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

    // Edit Profile Modal Logic
    const editProfileModal = document.getElementById('editProfileModal');
    const closeEditProfile = document.getElementById('closeEditProfile');
    const editProfileForm = document.getElementById('editProfileForm');
    const editProfileUserDisplay = document.getElementById('editProfileUserDisplay');

    if (closeEditProfile) {
        closeEditProfile.addEventListener('click', function () {
            editProfileModal.style.display = "none";
        });
    }

    const editProfileButtons = document.querySelectorAll('.btn-edit-profile');
    editProfileButtons.forEach(button => {
        button.addEventListener('click', function () {
            const userId = this.getAttribute('data-id');
            const username = this.getAttribute('data-username');

            document.getElementById('editFullName').value = this.getAttribute('data-fullname');
            document.getElementById('editArea').value = this.getAttribute('data-area');
            document.getElementById('editPosition').value = this.getAttribute('data-position');
            document.getElementById('editContact').value = this.getAttribute('data-contact');

            editProfileUserDisplay.innerText = "Usuario: " + username;
            editProfileForm.action = "/users/update_profile/" + userId;
            editProfileModal.style.display = "block";
        });
    });

    // View Profile Modal Logic (Removed inline onclick for CSP)
    const viewProfileButtons = document.querySelectorAll('.btn-view-profile');
    const viewProfileModal = document.getElementById('viewProfileModal');
    const closeViewProfile = document.getElementById('closeViewProfile');

    viewProfileButtons.forEach(button => {
        button.addEventListener('click', function () {
            const username = this.getAttribute('data-username');
            const role = this.getAttribute('data-role');
            const fullName = this.getAttribute('data-fullname');
            const area = this.getAttribute('data-area');
            const position = this.getAttribute('data-position');
            const contact = this.getAttribute('data-contact');

            document.getElementById('viewProfileAvatar').innerText = username.charAt(0).toUpperCase();
            document.getElementById('viewProfileName').innerText = username;

            const roleBadge = document.getElementById('viewProfileRoleBadge');
            roleBadge.innerText = role;

            document.getElementById('viewProfileFullName').innerText = fullName || 'No especificado';

            let posAreaText = '';
            if (position && area) posAreaText = `${position} en ${area}`;
            else if (position) posAreaText = position;
            else if (area) posAreaText = area;
            else posAreaText = 'No especificado';

            document.getElementById('viewProfilePosArea').innerText = posAreaText;
            document.getElementById('viewProfileContact').innerText = contact || 'No especificado';

            viewProfileModal.style.display = "block";
        });
    });

    if (closeViewProfile) {
        closeViewProfile.addEventListener('click', function () {
            viewProfileModal.style.display = 'none';
        });
    }

    // Close modals when clicking outside
    window.addEventListener('click', function (event) {
        if (event.target == addUserModal) {
            addUserModal.style.display = "none";
        }
        if (event.target == changePasswordModal) {
            changePasswordModal.style.display = "none";
        }
        if (event.target == editProfileModal) {
            editProfileModal.style.display = "none";
        }
        if (event.target == viewProfileModal) {
            viewProfileModal.style.display = "none";
        }
    });
});
