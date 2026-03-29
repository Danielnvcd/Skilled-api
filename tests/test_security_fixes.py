import pytest
from app.utils import is_strong_password, allowed_file
from werkzeug.datastructures import FileStorage
import io

def test_is_strong_password():
    assert is_strong_password("Debil123") == False
    assert is_strong_password("Fuerte123!") == True
    assert is_strong_password("fuerte123!") == False
    assert is_strong_password("FUERTE123!") == False
    assert is_strong_password("Fuerte!!") == False
    assert is_strong_password("Ab1!") == False # Too short

def test_allowed_file_valid_png():
    # Creamos un pseudo-archivo PNG
    png_magic = b'\x89PNG\r\n\x1a\n' + b'\0'*100
    file = FileStorage(stream=io.BytesIO(png_magic), filename='foto.png', content_type='image/png')
    
    # Hay que simular el config del current_app si allowed_file usa current_app.config
    # Haremos un mock o correremos dentro del test client
    pass
