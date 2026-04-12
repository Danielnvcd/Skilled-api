import os
import logging
from fastapi import Request, HTTPException, Depends
from sqlalchemy.orm import Session
from .database import get_db

from app.models import User
from flask import Flask
from flask.sessions import SecureCookieSessionInterface

from dotenv import load_dotenv
load_dotenv()

SECRET_KEY = os.environ.get('SECRET_KEY')

# Dummy Flask app just to spin up its SecureCookieSessionInterface
# so we decode EXACTLY as Flask encoded it.
flask_app_decoder = Flask(__name__)
flask_app_decoder.secret_key = SECRET_KEY
session_interface = SecureCookieSessionInterface()
signer = session_interface.get_signing_serializer(flask_app_decoder)

def get_current_user(request: Request, db: Session = Depends(get_db)):
    session_cookie = request.cookies.get('session')
    if not session_cookie:
        raise HTTPException(status_code=401, detail="No session cookie found")
        
    if not signer:
        raise HTTPException(status_code=500, detail="Missing SECRET_KEY configuration")
        
    try:
        # Usamos el propio signer de Flask para decodificar exactamente como Flask lo codificó.
        from app.constants import SESSION_LIFETIME_SECONDS
        session_data = signer.loads(session_cookie, max_age=SESSION_LIFETIME_SECONDS)
    except Exception as e:
        logging.warning("Cookie de sesión inválida o expirada en FastAPI: %s", e)
        raise HTTPException(status_code=401, detail="Session expired or invalid")

    user_id = session_data.get('_user_id') or session_data.get('user_id')
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid session data")

    user = db.query(User).filter(User.id == int(user_id)).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    # Verificar password_version: si el usuario cambió su contraseña la cookie
    # vieja queda inválida aunque siga firmada correctamente (igual que Flask).
    session_pw_ver = int(session_data.get('password_version', 1))
    if session_pw_ver != (user.password_version or 1):
        raise HTTPException(status_code=401, detail="Session invalidated by password change")

    return user

def get_inventario_user(user: User = Depends(get_current_user)):
    """Permite lectura a solicitantes, inventario y admin."""
    if user.role not in ['inventario', 'solicitante_material', 'admin']:
        raise HTTPException(status_code=403, detail="Forbidden: Required permissions missing")
    return user

def get_inventario_admin_user(user: User = Depends(get_current_user)):
    """Solo permite roles 'inventario' y 'admin' (operaciones de escritura/borrado)."""
    if user.role not in ['inventario', 'admin']:
        raise HTTPException(status_code=403, detail="Se requiere rol de inventario o administrador")
    return user
