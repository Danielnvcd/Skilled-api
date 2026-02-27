from app import create_app
from app.extensions import db
from app.models import Prenomina, ReporteSemanal, RegistroDiarioHoras, DepositosExtra, DescuentosExtra

app = create_app()

with app.app_context():
    print("Iniciando limpieza de datos de prueba...")
    
    try:
        # 0. Eliminar dependencias de Prenóminas
        num_depositos = DepositosExtra.query.delete()
        num_descuentos = DescuentosExtra.query.delete()
        print(f"Eliminados {num_depositos} depósitos extra y {num_descuentos} descuentos extra.")
        
        # 1. Eliminar Prenóminas
        num_prenominas = Prenomina.query.delete()
        print(f"Eliminadas {num_prenominas} registros de Prenomina.")
        
        # 2. Eliminar RegistrosDiarioHoras
        num_registros = RegistroDiarioHoras.query.delete()
        print(f"Eliminados {num_registros} registros de horas.")

        # 3. Eliminar Reportes Semanales
        num_reportes = ReporteSemanal.query.delete()
        print(f"Eliminados {num_reportes} registros de ReporteSemanal.")
        
        db.session.commit()
        print("¡Limpieza completada con éxito!")
    except Exception as e:
        db.session.rollback()
        print(f"Error durante la limpieza: {e}")
