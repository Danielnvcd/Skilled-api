from fastapi import FastAPI, Depends, HTTPException, APIRouter, Response
from sqlalchemy.orm import Session
from decimal import Decimal
import datetime
import uuid
import io
import qrcode
from PIL import Image

from .database import get_db
from .deps import get_current_user, get_inventario_user
from . import schemas
from app.models import Almacen, Estante, Producto, MovimientoInventario, SolicitudMaterial, SolicitudMaterialDetalle, User



app_fastapi = FastAPI(title="Inventario API", openapi_url="/openapi.json")

router = APIRouter()

# ... (botones anteriores omitidos por brevedad pero se mantienen en el archivo real)

# ─── Solicitudes ──────────────────────────────────────────────────────────────
@router.post("/solicitudes/", response_model=schemas.SolicitudResponse)
def create_solicitud(sol: schemas.SolicitudCreate, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    # Sólo solicitantes o admin pueden crear
    if current_user.role not in ['solicitante_material', 'admin', 'inventario']:
        raise HTTPException(status_code=403, detail="No tienes permiso para crear solicitudes")
    
    nueva = SolicitudMaterial(
        solicitante_id=current_user.id,
        proyecto=sol.proyecto,
        estatus='PENDIENTE'
    )
    db.add(nueva)
    db.flush() # Para obtener el ID de la solicitud

    for det in sol.detalles:
        producto = db.query(Producto).filter(Producto.id == det.producto_id).first()
        if not producto:
            continue
        nuevo_det = SolicitudMaterialDetalle(
            solicitud_id=nueva.id,
            producto_id=det.producto_id,
            cantidad_solicitada=Decimal(str(det.cantidad_solicitada))
        )
        db.add(nuevo_det)
    
    db.commit()
    db.refresh(nueva)
    return nueva

@router.get("/solicitudes/", response_model=list[schemas.SolicitudResponse])
def get_solicitudes(db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    query = db.query(SolicitudMaterial)
    
    # Si es solicitante, solo ve las suyas. Si es inventario/admin, ve todas.
    if current_user.role == 'solicitante_material':
        query = query.filter(SolicitudMaterial.solicitante_id == current_user.id)
    elif current_user.role not in ['inventario', 'admin']:
        raise HTTPException(status_code=403, detail="No tienes permiso")

    solicitudes = query.order_by(SolicitudMaterial.fecha_creacion.desc()).all()
    
    # Enriquecer con nombres de solicitantes y productos
    for s in solicitudes:
        s.solicitante_nombre = s.solicitante.username if s.solicitante else "Desconocido"
        for d in s.detalles:
            d.producto_descripcion = d.producto.descripcion if d.producto else "Producto eliminado"
            d.producto_codigo = d.producto.codigo if d.producto else "---"
            
    return solicitudes

@router.patch("/solicitudes/{sol_id}/estado", response_model=schemas.SolicitudResponse)
def update_solicitud_estado(sol_id: int, up: schemas.SolicitudUpdateEstado, db: Session = Depends(get_db), current_user = Depends(get_inventario_user)):
    solicitud = db.query(SolicitudMaterial).filter(SolicitudMaterial.id == sol_id).first()
    if not solicitud:
        raise HTTPException(status_code=404, detail="Solicitud no encontrada")
    
    if up.estatus not in ['APROBADA', 'RECHAZADA', 'ENTREGADA', 'PENDIENTE']:
        raise HTTPException(status_code=400, detail="Estado inválido")
    
    solicitud.estatus = up.estatus
    if up.estatus != 'PENDIENTE':
        solicitud.fecha_cierre = datetime.datetime.now()
    
    db.commit()
    db.refresh(solicitud)
    return solicitud

@router.get("/health")
def health_check():
    return {"status": "ok"}

# ─── Productos ────────────────────────────────────────────────────────────────
@router.get("/productos/", response_model=list[schemas.ProductoResponse])
def get_productos(skip: int = 0, limit: int = 200, db: Session = Depends(get_db), current_user = Depends(get_inventario_user)):
    return db.query(Producto).filter(Producto.activo == True).offset(skip).limit(limit).all()

@router.post("/productos/", response_model=schemas.ProductoResponse)
def create_producto(prod: schemas.ProductoCreate, db: Session = Depends(get_db), current_user = Depends(get_inventario_user)):
    if db.query(Producto).filter(Producto.codigo == prod.codigo).first():
        raise HTTPException(status_code=400, detail="El código de producto ya existe")
    nuevo = Producto(
        codigo=prod.codigo,
        descripcion=prod.descripcion,
        categoria=prod.categoria,
        unidad=prod.unidad,
        stock_actual=Decimal(str(prod.stock_actual)),
        stock_minimo=Decimal(str(prod.stock_minimo)),
        created_by_id=current_user.id
    )
    db.add(nuevo)
    db.commit()
    db.refresh(nuevo)
    return nuevo

@router.put("/productos/{producto_id}", response_model=schemas.ProductoResponse)
def update_producto(producto_id: int, data: schemas.ProductoUpdate, db: Session = Depends(get_db), current_user = Depends(get_inventario_user)):
    prod = db.query(Producto).filter(Producto.id == producto_id, Producto.activo == True).first()
    if not prod:
        raise HTTPException(status_code=404, detail="Producto no encontrado")
    if data.codigo is not None and data.codigo != prod.codigo:
        if db.query(Producto).filter(Producto.codigo == data.codigo).first():
            raise HTTPException(status_code=400, detail="El código ya existe en otro producto")
        prod.codigo = data.codigo
    if data.descripcion is not None: prod.descripcion = data.descripcion
    if data.categoria  is not None: prod.categoria  = data.categoria
    if data.unidad     is not None: prod.unidad     = data.unidad
    if data.stock_actual  is not None: prod.stock_actual  = Decimal(str(data.stock_actual))
    if data.stock_minimo  is not None: prod.stock_minimo  = Decimal(str(data.stock_minimo))
    db.commit()
    db.refresh(prod)
    return prod

@router.delete("/productos/{producto_id}", status_code=204)
def delete_producto(producto_id: int, db: Session = Depends(get_db), current_user = Depends(get_inventario_user)):
    prod = db.query(Producto).filter(Producto.id == producto_id).first()
    if not prod:
        raise HTTPException(status_code=404, detail="Producto no encontrado")
    prod.activo = False  # Soft delete
    db.commit()
    return Response(status_code=204)

# ─── Almacenes ────────────────────────────────────────────────────────────────
@router.get("/almacenes/", response_model=list[schemas.AlmacenResponse])
def get_almacenes(db: Session = Depends(get_db), current_user = Depends(get_inventario_user)):
    return db.query(Almacen).filter(Almacen.activo == True).all()

@router.post("/almacenes/", response_model=schemas.AlmacenResponse)
def create_almacen(alm: schemas.AlmacenCreate, db: Session = Depends(get_db), current_user = Depends(get_inventario_user)):
    nuevo = Almacen(
        nombre=alm.nombre,
        ubicacion=alm.ubicacion,
        activo=alm.activo,
        qr_code=str(uuid.uuid4())
    )
    db.add(nuevo)
    db.commit()
    db.refresh(nuevo)
    return nuevo

@router.put("/almacenes/{almacen_id}", response_model=schemas.AlmacenResponse)
def update_almacen(almacen_id: int, data: schemas.AlmacenUpdate, db: Session = Depends(get_db), current_user = Depends(get_inventario_user)):
    alm = db.query(Almacen).filter(Almacen.id == almacen_id).first()
    if not alm:
        raise HTTPException(status_code=404, detail="Bodega no encontrada")
    if data.nombre    is not None: alm.nombre    = data.nombre
    if data.ubicacion is not None: alm.ubicacion = data.ubicacion
    if data.activo    is not None: alm.activo    = data.activo
    db.commit()
    db.refresh(alm)
    return alm

@router.delete("/almacenes/{almacen_id}", status_code=204)
def delete_almacen(almacen_id: int, db: Session = Depends(get_db), current_user = Depends(get_inventario_user)):
    alm = db.query(Almacen).filter(Almacen.id == almacen_id).first()
    if not alm:
        raise HTTPException(status_code=404, detail="Bodega no encontrada")
    alm.activo = False  # Soft delete
    db.commit()
    return Response(status_code=204)

@router.get("/almacenes/{qr_code}/validar", response_model=schemas.AlmacenResponse)
def validar_almacen(qr_code: str, db: Session = Depends(get_db), current_user = Depends(get_inventario_user)):
    almacen = db.query(Almacen).filter(Almacen.qr_code == qr_code).first()
    if not almacen:
        raise HTTPException(status_code=404, detail="Almacén no encontrado o QR inválido")
    return almacen

@router.get("/almacenes/{almacen_id}/estantes", response_model=list[schemas.EstanteResponse])
def get_estantes_por_almacen(almacen_id: int, db: Session = Depends(get_db), current_user = Depends(get_inventario_user)):
    return db.query(Estante).filter(Estante.almacen_id == almacen_id, Estante.activo == True).all()

# ─── Estantes ─────────────────────────────────────────────────────────────────
@router.get("/estantes/", response_model=list[schemas.EstanteResponse])
def get_estantes(db: Session = Depends(get_db), current_user = Depends(get_inventario_user)):
    return db.query(Estante).filter(Estante.activo == True).all()

@router.post("/estantes/", response_model=schemas.EstanteResponse)
def create_estante(est: schemas.EstanteCreate, db: Session = Depends(get_db), current_user = Depends(get_inventario_user)):
    almacen = db.query(Almacen).filter(Almacen.id == est.almacen_id).first()
    if not almacen:
        raise HTTPException(status_code=404, detail="Almacén no encontrado")
    nuevo = Estante(
        nombre=est.nombre,
        descripcion=est.descripcion,
        almacen_id=est.almacen_id,
        qr_code=str(uuid.uuid4())
    )
    db.add(nuevo)
    db.commit()
    db.refresh(nuevo)
    return nuevo

@router.put("/estantes/{estante_id}", response_model=schemas.EstanteResponse)
def update_estante(estante_id: int, data: schemas.EstanteUpdate, db: Session = Depends(get_db), current_user = Depends(get_inventario_user)):
    est = db.query(Estante).filter(Estante.id == estante_id, Estante.activo == True).first()
    if not est:
        raise HTTPException(status_code=404, detail="Estante no encontrado")
    if data.nombre      is not None: est.nombre      = data.nombre
    if data.descripcion is not None: est.descripcion = data.descripcion
    if data.almacen_id  is not None:
        almacen = db.query(Almacen).filter(Almacen.id == data.almacen_id).first()
        if not almacen:
            raise HTTPException(status_code=404, detail="Bodega destino no encontrada")
        est.almacen_id = data.almacen_id
    db.commit()
    db.refresh(est)
    return est

@router.delete("/estantes/{estante_id}", status_code=204)
def delete_estante(estante_id: int, db: Session = Depends(get_db), current_user = Depends(get_inventario_user)):
    est = db.query(Estante).filter(Estante.id == estante_id).first()
    if not est:
        raise HTTPException(status_code=404, detail="Estante no encontrado")
    est.activo = False  # Soft delete
    db.commit()
    return Response(status_code=204)

@router.get("/estantes/{qr_code}/validar", response_model=schemas.EstanteResponse)
def validar_estante(qr_code: str, db: Session = Depends(get_db), current_user = Depends(get_inventario_user)):
    estante = db.query(Estante).filter(Estante.qr_code == qr_code, Estante.activo == True).first()
    if not estante:
        raise HTTPException(status_code=404, detail="Estante no encontrado o QR inválido")
    return estante

@router.get("/estantes/{estante_id}/qr-image")
def get_estante_qr_image(estante_id: int, db: Session = Depends(get_db), current_user = Depends(get_inventario_user)):
    """Devuelve el código QR del estante como imagen PNG lista para imprimir."""
    estante = db.query(Estante).filter(Estante.id == estante_id).first()
    if not estante:
        raise HTTPException(status_code=404, detail="Estante no encontrado")
    
    # Generar imagen QR
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(estante.qr_code)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    
    # Convertir a PNG bytes
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    
    return Response(content=buf.getvalue(), media_type="image/png")

# ─── Movimientos ──────────────────────────────────────────────────────────────
@router.post("/movimientos/", response_model=schemas.MovimientoResponse)
def create_movimiento(mov: schemas.MovimientoCreate, db: Session = Depends(get_db), current_user = Depends(get_inventario_user)):
    producto = db.query(Producto).with_for_update().filter(Producto.id == mov.producto_id).first()
    if not producto:
        raise HTTPException(status_code=404, detail="Producto no encontrado")
    
    cantidad_decimal = Decimal(str(mov.cantidad))
    
    if mov.tipo in ['SALIDA', 'TRASPASO']:
        if producto.stock_actual < cantidad_decimal:
            db.rollback()
            raise HTTPException(status_code=400, detail=f"Stock insuficiente. Disponible: {producto.stock_actual}")
        producto.stock_actual -= cantidad_decimal
    elif mov.tipo == 'ENTRADA':
        producto.stock_actual += cantidad_decimal
    elif mov.tipo == 'AJUSTE':
        if producto.stock_actual + cantidad_decimal < 0:
            db.rollback()
            raise HTTPException(status_code=400, detail="Ajuste provocaría stock negativo")
        producto.stock_actual += cantidad_decimal
    else:
        db.rollback()
        raise HTTPException(status_code=400, detail="Tipo de movimiento inválido")

    # Inferir almacen_destino/origen desde el estante si se proporcionó
    almacen_destino_id = mov.almacen_destino_id
    almacen_origen_id  = mov.almacen_origen_id
    if mov.estante_id and (not almacen_destino_id and not almacen_origen_id):
        estante = db.query(Estante).filter(Estante.id == mov.estante_id).first()
        if estante:
            if mov.tipo == 'ENTRADA':
                almacen_destino_id = estante.almacen_id
            else:
                almacen_origen_id = estante.almacen_id

    nuevo_mov = MovimientoInventario(
        tipo=mov.tipo,
        producto_id=mov.producto_id,
        cantidad=cantidad_decimal,
        almacen_origen_id=almacen_origen_id,
        almacen_destino_id=almacen_destino_id,
        motivo=mov.motivo or f"Estante #{mov.estante_id}" if mov.estante_id else mov.motivo,
        usuario_id=current_user.id
    )
    db.add(nuevo_mov)
    db.commit()
    db.refresh(nuevo_mov)
    return nuevo_mov

app_fastapi.include_router(router, prefix="/v1")

