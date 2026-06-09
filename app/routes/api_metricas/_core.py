"""Núcleo del paquete `api_metricas`. Solo expone el blueprint."""
from flask import Blueprint


bp = Blueprint('api_metricas', __name__, url_prefix='/api/metricas')
