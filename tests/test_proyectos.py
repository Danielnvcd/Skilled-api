"""Tests de integración para el módulo de proyectos."""
import json
import pytest
from app.models import Proyecto, User


class TestProyectosEdgeCases:
    """Pruebas para capturar errores humanos en la creación de proyectos."""

    def test_crear_proyecto_duplicado(self, logged_in_admin, db, admin_user):
        """No debería permitir crear un proyecto con un número que ya existe."""
        # 1. Crear el primer proyecto
        p1 = Proyecto(
            numero_proyecto='DUP-001',
            nombre='Proyecto Original',
            activo=True,
            coordinador_id=admin_user.id
        )
        db.session.add(p1)
        db.session.commit()

        resp = logged_in_admin.post('/proyectos/agregar',
            data={
                'numero_proyecto': 'DUP-001',
                'nombre': 'Proyecto Copia',
                'coordinador_id': admin_user.id
            },
            follow_redirects=True
        )
        
        html_response = resp.data.decode('utf-8')
        
        # El sistema debería prevenir esto (IntegrityError manejado o validación de form)
        # La respuesta va a tener un mensaje sobre ya existir
        assert 'ya existe' in html_response.lower() or 'duplicado' in html_response.lower()

        # Verificar que sólo hay uno
        proyectos = Proyecto.query.filter_by(numero_proyecto='DUP-001').all()
        assert len(proyectos) == 1
        assert proyectos[0].nombre == 'Proyecto Original'

    def test_crear_proyecto_campos_vacios(self, logged_in_admin, db):
        """No debe crear un proyecto sin número de proyecto (o nombre)."""
        resp = logged_in_admin.post('/proyectos/agregar',
            data={
                'numero_proyecto': '',
                'nombre': '',
                'coordinador_id': ''
            },
            follow_redirects=True
        )
        
        html_response = resp.data.decode('utf-8')
        assert 'obligatorio' in html_response.lower() or 'requerido' in html_response.lower()
        
        # Verificar BD
        proyectos_vacios = Proyecto.query.filter_by(numero_proyecto='').all()
        assert len(proyectos_vacios) == 0

    def test_agregar_trabajador_duplicado_a_proyecto(self, logged_in_admin, db, admin_user, trabajador):
        """Si un trabajador ya está en el proyecto, agregarlo de nuevo no debe repetirlo/reventar."""
        # 1. Crear proyecto y agregar trabajador inicialmente
        p = Proyecto(
            numero_proyecto='MIX-001',
            nombre='Proyecto Mixto',
            activo=True,
            coordinador_id=admin_user.id
        )
        p.participantes.append(trabajador)
        db.session.add(p)
        db.session.commit()

        # Asegurarnos de que está ahí una vez
        assert len(p.participantes) == 1

        resp = logged_in_admin.post(f'/proyectos/editar/{p.id}',
            data={
                'numero_proyecto': p.numero_proyecto,
                'nombre': p.nombre,
                'coordinador_id': p.coordinador_id,
                'participantes_json': json.dumps([trabajador.id, trabajador.id]) # simulamos intento de duplicado en frontend
            },
            follow_redirects=True
        )
        
        html_response = resp.data.decode('utf-8')
        assert ('ya está' in html_response.lower() or 
                'ya asignado' in html_response.lower() or 
                'asignado' in html_response.lower())

        # Volver a cargar un refresh de BD y verificar logitud de lista
        db.session.refresh(p)
        assert len(p.participantes) == 1
