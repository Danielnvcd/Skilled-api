// Interceptor global: sesión expirada en AJAX
(function(){
    // Helper para mostrar un toast no intrusivo en lugar de alert
    function showToastAndRedirect(message, redirectUrl) {
        if (!document.getElementById('session-error-toast')) {
            const toast = document.createElement('div');
            toast.id = 'session-error-toast';
            toast.textContent = message;
            toast.style.cssText = 'position: fixed; top: 20px; right: 20px; background: #ef4444; color: white; padding: 15px 25px; border-radius: 8px; z-index: 10000; box-shadow: 0 4px 12px rgba(0,0,0,0.15); font-family: "Inter", sans-serif; font-weight: 500; opacity: 0; transition: opacity 0.3s ease;';
            document.body.appendChild(toast);
            
            // Fade in
            requestAnimationFrame(() => {
                toast.style.opacity = '1';
            });
            
            if (redirectUrl) {
                // Redirect after delay
                setTimeout(() => {
                    window.location.href = redirectUrl;
                }, 1500);
            } else {
                // Remove toast after delay instead of redirecting
                setTimeout(() => {
                    toast.style.opacity = '0';
                    setTimeout(() => {
                        if (toast.parentNode) toast.parentNode.removeChild(toast);
                    }, 300);
                }, 3000);
            }
        }
    }

    function handleAuthError(status, responseData, defaultRedirect) {
        if ([401, 403, 419].includes(status)) {
            let msg = 'Tu sesión ha expirado, inténtalo de nuevo.';
            if (status === 403) msg = 'No tienes permiso para realizar esta acción.';
            if (responseData && responseData.error) msg = responseData.error;
            
            let url = defaultRedirect || '/login';
            if (status === 403) url = null; // Default behavior for 403 is no redirect
            if (responseData && responseData.redirect) url = responseData.redirect;
            
            showToastAndRedirect(msg, url);
            return true; // handled
        }
        return false;
    }

    // Interceptar fetch
    const _fetch = window.fetch;
    window.fetch = function(){
        return _fetch.apply(this, arguments).then(function(response){
            if ([401, 403, 419].includes(response.status)) {
                response.clone().json().then(function(data){
                    handleAuthError(response.status, data);
                }).catch(function(){
                    handleAuthError(response.status, null);
                });
            }
            return response;
        });
    };

    // Interceptar XMLHttpRequest
    const _send = window.XMLHttpRequest.prototype.send;
    window.XMLHttpRequest.prototype.send = function() {
        this.addEventListener('load', function() {
            if ([401, 403, 419].includes(this.status)) {
                let data = null;
                try {
                    if (this.responseText) {
                        data = JSON.parse(this.responseText);
                    }
                } catch (e) {}
                
                handleAuthError(this.status, data);
            }
        });
        return _send.apply(this, arguments);
    };
})();
