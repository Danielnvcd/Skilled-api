from app import create_app, socketio

app = create_app()

if __name__ == '__main__':
    # En desarrollo usamos socketio.run para que el upgrade a WebSocket
    # funcione en el dev server (Werkzeug). En producción, Gunicorn sigue
    # invocando `run:app` — ver `Gunicorn .config`.
    socketio.run(app, host='0.0.0.0', port=5000, debug=False, allow_unsafe_werkzeug=True)
