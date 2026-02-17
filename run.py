from app import create_app

app = create_app()

if __name__ == '__main__':
    # Usando ssl_context='adhoc' para habilitar HTTPS localmente
    # Nota: El navegador mostrará una advertencia de seguridad que deberás aceptar.
    app.run(host='0.0.0.0', port=5000, debug=False)