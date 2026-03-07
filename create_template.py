import pandas as pd
import os
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.utils import get_column_letter
from openpyxl.styles import Alignment

# Todas las columnas posibles del empleado
columnas = [
    "No. Empleado",
    "Nombre(s)",
    "Apellidos",
    "Area",
    "Puesto",
    "Tipo de Nomina (Semanal/Por hora/Cuadrado)",
    "Salario Real Pactado por Semana",
    "Sueldo Base (SB)",
    "Salario Diario Integrado (SDI)",
    "Fecha Ingreso (YYYY-MM-DD)",
    "RFC",
    "CURP",
    "NSS",
    "Correo",
    "Celular",
    "Sexo (M/F)",
    "Estado Civil",
    "Tipo de Movimiento",
    "Tipo de Contrato",
    "Tipo de Jornada",
    "Descripcion de Servicio",
    "Fecha Inicio (YYYY-MM-DD)",
    "Termino de Prueba (YYYY-MM-DD)",
    "Nacionalidad",
    "Edad",
    "Domicilio",
    "Tipo de Sangre",
    "Alergias",
    "Enfermedades Cronicas",
    "Contacto de Emergencia",
    "Parentesco del Contacto",
    "Numero Contacto Emergencia",
    "Usa Lentes (Si/No)",
    "Licencia de Conducir (Tipo)",
    "Estatura",
    "Letra",
    "Horas Extra",
    "Infonavit",
    "Ajuste Inbursa",
    "Caja de Ahorro",
    "Viaticos",
    "Pago Dia Festivo",
    "Pagos Efectivo",
    "Folio Mov IDSE",
    "Tipo Pago",
    "Ubicacion Estado",
    "Observaciones"
]

# Crear un DataFrame vacio solo con las columnas
df = pd.DataFrame(columns=columnas)

# Asegurar que el directorio exista
os.makedirs('static/downloads', exist_ok=True)

# Guardar a excel
writer = pd.ExcelWriter('static/downloads/plantilla_empleados.xlsx', engine='openpyxl')
df.to_excel(writer, index=False, sheet_name='Empleados')

workbook = writer.book
worksheet = writer.sheets['Empleados']

# Ajustar ancho
for col in worksheet.columns:
    max_length = 0
    column = col[0].column_letter
    for cell in col:
        # Avoid checking empty cells if they crash max_length
        try:
            if cell.value and len(str(cell.value)) > max_length:
                max_length = len(str(cell.value))
        except:
            pass
    # Set a minimum width of 20, or higher based on column name
    adjusted_width = max(20, (max_length + 2) * 1.2)
    worksheet.column_dimensions[column].width = adjusted_width

# Aplicar wrap_text a las celdas para que se ajusten solas
wrap_alignment = Alignment(wrap_text=True, vertical='center')
for row in worksheet.iter_rows(min_row=2, max_row=1000, min_col=1, max_col=len(columnas)):
    for cell in row:
        cell.alignment = wrap_alignment

# === DATA VALIDATIONS (DROPDOWNS) ===

# Configurar listados
lista_nomina = '"Semanal,Por hora,Cuadrado"'
lista_sexo = '"M,F,X"'
lista_lentes = '"Si,No"'
lista_tipo_pago = '"EFECTIVO,TRANSFERENCIA"'

dv_nomina = DataValidation(type="list", formula1=lista_nomina, allow_blank=True)
dv_sexo = DataValidation(type="list", formula1=lista_sexo, allow_blank=True)
dv_lentes = DataValidation(type="list", formula1=lista_lentes, allow_blank=True)
dv_tipo_pago = DataValidation(type="list", formula1=lista_tipo_pago, allow_blank=True)

# Añadimos las validaciones a la hoja
worksheet.add_data_validation(dv_nomina)
worksheet.add_data_validation(dv_sexo)
worksheet.add_data_validation(dv_lentes)
worksheet.add_data_validation(dv_tipo_pago)

# Mapear los índices de columnas (A, B, C...)
def get_col_letter(col_name):
    try:
        idx = columnas.index(col_name) + 1 # +1 porque excel es 1-based
        return get_column_letter(idx)
    except ValueError:
        return None

# Aplicamos los rangos (desde la fila 2 hasta la 1000 como muestra)
letra_nomina = get_col_letter("Tipo de Nomina (Semanal/Por hora/Cuadrado)")
if letra_nomina:
    dv_nomina.add(f'{letra_nomina}2:{letra_nomina}1000')

letra_sexo = get_col_letter("Sexo (M/F)")
if letra_sexo:
    dv_sexo.add(f'{letra_sexo}2:{letra_sexo}1000')

letra_lentes = get_col_letter("Usa Lentes (Si/No)")
if letra_lentes:
    dv_lentes.add(f'{letra_lentes}2:{letra_lentes}1000')
    
letra_pago = get_col_letter("Tipo Pago")
if letra_pago:
    dv_tipo_pago.add(f'{letra_pago}2:{letra_pago}1000')

writer.close()
print("Plantilla (con campos seleccionables) generada exitosamente.")
