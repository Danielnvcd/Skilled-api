(function() {
    // Al usar Tailwind vía CDN (asíncrono), los estilos CSS tardan milisegundos en formarse, 
    // por lo que las dimensiones topológicas son 0 inicialmente.
    // Usamos un intervalo de alta frecuencia para detectar el milisegundo exacto en que la interfaz se forma.
    
    function autoScrollMenu() {
        const activeItem = document.querySelector('#sidebar-menu [data-active="true"]');
        const menu = document.getElementById("sidebar-menu");
        
        if (!activeItem || !menu) return;
        
        let attempts = 0;
        const intervalId = setInterval(function() {
            attempts++;
            
            // Detectamos que Tailwind ya acomodó el contenido cuando el contenedor gana un "scroll" interno
            // O si pasan demasiados intentos (500ms salvavidas)
            if (menu.scrollHeight > menu.clientHeight || attempts > 25) {
                clearInterval(intervalId); // Detenemos la busqueda instantaneamente
                
                // Calculo matematico directo para centrar el scroll del navegador internamente
                const itemTop = activeItem.offsetTop;
                const menuHeight = menu.clientHeight;
                const itemHeight = activeItem.clientHeight;
                
                if (itemTop > (menuHeight / 2)) {
                    menu.scrollTop = itemTop - (menuHeight / 2) + (itemHeight / 2);
                }
            }
        }, 20); // Revisión agresiva cada 20ms para reaccionar antes de que el ojo humano lo note
    }
    
    // Ejecutarlo
    autoScrollMenu();
})();
