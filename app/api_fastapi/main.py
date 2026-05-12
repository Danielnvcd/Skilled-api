import os
import logging
import ipaddress
from fastapi import FastAPI, Depends, HTTPException, APIRouter, Response, Request, Query
from sqlalchemy.orm import Session, joinedload, selectinload
from decimal import Decimal
import datetime
import uuid
import io
import qrcode
from PIL import Image

from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from limits import parse as _parse_limit
from limits.strategies import MovingWindowRateLimiter as _MovingWindow

from .database import get_db
from .deps import get_current_user, get_inventario_user, get_inventario_admin_user
from . import schemas
from app.models import (
    Almacen, Estante, Producto, MovimientoInventario,
    SolicitudMaterial, SolicitudMaterialDetalle, User, AuditLog, Proyecto
)


def get_real_ip(request: Request) -> str:
    """
    Devuelve la IP real del cliente detrás de Cloudflare Tunnel.

    Reglas:
      1. Si la conexión directa viene de un rango oficial de Cloudflare → confiar en CF-Connecting-IP.
      2. Si la conexión directa es desde loopback/RFC1918 (proxy interno, dev local) → permitir
         X-Forwarded-For como fallback.
      3. En cualquier otro caso (conexión externa directa sin Cloudflare) → ignorar headers spoofables
         y usar el remote.host real. Esto cierra el vector donde un atacante que bypassea Cloudflare
         puede envenenar logs poniendo X-Forwarded-For arbitrario.
    """
    from app.extensions import is_cloudflare_ip
    remote = request.client.host if request.client else ""
    cf_ip = request.headers.get("CF-Connecting-IP")
    if cf_ip and is_cloudflare_ip(remote):
        return cf_ip.strip()

    # X-Forwarded-For sólo se confía si el peer directo es un proxy local
    if remote:
        try:
            addr = ipaddress.ip_address(remote)
            if addr.is_loopback or addr.is_private:
                xff = request.headers.get("X-Forwarded-For")
                if xff:
                    return xff.split(",")[0].strip()
        except ValueError:
            pass

    return remote or "unknown"

# ─── Configuración de la app ───────────────────────────────────────────────────
_is_prod = os.environ.get('FLASK_ENV') == 'production'
_redis_url = os.environ.get('REDIS_URL', 'memory://')

app_fastapi = FastAPI(
    title="Inventario API",
    openapi_url=None if _is_prod else "/openapi.json",
    docs_url=None if _is_prod else "/docs",
    redoc_url=None if _is_prod else "/redoc",
)

# ─── Rate limiting (SlowAPI) ───────────────────────────────────────────────────
# - key_func: IP real (respeta Cloudflare Tunnel, anti-spoofing).
# - storage_uri: Redis compartido con Flask-Limiter → contadores persistentes entre workers.
# - default_limits: 60 req/min por IP para toda la API; los endpoints sensibles tienen límites propios.
limiter = Limiter(
    key_func=get_real_ip,
    default_limits=["60/minute"],
    storage_uri=_redis_url,
)
app_fastapi.state.limiter = limiter
app_fastapi.add_middleware(SlowAPIMiddleware)


def _json_rate_limit_handler(request: Request, exc: RateLimitExceeded):
    """Devuelve 429 JSON en lugar del texto plano del handler por defecto."""
    from fastapi.responses import JSONResponse
    return JSONResponse(
        status_code=429,
        content={"error": "Demasiadas solicitudes. Espera un momento e intenta de nuevo."},
        headers={"Retry-After": str(exc.retry_after) if hasattr(exc, "retry_after") else "60"},
    )


app_fastapi.add_exception_handler(RateLimitExceeded, _json_rate_limit_handler)


def _strict_limit(limit_str: str):
    """
    Dependencia FastAPI: aplica un rate limit estricto por IP ANTES del auth.

    Por qué no usar @limiter.limit en rutas con Depends(auth):
      SlowAPI's middleware exime las rutas marcadas con @limiter.limit esperando
      que el decorador las maneje internamente. Pero el decorador envuelve el cuerpo
      de la función; si Depends(get_current_user) lanza 401 primero, el cuerpo
      nunca corre → el contador nunca incrementa → sin protección real.
    Esta dependencia corre ANTES del auth (se declara primero en la firma) y usa
    el mismo storage Redis del limiter global para contadores compartidos entre workers.
    """
    _parsed = _parse_limit(limit_str)

    async def _dep(request: Request):
        ip = get_real_ip(request)
        strategy = _MovingWindow(limiter._storage)
        if not strategy.hit(_parsed, ip):
            raise HTTPException(
                status_code=429,
                detail="Demasiadas solicitudes. Espera un momento e intenta de nuevo.",
                headers={"Retry-After": "60"},
            )

    return Depends(_dep)


router = APIRouter()

logger = logging.getLogger(__name__)

# ─── Helper: registrar auditoría desde FastAPI ─────────────────────────────────
def _audit(db: Session, user: User, action: str, request: Request | None = None):
    """Escribe una entrada en AuditLog usando la IP real (CF-Connecting-IP si aplica)."""
    ip = get_real_ip(request) if request else "api"
    entry = AuditLog(user=user.username, action=action, ip=ip)
    db.add(entry)
    # db.commit() se hará al final del endpoint que llama a este helper


# ─── Solicitudes ──────────────────────────────────────────────────────────────
@router.post("/solicitudes/", response_model=schemas.SolicitudResponse)
def create_solicitud(
    request: Request,
    sol: schemas.SolicitudCreate,
    _rl: None = _strict_limit("10/minute"),
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
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

    ids_invalidos = []
    for det in sol.detalles:
        producto = db.query(Producto).filter(Producto.id == det.producto_id, Producto.activo == True).first()
        if not producto:
            ids_invalidos.append(det.producto_id)
            continue
        nuevo_det = SolicitudMaterialDetalle(
            solicitud_id=nueva.id,
            producto_id=det.producto_id,
            cantidad_solicitada=Decimal(str(det.cantidad_solicitada))
        )
        db.add(nuevo_det)

    if ids_invalidos:
        db.rollback()
        raise HTTPException(
            status_code=400,
            detail=f"Los siguientes producto_id no existen o están inactivos: {ids_invalidos}"
        )

    _audit(db, current_user, f"Nueva solicitud de material — proyecto: {sol.proyecto or 'Sin proyecto'}", request)
    db.commit()
    db.refresh(nueva)
    return nueva

@router.get("/solicitudes/", response_model=list[schemas.SolicitudResponse])
def get_solicitudes(
    skip: int = Query(0, ge=0, le=1_000_000),
    limit: int = Query(200, ge=0, le=500),
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    query = db.query(SolicitudMaterial)

    # Si es solicitante, solo ve las suyas. Si es inventario/admin, ve todas.
    if current_user.role == 'solicitante_material':
        query = query.filter(SolicitudMaterial.solicitante_id == current_user.id)
    elif current_user.role not in ['inventario', 'admin']:
        raise HTTPException(status_code=403, detail="No tienes permiso")

    solicitudes = query\
        .options(
            joinedload(SolicitudMaterial.solicitante),
            selectinload(SolicitudMaterial.detalles)
                .joinedload(SolicitudMaterialDetalle.producto)
        )\
        .order_by(SolicitudMaterial.fecha_creacion.desc())\
        .offset(skip).limit(limit)\
        .all()

    # Enriquecer con nombres de solicitantes y productos (datos ya en memoria)
    for s in solicitudes:
        s.solicitante_nombre = s.solicitante.username if s.solicitante else "Desconocido"
        for d in s.detalles:
            d.producto_descripcion = d.producto.descripcion if d.producto else "Producto eliminado"
            d.producto_codigo     = d.producto.codigo      if d.producto else "---"
            d.producto_unidad     = d.producto.unidad      if d.producto else "pza"

    return solicitudes

@router.patch("/solicitudes/{sol_id}/estado", response_model=schemas.SolicitudResponse)
def update_solicitud_estado(
    request: Request,
    sol_id: int,
    up: schemas.SolicitudUpdateEstado,
    db: Session = Depends(get_db),
    current_user = Depends(get_inventario_admin_user)
):
    solicitud = db.query(SolicitudMaterial).filter(SolicitudMaterial.id == sol_id).first()
    if not solicitud:
        raise HTTPException(status_code=404, detail="Solicitud no encontrada")

    # Capturar estado previo para la bitácora: cambios de estatus de solicitudes son sensibles
    # (revertir ENTREGADA→PENDIENTE oculta entregas reales) y deben quedar trazados.
    estado_previo = solicitud.estatus

    solicitud.estatus = up.estatus
    if up.estatus == 'PENDIENTE':
        solicitud.fecha_cierre = None   # Reabrir: limpiar fecha de cierre
    else:
        solicitud.fecha_cierre = datetime.datetime.now()

    if estado_previo != up.estatus:
        _audit(
            db, current_user,
            f"Solicitud #{sol_id} estatus: {estado_previo} → {up.estatus}",
            request,
        )

    db.commit()
    db.refresh(solicitud)
    return solicitud

@router.get("/health")
def health_check():
    return {"status": "ok"}

# ─── Productos ────────────────────────────────────────────────────────────────
@router.get("/productos/", response_model=list[schemas.ProductoResponse])
def get_productos(
    skip: int = Query(0, ge=0, le=1_000_000),
    limit: int = Query(200, ge=0, le=1000),
    db: Session = Depends(get_db),
    current_user = Depends(get_inventario_user),
):
    return db.query(Producto).filter(Producto.activo == True).offset(skip).limit(limit).all()

@router.post("/productos/", response_model=schemas.ProductoResponse)
def create_producto(
    request: Request,
    prod: schemas.ProductoCreate,
    db: Session = Depends(get_db),
    current_user = Depends(get_inventario_admin_user)
):
    if db.query(Producto).filter(Producto.codigo == prod.codigo).first():
        raise HTTPException(status_code=400, detail="El código de producto ya existe")
    nuevo = Producto(
        codigo=prod.codigo,
        descripcion=prod.descripcion,
        categoria=prod.categoria,
        unidad=prod.unidad,
        stock_actual=Decimal(str(prod.stock_actual)),
        stock_minimo=Decimal(str(prod.stock_minimo)),
        imagen_url=prod.imagen_url or None,
        created_by_id=current_user.id
    )
    db.add(nuevo)
    _audit(db, current_user, f"Producto creado: {prod.codigo} — {prod.descripcion}", request)
    db.commit()
    db.refresh(nuevo)
    return nuevo

@router.put("/productos/{producto_id}", response_model=schemas.ProductoResponse)
def update_producto(
    request: Request,
    producto_id: int,
    data: schemas.ProductoUpdate,
    db: Session = Depends(get_db),
    current_user = Depends(get_inventario_admin_user)  # Solo admin/inventario
):
    prod = db.query(Producto).filter(Producto.id == producto_id, Producto.activo == True).first()
    if not prod:
        raise HTTPException(status_code=404, detail="Producto no encontrado")

    cambios = []
    if data.codigo is not None and data.codigo != prod.codigo:
        if db.query(Producto).filter(Producto.codigo == data.codigo).first():
            raise HTTPException(status_code=400, detail="El código ya existe en otro producto")
        cambios.append(f"codigo: {prod.codigo}→{data.codigo}")
        prod.codigo = data.codigo
    if data.descripcion is not None:
        cambios.append(f"descripcion actualizada")
        prod.descripcion = data.descripcion
    if data.categoria  is not None: prod.categoria  = data.categoria
    if data.unidad     is not None: prod.unidad     = data.unidad
    if data.imagen_url is not None: prod.imagen_url = data.imagen_url or None
    if data.stock_actual  is not None:
        cambios.append(f"stock_actual: {prod.stock_actual}→{data.stock_actual}")
        prod.stock_actual  = Decimal(str(data.stock_actual))
    if data.stock_minimo  is not None: prod.stock_minimo  = Decimal(str(data.stock_minimo))

    if cambios:
        _audit(db, current_user, f"Producto #{producto_id} editado: {'; '.join(cambios)}", request)

    db.commit()
    db.refresh(prod)
    return prod

@router.delete("/productos/{producto_id}", status_code=204)
def delete_producto(
    request: Request,
    producto_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_inventario_admin_user)  # Solo admin/inventario
):
    prod = db.query(Producto).filter(Producto.id == producto_id).first()
    if not prod:
        raise HTTPException(status_code=404, detail="Producto no encontrado")
    prod.activo = False  # Soft delete
    _audit(db, current_user, f"Producto #{producto_id} ({prod.codigo}) desactivado (soft delete)", request)
    db.commit()
    return Response(status_code=204)

# ─── Almacenes ────────────────────────────────────────────────────────────────
@router.get("/almacenes/", response_model=list[schemas.AlmacenResponse])
def get_almacenes(db: Session = Depends(get_db), current_user = Depends(get_inventario_user)):
    return db.query(Almacen).filter(Almacen.activo == True).all()

@router.post("/almacenes/", response_model=schemas.AlmacenResponse)
def create_almacen(
    request: Request,
    alm: schemas.AlmacenCreate,
    db: Session = Depends(get_db),
    current_user = Depends(get_inventario_admin_user)
):
    nuevo = Almacen(
        nombre=alm.nombre,
        ubicacion=alm.ubicacion,
        activo=alm.activo,
        qr_code=str(uuid.uuid4())
    )
    db.add(nuevo)
    _audit(db, current_user, f"Almacén creado: {alm.nombre}", request)
    db.commit()
    db.refresh(nuevo)
    return nuevo

@router.put("/almacenes/{almacen_id}", response_model=schemas.AlmacenResponse)
def update_almacen(
    request: Request,
    almacen_id: int,
    data: schemas.AlmacenUpdate,
    db: Session = Depends(get_db),
    current_user = Depends(get_inventario_admin_user)  # Solo admin/inventario
):
    alm = db.query(Almacen).filter(Almacen.id == almacen_id).first()
    if not alm:
        raise HTTPException(status_code=404, detail="Bodega no encontrada")
    if data.nombre    is not None: alm.nombre    = data.nombre
    if data.ubicacion is not None: alm.ubicacion = data.ubicacion
    if data.activo    is not None: alm.activo    = data.activo
    _audit(db, current_user, f"Almacén #{almacen_id} editado", request)
    db.commit()
    db.refresh(alm)
    return alm

@router.delete("/almacenes/{almacen_id}", status_code=204)
def delete_almacen(
    request: Request,
    almacen_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_inventario_admin_user)  # Solo admin/inventario
):
    alm = db.query(Almacen).filter(Almacen.id == almacen_id).first()
    if not alm:
        raise HTTPException(status_code=404, detail="Bodega no encontrada")
    alm.activo = False  # Soft delete
    _audit(db, current_user, f"Almacén #{almacen_id} ({alm.nombre}) desactivado (soft delete)", request)
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
def create_estante(
    request: Request,
    est: schemas.EstanteCreate,
    db: Session = Depends(get_db),
    current_user = Depends(get_inventario_admin_user)
):
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
    _audit(db, current_user, f"Estante creado: {est.nombre} en almacén #{est.almacen_id}", request)
    db.commit()
    db.refresh(nuevo)
    return nuevo

@router.put("/estantes/{estante_id}", response_model=schemas.EstanteResponse)
def update_estante(
    request: Request,
    estante_id: int,
    data: schemas.EstanteUpdate,
    db: Session = Depends(get_db),
    current_user = Depends(get_inventario_admin_user)  # Solo admin/inventario
):
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
    _audit(db, current_user, f"Estante #{estante_id} editado", request)
    db.commit()
    db.refresh(est)
    return est

@router.delete("/estantes/{estante_id}", status_code=204)
def delete_estante(
    request: Request,
    estante_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_inventario_admin_user)  # Solo admin/inventario
):
    est = db.query(Estante).filter(Estante.id == estante_id).first()
    if not est:
        raise HTTPException(status_code=404, detail="Estante no encontrado")
    est.activo = False  # Soft delete
    _audit(db, current_user, f"Estante #{estante_id} ({est.nombre}) desactivado (soft delete)", request)
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
@router.get("/movimientos/", response_model=list[schemas.MovimientoResponse])
def get_movimientos(
    producto_id: int | None = None,
    tipo: str | None = Query(None, max_length=20),
    limit: int = Query(200, ge=0, le=1000),
    db: Session = Depends(get_db),
    current_user = Depends(get_inventario_user),
):
    q = db.query(MovimientoInventario)
    if producto_id:
        q = q.filter(MovimientoInventario.producto_id == producto_id)
    if tipo:
        q = q.filter(MovimientoInventario.tipo == tipo.upper())
    return q.order_by(MovimientoInventario.fecha.desc()).limit(limit).all()

@router.get("/productos/bajo-minimo/")
def get_productos_bajo_minimo(
    db: Session = Depends(get_db),
    current_user = Depends(get_inventario_user),
):
    from sqlalchemy import text
    productos = db.query(Producto).filter(
        Producto.activo == True,
        Producto.stock_actual <= Producto.stock_minimo
    ).order_by(Producto.categoria, Producto.descripcion).all()
    return [{
        'id': p.id,
        'codigo': p.codigo,
        'descripcion': p.descripcion,
        'categoria': p.categoria,
        'unidad': p.unidad,
        'stock_actual': float(p.stock_actual),
        'stock_minimo': float(p.stock_minimo),
    } for p in productos]

@router.post("/movimientos/", response_model=schemas.MovimientoResponse)
def create_movimiento(
    request: Request,
    mov: schemas.MovimientoCreate,
    _rl: None = _strict_limit("20/minute"),
    db: Session = Depends(get_db),
    current_user = Depends(get_inventario_user),
):
    # ENTRADA/SALIDA/TRASPASO deben ser cantidades positivas
    if mov.tipo in ['ENTRADA', 'SALIDA', 'TRASPASO'] and mov.cantidad <= 0:
        raise HTTPException(status_code=422, detail="La cantidad debe ser positiva para este tipo de movimiento")

    producto = db.query(Producto).with_for_update(nowait=True).filter(Producto.id == mov.producto_id).first()
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
        motivo=mov.motivo or (f"Estante #{mov.estante_id}" if mov.estante_id else None),
        usuario_id=current_user.id
    )
    db.add(nuevo_mov)
    _audit(
        db, current_user,
        f"Movimiento {mov.tipo} — producto #{mov.producto_id} — cantidad: {mov.cantidad}",
        request
    )
    db.commit()
    db.refresh(nuevo_mov)
    return nuevo_mov

@router.get("/proyectos/")
def get_proyectos(db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    proyectos = db.query(Proyecto).filter(Proyecto.activo == True).order_by(Proyecto.numero_proyecto).all()
    return [{'id': p.id, 'numero_proyecto': p.numero_proyecto, 'nombre': p.nombre or ''} for p in proyectos]

app_fastapi.include_router(router, prefix="/v1")
