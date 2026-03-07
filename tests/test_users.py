import pytest
from app.models import User
from app.routes.users import is_strong_password

def test_is_strong_password():
    # Weak passwords
    assert is_strong_password("short") is False # Too short
    assert is_strong_password("alllowercase") is False # No uppercase, no numb, no special
    assert is_strong_password("ALLUPPERCASE") is False # No lowercase, no numb, no special
    assert is_strong_password("OnlyLetters") is False # No number, no special
    assert is_strong_password("LettersAnd1") is False # No special
    assert is_strong_password("Letters!And") is False # No number
    
    # Strong passwords
    assert is_strong_password("Strong!123") is True
    assert is_strong_password("$Egura123!*") is True

def test_user_creation_enforces_strong_password(logged_in_admin, db):
    # Intentional weak password
    response_weak = logged_in_admin.post('/users/add', data={
        'username': 'testweak',
        'password': 'weakpassword',
        'role': 'admin'
    }, follow_redirects=True)
    
    assert response_weak.status_code == 200
    assert b'La contrase\xc3\xb1a es demasiado d\xc3\xa9bil' in response_weak.data
    
    # Verify user was not created
    user_weak = User.query.filter_by(username='testweak').first()
    assert user_weak is None
    
    # Intentional strong password
    response_strong = logged_in_admin.post('/users/add', data={
        'username': 'teststrong',
        'password': 'StrongPassword123!',
        'role': 'admin'
    }, follow_redirects=True)
    
    assert response_strong.status_code == 200
    assert b'Usuario creado exitosamente.' in response_strong.data
    
    # Verify user was created
    user_strong = User.query.filter_by(username='teststrong').first()
    assert user_strong is not None
    assert user_strong.username == 'teststrong'

def test_user_password_update_enforces_strong_password(logged_in_admin, db):
    # First, create a user properly to mutate
    logged_in_admin.post('/users/add', data={
        'username': 'testupdate',
        'password': 'StrongPassword123!',
        'role': 'admin'
    }, follow_redirects=True)
    
    user_update = User.query.filter_by(username='testupdate').first()
    user_id = user_update.id
    
    # Attempt weak password
    response_weak = logged_in_admin.post(f'/users/update_password/{user_id}', data={
        'new_password': 'weak'
    }, follow_redirects=True)
    
    assert response_weak.status_code == 200
    assert b'La contrase\xc3\xb1a nueva es d\xc3\xa9bil' in response_weak.data
    
    # Attempt strong password
    response_strong = logged_in_admin.post(f'/users/update_password/{user_id}', data={
        'new_password': 'NewStrongPassword321@'
    }, follow_redirects=True)
    
    assert response_strong.status_code == 200
    assert b'Contrase\xc3\xb1a actualizada para' in response_strong.data

def test_user_profile_pic_upload(logged_in_admin, db, monkeypatch):
    import os
    import io
    
    # Mock upload folder temporarily to avoid writing real files
    fake_upload_dir = "test_uploads"
    os.makedirs(fake_upload_dir, exist_ok=True)
    
    logged_in_admin.post('/users/add', data={
        'username': 'pic_uploader',
        'password': 'StrongPassword123!',
        'role': 'admin'
    }, follow_redirects=True)
    
    user = User.query.filter_by(username='pic_uploader').first()
    assert user is not None
    assert user.profile_pic == 'default.png'
    
    # Simulate an image upload
    img_data = b"fake_image_byte_content"
    img_file = io.BytesIO(img_data)
    
    # Patch the current_app.config dynamically
    from flask import current_app
    monkeypatch.setitem(current_app.config, 'UPLOAD_FOLDER', fake_upload_dir)
    
    response = logged_in_admin.post(f'/users/update_profile/{user.id}', data={
        'full_name': 'Test PIC',
        'profile_pic': (img_file, 'myphoto.jpg')
    }, content_type='multipart/form-data', follow_redirects=True)
    
    assert response.status_code == 200
    assert b'Perfil actualizado' in response.data
    
    db.session.refresh(user)
    assert user.profile_pic != 'default.png'
    assert user.profile_pic.startswith('profile_')
    assert user.profile_pic.endswith('.jpg')
    
    # Clean up dummy test files
    for f in os.listdir(fake_upload_dir):
        os.remove(os.path.join(fake_upload_dir, f))
    os.rmdir(fake_upload_dir)
