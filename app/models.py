import uuid
from datetime import datetime
from app.extensions import db

class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), default='user')
    totp_secret = db.Column(db.String(32), nullable=True)
    
    # Profile Fields
    full_name = db.Column(db.String(150), nullable=True)
    area = db.Column(db.String(100), nullable=True)
    position = db.Column(db.String(100), nullable=True)
    factory = db.Column(db.String(100), nullable=True)
    contact_info = db.Column(db.String(200), nullable=True)
    profile_pic = db.Column(db.String(255), nullable=True, default='default.png')
    last_seen = db.Column(db.DateTime, nullable=True, default=None)

class AuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user = db.Column(db.String(80))
    action = db.Column(db.String(200))
    ip = db.Column(db.String(45))
    created_at = db.Column(db.DateTime, default=datetime.now)
