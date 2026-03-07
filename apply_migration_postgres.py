from app import create_app, db
from sqlalchemy import text

app = create_app()
with app.app_context():
    try:
        db.session.execute(text("ALTER TABLE ajuste_descuentos ADD COLUMN cobrado BOOLEAN DEFAULT false;"))
        db.session.commit()
        print("Migracion Postgres exitosa.")
    except Exception as e:
        print(f"Error o ya migrado: {e}")
