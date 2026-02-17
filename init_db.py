from app import create_app
from app.extensions import db
from app.models import User
from werkzeug.security import generate_password_hash

app = create_app()

with app.app_context():
    print("Creando tablas de la base de datos...")
    db.create_all()
    print("Tablas verificadas/creadas.")

    # Check if user 'daniel' exists
    user = User.query.filter_by(username='daniel').first()
    if not user:
        print("Creando usuario administrador 'daniel'...")
        password_hash = generate_password_hash('juan123')
        new_user = User(username='daniel', password_hash=password_hash, role='admin')
        db.session.add(new_user)
        db.session.commit()
        print("Usuario 'daniel' creado exitosamente con rol 'admin'.")
    else:
        print("El usuario 'daniel' ya existe.")
