# Forzar recarga 5
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.wsgi import WSGIMiddleware

from app import create_app
from app.api_fastapi.main import app_fastapi

# Instancia de la app WSGI (Flask)
flask_app = create_app()

# Creamos una app ASGI raíz (FastAPI) para montar ambos servicios localmente
root_app = FastAPI(title="Nominas y API Combinada Local")

# 1. Montamos la app de FastAPI específica bajo /api/v1
# Nota: Como main.py ya dice app_fastapi.include_router(router, prefix="/api/v1"),
# si montamos en "/", /api/v1 funcionará directo. Lo haremos montando en la raíz de FastAPI principal.
root_app.mount("/api", app_fastapi) 

# 2. El restro del tráfico ("/") será manejado por Flask usando WSGIMiddleware
root_app.mount("/", WSGIMiddleware(flask_app))

if __name__ == '__main__':
    # Usamos uvicorn para correr el servidor ASGI unificado en el puerto 5000
    uvicorn.run("run:root_app", host='0.0.0.0', port=5000, reload=True)