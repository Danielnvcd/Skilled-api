"""Validación de longitudes de campos del modelo Trabajador."""

# ──────────────────────────────────────────────
# Validación de longitudes de campos en Trabajadores
# ──────────────────────────────────────────────
TRABAJADOR_LENGTHS = {
    'no_empleado': 50,
    'nombre': 250,
    'nombre_apellidos': 250,
    'tipo_mov': 100,
    'tipo_cont': 100,
    'area': 150,
    'puesto': 150,
    'tipo_jornada': 100,
    'curp': 18,
    'rfc': 13,
    'nss': 20,
    'letra_fecha_nac': 50,
    'sexo': 20,
    'nacionalidad': 100,
    'estado_civil': 50,
    'correo': 150,
    'celular': 20,
    'tipo_sangre': 10,
    'contacto_emergencia': 200,
    'parentesco_contacto': 100,
    'numero_contacto_emerg': 20,
    'lentes': 20,
    'licencia_conducir': 50,
    'estatura': 20,
    'letra': 100,
    'folio_mov_idse': 100,
    'tipo_pago': 100,
    'tipo_nomina': 50,
    'no_proyecto': 100,
    'coord_a_cargo': 150,
    'ubicacion_actual': 150,
    'ubicacion_estado': 100,
    'planta': 100,
    'credencial_id': 40,
}


def validate_lengths(data, lengths_dict=TRABAJADOR_LENGTHS):
    """
    Valida las longitudes recibidas en `data` contra el `lengths_dict`.
    Realiza strip() sobre los valores validables. Retorna lista de errores.
    """
    errores = []
    # Procesar solo campos declarados en el lengths_dict que lleguen en data
    for campo, max_len in lengths_dict.items():
        if campo in data:
            val = data[campo]
            # Solo validamos si es string
            if isinstance(val, str):
                val_stripped = val.strip()
                if len(val_stripped) > max_len:
                    errores.append(f"El campo '{campo}' excede el límite de {max_len} caracteres (recibidos: {len(val_stripped)}).")
    return errores
