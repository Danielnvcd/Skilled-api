// Modal Logic for Users Page

document.addEventListener('DOMContentLoaded', function () {
    // Add User Modal Logic
    const addUserModal = document.getElementById('addUserModal');
    const btnAddUser = document.getElementById('btnAddUser');
    const closeAddUser = document.getElementById('closeAddUser');

    if (btnAddUser) {
        btnAddUser.addEventListener('click', function () {
            const form = document.querySelector('#addUserModal form');
            if (form) form.reset();
            const pwdInput = document.getElementById('addPasswordInput');
            if (pwdInput) {
                pwdInput.value = '';
                pwdInput.dispatchEvent(new Event('input'));
            }
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

            if (form) form.reset();
            const pwdInput = document.getElementById('changePasswordInput');
            if (pwdInput) {
                pwdInput.value = '';
                pwdInput.dispatchEvent(new Event('input'));
            }

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
            const fileInput = document.getElementById('profile_pic');
            const previewAvatar = document.getElementById('uploadAvatarPreview');
            const uploadText = document.getElementById('file-upload-text');
            const removeBtn = document.getElementById('preview-remove-btn');

            if (fileInput) fileInput.value = '';
            if (uploadText) uploadText.innerText = 'Ningún archivo seleccionado';
            if (removeBtn) removeBtn.style.display = 'none';

            const profilePicUrl = this.getAttribute('data-profile-pic');
            if (previewAvatar) {
                if (profilePicUrl && profilePicUrl.trim() !== '') {
                    previewAvatar.innerHTML = `<img src="${profilePicUrl}" alt="Current Profile">`;
                    previewAvatar.style.borderStyle = 'solid';
                } else {
                    previewAvatar.innerHTML = '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#94A3B8" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path><circle cx="12" cy="7" r="4"></circle></svg>';
                    previewAvatar.style.borderStyle = 'dashed';
                }
            }

            editProfileModal.style.display = "block";
        });
    });

    const fileInput = document.getElementById('profile_pic');
    if (fileInput) {
        fileInput.addEventListener('change', function (e) {
            const previewAvatar = document.getElementById('uploadAvatarPreview');
            const uploadText = document.getElementById('file-upload-text');
            const removeBtn = document.getElementById('preview-remove-btn');

            if (this.files && this.files[0]) {
                const file = this.files[0];
                const reader = new FileReader();

                reader.onload = function (e) {
                    previewAvatar.innerHTML = `<img src="${e.target.result}" alt="Preview">`;
                    previewAvatar.style.borderStyle = 'solid';
                    uploadText.innerText = file.name;
                    removeBtn.style.display = 'inline-block';
                }

                reader.readAsDataURL(file);
            }
        });

        const removePreviewBtn = document.getElementById('preview-remove-btn');
        if (removePreviewBtn) {
            removePreviewBtn.addEventListener('click', function () {
                fileInput.value = '';
                document.getElementById('file-upload-text').innerText = 'Ningún archivo seleccionado';
                this.style.display = 'none';
                const previewAvatar = document.getElementById('uploadAvatarPreview');
                previewAvatar.innerHTML = '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#94A3B8" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path><circle cx="12" cy="7" r="4"></circle></svg>';
                previewAvatar.style.borderStyle = 'dashed';
            });
        }
    }

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
            const profilePicUrl = this.getAttribute('data-profile-pic');

            const avatarElem = document.getElementById('viewProfileAvatar');
            if (profilePicUrl && profilePicUrl.trim() !== '') {
                avatarElem.style.backgroundImage = `url('${profilePicUrl}')`;
                avatarElem.style.backgroundSize = 'cover';
                avatarElem.style.backgroundPosition = 'center';
                avatarElem.innerText = '';
            } else {
                avatarElem.style.backgroundImage = 'none';
                avatarElem.innerText = username.charAt(0).toUpperCase();
            }

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

    // Password Strength Logic
    function calculatePasswordStrength(password) {
        let score = 0;
        let checks = {
            length: password.length >= 8,
            uppercase: /[A-Z]/.test(password),
            lowercase: /[a-z]/.test(password),
            number: /[0-9]/.test(password),
            special: /[^A-Za-z0-9]/.test(password)
        };

        if (checks.length) score += 1;
        if (checks.uppercase) score += 1;
        if (checks.lowercase) score += 1;
        if (checks.number) score += 1;
        if (checks.special) score += 1;

        return { score, checks };
    }

    function updateStrengthMeter(inputElem, containerId, btnId) {
        const container = document.getElementById(containerId);
        const submitBtn = document.getElementById(btnId);
        const bars = container.querySelectorAll('.pwd-bar');
        const textElem = container.querySelector('.pwd-text');

        if (!inputElem || !container || !submitBtn) return;

        if (inputElem.value.length === 0) {
            container.style.display = 'none';
            submitBtn.disabled = true;
            submitBtn.style.opacity = '0.5';
            submitBtn.style.cursor = 'not-allowed';
            return;
        }

        container.style.display = 'block';
        const { score, checks } = calculatePasswordStrength(inputElem.value);

        // Reset bars
        bars.forEach(bar => bar.style.background = '#E5E7EB');

        let strengthText = "";
        let isStrong = false;

        if (score <= 2) {
            bars[0].style.background = '#E11D48'; // Red
            strengthText = "Débil (Ej: Nomina12)";
        } else if (score >= 3 && score < 5) {
            bars[0].style.background = '#D97706'; // Yellow
            bars[1].style.background = '#D97706';
            strengthText = "Media (Ej: G3stionSegura)";
        } else if (score === 5) {
            bars[0].style.background = '#10B981'; // Green
            bars[1].style.background = '#10B981';
            bars[2].style.background = '#10B981';
            strengthText = "Fuerte (Ej: $Egura123!*)";
            isStrong = true;
        }

        textElem.innerText = strengthText;
        textElem.style.color = score <= 2 ? '#E11D48' : (score < 5 ? '#D97706' : '#10B981');

        if (isStrong) {
            submitBtn.disabled = false;
            submitBtn.style.opacity = '1';
            submitBtn.style.cursor = 'pointer';
        } else {
            submitBtn.disabled = true;
            submitBtn.style.opacity = '0.5';
            submitBtn.style.cursor = 'not-allowed';
            textElem.innerHTML = strengthText + "<br><small style='color: #6B7280; font-weight: normal; margin-top: 4px; display: inline-block;'>Falta: " +
                (!checks.length ? "8 caracteres " : "") +
                (!checks.uppercase ? "1 mayúscula " : "") +
                (!checks.lowercase ? "1 minúscula " : "") +
                (!checks.number ? "1 número " : "") +
                (!checks.special ? "1 símbolo" : "") + "</small>";
        }
    }

    const addPwdInput = document.getElementById('addPasswordInput');
    if (addPwdInput) {
        addPwdInput.addEventListener('input', function () {
            updateStrengthMeter(this, 'addPwdStrength', 'btnSubmitAddUser');
        });
        updateStrengthMeter(addPwdInput, 'addPwdStrength', 'btnSubmitAddUser');
    }

    const changePwdInput = document.getElementById('changePasswordInput');
    if (changePwdInput) {
        changePwdInput.addEventListener('input', function () {
            updateStrengthMeter(this, 'changePwdStrength', 'btnSubmitChangePwd');
        });
        // Initial setup when modal opens
        const btnChange = document.getElementById('btnSubmitChangePwd');
        if (btnChange) {
            btnChange.disabled = true;
            btnChange.style.opacity = '0.5';
            btnChange.style.cursor = 'not-allowed';
        }
    }
});
