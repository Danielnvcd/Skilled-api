"""Núcleo del paquete `api_dashboard`. Solo expone el blueprint."""
from flask import Blueprint


bp = Blueprint('api_dashboard', __name__, url_prefix='/api/dashboard')
