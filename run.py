# Forzar recarga 5
import os
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.wsgi import WSGIMiddleware
from fastapi.middleware.cors import CORSMiddleware

from app import create_app
from app.api_fastapi.main import app_fastapi

# Instancia de la app WSGI (Flask)
flask_app = create_app()

# Creamos una app ASGI raíz (FastAPI) para montar ambos servicios localmente
root_app = FastAPI(title="Nominas y API Combinada Local", docs_url=None, redoc_url=None)

# ─── CORS: solo dominios explícitamente autorizados ───────────────────────────
# Define ALLOWED_ORIGIN en .env como lista separada por comas en producción.
# Ejemplo: ALLOWED_ORIGIN=https://mi-dominio.com,https://app.mi-dominio.com
_raw_origin = os.environ.get('ALLOWED_ORIGIN', 'https://app.skilledmx.cloud,https://dev.skilledmx.cloud')
_allowed_origins = [o.strip() for o in _raw_origin.split(',') if o.strip()]

root_app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["Content-Type", "X-Requested-With", "Authorization"],
)

# 1. Montamos la app de FastAPI específica bajo /api
root_app.mount("/api", app_fastapi)

# 2. El resto del tráfico ("/") será manejado por Flask usando WSGIMiddleware
root_app.mount("/", WSGIMiddleware(flask_app))

if __name__ == '__main__':
    # Usamos uvicorn para correr el servidor ASGI unificado en el puerto 5000
    uvicorn.run("run:root_app", host='0.0.0.0', port=5000, reload=True)