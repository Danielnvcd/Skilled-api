// Verificar tema inmediatamente para evitar FOUC (parpadeo blanco)
(function() {
    const currentTheme = localStorage.getItem('theme') || 'dark';
    if (currentTheme === 'light') {
        document.documentElement.classList.add('light-theme');
    }
})();

document.addEventListener("DOMContentLoaded", function() {
    const toggleBtn = document.getElementById('themeToggleBtn');
    const htmlElement = document.documentElement;

    if (toggleBtn) {
        // Establecer ícono inicial
        updateToggleIcon(htmlElement.classList.contains('light-theme'));

        toggleBtn.addEventListener('click', function() {
            htmlElement.classList.toggle('light-theme');
            
            const isLight = htmlElement.classList.contains('light-theme');
            localStorage.setItem('theme', isLight ? 'light' : 'dark');
            
            updateToggleIcon(isLight);
        });
    }

    function updateToggleIcon(isLight) {
        if (isLight) {
            toggleBtn.innerHTML = '<i class="fa-solid fa-moon"></i>';
            toggleBtn.title = 'Cambiar a Modo Oscuro';
        } else {
            toggleBtn.innerHTML = '<i class="fa-solid fa-sun"></i>';
            toggleBtn.title = 'Cambiar a Modo Claro';
        }
    }
});
