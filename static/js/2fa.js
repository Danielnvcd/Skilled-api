document.addEventListener('DOMContentLoaded', function () {
    const codeInput = document.querySelector('input[name="code"]');
    if (codeInput) {
        // Auto-focus
        codeInput.focus();

        // Allow only numbers
        codeInput.addEventListener('input', function (e) {
            this.value = this.value.replace(/[^0-9]/g, '').slice(0, 6);
        });
    }
});
