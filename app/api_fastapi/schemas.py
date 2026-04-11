from pydantic import BaseModel, ConfigDict, Field
from typing import Optional, List, Literal
from datetime import datetime

# --- Pydantic Models ---

class ProductoBase(BaseModel):
    codigo: str = Field(..., max_length=50, pattern=r'^[A-Za-z0-9\-_\.\/]+$',
                        description="Código alfanumérico único del producto")
    descripcion: str = Field(..., max_length=250)
    categoria: str = Field(..., max_length=100)
    unidad: str = Field(..., max_length=50)
    stock_minimo: float = Field(default=0.0, ge=0, le=1_000_000)

class ProductoCreate(ProductoBase):
    stock_actual: float = Field(default=0.0, ge=0, le=1_000_000)

class ProductoUpdate(BaseModel):
    codigo: Optional[str] = Field(default=None, max_length=50,
                                  pattern=r'^[A-Za-z0-9\-_\.\/]+$')
    descripcion: Optional[str] = Field(default=None, max_length=250)
    categoria: Optional[str] = Field(default=None, max_length=100)
    unidad: Optional[str] = Field(default=None, max_length=50)
    stock_actual: Optional[float] = Field(default=None, ge=0, le=1_000_000)
    stock_minimo: Optional[float] = Field(default=None, ge=0, le=1_000_000)

class ProductoResponse(ProductoBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    stock_actual: float
    activo: bool
    created_at: datetime
    updated_at: datetime
    created_by_id: Optional[int]

class EstanteBase(BaseModel):
    nombre: str = Field(..., max_length=100)
    descripcion: Optional[str] = Field(default=None, max_length=250)

class EstanteCreate(EstanteBase):
    almacen_id: int

class EstanteUpdate(BaseModel):
    nombre: Optional[str] = Field(default=None, max_length=100)
    descripcion: Optional[str] = Field(default=None, max_length=250)
    almacen_id: Optional[int] = None

class EstanteResponse(EstanteBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    almacen_id: int
    qr_code: str
    activo: bool
    created_at: datetime

class AlmacenBase(BaseModel):
    nombre: str = Field(..., max_length=100)
    ubicacion: Optional[str] = Field(default=None, max_length=250)
    activo: bool = True

class AlmacenCreate(AlmacenBase):
    pass

class AlmacenUpdate(BaseModel):
    nombre: Optional[str] = Field(default=None, max_length=100)
    ubicacion: Optional[str] = Field(default=None, max_length=250)
    activo: Optional[bool] = None

class AlmacenResponse(AlmacenBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    qr_code: str

class MovimientoCreate(BaseModel):
    tipo: Literal['ENTRADA', 'SALIDA', 'AJUSTE', 'TRASPASO']
    producto_id: int
    # AJUSTE puede ser negativo (reducción); ENTRADA/SALIDA/TRASPASO deben ser > 0
    cantidad: float = Field(..., ge=-100_000, le=100_000)
    almacen_origen_id: Optional[int] = None
    almacen_destino_id: Optional[int] = None
    estante_id: Optional[int] = None
    motivo: Optional[str] = Field(default=None, max_length=250)

class MovimientoResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tipo: str
    producto_id: int
    cantidad: float
    fecha: datetime
    almacen_origen_id: Optional[int]
    almacen_destino_id: Optional[int]
    usuario_id: int
    motivo: Optional[str]

# --- Solicitudes de Material ---

class SolicitudDetalleBase(BaseModel):
    producto_id: int
    cantidad_solicitada: float = Field(..., gt=0, le=10_000)

class SolicitudDetalleCreate(SolicitudDetalleBase):
    pass

class SolicitudDetalleResponse(SolicitudDetalleBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    cantidad_aprobada: float
    cantidad_entregada: float
    producto_descripcion: Optional[str] = None
    producto_codigo: Optional[str] = None
    producto_unidad: Optional[str] = None

class SolicitudBase(BaseModel):
    proyecto: Optional[str] = Field(default=None, max_length=200)

class SolicitudCreate(SolicitudBase):
    detalles: List[SolicitudDetalleCreate] = Field(..., min_length=1, max_length=100)

class SolicitudResponse(SolicitudBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    solicitante_id: int
    estatus: str
    fecha_creacion: datetime
    fecha_cierre: Optional[datetime] = None
    solicitante_nombre: Optional[str] = None
    detalles: List[SolicitudDetalleResponse]

class SolicitudUpdateEstado(BaseModel):
    estatus: Literal['APROBADA', 'RECHAZADA', 'ENTREGADA', 'PENDIENTE']
