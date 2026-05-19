# Plan — Funciones nuevas para el rol Inventario

**Fecha:** 2026-05-19
**Branch base:** `Inventario`
**Autor:** Daniel Rivera

## Contexto

El módulo de Inventario hoy cubre: catálogo de productos, almacenes y estantes con QR, movimientos (ENTRADA/SALIDA/AJUSTE/TRASPASO), solicitudes con PDF, importación masiva por Excel, "mis pedidos" y un endpoint de productos bajo mínimo (sin UI).

Falta lo que cualquier almacenista pide en el día a día: ver el historial de un producto, saber qué hay que comprar, entregar parcial, recibir alertas y poder hacer conteos físicos. Este documento describe esas funciones para implementarlas por fases.

## Resumen de fases

| Fase | Funciones | Esfuerzo | Bloqueante de |
|------|-----------|----------|---------------|
| **1 – Crítico** | Kardex · Dashboard · Bajo mínimo UI · Entrega parcial · Notificaciones | 1–2 semanas | — |
| **2 – Importante** | Reportes exportables · Conteo físico · Devoluciones · Auditoría visible · Filtros avanzados | 1–2 semanas | Fase 1 |
| **3 – Compras** | Proveedores · Órdenes de compra · Costeo | 2–3 semanas | Fase 2 |
| **4 – Refactor opcional** | Stock por almacén | 1 semana | Decisión de negocio |

---

## FASE 1 — CRÍTICO

### 1.1 Kardex por producto

**Objetivo:** Pantalla con el historial cronológico de cada producto mostrando saldo corrido (cada fila muestra `cantidad anterior`, `movimiento`, `saldo nuevo`).

**Archivos a tocar:**
- `app/routes/inventario_ui.py` → nueva ruta `/inventario/kardex/<int:producto_id>`
- `app/routes/inventario_api.py` → nuevo endpoint `GET /api/v1/productos/<id>/kardex`
- `templates/inventario_kardex.html` (nuevo)
- `static/js/inventario_kardex.js` (nuevo)
- `static/css/inventario_web.css` → estilos de tabla

**Endpoint:**
```
GET /api/v1/productos/<id>/kardex?desde=2026-01-01&hasta=2026-05-19&limit=500
Response:
{
  "producto": { "id": 1, "codigo": "TOR-001", "descripcion": "...", "stock_actual": 50 },
  "saldo_inicial": 30,
  "movimientos": [
    { "fecha": "...", "tipo": "ENTRADA", "cantidad": 20, "saldo": 50,
      "usuario": "daniel", "almacen": "Bodega Centro", "motivo": "OC-123" }
  ]
}
```

**Cambios de BD:** Ninguno. Toda la información ya existe en `MovimientoInventario`.

**Cálculo del saldo:**
- Calcular `saldo_inicial = stock_actual - SUM(movimientos posteriores a 'desde')`.
- Hacer el corrido en Python (rápido para <5000 movs); si crece, mover a SQL con `SUM() OVER (ORDER BY fecha)`.

**UI:**
- Filtros: rango de fechas (default últimos 30 días), tipo de movimiento.
- Tabla: Fecha · Tipo · Cantidad (± con color) · Saldo · Almacén · Usuario · Motivo.
- Botón "Exportar a Excel" (ver Fase 2).
- Entrada: link desde catálogo (botón "Ver kardex" en cada producto).

**Criterios de aceptación:**
- [ ] Suma de entradas − suma de salidas = stock_actual (válido siempre).
- [ ] La fila más reciente muestra saldo = `producto.stock_actual`.
- [ ] Filtro por fechas no rompe el saldo corrido.
- [ ] Cargar 1000 movimientos toma <1s.

---

### 1.2 Dashboard de inventario

**Objetivo:** Pantalla `/inventario/dashboard` con KPIs y gráficas para que el responsable vea el estado global de un vistazo.

**Archivos a tocar:**
- `app/routes/inventario_ui.py` → ruta `/inventario/dashboard`
- `app/routes/inventario_api.py` → endpoint `GET /api/v1/dashboard`
- `templates/inventario_dashboard.html` (nuevo)
- `static/js/inventario_dashboard.js` (nuevo, usar Chart.js)

**Endpoint:**
```
GET /api/v1/dashboard
Response:
{
  "kpis": {
    "total_productos_activos": 234,
    "productos_bajo_minimo": 12,
    "valor_total_inventario": 0,    // 0 hasta Fase 3 (costeo)
    "movimientos_hoy": 18,
    "solicitudes_pendientes": 5
  },
  "movimientos_ultimos_7_dias": [
    { "fecha": "2026-05-13", "entradas": 5, "salidas": 12 }
  ],
  "top_10_productos_mas_movidos_30d": [
    { "producto_id": 1, "descripcion": "...", "cantidad_total": 250 }
  ],
  "consumo_por_proyecto_30d": [
    { "proyecto": "Obra Norte", "cantidad": 120 }
  ]
}
```

**Cambios de BD:** Ninguno.

**UI (4 tarjetas KPI + 3 gráficas):**
- Tarjetas: Productos activos · Bajo mínimo (rojo si >0) · Movs hoy · Solicitudes pendientes.
- Gráfica 1: Línea de entradas vs salidas últimos 7 días.
- Gráfica 2: Barras horizontales top 10 productos más movidos.
- Gráfica 3: Pie de consumo por proyecto.
- Cada KPI/gráfica linkea al detalle (bajo mínimo → pantalla 1.3, solicitudes → solicitudes filtradas, etc.).

**Criterios de aceptación:**
- [ ] Carga inicial <2s con 10000 movimientos.
- [ ] KPIs cuadran contra queries manuales de SQL.
- [ ] Refresco automático cada 60s sin recargar la página.

---

### 1.3 UI de productos bajo mínimo

**Objetivo:** Pantalla `/inventario/bajo-minimo` que liste productos con `stock_actual <= stock_minimo`, ordenados por urgencia (faltante absoluto).

**Archivos a tocar:**
- `app/routes/inventario_ui.py` → ruta `/inventario/bajo-minimo`
- API: **ya existe** `/api/v1/productos/bajo-minimo/` — solo ampliar para incluir consumo promedio.
- `templates/inventario_bajo_minimo.html` (nuevo)
- `static/js/inventario_bajo_minimo.js` (nuevo)

**Endpoint (ampliar):**
```
GET /api/v1/productos/bajo-minimo/
Response:
[
  {
    "id": 1, "codigo": "...", "descripcion": "...", "categoria": "...",
    "stock_actual": 5, "stock_minimo": 20, "faltante": 15,
    "consumo_promedio_30d": 8.3,     // nuevo: SUM(SALIDAs últimos 30d) / 30
    "dias_de_stock_restante": 18      // nuevo: stock_actual / consumo_promedio_30d
  }
]
```

**UI:**
- Tabla: Código · Descripción · Categoría · Stock actual · Mínimo · Faltante · Días restantes · Acciones.
- Color: rojo si días_restantes <7, amarillo <14.
- Filtros por categoría y por urgencia.
- Acciones por fila: "Ver kardex" · "Crear orden de compra" (deshabilitado hasta Fase 3).
- Botón "Exportar a Excel".

**Criterios de aceptación:**
- [ ] Orden por defecto: mayor faltante primero.
- [ ] Cálculo de "días restantes" correcto y maneja división por 0.
- [ ] Refresca al modificar stock desde otra pantalla.

---

### 1.4 Entrega parcial de solicitudes

**Objetivo:** Permitir aprobar y entregar cantidades distintas a las solicitadas, descontando stock automáticamente. Los modelos ya soportan esto (`SolicitudMaterialDetalle.cantidad_aprobada` y `cantidad_entregada` en `models.py:490-491`) pero el flujo actual solo cambia estatus.

**Archivos a tocar:**
- `app/routes/inventario_api.py` → modificar `update_solicitud_estado` y agregar `PATCH /api/v1/solicitudes/<id>/detalles/<det_id>` y `POST /api/v1/solicitudes/<id>/entregar`.
- `static/js/inventario_solicitudes.js` → editar UI de aprobación/entrega.
- `templates/inventario_solicitudes.html` → modal con inputs por línea.

**Nuevos endpoints:**
```
PATCH /api/v1/solicitudes/<id>/detalles/<det_id>
Body: { "cantidad_aprobada": 5 }
→ Solo permitido si estatus = PENDIENTE o APROBADA.

POST /api/v1/solicitudes/<id>/entregar
Body: {
  "entregas": [
    { "detalle_id": 1, "cantidad_entregada": 5 },
    { "detalle_id": 2, "cantidad_entregada": 0 }
  ]
}
→ Crea SALIDAs en MovimientoInventario por cada línea con cantidad>0.
→ Actualiza cantidad_entregada del detalle.
→ Si todas las líneas se entregaron por completo → estatus = ENTREGADA.
→ Si quedan líneas pendientes → estatus = APROBADA (entrega parcial).
→ Usar with_for_update sobre Producto para evitar race conditions (mismo patrón que create_movimiento).
```

**Cambios de BD:** Ninguno (modelos ya tienen los campos).

**Reglas de negocio:**
- `cantidad_aprobada` ≤ `cantidad_solicitada`.
- `cantidad_entregada` ≤ `cantidad_aprobada` y ≤ `stock_actual`.
- Rechazar línea = aprobar 0.
- Una entrega parcial **no cierra** la solicitud; queda en APROBADA hasta entregar el resto o cancelar el saldo.
- Cancelar saldo pendiente: nuevo endpoint `POST /api/v1/solicitudes/<id>/cerrar` que pone estatus=ENTREGADA con saldo no entregado registrado en motivo.

**UI:**
- En el modal de solicitud: cada línea muestra Solicitada / Aprobada (editable) / Entregada / Pendiente.
- Botón "Aprobar todo" auto-llena aprobada=solicitada.
- Botón "Entregar lo aprobado" abre confirmación y dispara `POST /entregar`.
- Banner amarillo si entrega parcial: "Quedan X de Y unidades pendientes".

**Criterios de aceptación:**
- [ ] No se puede entregar más de lo aprobado.
- [ ] No se puede entregar más de lo que hay en stock.
- [ ] Stock se descuenta correctamente en BD (un movimiento SALIDA por línea entregada).
- [ ] Solicitud queda en ENTREGADA solo cuando no quedan pendientes.
- [ ] Test: dos usuarios entregan al mismo tiempo → solo uno tiene éxito.

---

### 1.5 Notificaciones automáticas

**Objetivo:** Disparar notificaciones (`models.py:497 Notificacion`) en eventos clave para que la gente se entere sin recargar.

**Archivos a tocar:**
- `app/routes/inventario_api.py` → agregar llamadas a `crear_notif_*` en los puntos clave.
- `app/utils.py` o `app/models.py` → reutilizar/extender `crear_notif_admins` con variantes (`crear_notif_inventario`, `crear_notif_usuario`).
- `static/js/notificaciones.js` → ya debe existir el cliente; revisar si renderiza estos tipos.

**Eventos a disparar:**

| Evento | Quién recibe | Tipo | Cuándo |
|--------|--------------|------|--------|
| Producto bajó del mínimo | Inventario + admin | `STOCK_BAJO` | Después de una SALIDA si `stock_actual <= stock_minimo` y antes era mayor |
| Nueva solicitud creada | Inventario + admin | `SOLICITUD_NUEVA` | En `create_solicitud` |
| Solicitud aprobada | Solicitante | `SOLICITUD_APROBADA` | En `update_solicitud_estado` cuando pasa a APROBADA |
| Solicitud rechazada | Solicitante | `SOLICITUD_RECHAZADA` | Idem RECHAZADA |
| Solicitud entregada | Solicitante | `SOLICITUD_ENTREGADA` | En endpoint de entrega |

**Helper sugerido:**
```python
def crear_notif_rol(rol, tipo, titulo, mensaje, url=None):
    """Una notif por cada usuario activo con el rol indicado."""
```

**UI:** Reutilizar el centro de notificaciones existente.

**Criterios de aceptación:**
- [ ] Disparar STOCK_BAJO solo al CRUZAR el umbral (no en cada movimiento bajo mínimo, eso ahoga).
- [ ] Idempotencia: si la solicitud cambia 2 veces al mismo estatus no genera 2 notifs.
- [ ] No bloquear la respuesta HTTP si la notif falla (try/except y log).

---

## FASE 2 — IMPORTANTE

### 2.1 Reportes exportables (Excel/PDF)

**Pantallas/reportes:**
1. **Inventario actual** (Excel) — todos los productos activos con stock, mínimo y categoría.
2. **Movimientos por periodo** (Excel) — filtro por fechas, producto, tipo.
3. **Kardex de producto** (Excel + PDF) — reutiliza endpoint 1.1.
4. **Consumo por proyecto** (Excel) — agrupado, con totales.
5. **Solicitudes del periodo** (Excel) — listado plano para conciliar.

**Archivos:**
- `app/routes/inventario_ui.py` → `/inventario/reportes` (selector) y endpoints `/reportes/<nombre>.xlsx`.
- Usar `openpyxl` (ya está, ver `plantilla_materiales`) y `xhtml2pdf` (ya está, ver `solicitud_pdf`).

**Criterios:**
- [ ] Streaming con `send_file` y BytesIO (no escribir a disco).
- [ ] Limitar a 10000 filas por reporte; advertir si se trunca.
- [ ] `@limiter.limit("10/minute")` para evitar abuso.

---

### 2.2 Conteo físico / Toma de inventario

**Objetivo:** Capturar el conteo real de un almacén y generar AJUSTES automáticos por las diferencias.

**Nueva tabla:**
```python
class TomaInventario(db.Model):
    id, almacen_id, fecha_inicio, fecha_cierre,
    usuario_id, estatus  # ABIERTA | CERRADA | CANCELADA

class TomaInventarioDetalle(db.Model):
    id, toma_id, producto_id,
    cantidad_sistema,   # snapshot al iniciar
    cantidad_fisica,    # capturado por el almacenista
    diferencia          # generated: fisica - sistema
```

**Flujo:**
1. Iniciar toma → snapshot del stock_actual de cada producto del almacén.
2. Capturar conteo (manual o por QR/scanner).
3. Cerrar toma → por cada `diferencia != 0` se genera un `MovimientoInventario` tipo AJUSTE.

**Endpoints:**
- `POST /api/v1/tomas/` (iniciar)
- `GET /api/v1/tomas/<id>` (detalle)
- `PATCH /api/v1/tomas/<id>/detalles/<id>` (capturar cantidad)
- `POST /api/v1/tomas/<id>/cerrar` (genera ajustes y cierra)

**Criterios:**
- [ ] Solo una toma ABIERTA por almacén a la vez.
- [ ] Al cerrar, los ajustes citan la toma en `motivo` ("Toma física #N").
- [ ] PDF de acta de toma con firmas (similar a solicitud_pdf).

---

### 2.3 Devoluciones

**Objetivo:** Material entregado que regresa al inventario (sobrante de obra, mal pedido, etc.).

**Cambio mínimo:**
- Reutilizar `MovimientoInventario` tipo ENTRADA con `motivo = "Devolución solicitud #N"`.
- Nuevo endpoint `POST /api/v1/solicitudes/<id>/devolucion` con body `{ "detalles": [...] }`.
- Decrementa `cantidad_entregada` del detalle, incrementa stock_actual del producto.

**UI:** Botón "Registrar devolución" en pantalla de solicitudes ENTREGADAS.

**Criterios:**
- [ ] No se puede devolver más de lo entregado.
- [ ] Auditado en AuditLog y en historial del producto.

---

### 2.4 Auditoría visible

**Objetivo:** Pantalla `/inventario/auditoria` que muestre los `AuditLog` relacionados con inventario (ya se escriben con `_audit()`).

**Archivos:**
- `app/routes/inventario_ui.py` → ruta + endpoint `GET /api/v1/auditoria/inventario`.
- Filtros: usuario, acción (texto), fechas.

**Criterios:**
- [ ] Solo accesible a `admin` y `inventario`.
- [ ] Paginado server-side (no traer 100K filas).

---

### 2.5 Filtros avanzados en catálogo

**Hoy:** Catálogo lista todo. Falta filtrar por categoría, stock bajo, sin movimientos en N días, creado por usuario, etc.

**Cambios:** Solo `static/js/inventario_web.js` + ampliar el query string aceptado por `GET /api/v1/productos/`.

---

## FASE 3 — Compras (opcional si manejan OC)

### 3.1 Catálogo de proveedores

```python
class Proveedor(db.Model):
    id, nombre, rfc, contacto, telefono, email, activo
```
CRUD estándar bajo `/inventario/proveedores`.

### 3.2 Órdenes de compra

```python
class OrdenCompra(db.Model):
    id, proveedor_id, folio, fecha_creacion, fecha_esperada,
    estatus,  # BORRADOR | ENVIADA | RECIBIDA_PARCIAL | RECIBIDA | CANCELADA
    usuario_id

class OrdenCompraDetalle(db.Model):
    id, oc_id, producto_id, cantidad_pedida, cantidad_recibida, costo_unitario
```

**Flujo:**
1. Desde "Bajo mínimo" → "Generar OC" auto-llena con productos seleccionados.
2. Editar y enviar.
3. Al recibir (total o parcial) → genera ENTRADAs automáticas con el `costo_unitario` registrado.

### 3.3 Costeo

- Guardar costo en `MovimientoInventario.costo_unitario` (nueva columna).
- Calcular costo promedio ponderado del producto y guardar en `Producto.costo_promedio` (nueva columna).
- Esto desbloquea el KPI "Valor total de inventario" del dashboard.

---

## FASE 4 — Refactor opcional: Stock por almacén

**Hoy:** `Producto.stock_actual` es un solo número global, aunque `MovimientoInventario` registra almacén origen/destino. Si necesitan saber **cuánto hay en cada bodega**, hace falta:

```python
class StockPorAlmacen(db.Model):
    producto_id, almacen_id, cantidad
    # PK compuesta (producto_id, almacen_id)
```

**Implica:**
- Reescribir el cálculo en `create_movimiento`.
- Migrar el stock actual: arrancar con todo en "Bodega Principal" o repartir según última ubicación conocida.
- Repensar el modelo de TRASPASO (hoy es informativo, ahí movería entre filas).

**Decisión necesaria:** ¿realmente lo necesitan? Si las bodegas son pocas y físicamente cercanas, puede no valer la pena. Confirmar con el negocio antes de meter mano.

---

## Convenciones para todas las fases

- **Permisos:** Reusar `_require_inventario` (lectura) y `_require_inventario_admin` (escritura) de `inventario_api.py`.
- **Auditoría:** Toda escritura llama a `_audit(user, ...)` antes del commit.
- **Validación:** Schemas marshmallow en `inventario_api.py` con `EXCLUDE` de campos extra.
- **Rate limit:** `@limiter.limit("X/minute")` en endpoints sensibles (importar, entregar, reportes).
- **Concurrencia:** `with_for_update(nowait=True)` al modificar stock (patrón ya usado en `create_movimiento`).
- **PDF/Excel:** Streaming con `BytesIO` + `send_file`, nunca escribir a disco.
- **Notificaciones:** Siempre dentro de try/except — no romper el flujo principal si falla.
- **Migraciones:** Cada cambio de modelo va con su migración Alembic.
- **Tests:** Cada endpoint nuevo con su test happy-path + un edge case (auth, validación, concurrencia).

## Orden sugerido de implementación

1. **Kardex** (estándar, sin riesgo, mucho valor).
2. **UI bajo mínimo** (1 día, base para órdenes de compra).
3. **Dashboard** (visible, motiva al equipo a usarlo).
4. **Entrega parcial** (núcleo del flujo diario).
5. **Notificaciones** (cierra el bucle de comunicación).
6. Pausa, demo y feedback antes de seguir con Fase 2.

## Preguntas abiertas para confirmar antes de empezar

- [ ] ¿Manejan **un solo almacén físico** o varios con stock independiente? (define si necesitan Fase 4).
- [ ] ¿Necesitan **costeo** del inventario (valor en pesos)? (define Fase 3).
- [ ] ¿La **entrega parcial** debe permitir cancelar el saldo no entregado o queda abierta para entregar después?
- [ ] ¿Las notificaciones se envían también por **correo**, o solo dentro de la app?
- [ ] ¿El **conteo físico** debe ser por almacén completo o también por estante?
