// Interceptor global: sesión expirada en AJAX
(function(){
    const _fetch = window.fetch;
    window.fetch = function(){
        return _fetch.apply(this, arguments).then(function(response){
            if (response.status === 419) {
                response.clone().json().then(function(data){
                    alert(data.error || 'Tu formulario expiró, inténtalo de nuevo.');
                    window.location.href = data.redirect || '/login';
                }).catch(function(){
                    alert('Tu formulario expiró, inténtalo de nuevo.');
                    window.location.href = '/login';
                });
            }
            return response;
        });
    };
})();
