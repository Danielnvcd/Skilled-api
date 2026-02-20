import os
from datetime import datetime, date
from xhtml2pdf import pisa
from io import BytesIO
from run import app
from flask import render_template
from collections import namedtuple

# Create dummy objects substituting SQLAlchemy models
DummyProyecto = namedtuple('Proyecto', ['numero_proyecto', 'nombre'])
DummyTrabajador = namedtuple('Trabajador', ['no_empleado', 'nombre_apellidos', 'tipo_nomina'])
DummyReporte = namedtuple('Reporte', ['proyecto', 'fecha_inicio_semana', 'fecha_fin_semana'])

class DummyPrenomina:
    def __init__(self):
        self.trabajador = DummyTrabajador(no_empleado='EMP-0452', nombre_apellidos='CARLOS MENDOZA RUIZ', tipo_nomina='Semanal')
        self.tipo_pago = 'TRANSFERENCIA'
        self.salario_base = 3500.00
        self.pago_horas_extras = 450.00
        self.pago_viaticos = 600.00
        self.pago_festivos = 0.00
        self.depositos_otros = 0.00
        self.depositos_prestamos = 0.00
        
        self.descuento_infonavit = 540.00
        self.ajuste_inbursa = 0.00
        self.descuento_prestamos = 300.00
        self.descuento_incidencias = 0.00
        self.descuentos_otros = 0.00
        self.recuperacion_manual = 0.00
        
        self.total_percepciones = self.salario_base + self.pago_horas_extras + self.pago_viaticos
        self.total_deducciones = self.descuento_infonavit + self.descuento_prestamos
        self.total_a_pagar = self.total_percepciones - self.total_deducciones

with app.app_context():
    # Setup mock data for rendering
    proyecto = DummyProyecto(numero_proyecto='PRJ-9021', nombre='Mantenimiento Nave 3 Audi')
    reporte = DummyReporte(
        proyecto=proyecto, 
        fecha_inicio_semana=date(2026, 2, 10), 
        fecha_fin_semana=date(2026, 2, 16)
    )
    
    p = DummyPrenomina()
    
    # Render HTML string
    html_salida = render_template('recibo_pdf.html', reporte=reporte, p=p, loop_last=True)
    
    # Process with xhtml2pdf
    pdf = BytesIO()
    pisa_status = pisa.CreatePDF(BytesIO(html_salida.encode('utf-8')), dest=pdf)
    
    # Save to project folder
    pdf_path = os.path.join(app.config['BASE_DIR'], 'Prenomina_Demostracion_Skilled.pdf')
    
    with open(pdf_path, 'wb') as f:
        f.write(pdf.getvalue())
        
    print(f"ÉXITO: Se ha generado un PDF falso en la carpeta del proyecto: {pdf_path}")
