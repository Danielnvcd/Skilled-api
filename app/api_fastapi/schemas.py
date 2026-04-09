from pydantic import BaseModel, condecimal, Field
from typing import Optional, List
from datetime import datetime

# --- Pydantic Models ---

class ProductoBase(BaseModel):
    codigo: str
    descripcion: str
    categoria: str
    unidad: str
    stock_minimo: float = 0.0

class ProductoCreate(ProductoBase):
    stock_actual: float = 0.0

class ProductoUpdate(BaseModel):
    codigo: Optional[str] = None
    descripcion: Optional[str] = None
    categoria: Optional[str] = None
    unidad: Optional[str] = None
    stock_actual: Optional[float] = None
    stock_minimo: Optional[float] = None

class ProductoResponse(ProductoBase):
    id: int
    stock_actual: float
    activo: bool
    created_at: datetime
    updated_at: datetime
    created_by_id: Optional[int]

    class Config:
        from_attributes = True

class EstanteBase(BaseModel):
    nombre: str
    descripcion: Optional[str] = None

class EstanteCreate(EstanteBase):
    almacen_id: int

class EstanteUpdate(BaseModel):
    nombre: Optional[str] = None
    descripcion: Optional[str] = None
    almacen_id: Optional[int] = None

class EstanteResponse(EstanteBase):
    id: int
    almacen_id: int
    qr_code: str
    activo: bool
    created_at: datetime

    class Config:
        from_attributes = True

class AlmacenBase(BaseModel):
    nombre: str
    ubicacion: Optional[str]
    activo: bool = True

class AlmacenCreate(AlmacenBase):
    pass

class AlmacenUpdate(BaseModel):
    nombre: Optional[str] = None
    ubicacion: Optional[str] = None
    activo: Optional[bool] = None

class AlmacenResponse(AlmacenBase):
    id: int
    qr_code: str

    class Config:
        from_attributes = True

class MovimientoCreate(BaseModel):
    tipo: str # ENTRADA, SALIDA, AJUSTE, TRASPASO
    producto_id: int
    cantidad: float
    almacen_origen_id: Optional[int] = None
    almacen_destino_id: Optional[int] = None
    estante_id: Optional[int] = None  # Estante donde ocurre el movimiento (referencia)
    motivo: Optional[str] = None

class MovimientoResponse(BaseModel):
    id: int
    tipo: str
    producto_id: int
    cantidad: float
    fecha: datetime
    almacen_origen_id: Optional[int]
    almacen_destino_id: Optional[int]
    usuario_id: int
    motivo: Optional[str]

    class Config:
        from_attributes = True

# --- Solicitudes de Material ---

class SolicitudDetalleBase(BaseModel):
    producto_id: int
    cantidad_solicitada: float

class SolicitudDetalleCreate(SolicitudDetalleBase):
    pass

class SolicitudDetalleResponse(SolicitudDetalleBase):
    id: int
    cantidad_aprobada: float
    cantidad_entregada: float
    producto_descripcion: Optional[str] = None
    producto_codigo: Optional[str] = None

    class Config:
        from_attributes = True

class SolicitudBase(BaseModel):
    proyecto: Optional[str] = None

class SolicitudCreate(SolicitudBase):
    detalles: List[SolicitudDetalleCreate]

class SolicitudResponse(SolicitudBase):
    id: int
    solicitante_id: int
    estatus: str
    fecha_creacion: datetime
    fecha_cierre: Optional[datetime] = None
    solicitante_nombre: Optional[str] = None
    detalles: List[SolicitudDetalleResponse]

    class Config:
        from_attributes = True

class SolicitudUpdateEstado(BaseModel):
    estatus: str # APROBADA, RECHAZADA, ENTREGADA
