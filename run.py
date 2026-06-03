# ── Monkey-patching gevent ───────────────────────────────────────────────────
# DEBE ejecutarse ANTES de cualquier otro import (socket, ssl, threading, etc).
# Activado vía env var SOCKETIO_ASYNC_MODE=gevent (lo setea el unit de
# gunicorn). En dev local sin esa env var, se omite y se usa Werkzeug normal
# para no alterar el debugger.
#
# psycopg3 (>=3.1.14) soporta gevent nativamente — NO necesitamos psycogreen.
import os
if os.environ.get('SOCKETIO_ASYNC_MODE') == 'gevent':
    from gevent import monkey
    monkey.patch_all()

from app import create_app, socketio

app = create_app()

if __name__ == '__main__':
    # En desarrollo usamos socketio.run para que el upgrade a WebSocket
    # funcione en el dev server (Werkzeug). En producción, Gunicorn sigue
    # invocando `run:app` — ver `Gunicorn .config`.
    socketio.run(app, host='0.0.0.0', port=5000, debug=False, allow_unsafe_werkzeug=True)
