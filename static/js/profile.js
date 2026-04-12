document.addEventListener('DOMContentLoaded', function () {

    // ── Mismos criterios que is_strong_password() en app/utils.py ─────────────
    function calculatePasswordStrength(password) {
        const checks = {
            length:    password.length >= 8,
            uppercase: /[A-Z]/.test(password),
            lowercase: /[a-z]/.test(password),
            number:    /[0-9]/.test(password),
            special:   /[^A-Za-z0-9]/.test(password),
        };
        const score = Object.values(checks).filter(Boolean).length;
        return { score, checks };
    }

    const pwdInput         = document.getElementById('profilePasswordInput');
    const confirmInput     = document.getElementById('profileConfirmInput');
    const strengthBox      = document.getElementById('profilePwdStrength');
    const confirmMsg       = document.getElementById('profileConfirmMsg');
    const submitBtn        = document.getElementById('btnSubmitProfile');

    if (!pwdInput || !submitBtn) return;   // no estamos en la página de perfil

    let pwdStrong = false;
    let pwdMatch  = false;

    function syncSubmitBtn() {
        const ok = pwdStrong && pwdMatch;
        submitBtn.disabled          = !ok;
        submitBtn.style.opacity     = ok ? '1'           : '0.5';
        submitBtn.style.cursor      = ok ? 'pointer'     : 'not-allowed';
    }

    function updateStrength() {
        const val = pwdInput.value;

        if (!val) {
            strengthBox.style.display = 'none';
            pwdStrong = false;
            syncSubmitBtn();
            return;
        }

        strengthBox.style.display = 'block';
        const { score, checks } = calculatePasswordStrength(val);
        const bars     = strengthBox.querySelectorAll('.pwd-bar');
        const textElem = strengthBox.querySelector('.pwd-text');

        // Resetear barras
        bars.forEach(b => b.style.background = '#E5E7EB');

        const missing = [
            !checks.length    ? '8 caracteres' : '',
            !checks.uppercase ? '1 mayúscula'  : '',
            !checks.lowercase ? '1 minúscula'  : '',
            !checks.number    ? '1 número'     : '',
            !checks.special   ? '1 símbolo'    : '',
        ].filter(Boolean);

        if (score <= 2) {
            bars[0].style.background = '#E11D48';
            textElem.style.color     = '#E11D48';
            pwdStrong = false;
            textElem.innerHTML = 'Débil (Ej: Nomina12)' +
                (missing.length ? `<br><small style="color:#6B7280;font-weight:normal;margin-top:4px;display:inline-block">Falta: ${missing.join(' ')}</small>` : '');
        } else if (score < 5) {
            bars[0].style.background = '#D97706';
            bars[1].style.background = '#D97706';
            textElem.style.color     = '#D97706';
            pwdStrong = false;
            textElem.innerHTML = 'Media (Ej: G3stionSegura)' +
                (missing.length ? `<br><small style="color:#6B7280;font-weight:normal;margin-top:4px;display:inline-block">Falta: ${missing.join(' ')}</small>` : '');
        } else {
            bars[0].style.background = '#10B981';
            bars[1].style.background = '#10B981';
            bars[2].style.background = '#10B981';
            textElem.style.color     = '#10B981';
            textElem.innerText       = 'Fuerte (Ej: $Egura123!*)';
            pwdStrong = true;
        }

        // Re-evaluar coincidencia cuando cambia la contraseña principal
        updateConfirm();
    }

    function updateConfirm() {
        const val = confirmInput ? confirmInput.value : '';

        if (!val) {
            if (confirmMsg) confirmMsg.style.display = 'none';
            pwdMatch = false;
            syncSubmitBtn();
            return;
        }

        confirmMsg.style.display = 'block';
        if (pwdInput.value === val) {
            confirmMsg.textContent = '✓ Las contraseñas coinciden';
            confirmMsg.style.color = '#10B981';
            pwdMatch = true;
        } else {
            confirmMsg.textContent = '✗ Las contraseñas no coinciden';
            confirmMsg.style.color = '#E11D48';
            pwdMatch = false;
        }

        syncSubmitBtn();
    }

    pwdInput.addEventListener('input', updateStrength);
    if (confirmInput) confirmInput.addEventListener('input', updateConfirm);

    // Estado inicial: botón deshabilitado
    syncSubmitBtn();
});
