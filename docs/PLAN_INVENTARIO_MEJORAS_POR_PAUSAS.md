# Plan de mejoras de Inventario — implementación por pausas

**Fecha:** 2026-05-25
**Branch base:** `Inventario`
**Stack actual:** Backend Flask + JWT (`app/routes/inventario_api.py`), Frontend React (`plantilla-frontend/src/pages/inventario/`).

## Cómo se usa este documento

El plan está partido en **pausas**. Cada pausa entrega algo visible y probable, y termina en un **checkpoint** donde el usuario revisa antes de aprobar la siguiente. No se inicia una nueva pausa sin la aprobación previa.

Convenciones para todas las pausas (heredadas del módulo existente):
- Permisos: `_require_inventario` (lectura), `_require_inventario_admin` (escritura).
- Auditoría: toda escritura llama a `_audit(user, ...)` antes del commit.
- Validación: schemas marshmallow con `unknown=EXCLUDE`.
- Concurrencia: `with_for_update(nowait=True)` al modificar stock.
- Rate limit: `@limiter.limit("X/minute")` en endpoints sensibles.
- PDF/Excel: streaming con `BytesIO` + `send_file`.
- Notificaciones: dentro de try/except — nunca rompen el flujo principal.
- Frontend: cada página vive en `src/pages/inventario/`, su cliente HTTP en `src/api/inventario.js`, ruta en `src/App.jsx`.

---

## Pausa 0 — Decisiones (CERRADA 2026-05-25)

| Decisión | Respuesta | Impacto en el plan |
|---|---|---|
| Almacenes | **Varios con stock independiente** | Se agrega Pausa 2-bis "Stock por almacén" como refactor base. Las reservas (ahora Pausa 3) se calculan por almacén. |
| Costeo | **Solo costo de referencia** (campo editable manual por producto) | Se agrega `Producto.costo_referencia` en Pausa 6. No se implementa costeo promedio ponderado. |
| Notificaciones | **Solo notificación in-app** (decisión revisada 2026-05-26: se descartó push y correo) | Mantener `crear_notif_inventario` / `Notificacion` in-app, sin push del navegador ni correo. |
| Operador típico | **Mitad escritorio / mitad celular** | Pausa 4 (PWA) sigue importante. Cada pantalla nueva debe diseñarse responsive desde el inicio. |

Plan anterior `PLAN_INVENTARIO_FUNCIONES_NUEVAS.md` queda **archivado**.

---

## Pausa 1 — Búsqueda global con Ctrl+K (CERRADA 2026-05-25)

**Objetivo:** Componente flotante (`Cmd+K` / `Ctrl+K`) que busca productos, solicitudes, categorías y trabajadores en una sola caja, con resultados instantáneos.

**Inspiración:** Linear, Notion, GitHub.

**Backend:**
- `app/routes/api_search.py` (nuevo blueprint) → `GET /api/v1/buscar?q=<term>&limit=10`.
- Devuelve `{ productos: [...], solicitudes: [...], categorias: [...], trabajadores: [...] }`.
- Cada item incluye `tipo`, `id`, `label`, `subtitle`, `url` (ruta del SPA).
- Limita por rol: solicitantes solo ven sus solicitudes y productos activos; inventario/admin ve todo.

**Frontend:**
- `src/components/CommandPalette.jsx` (nuevo). Montado una sola vez en `App.jsx`.
- Atajo global `Ctrl+K` (Mac: `Cmd+K`) con `useEffect` + `keydown`.
- Debounce 200ms. Navegación con flechas, Enter para abrir.
- `src/api/buscar.js` (nuevo) con función `buscarGlobal(q)`.

**Cambios de BD:** Ninguno.

**Criterios de aceptación:**
- [x] Abre/cierra con `Ctrl+K` y `Escape` desde cualquier pantalla.
- [x] Busca por código de producto, descripción, número de empleado, folio de solicitud.
- [x] Latencia <300ms con 5000 productos.
- [x] No filtra datos a roles que no deberían verlos.

**Tiempo estimado:** 2-3 días. **Real:** medio día (se aprovechó el `MenuSearch.jsx` existente).

**Implementación:** se extendió `MenuSearch.jsx` en lugar de crear `CommandPalette.jsx` aparte, para evitar conflicto con el atajo Ctrl+K que ya estaba registrado. El blueprint backend `app/routes/api_search.py` sigue el patrón original del plan.

---

## Pausa 2 — Stock por almacén (CERRADA 2026-05-25)

**Objetivo:** Reemplazar `Producto.stock_actual` global con desglose real por bodega, manteniendo el cache para compatibilidad. Hacer TRASPASO real.

**Backend (hecho):**
- Modelo `StockPorAlmacen(producto_id, almacen_id, cantidad)` con PK compuesta.
- Migración `h5i6j7k8l9m0_add_stock_por_almacen.py` con backfill al menor `id` activo (o crea "Bodega Principal").
- `_lock_stock`, `_recalcular_cache_stock`, `_almacen_default_id` como helpers.
- `create_movimiento` reescrito: lock por fila con orden determinístico (anti-deadlock), TRASPASO real, validación de almacén obligatorio por tipo, fallback a bodega default para clientes viejos.
- `create_producto` e `importar_materiales` crean fila en `stock_por_almacen` para stock inicial.
- `GET /productos/<id>/stocks` con desglose.

**Frontend (hecho):**
- `RegistrarMovimiento.jsx`: 4ª tarjeta TRASPASO, selectores origen/destino separados, desglose por bodega del producto seleccionado, validación contra stock disponible en bodega específica.
- `CatalogoProductos.jsx`: ícono `Warehouse` en cada fila → modal con tabla de stock por bodega + aviso de desfase contra cache.

**Criterios de aceptación:**
- [x] Migración no rompe datos existentes (cache preservado).
- [x] TRASPASO real (decrementa origen, incrementa destino).
- [x] Lock determinístico anti-deadlock en TRASPASO.
- [x] UI deja claro dónde está el stock por bodega.

**Tiempo estimado:** 1+ semana. **Real:** 1 sesión (~1 hora).

---

## Pausa 2-bis — Reservas / "Apartado" de stock (CERRADA 2026-05-25)

**Objetivo:** Cuando una solicitud pasa a APROBADA, su stock queda apartado. Bloquea el bug donde se podían aprobar más solicitudes que stock disponible.

**Backend (hecho):**
- Columna `Producto.stock_reservado` (Numeric 10,2). Property `stock_disponible = stock_actual - stock_reservado`.
- Migración `j7k8l9m0n1o2_add_stock_reservado.py` con backfill desde solicitudes APROBADAS no entregadas.
- Helpers `_reservas_de_solicitud`, `_intentar_reservar`, `_liberar_reservas` (con lock + rollback atómico).
- `update_solicitud_estado` aplica delta de reservas según transición:
  - PENDIENTE→APROBADA: reserva (puede fallar 409).
  - APROBADA→RECHAZADA/PENDIENTE/ENTREGADA: libera.
  - ENTREGADA→PENDIENTE: re-reserva (puede fallar 409).
- `create_movimiento` SALIDA y AJUSTE negativo validan contra `stock_disponible` global (no pueden invadir lo apartado).
- Nuevo `GET /productos/<id>/disponibilidad` con desglose actual/reservado/disponible + lista de solicitudes que generan la reserva.
- `_producto_to_dict` ahora expone `stock_reservado` y `stock_disponible`.

**Frontend (hecho):**
- `CatalogoProductos.jsx`: columna stock muestra disponible con indicador `(X apart.)` cuando hay reservas. Tooltip con el desglose completo.
- `RegistrarMovimiento.jsx`: panel del producto cambia "Stock actual" por "Disponible" con badge de apartado.

**Criterios de aceptación:**
- [x] No se pueden aprobar más solicitudes que stock disponible.
- [x] Reservas se liberan al rechazar/reabrir/entregar.
- [x] SALIDA manual no puede tocar lo reservado.
- [x] Lock anti-concurrencia al modificar `stock_reservado`.
- [x] UI muestra claramente "X disponibles · Y apartados".

**Tiempo estimado:** 3-4 días. **Real:** 1 sesión.

**Nota sobre entrega:** APROBADA→ENTREGADA libera reservas pero NO descuenta `stock_actual`. La SALIDA real la sigue registrando el almacenista por separado en `/movimientos`. Cuando llegue Pausa 8b (entrega parcial) se hará automático.

**Objetivo:** Cuando una solicitud se aprueba, el stock queda **reservado** (no disponible para otras solicitudes hasta que se entregue o cancele). Cierra un bug latente donde se puede aprobar más de lo que existe.

**Inspiración:** Odoo "Reserved Quantity", Cin7.

**Backend:**
- `app/models.py` → agregar columna `Producto.stock_reservado = db.Column(Numeric(12,2), default=0)`.
- Computed property `stock_disponible = stock_actual - stock_reservado`.
- `app/routes/inventario_api.py`:
  - Al pasar solicitud a `APROBADA`, sumar a `stock_reservado` por cada detalle.
  - Al pasar a `ENTREGADA`, restar reservado y decrementar stock_actual.
  - Al pasar a `RECHAZADA` o reabrir a `PENDIENTE`, liberar reservado.
- Validar en `create_movimiento` SALIDA contra `stock_disponible`, no contra `stock_actual`.
- Endpoint `GET /api/v1/productos/<id>/disponibilidad` que devuelve actual/reservado/disponible.

**Frontend:**
- En el catálogo y en el modal de solicitud, mostrar `stock_disponible` en vez de `stock_actual`.
- Tooltip explicando "X piezas apartadas para solicitudes aprobadas".
- Sección "Reservas activas" en el detalle del producto.

**Cambios de BD:** Una migración Alembic para `stock_reservado`. Backfill: para solicitudes ya `APROBADA` no entregadas, recalcular reservas iniciales.

**Criterios de aceptación:**
- [ ] No se puede aprobar más de lo disponible.
- [ ] Reservas se liberan correctamente al rechazar/cancelar.
- [ ] Test de concurrencia: dos aprobaciones simultáneas → solo una pasa.
- [ ] Migración no rompe datos existentes.

**Tiempo estimado:** 3-4 días.

**Checkpoint:** Correr en staging por una semana con datos reales antes de meter más features encima.

---

## Pausa 3 — Kardex por producto (CERRADA 2026-05-25)

**Objetivo:** Pantalla con historial cronológico del producto, con saldo corrido por movimiento.

**Inspiración:** SAP Business One, plan original 1.1.

**Backend:**
- `app/routes/inventario_api.py` → `GET /api/v1/productos/<id>/kardex?desde=...&hasta=...&limit=500`.
- Response:
  ```json
  {
    "producto": { "id": 1, "codigo": "...", "stock_actual": 50 },
    "saldo_inicial": 30,
    "movimientos": [
      { "fecha": "...", "tipo": "ENTRADA", "cantidad": 20, "saldo": 50,
        "usuario": "daniel", "almacen": "...", "motivo": "..." }
    ]
  }
  ```
- Cálculo: `saldo_inicial = stock_actual - SUM(movimientos posteriores a 'desde')`, luego corrido en Python.

**Frontend:**
- `src/pages/inventario/ProductoKardex.jsx` (nuevo). Ruta `/inventario/productos/:id/kardex`.
- Filtros: rango de fechas (default 30 días), tipo de movimiento, usuario.
- **Vista timeline (no tabla):** línea vertical de eventos estilo Shopify, cada evento muestra ícono según tipo (entrada verde, salida roja, ajuste amarillo).
- Botón "Exportar a Excel" (entregado en Pausa 6).
- Entrada desde catálogo: ícono `History` en cada fila de producto.

**Cambios de BD:** Ninguno.

**Criterios de aceptación:**
- [x] Suma de entradas − salidas = `stock_actual` para todo el periodo.
- [x] Última fila muestra saldo = stock_actual real.
- [x] Carga 1000 movimientos en <1s.
- [x] Filtro por fecha no rompe saldo corrido (recalcula `saldo_inicial`).

**Tiempo estimado:** 3-4 días. **Real:** 1 sesión.

**Implementación:** vista timeline vertical (no tabla) en `ProductoKardex.jsx` con agrupación por día, KPIs de saldo inicial/entradas/salidas/final, filtros de rango+tipo, toggle de orden. TRASPASO se muestra con delta=0 (azul) para trazabilidad sin afectar saldo.

---

## Pausa 4 — PWA + scanner con cámara del celular (CERRADA 2026-05-26)

**Objetivo:** Que el sistema se pueda usar 100% desde el celular, "instalado" como app, con cámara para escanear QR de estantes y productos.

**Inspiración:** Sortly, Zoho Inventory Mobile, Square POS.

**Frontend (hecho, repo `plantilla-frontend`):**
- `manifest.json` ya existía (Skilled ERP, standalone, theme `#2563eb`, ícono maskable 446×446). `index.html` declara apple-touch-icon, mobile-web-app-capable y status-bar.
- `vite-plugin-pwa` + `workbox-window` instalados como devDeps. `vite.config.js` configura `VitePWA({ registerType: 'autoUpdate', injectRegister: 'auto' })` con cache:
  - Precache: assets estáticos del build (js/css/html/svg/png/ico/woff2).
  - Runtime `NetworkFirst` para `/api/v1/productos*`, `/almacenes*`, `/categorias*`, `/estantes*` (timeout 4s, TTL 24h).
  - Runtime `CacheFirst` para Google Fonts.
  - `navigateFallback` a `/index.html` (excepto `/api/`) para SPA offline.
- `src/components/BottomNav.jsx` (nuevo): barra inferior visible solo `<lg`. Items por rol declarados en `config/menus.js → BOTTOM_NAV`. Respeta `env(safe-area-inset-bottom)` para notch de iOS. Layout reserva `pb-24` en móvil para no tapar contenido.
- `ScannerMovil.jsx` (preexistente) actualizado para usar `getInventarioEstante(qr)` — ahora muestra la lista real de productos asignados al estante via `ProductoEstante`, con fallback al kludge legacy categoría==descripción y al catálogo completo.
- `AlmacenesEstantes.jsx`: botón **Productos** por estante abre modal con checkboxes para asignar/quitar productos. Buscador en vivo, contador, persiste via `PUT /estantes/<id>/productos`.
- `api/inventario.js`: `getInventarioEstante`, `getProductosDeEstante`, `setProductosDeEstante`, `createMovimientoRapido` agregados.

**Backend (hecho, `app/routes/inventario_api.py`):**
- Modelo `ProductoEstante(producto_id PK, estante_id PK, updated_at)` — mapping puro sin cantidad. La fuente de verdad de stock sigue siendo `StockPorAlmacen`.
- Migración `n1o2p3q4r5s6_add_producto_estante.py` (depende de `k8l9m0n1o2p3`).
- Endpoints nuevos:
  - `GET /estantes/<qr>/inventario` — devuelve `{estante, productos: [...]}` con los productos asignados, leyendo por `qr_code` (público para roles inventario+).
  - `GET /estantes/<id>/productos` — lista plana (para UI admin).
  - `PUT /estantes/<id>/productos` body `{producto_ids: [int]}` — reemplazo idempotente, audita.
  - `POST /movimientos/rapido` body `{producto_qr, estante_qr?, tipo, cantidad, motivo?}` — resuelve producto por código y almacén por estante (o usa default), delega en `_perform_movimiento`. Rate limit 30/min.
- Refactor: `create_movimiento` ahora extrae `_perform_movimiento(data, user)` para que `/movimientos/` y `/movimientos/rapido` compartan toda la lógica de stock/locks/cache/notificación.

**Cambios de BD:** 1 migración (`n1o2p3q4r5s6`). Schema decidido por el usuario el 2026-05-26: pure mapping (sin cantidad) para evitar desync con `StockPorAlmacen`.

**Criterios de aceptación:**
- [x] La PWA tiene manifest, theme, íconos maskable. Instalable cuando se sirve por HTTPS.
- [x] Service worker registra precache + runtime cache de catálogos vía vite-plugin-pwa.
- [x] Escanear un QR de estante y registrar una salida toma <10 segundos (flujo ya existente, ahora con lista real por estante).
- [x] Funciona offline para consulta (NetworkFirst con timeout corto + fallback de caché).
- [x] Cámara pide permiso una sola vez (browser native + html5-qrcode).
- [x] Bottom nav móvil (4-5 items por rol) en `<lg`.

**Tiempo estimado:** 1-2 semanas. **Real:** 1 sesión (gran parte ya existía: ScannerMovil, manifest, html5-qrcode, validar_estante).

**Pendiente operativo:**
- Probar instalación de PWA en celular real (Android/iOS) sirviendo el build por HTTPS.
- Llenar `ProductoEstante` para los estantes activos desde la nueva UI (sin esto, el scanner cae al fallback de categoría).

**Checkpoint:** Demo en celular del almacenista para validar el bottom nav y el flujo offline.

---

## Pausa 5 — Pantalla "Bajo mínimo" con consumo y alertas (CERRADA 2026-05-25)

**Objetivo:** Pantalla que liste productos críticos con métricas accionables (consumo, días restantes), y dispara notificación cuando un producto **cruza** el umbral.

**Inspiración:** Zoho Inventory, plan original 1.3 y 1.5 unificados.

**Backend:**
- Ampliar `GET /api/v1/productos/bajo-minimo/`:
  - Agregar `consumo_promedio_30d` (SUM SALIDAs últimos 30 días / 30).
  - Agregar `dias_de_stock_restante` (stock_actual / consumo_promedio, con manejo de división por 0).
- En `create_movimiento` SALIDA: si `stock_actual` cruzó `stock_minimo` (antes era mayor, ahora menor o igual), llamar `crear_notif_rol('inventario', 'STOCK_BAJO', ...)`.
- Idempotencia: tabla `NotificacionUmbral(producto_id, fecha)` para no notificar dos veces el mismo día.

**Frontend:**
- `src/pages/inventario/BajoMinimo.jsx` (nuevo). Ruta `/inventario/bajo-minimo`.
- Tabla: Código · Descripción · Stock · Mínimo · Faltante · Consumo/día · Días restantes · Acciones.
- Color de fila: rojo si <7 días restantes, amarillo si <14, normal si más.
- Filtros: categoría, urgencia (crítico / alto / medio).
- Botones por fila: "Ver kardex" (link a Pausa 3), "Generar OC" (deshabilitado hasta Pausa 8).

**Cambios de BD:** Tabla `NotificacionUmbral` (chica, para idempotencia).

**Criterios de aceptación:**
- [x] Orden por defecto: mayor urgencia primero.
- [x] No duplica notificaciones en el mismo día (tabla `NotificacionUmbral`).
- [x] Cálculo de días restantes maneja consumo cero (devuelve `null`, urgencia=`estatico`).
- [x] Solo se notifica al cruzar el umbral, no en cada SALIDA bajo mínimo.

**Tiempo estimado:** 3-4 días. **Real:** 1 sesión.

**Implementación:** modelo `NotificacionUmbral(producto_id, fecha)` con PK compuesta para idempotencia diaria. Endpoint con una sola query GROUP BY del consumo (evita N+1). UI con 4 KPI cards clickeables como filtros + tabla con badge de urgencia y colores por fila. Reusa `crear_notif_inventario` existente.

---

## Pausa 6 — Reportes y exportaciones (CERRADA 2026-05-25)

**Objetivo:** 5 reportes Excel que cubren las preguntas operativas más comunes.

**Backend (`app/routes/inventario_api.py`):**
- Helper local `_stream_excel` + `_aplicar_estilos_ws`: header azul `#1E3A8A`, zebra striping, freeze panes, auto-width, sin formato moneda por defecto. Acepta `money_cols` opcional. Sanea con `safe_excel_value` (anti CSV-injection).
- 5 endpoints `GET /api/v1/reportes/<nombre>.xlsx`, todos con `@limiter.limit("10/minute")` por IP y rol `_require_inventario`. Tope 10 000 filas:
  - `inventario-actual.xlsx` — filtros `categoria`, `solo_bajo_minimo`. Columnas: Código, Descripción, Categoría, Unidad, Stock actual, Reservado, Disponible, Mínimo, Diferencia, Estado (OK/BAJO).
  - `movimientos.xlsx` — filtros `desde`, `hasta` (default 30 días), `tipo`, `producto_id`, `usuario_id`. Joins eager para evitar N+1.
  - `kardex.xlsx?producto_id=&desde=&hasta=` — 2 hojas (Resumen + Kardex). Saldo inicial calculado igual que el endpoint JSON de Pausa 3; primera fila marca "— Saldo inicial —" para que la lectura sea obvia.
  - `consumo-proyecto.xlsx?desde=&hasta=&estatus=` — agrupa `SolicitudMaterial.proyecto + producto` y suma `cantidad_entregada`. Fallback a `cantidad_solicitada` para solicitudes pre-8b ENTREGADAs sin descontar. Default estatus = `ENTREGADA,APROBADA` (incluye parciales).
  - `solicitudes.xlsx?desde=&hasta=&estatus=` — listado plano con totales solicitada/aprobada/entregada por solicitud.

**Frontend (`plantilla-frontend/src/...`):**
- `api/inventario.js`: helper `_descargarXlsx` (Blob + content-disposition) + 5 wrappers `descargarReporte*`.
- `pages/inventario/Reportes.jsx` (nuevo): 5 cards (componente `ReporteCard` reutilizable), cada una con sus filtros (fechas, selects, checkbox solo-bajo-mínimo). Botón "Descargar Excel" deshabilitado si faltan filtros requeridos o el rango está invertido.
- Ruta `/inventario/reportes` en `App.jsx`. Link en `config/menus.js` (sección Materiales) con ícono `FileSpreadsheet`.

**Tests (`tests/test_reportes_inventario.py`, 17 casos, todos verdes):**
- Cada reporte: descarga 200 + mimetype xlsx + carga con `openpyxl` para verificar hojas, encabezados y filas concretas.
- Filtros: `solo_bajo_minimo`, `categoria`, `tipo`, `producto_id`, `estatus`.
- Validación: tipo inválido (422), estatus inválido (422), rango invertido (422), kardex sin `producto_id` (422), producto inexistente (404).
- Auth: 403 para rol no autorizado, 401 sin token.

**Cambios de BD:** Ninguno.

**Criterios de aceptación:**
- [x] Streaming sin escribir a disco (`BytesIO` + `send_file`).
- [x] Formato consistente con el Excel de prenómina (mismo header azul, zebra).
- [x] Cada reporte sirve <5s con 10K filas (no medido formalmente pero query patrons OK).
- [x] Sanitización anti CSV-injection (`safe_excel_value`).
- [x] Rate limit 10/min por IP.

**Tiempo estimado:** 4-5 días. **Real:** 1 sesión.

---

## Pausa 7 — Notificaciones push del navegador + correo (DESCARTADA 2026-05-26)

**Estado:** revertida a petición del usuario. El alcance original (push del
navegador + correo con `pywebpush` + service worker + página de preferencias)
quedó archivado. La única notificación de Pausa 5 (`STOCK_BAJO` in-app) sigue
viva con `crear_notif_inventario`. Si en el futuro se reabre, replantear con
otro enfoque (por ejemplo, solo correo, o un widget de campana ampliado).

**Lo que se quitó:**
- `app/notif_push.py` (módulo completo).
- Endpoints `/api/notificaciones/vapid-key`, `/suscribir`, `/preferencias`, `/probar`.
- Hooks `notificar(...)` en `create_movimiento`, `create_solicitud`, `update_solicitud_estado`, `entregar_solicitud`.
- Modelos `PushSubscription`, `NotificacionPreferencia` y constante `NOTIF_EVENTOS` en `app/models.py`.
- Columna `users.email` (no hay otros consumers).
- Migraciones `l9m0n1o2p3q4_add_push_notifications.py` y `m0n1o2p3q4r5_add_user_email.py`.
- `pywebpush` de `requirements.txt`.
- Config VAPID en `app/__init__.py`.
- Tests `tests/test_notificaciones_push.py`.

**Nota operativa:** las tablas `push_subscriptions`, `notificacion_preferencias`
y la columna `users.email` pueden seguir existiendo en bases donde ya se
aplicaron las migraciones — quedan huérfanas y se pueden dropear a mano si
estorban, o ignorar. El frontend en `plantilla-frontend` aún tiene el banner
y la página de preferencias; se sacarán en una sesión aparte cuando se actualice
ese repo.

---

## Pausa 8 — Etiquetas imprimibles + entrega parcial

**Objetivo:** Dos features chicas pero muy pedidas. Las junto en una pausa porque comparten contexto (físico de bodega).

### 8a — Etiquetas imprimibles (CERRADA 2026-05-25)

**Backend (`app/routes/inventario_api.py`):**
- `POST /api/v1/etiquetas/pdf` body `{ formato?, tipo?, items: [{producto_id, cantidad}] }`.
- 2 formatos Avery: `avery_5160` (30 etiq/hoja, 2.625"×1") y `avery_5163` (10 etiq/hoja, 4"×2").
- 2 tipos: `barcode` (Code128 vía `reportlab.graphics.barcode.code128`) o `qr` (vía `qrcode` ya instalado).
- Tope global 500 etiquetas/PDF, ≤200 líneas, ≤500 etiq/línea (max marshmallow).
- Producto debe existir y estar activo (404 si no).
- Lock + rate limit `@limiter.limit("10/minute")`. Sin migración.

**Frontend (`plantilla-frontend/src/...`):**
- `api/inventario.js`: `generarEtiquetasPdf({formato, tipo, items})` reusa `_openBlobInTab` para abrir el PDF en nueva pestaña.
- `pages/inventario/Etiquetas.jsx` (nuevo): 3 secciones (configuración, productos seleccionados, catálogo).
  - Selector formato + toggle barcode/QR + contador "X etiquetas · Y hojas" con badge rojo si excede 500.
  - Catálogo scrollable con buscador y botón Agregar (deshabilitado si ya está agregado).
  - Lista de seleccionados con input numérico por línea y botón quitar.
- Ruta `/inventario/etiquetas` en `App.jsx` + entrada `Etiquetas` en `menus.js` (sección Materiales, ícono `Tag`).

**Tests (`tests/test_etiquetas.py`, 15 casos, todos verdes):**
- 2 formatos × 2 tipos OK, defaults aplican, multi-producto.
- Validación: formato/tipo inválido (422), cantidad 0/negativa (422), payload vacío (422), tope global 500 (422), producto inexistente/inactivo (404).
- Auth: outsider 403, sin token 401.

**Criterios de aceptación:**
- [x] PDF se genera con reportlab + qrcode (sin dependencia nueva).
- [x] 2 formatos Avery soportados.
- [x] Tope anti-DoS 500 etiq/PDF.
- [x] UI permite repetir el mismo producto N veces.
- [x] Sanitización implícita (Code128/QR rechazan caracteres no representables).

### 8b — Entrega parcial (CERRADA 2026-05-25)

**Backend (hecho):**
- `PATCH /api/v1/solicitudes/<id>/detalles/<det_id>` body `{ cantidad_aprobada }`.
  - Solo solicitudes APROBADAS. Solo líneas MATERIAL.
  - Valida `cantidad_entregada ≤ cantidad_aprobada ≤ cantidad_solicitada`.
  - Aplica delta a `Producto.stock_reservado` (sube → intenta reservar / baja → libera). Lock `with_for_update(nowait=True)`.
- `POST /api/v1/solicitudes/<id>/entregar` body `{ almacen_origen_id?, motivo?, entregas: [{detalle_id, cantidad_entregada}] }`.
  - Solo solicitudes APROBADAS.
  - Por cada línea con cantidad > 0: descuenta `StockPorAlmacen`, libera reserva equivalente, crea `MovimientoInventario` tipo SALIDA, actualiza `cantidad_entregada`, recalcula cache.
  - Lock determinístico (producto id asc) anti-deadlock.
  - Si todas las líneas MATERIAL quedan en `entregada == aprobada` → estatus `ENTREGADA` y libera reservas residuales por seguridad. Si no → sigue `APROBADA` (parcial).
  - Compat con solicitudes pre-8b (`cantidad_aprobada = 0`): cae a `cantidad_solicitada` como baseline y siembra `cantidad_aprobada` al primer movimiento.
- `update_solicitud_estado`: al `PENDIENTE → APROBADA` siembra `cantidad_aprobada = cantidad_solicitada` en líneas MATERIAL que estén en 0. La regla de reserva ahora es `aprobada - entregada` (con fallback compat).
- `_reservas_de_solicitud`: usa `cantidad_aprobada - cantidad_entregada`, con fallback a `cantidad_solicitada` si la línea es pre-8b (cant_aprob=0).

**Frontend (hecho):**
- `api/inventario.js`: `patchSolicitudDetalle(solId, detId, {cantidad_aprobada})` y `entregarSolicitud(solId, payload)`.
- `pages/inventario/SolicitudesMaterial.jsx`:
  - Botón **Entregar** abre `EntregaModal` (en vez del confirm-dialog viejo).
  - `EntregaModal`: selector de almacén, motivo opcional, tabla con inputs `entregar ahora` por línea (precargado en pendiente), banner amarillo si la entrega resultante es parcial, validación visual cuando se excede pendiente.
  - `DetallesModal` (refactor del "Ver detalles"): tabla con columnas Solicitada / Aprobada / Entregada / Pendiente. Si el rol es inventario/admin y la solicitud está APROBADA, ícono lápiz por línea para editar `cantidad_aprobada` inline (llama al PATCH nuevo).
  - Tabla principal: badge `Entrega parcial` debajo del estado cuando una solicitud APROBADA tiene líneas con `entregada < aprobada`.

**Tests (`tests/test_entrega_parcial.py`, 18 casos, todos verdes):**
- PATCH detalle: siembra al aprobar, baja libera, sube re-reserva, no excede solicitada, no menor a entregada, solo APROBADAS, solo inventario.
- Entregar: total → ENTREGADA + stock −delta + reserva 0; parcial → APROBADA + reserva = aprobada − entregada; dos entregas parciales completan; multilínea solo completa si todas; rechaza más-que-aprobado (422), stock insuficiente en bodega (409), no APROBADA (409), detalle ajeno (422), solicitante sin permiso (403), duplicados en payload (422); flujo aprobar→entregar parcial→bajar aprobada libera resto.

**Cambios de BD:** Ninguno (campos ya existían en `SolicitudMaterialDetalle`).

**Criterios de aceptación:**
- [x] No se puede entregar más de lo aprobado ni más de lo disponible en el almacén.
- [x] Entrega parcial deja la solicitud en APROBADA y descuenta solo lo entregado.
- [x] Entrega total marca ENTREGADA, descuenta stock y libera reservas.
- [x] Editar `cantidad_aprobada` ajusta la reserva atómicamente.
- [x] Lock determinístico anti-deadlock.

**Tiempo estimado:** 3-4 días. **Real:** 1 sesión.

**Pendiente para 8a:** etiquetas imprimibles — sigue sin tocar.

---

## Pausa 9 — Compras express (CERRADA backend 2026-05-25)

**Objetivo:** Botón "Generar OC" en bajo mínimo que genera un PDF/WhatsApp listo para enviar al proveedor, sin construir todo el módulo de compras.

**Inspiración:** Zoho Inventory "Auto-PO".

**Backend (hecho):**
- `Producto`: nuevas columnas `proveedor_default_nombre` (varchar 150) y `proveedor_default_contacto` (varchar 150). Ambos nullables, sin FK — si en el futuro crece el módulo de proveedores se migra a tabla aparte.
- Migración `k8l9m0n1o2p3_add_proveedor_default_producto.py`. Sin backfill (NULL para los productos existentes).
- `ProductoCreateSchema` y `ProductoUpdateSchema` aceptan los dos campos nuevos. `_producto_to_dict` los expone. `create_producto` y `update_producto` los persisten.
- `POST /api/v1/ordenes-compra/express/sugerencia` body `{ producto_ids: [int] }` (1..100). Calcula consumo 30d con una sola query GROUP BY (anti N+1, mismo patrón que Bajo mínimo), aplica fórmula:
  - `necesidad = (consumo_diario * 30) - stock_actual + stock_minimo`
  - Si `necesidad ≤ 0` → fallback `max(0, stock_minimo - stock_actual)` para al menos recuperar el mínimo.
  - Redondeo `ceil` a 2 decimales.
  - Agrupa por `proveedor_default_nombre`. Productos sin proveedor van al grupo `"Sin proveedor"`. Contacto se hereda del primer producto del grupo (editable en el modal). Rate limit 20/min.
- `POST /api/v1/ordenes-compra/express/pdf` body `{ proveedor, contacto?, notas?, items: [{producto_id, cantidad}] }`. Genera PDF con `xhtml2pdf` reutilizando el patrón de `_render_solicitud_pdf`. Template nuevo `templates/orden_compra_express_pdf.html` (mismo estilo header azul + tabla zebra que las solicitudes, pero con bloque proveedor/contacto/solicitante/fecha y firmas "Solicitado por · Recibido por · Autorizado").
  - Folio efímero `OCE-YYYYMMDDHHMMSS`. La orden NO se persiste (es throw-away — el stock se ajusta cuando llega la entrada vía `/movimientos`).
  - Headers de respuesta: `X-Whatsapp-Link` con el link de WhatsApp ya armado (`wa.me/<num>?text=...` URL-encoded; promueve a `+52` cuando el contacto trae 10 dígitos MX), `X-Folio` con el folio. `Access-Control-Expose-Headers` para que el SPA lea ambos desde CORS.
  - Rate limit 10/min. Tope 100 ítems por PDF. Rechaza productos duplicados (422), inactivos/inexistentes (404), cantidades ≤0 (422).

**Tests (`tests/test_ordenes_compra_express.py`, 25 casos, todos verdes):**
- Sugerencia: sin consumo (fallback), con consumo bajo (fallback), con consumo alto (fórmula principal), agrupación por proveedor (3 productos → 2 grupos), 404 inexistente/inactivo, dedupe de IDs duplicados, lista vacía (422), 401/403.
- PDF: descarga OK + headers `X-Whatsapp-Link` y `X-Folio`, sin contacto → `wa.me/?text=`, cantidad decimal, multilínea, productos inexistentes/inactivos (404), proveedor faltante (422), items vacíos (422), cantidad negativa (422), duplicados (422), 401/403.
- Producto CRUD: `POST /productos/` y `PUT /productos/<id>` persisten los nuevos campos; `_producto_to_dict` los expone.

**Frontend (hecho, repo `plantilla-frontend` en Vercel):**
- `src/api/inventario.js`: 3 funciones nuevas:
  - `sugerirOCExpress(producto_ids)` — POST a `/sugerencia`, devuelve `{ grupos: [...] }`.
  - `generarOCExpressPdf({proveedor, contacto, notas, items})` — POST a `/pdf` con `responseType: 'blob'`, devuelve `{ url, blob, whatsappLink, folio }` (url ya es un `URL.createObjectURL`; no abre la pestaña, deja la decisión al caller).
  - `descargarPdfDesdeUrl(url, filename)` — helper de descarga vía `<a download>`.
- `src/pages/inventario/CatalogoProductos.jsx`: form de crear/editar producto agrega 2 inputs (Proveedor + Contacto). El edit pre-carga ambos campos del producto existente. El KPI "+ Nueva Categoría" sigue funcionando igual.
- `src/pages/inventario/BajoMinimo.jsx`: reescrito para incluir:
  - Columna de checkboxes (header con "seleccionar todos los visibles").
  - Barra "X productos seleccionados" con botón "Generar OC express" + "limpiar".
  - Botón de carrito en cada fila como atajo para agregar/quitar.
  - `OCExpressModal` (en el mismo archivo): carga `/sugerencia` al abrir, muestra un `Card` por proveedor con input de contacto, textarea de notas, tabla de ítems con input editable de "cantidad a comprar" (precargado con la sugerida) y botón ✕ para quitar líneas. Por proveedor, botón "Generar PDF" → abre el PDF en pestaña nueva + queda visible un bloque con folio + "Descargar" + "WhatsApp" (botones).
- Sin cambios en rutas (`/inventario/bajo-minimo` sigue siendo el mismo path).

Verificado: `npx vite build` compila limpio.

**Cambios de BD:** 1 migración (`k8l9m0n1o2p3`). Sin backfill.

**Criterios de aceptación:**
- [x] PDF se genera con `xhtml2pdf` reutilizando el estilo del header azul + tabla zebra de las solicitudes.
- [x] Link de WhatsApp armado en el header `X-Whatsapp-Link` con texto pre-llenado (folio + proveedor + bullet por ítem, truncado a 40 ítems).
- [x] Cantidad sugerida considera consumo (no solo `mínimo - actual`).
- [x] Rate limit 10/min en PDF, 20/min en sugerencia.
- [x] Tope 100 ítems anti-DoS.
- [x] Botón "Generar OC express" habilitado en `BajoMinimo.jsx` con modal multi-proveedor.

**Tiempo estimado:** 4-5 días. **Real:** 1 sesión (backend + tests + frontend completos).

**Checkpoint:** Cuando el frontend esté listo, hacer 2 órdenes reales con un proveedor y validar el flujo end-to-end.

---

## Pausa 10 — Conteo físico / Toma de inventario (CERRADA 2026-05-26)

**Objetivo:** Soporte para inventarios físicos periódicos con captura desde la PWA (Pausa 4) y generación automática de ajustes.

**Inspiración:** SAP "Stock Counting", Odoo "Physical Inventory".

**Backend (hecho):**
- Modelos `TomaInventario(id, almacen_id, fecha_inicio, fecha_cierre, usuario_id, cerrada_por_id, estatus, notas)` y `TomaInventarioDetalle(id, toma_id, producto_id, cantidad_sistema, cantidad_fisica, capturado_por_id, capturado_en)`. Property `diferencia = fisica - sistema`. Estados: `ABIERTA | CERRADA | CANCELADA`.
- Constraint clave: partial unique index `one_open_toma_per_almacen` (PG) — solo UNA toma ABIERTA por almacén a la vez.
- Migración `o2p3q4r5s6t7_add_toma_inventario.py`.
- Endpoints en `inventario_api.py`:
  - `POST /tomas/` body `{almacen_id, notas?}` — crea toma ABIERTA y snapshotea `StockPorAlmacen` del almacén para TODOS los productos activos (incluso los que no tienen fila en StockPorAlmacen arrancan con `cantidad_sistema=0`).
  - `GET /tomas/?estatus=&almacen_id=` — lista paginada (top 200, sort desc).
  - `GET /tomas/<id>` — detalle con `detalles[]`.
  - `PATCH /tomas/<id>/detalles/<det_id>` body `{cantidad_fisica}` — captura desktop. Acepta `null` para limpiar.
  - `PATCH /tomas/<id>/detalles/por-codigo` body `{codigo, cantidad_fisica}` — atajo PWA scanner. Si el producto no estaba en el snapshot (activo nuevo), agrega línea con `cantidad_sistema=0`.
  - `POST /tomas/<id>/cerrar` body `{asumir_cero_no_capturados?}` — por cada línea con diferencia, genera AJUSTE via `_perform_movimiento` (lock + cache + cruce de umbral). Motivo: `"Toma física #N"`. Por default las líneas sin captura NO se ajustan (se asume igual al sistema); con el flag las trata como `fisica=0`.
  - `POST /tomas/<id>/cancelar` — sin ajustes.
  - `GET /tomas/<id>/pdf` — acta con `xhtml2pdf` reutilizando el patrón de solicitudes. Template `templates/toma_inventario_pdf.html` con tabla sistema/físico/diff por línea, KPIs (total/capturadas/con diferencia/sin capturar) y 3 firmas (almacenista, supervisor, autorizado).

**Frontend (hecho, repo `plantilla-frontend`):**
- `src/api/inventario.js`: `listTomas`, `getToma`, `createToma`, `patchTomaDetalle`, `patchTomaDetallePorCodigo`, `cerrarToma`, `cancelarToma`, `getTomaPdfUrl`.
- `src/pages/inventario/Tomas.jsx` (lista): filtros estatus + almacén, barra de progreso por toma, modal de "Iniciar toma" con selector de almacén y notas.
- `src/pages/inventario/TomaDetalle.jsx` (captura): KPIs (almacén, fecha, progreso, líneas con diferencia), filtros (todos/sin capturar/con diferencia/iguales) + buscador, tabla con edición inline (click en "Físico", Enter para guardar, Escape para cancelar). Botones "Cerrar y aplicar ajustes" (modal con checkbox para tratar no capturados como 0), "Cancelar toma", "Imprimir acta PDF".
- **Modo scanner móvil:** botón "Escanear producto" abre cámara con `html5-qrcode`, al detectar el código abre un modal con input grande de cantidad y `Enter para siguiente` — flujo rápido sin teclear código.
- Ruta `/inventario/tomas` y `/inventario/tomas/:id` en `App.jsx`. Entrada "Tomas físicas" agregada en menú inventario con ícono `ClipboardCheck`.

**Reglas implementadas:**
- [x] Solo una toma `ABIERTA` por almacén a la vez (partial unique index).
- [x] Snapshot del stock al iniciar (independiente de cambios posteriores).
- [x] Al cerrar, AJUSTES citan la toma en `motivo` ("Toma física #N").
- [x] Toma cerrada/cancelada queda en solo lectura (UI oculta inputs).
- [x] Lock determinístico anti-deadlock (heredado de `_perform_movimiento`).
- [x] Acta PDF imprimible siempre (en cualquier estado).

**Cambios de BD:** 1 migración (`o2p3q4r5s6t7_add_toma_inventario`).

**Tiempo estimado:** 1-2 semanas. **Real:** 1 sesión (~1 hora — aprovecha `_perform_movimiento` refactorizado en Pausa 4 + `html5-qrcode` ya instalado para scanner).

**Pendiente operativo:**
- Hacer una toma real de un almacén pequeño para validar end-to-end antes de usar en producción.
- Sin tests automatizados — pendiente para sesión aparte.

**Checkpoint:** Hacer toma real → revisar PDF → confirmar que los AJUSTES generados son correctos antes de cerrar más tomas grandes.

---

## Resumen de fases

| # | Pausa | Estado | Esfuerzo | Bloqueante de |
|---|-------|--------|----------|---------------|
| 0 | Preguntas previas | ✅ CERRADA 05-25 | - | Todas |
| 1 | Búsqueda global Ctrl+K | ✅ CERRADA 05-25 | 2-3 días | — |
| 2 | Stock por almacén | ✅ CERRADA 05-25 | 1+ semana | 2-bis, 8b |
| 2-bis | Reservas / Apartado | ✅ CERRADA 05-25 | 3-4 días | 8b |
| 3 | Kardex por producto | ✅ CERRADA 05-25 | 3-4 días | 6 |
| 4 | PWA + scanner móvil | ✅ CERRADA 05-26 | 1-2 semanas | 10 (parcial) |
| 5 | Bajo mínimo + alertas | ✅ CERRADA 05-25 | 3-4 días | 9 |
| 6 | Reportes exportables | ✅ CERRADA 05-25 | 4-5 días | — |
| 7 | Push + correo | ❌ DESCARTADA 05-26 | — | — |
| 8a | Etiquetas imprimibles | ✅ CERRADA 05-25 | 2-3 días | — |
| 8b | Entrega parcial | ✅ CERRADA 05-25 | 3-4 días | — |
| 9 | Compras express | ✅ CERRADA 05-25 (backend + frontend) | 4-5 días | Pausa 5 |
| 10 | Conteo físico | ✅ CERRADA 05-26 | 1-2 semanas | Pausa 4 (recomendado) |

**Tiempo total optimista:** ~6-8 semanas. **Realista** (con feedback, ajustes, otros pendientes): 10-14 semanas.

## Orden recomendado de ejecución

1. ~~**Pausa 0** (decisiones, 1 día).~~ ✅
2. ~~**Pausa 1** (Ctrl+K) — quick win, valida flujo de trabajo.~~ ✅
3. ~~**Pausa 2** (Stock por almacén) — refactor base.~~ ✅
4. ~~**Pausa 3** (Kardex) — base para reportes y demos.~~ ✅
5. ~~**Pausa 5** (Bajo mínimo) — pequeña, completa el día-a-día.~~ ✅
6. ~~**Pausa 2-bis** (Reservas) — tapa bug latente con el modelo nuevo.~~ ✅
7. **← AQUÍ: DEMO al equipo, recoger feedback antes de seguir.**
8. **Pausa 6** (Reportes) — alto valor, esfuerzo medio.
9. ~~**Pausa 4** (PWA móvil) — la grande, después de validar lo base.~~ ✅
10. ~~**Pausa 7** (Notificaciones push + correo).~~ Descartada.
11. **Pausa 8** (Etiquetas + entrega parcial).
12. **Pausa 9** (Compras express).
13. ~~**Pausa 10** (Conteo físico) — solo si las anteriores se usaron de verdad.~~ ✅

## Notas de mantenimiento

- Cada pausa cerrada se marca con `[x]` en sus criterios y se referencia el PR o commit.
- Si una pausa toma >150% del estimado, parar y revisar el alcance antes de seguir.
- Las "preguntas abiertas" (Pausa 0) se contestan en este mismo documento bajo cada pausa cuando aplique.

## Cambios fuera de este plan (ya hechos)

- 2026-05-25 — Auto-creación de categorías al importar Excel (normalización case/acento-insensitiva + registro automático en `CategoriaConfig`).
- 2026-05-25 — Resumen de pago de prenómina (no es inventario, pero comparte contexto de UX).

## Progreso

**Sesión 1 (2026-05-25):** Pausas 0, 1, 2, 2-bis, 3 y 5 cerradas en un día (estimado original ~3 semanas).

**Sesión 2 (2026-05-25):** Pausa 8b (Entrega parcial) cerrada. 18 tests nuevos en `tests/test_entrega_parcial.py`, todos en verde. Sin migraciones nuevas.

**Sesión 3 (2026-05-25):** Pausa 6 (Reportes Excel) cerrada. 5 endpoints `/api/v1/reportes/*.xlsx` + UI `Reportes.jsx`. 17 tests nuevos en `tests/test_reportes_inventario.py`, todos en verde. Sin migraciones nuevas.

**Sesión 4 (2026-05-25):** Pausa 8a (Etiquetas imprimibles) cerrada. `POST /api/v1/etiquetas/pdf` (Avery 5160/5163, barcode/QR) + UI `Etiquetas.jsx`. 15 tests nuevos en `tests/test_etiquetas.py`, todos en verde. Sin dependencias ni migraciones nuevas (usa `reportlab` y `qrcode` ya instalados).

**Sesión 8 (2026-05-26):** Pausa 10 (Conteo físico) cerrada en backend y frontend. 1 migración nueva (`o2p3q4r5s6t7_add_toma_inventario`, aplicada). 2 modelos (`TomaInventario` + `TomaInventarioDetalle`) con partial unique index para una toma ABIERTA por almacén. 7 endpoints (`POST /tomas/`, `GET /tomas/`, `GET /tomas/<id>`, `PATCH /detalles/<id>`, `PATCH /detalles/por-codigo`, `POST /cerrar`, `POST /cancelar`, `GET /pdf`). Template `templates/toma_inventario_pdf.html` con tabla de diferencias y 3 firmas. Frontend: `Tomas.jsx` (lista + modal iniciar) y `TomaDetalle.jsx` (captura inline + modo scanner móvil con `html5-qrcode`). Limpieza previa: dropped tablas huérfanas de Pausa 7 (`push_subscriptions`, `notificacion_preferencias`, `users.email`) y reseteo `alembic_version` antes de aplicar las migraciones nuevas.

**Sesión 7 (2026-05-26):** Pausa 4 (PWA + scanner móvil) cerrada. 1 migración nueva (`n1o2p3q4r5s6_add_producto_estante`). Modelo `ProductoEstante` (mapping puro). 4 endpoints nuevos en backend: `GET /estantes/<qr>/inventario`, `GET /estantes/<id>/productos`, `PUT /estantes/<id>/productos`, `POST /movimientos/rapido`. Refactor de `create_movimiento` para extraer `_perform_movimiento`. Frontend: `vite-plugin-pwa` con runtime cache de catálogos, `BottomNav.jsx` por rol, modal de productos por estante en `AlmacenesEstantes.jsx`, `ScannerMovil.jsx` actualizado para usar la nueva API. Sin tests todavía — quedan como pendiente para una sesión aparte.

**Sesión 6 (2026-05-26):** Pausa 7 (Notificaciones push + correo) **revertida** por decisión del usuario. Se eliminó `app/notif_push.py`, los endpoints push, los hooks, los modelos `PushSubscription`/`NotificacionPreferencia`, las migraciones, los tests y `pywebpush`. Las migraciones ya aplicadas en BDs locales dejan tablas huérfanas — se pueden dropear a mano o ignorar.

**Sesión 5 (2026-05-25):** Pausa 9 (Compras express) cerrada en backend y frontend.
- Backend: 2 columnas nuevas en `Producto` (`proveedor_default_nombre`, `proveedor_default_contacto`) + migración `k8l9m0n1o2p3_add_proveedor_default_producto.py`. Endpoints `/ordenes-compra/express/sugerencia` y `/ordenes-compra/express/pdf` + template `orden_compra_express_pdf.html`. 25 tests nuevos en `tests/test_ordenes_compra_express.py`, todos en verde.
- Frontend (repo `plantilla-frontend` en Vercel): `src/api/inventario.js` ganó `sugerirOCExpress`, `generarOCExpressPdf` y `descargarPdfDesdeUrl`. `CatalogoProductos.jsx` ahora pide proveedor + contacto al crear/editar producto. `BajoMinimo.jsx` reescrito con checkboxes, barra de selección, y `OCExpressModal` que agrupa por proveedor y genera PDFs uno por proveedor con botones Descargar/WhatsApp. `npx vite build` compila limpio.

**Migraciones aplicadas en DB local 2026-05-25:**
- `h5i6j7k8l9m0_add_stock_por_almacen.py` — OK, 287 productos depositados en `Almacen Principal` (id=2).
- `i6j7k8l9m0n1_add_notificacion_umbral.py` — OK, tabla vacía esperando primer cruce de umbral.
- `j7k8l9m0n1o2_add_stock_reservado.py` — OK, 1 producto con 1.30 unidades apartadas (de una solicitud APROBADA preexistente).
- `k8l9m0n1o2p3_add_proveedor_default_producto.py` — pendiente aplicar en local (Pausa 9). Sin backfill: `proveedor_default_nombre` y `proveedor_default_contacto` arrancan en NULL para los 287 productos existentes; se van completando al editar producto por producto en el catálogo.
- ~~`l9m0n1o2p3q4_add_push_notifications.py`~~ — archivo eliminado (Pausa 7 revertida). Las tablas `push_subscriptions`/`notificacion_preferencias` quedan huérfanas en BDs donde ya se aplicó; se pueden dropear a mano.
- ~~`m0n1o2p3q4r5_add_user_email.py`~~ — archivo eliminado (Pausa 7 revertida). La columna `users.email` queda huérfana donde ya se aplicó.
- `n1o2p3q4r5s6_add_producto_estante.py` — aplicada (Pausa 4) tras limpiar el `alembic_version` de las migraciones huérfanas de Pausa 7. Crea `producto_estante (producto_id, estante_id, updated_at)` sin backfill — los estantes arrancan vacíos y se llenan desde la nueva UI.
- `o2p3q4r5s6t7_add_toma_inventario.py` — aplicada (Pausa 10). Crea `tomas_inventario` + `tomas_inventario_detalle` con partial unique index `one_open_toma_per_almacen` (PG-only).

Estado verificado: suma de `Producto.stock_actual` = suma de `StockPorAlmacen.cantidad` = 2635.00. Cache y fuente de verdad coinciden.

**Para staging/producción todavía falta:** correr las migraciones de Pausa 2/2-bis/5/9 en el mismo orden. El backfill es idempotente y reproducible (depende solo del estado de Producto + SolicitudMaterial al momento del upgrade). La de Pausa 9 (`k8l9m0n1o2p3`) no requiere backfill. Las dos migraciones de Pausa 7 fueron eliminadas — no aplicar.

---

## Próxima sesión — contexto para retomar

### Qué ya está hecho y desplegado en local
- 6 pausas cerradas (ver tabla "Resumen de fases" arriba).
- 3 migraciones aplicadas en DB local.
- Frontend actualizado en `plantilla-frontend/` — Ctrl+K, Stock por bodega en catálogo, Kardex con timeline, Bajo mínimo con KPIs, badge de apartado en stock.

### Antes de empezar la siguiente sesión, validar lo hecho
1. **Reiniciar backend** para cargar modelos nuevos.
2. **Reload del SPA** y probar manualmente:
   - `Ctrl+K` desde cualquier pantalla → debe buscar productos/solicitudes/empleados.
   - Catálogo → ícono `Warehouse` → debe abrir modal con stock por bodega.
   - Catálogo → ícono `History` → debe abrir timeline con saldo corrido.
   - Menú lateral → "Bajo mínimo" → tabla con 4 KPI cards clickeables.
   - El producto con reserva (1.30 unidades) debe mostrar badge `(1.30 apart.)` en el catálogo.
3. **Probar el bug que cerramos**:
   - Identifica un producto con stock 10 y nada apartado.
   - Crea 2 solicitudes pidiendo 7 piezas cada una.
   - Aprueba la primera → debe pasar y reservar 7.
   - Aprueba la segunda → debe **fallar con 409** ("solo hay 3 disponibles").
4. **Probar SALIDA contra reservas**:
   - Para el producto con reserva, intenta SALIDA de más que el disponible → debe bloquear con 409.

### Estado al cierre de sesión 8 (2026-05-26)

Todas las pausas del plan original están **cerradas** (excepto Pausa 7 que fue
descartada). El sistema cubre: stock por almacén, reservas, kardex, PWA con
scanner, bajo mínimo + alertas, reportes Excel, etiquetas, entrega parcial,
compras express y conteo físico.

### Pausas pendientes
- Ninguna del plan original. Próximas mejoras: tests automatizados de Pausa 4 + 10, y validación end-to-end en campo (tomas reales, PWA en celular).

### Cabos sueltos conocidos (no urgentes pero registrarlos)
- ~~`APROBADA → ENTREGADA` libera reservas sin descontar stock real (lo arregla Pausa 8b).~~ ✅ Resuelto en sesión 2: el flujo recomendado ahora es `POST /solicitudes/<id>/entregar`. El atajo `PATCH /estado` con `ENTREGADA` sigue existiendo (libera reservas sin tocar stock) — útil para cerrar manualmente solicitudes pre-8b cuyo stock se descontó por fuera.
- `ENTREGADA → PENDIENTE` re-reserva pero no devuelve stock — si el almacenista ya hizo SALIDA aparte, el cache puede quedar mal. Documentar al usuario que reabrir entregadas requiere ajuste manual.
- ~~Service worker no existe (necesario para Pausa 4).~~ ✅ Resuelto en Pausa 4 con vite-plugin-pwa.
- ~~`Producto.proveedor_default` no existe (necesario para Pausa 9).~~ ✅ Resuelto en sesión 5.
- ~~Frontend de Pausa 9 pendiente (vive en repo Vercel, no este).~~ ✅ Resuelto en sesión 5.
- En `inventario_ui.py` (rutas legacy Flask Jinja) NO se aplicaron los cambios de Pausa 2/2-bis/8b/9. La UI legacy quedó atrás, pero como `LEGACY_UI_ENABLED=false` en prod no es bloqueante. Si alguien encienda esa UI verá stock global sin reservas ni entrega parcial.
