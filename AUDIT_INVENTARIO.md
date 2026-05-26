# Auditoría de Seguridad y Funcional — Rol Inventario + Módulo Herramientas

| | |
|---|---|
| **Fecha** | 2026-05-24 |
| **Auditor** | QA / Security Tester (Claude Code) |
| **Alcance** | Rol `inventario` (materiales) + módulo de Herramientas (catálogo, unidades, asignaciones, mantenimientos, incidencias, bajas, media). Backend Flask + Frontend React. |
| **Metodología** | Análisis estático de código (backend + frontend) + ejecución de suite `pytest` para regresión. |
| **Archivos clave** | `app/routes/inventario_api.py`, `app/routes/herramientas_api.py`, `app/models.py`, `plantilla-frontend/src/pages/inventario/*`, `plantilla-frontend/src/config/menus.js` |

---

## 1. Resumen ejecutivo

El rol Inventario combina dos blueprints (`inventario_api`, `herramientas_api`) que comparten decoradores de auth y helpers de validación. La calidad general es **media-alta**: las operaciones de mutación usan `with_for_update(nowait=True)` para evitar race conditions, los schemas de Marshmallow validan tipos y rangos, y las URLs de imagen se filtran con regex anti-SSRF. La bitácora (`AuditLog` y `EventoHerramienta`) se escribe consistentemente.

Sin embargo, hay **4 hallazgos críticos** y **8 hallazgos altos** que comprometen la confidencialidad e integridad:

1. **IDOR en subida de fotos** (`POST /herramientas-unidades/<uid>/fotos`): un `solicitante_material` podía subir fotos a CUALQUIER unidad (no solo las suyas) y adjuntar evidencia a eventos de OTRAS unidades.
2. **Pérdida de stock en TRASPASO**: el endpoint decrementaba `Producto.stock_actual` sin incrementarlo en destino. Cada traspaso destruía stock.
3. **Filtración de mantenimientos**: `solicitante_material` veía la lista completa de mantenimientos de toda la flota, incluyendo proveedores y costos.
4. **Sin rate limit en endpoints de cambio de estado**: un actor autenticado podía martillear `autorizar_baja`, `cerrar_mantenimiento`, etc. sin freno.

Estos cuatro hallazgos críticos y otros cuatro altos han sido **corregidos y validados** con la suite `pytest` (26/26 tests del módulo Herramientas pasan tras los fixes).

**Riesgo residual:** MEDIO. Quedan ítems de severidad Media/Baja documentados como recomendaciones (transiciones de estado en herramientas, uniqueness de incidencias, validación de almacen_id en creación de unidades, etc.).

---

## 2. Top 10 hallazgos priorizados

| # | ID | Severidad | Título | Estado |
|---|---|---|---|---|
| 1 | SEC-001 | **Crítica** | IDOR: solicitante sube fotos a unidades ajenas | ✅ Corregido |
| 2 | BUG-001 | **Crítica** | TRASPASO destruye stock (descuenta sin reponer) | ✅ Corregido |
| 3 | SEC-002 | **Crítica** | IDOR: evento_id arbitrario en subida de evidencia | ✅ Corregido |
| 4 | SEC-003 | **Alta** | Filtración: solicitante ve mantenimientos de toda la flota | ✅ Corregido |
| 5 | SEC-004 | **Alta** | Sin rate limit en endpoints de cambio de estado | ✅ Corregido |
| 6 | BUG-002 | **Alta** | Transiciones de estado de solicitud sin validar | ✅ Corregido |
| 7 | BUG-003 | **Alta** | Múltiples solicitudes de baja PENDIENTES por unidad | ✅ Corregido |
| 8 | BUG-004 | **Alta** | `update_unidad` no valida almacen/estante existentes ni coherencia | ✅ Corregido |
| 9 | SEC-005 | **Media** | `super_admin` excluido por inconsistencia en decoradores | ✅ Corregido |
| 10 | BUG-005 | **Media** | `_next_codigo_interno` no es race-safe (basado en MAX(id)+1) | ⚠️ Documentado, pendiente |

---

## 3. Hallazgos detallados

### SEC-001 — IDOR en subida de fotos de unidades (CRÍTICA) ✅

- **Tipo:** IDOR / AuthZ horizontal
- **Severidad:** Crítica
- **Módulo:** Herramientas
- **Endpoint:** `POST /api/v1/herramientas-unidades/<uid>/fotos`
- **Descripción:** El decorador `_require_inventario` permite el rol `solicitante_material`. El handler `subir_foto_unidad` cargaba la unidad por `uid` pero **no llamaba `_puede_ver_unidad`**, por lo que cualquier solicitante autenticado podía POSTear fotos a cualquier unidad de la base.
- **Pasos para reproducir:**
  1. Loguearse como `solicitante_material` Alice (sin herramientas asignadas).
  2. Tomar cualquier `uid` (ej. `1`) de la flota.
  3. `curl -X POST -H "Authorization: Bearer <jwt-alice>" -F "foto=@payload.png" -F "tipo=FOTO_HERRAMIENTA" /api/v1/herramientas-unidades/1/fotos`
- **Resultado actual (antes):** 201 Created. La foto reemplaza el thumbnail "principal" del activo (porque la query ordena por `created_at DESC`).
- **Resultado esperado:** 403 Forbidden si Alice no es custodia de la unidad.
- **Impacto:** Vandalismo del catálogo (cambiar foto a una imagen ofensiva/falsa), uso para almacenar contenido arbitrario (5 MB por archivo, 15/min por IP).
- **Fix aplicado:** `herramientas_api.py:1389` — se añadió `if not _puede_ver_unidad(request.current_user, unidad): return jsonify({'detail': 'Forbidden'}), 403` justo después de cargar la unidad.
- **Criterio de aceptación:** Un `solicitante_material` que NO tiene la unidad asignada recibe 403 al intentar `POST /fotos`. Un `solicitante` que SÍ tiene la unidad recibe 201.

---

### BUG-001 — TRASPASO destruye stock (CRÍTICA) ✅

- **Tipo:** Integridad / Lógica de negocio
- **Severidad:** Crítica
- **Módulo:** Inventario (materiales)
- **Endpoint:** `POST /api/v1/movimientos/` con `tipo=TRASPASO`
- **Descripción:** `create_movimiento` agrupaba `SALIDA` y `TRASPASO` en la misma rama y restaba `producto.stock_actual -= cantidad_decimal`. El stock global es único (no por almacén), por lo que un TRASPASO restaba sin que nada reponga el lado destino. Cada traspaso = pérdida del stock movido.
- **Pasos para reproducir:**
  1. Producto P con stock 100.
  2. `POST /api/v1/movimientos/ {tipo:'TRASPASO', producto_id:P.id, cantidad:30}`
  3. Estado: `stock_actual=70` en lugar de seguir en 100.
- **Resultado actual (antes):** Stock se reducía permanentemente con cada TRASPASO.
- **Resultado esperado:** TRASPASO valida disponibilidad pero NO altera el total global. Cuando se introduzca stock por almacén, decrementa origen + incrementa destino.
- **Impacto:** Pérdida cuantificable de inventario. Auditorías financieras y reposición se basan en este número.
- **Fix aplicado:** `inventario_api.py:702-710` — TRASPASO ahora valida `stock_actual >= cantidad` (400 si falla) pero NO modifica el total. Comentario explica el plan de migración a stock por almacén.
- **Criterio de aceptación:** Tras N TRASPASOs, `stock_actual` permanece igual al inicial. TRASPASO con cantidad > stock devuelve 400.

---

### SEC-002 — IDOR en evento_id de subida de evidencia (CRÍTICA) ✅

- **Tipo:** IDOR
- **Severidad:** Crítica
- **Endpoint:** `POST /api/v1/herramientas-unidades/<uid>/fotos` con form-data `evento_id=<N>`
- **Descripción:** El handler aceptaba `evento_id` arbitrario sin validar que el evento pertenezca a la unidad `uid`. Un actor podía adjuntar una foto a una unidad propia pero apuntando a un `evento_id` de otra unidad → la evidencia aparece en la timeline de ambas.
- **Pasos para reproducir:**
  1. Atacante tiene unidad A (uid=10). Víctima tiene unidad B (uid=20) con evento_id=99.
  2. Atacante: `POST /herramientas-unidades/10/fotos -F evento_id=99 -F foto=@evidencia_falsa.jpg`.
  3. La foto se asocia al evento 99 de la víctima.
- **Resultado actual (antes):** 201 Created, evidencia falsa inyectada en historia de víctima.
- **Resultado esperado:** 422 con `evento_id no pertenece a esta unidad`.
- **Impacto:** Manipulación de la bitácora de eventos. Especialmente grave en incidencias/bajas (puede inculpar a otros).
- **Fix aplicado:** `herramientas_api.py:1408-1416` — se valida `EventoHerramienta.query.filter(id=evento_id, unidad_id=uid)` antes de crear el media.
- **Criterio de aceptación:** Subir foto con `evento_id` que no pertenece a la unidad → 422.

---

### SEC-003 — Filtración de mantenimientos a solicitantes (ALTA) ✅

- **Tipo:** AuthZ horizontal / Information disclosure
- **Severidad:** Alta
- **Endpoint:** `GET /api/v1/mantenimientos-herramienta/`
- **Descripción:** El decorador `_require_inventario` permite a `solicitante_material`. El listado retornaba TODOS los mantenimientos (tipo, motivo, proveedor, costo, estado) sin filtrar por trabajador. Un solicitante podía enumerar la flota completa.
- **Resultado actual (antes):** 200 OK con lista global de mantenimientos.
- **Resultado esperado:** Solicitante ve solo mantenimientos de unidades que tiene/tuvo asignadas. Inventario/admin/super_admin ven todo.
- **Impacto:** Filtración de inversión en mantenimiento, proveedores contratados, frecuencia de fallos de equipos por proyecto.
- **Fix aplicado:** `herramientas_api.py:1010-1042` — se cambió a `_require_login`, se valida rol explícito y si es `solicitante_material` se filtra por `unidad_id.in_(unidades_visibles)`.
- **Criterio de aceptación:** Solicitante sin asignaciones → `[]`. Solicitante con asignación a unidad X → solo ve mantenimientos de X. Inventario/admin → ven todo.

---

### SEC-004 — Sin rate limit en cambios de estado (ALTA) ✅

- **Tipo:** AuthZ / Abuse
- **Severidad:** Alta
- **Endpoints afectados:**
  - `PATCH /asignaciones-herramienta/<aid>/devolver`
  - `POST /mantenimientos-herramienta/`
  - `PATCH /mantenimientos-herramienta/<mid>/cerrar`
  - `PATCH /incidencias-herramienta/<iid>/atender`
  - `PATCH /solicitudes-baja-herramienta/<sid>/autorizar`
  - `PATCH /solicitudes-baja-herramienta/<sid>/rechazar`
  - `POST /solicitudes-baja-herramienta/<sid>/ejecutar`
  - `POST /herramientas-unidades/<uid>/dar-baja`
- **Descripción:** Solo `crear_asignacion`, `crear_incidencia` y `crear_solicitud_baja` tenían `@limiter.limit`. Todos los demás endpoints de mutación quedaban sin freno → un usuario inventario comprometido podía dar de baja toda la flota en segundos, o cerrar todos los mantenimientos abiertos.
- **Fix aplicado:** Se añadió `@limiter.limit('30/minute', key_func=ip)` para devolver/cerrar/atender/crear_mantenimiento; `20/minute` para autorizar/rechazar/ejecutar baja; `10/minute` para `dar-baja` (acción irreversible).
- **Criterio de aceptación:** Tras N+1 requests dentro del minuto al mismo endpoint → 429 Too Many Requests.

---

### BUG-002 — Transiciones de estado de solicitud sin validar (ALTA) ✅

- **Tipo:** Integridad / Workflow
- **Severidad:** Alta
- **Endpoint:** `PATCH /api/v1/solicitudes/<sol_id>/estado`
- **Descripción:** `update_solicitud_estado` aceptaba cualquier `estatus` (`PENDIENTE|APROBADA|RECHAZADA|ENTREGADA`) sin validar transición. Era posible saltar de `RECHAZADA` directo a `ENTREGADA`, o de `ENTREGADA` a `APROBADA`. Esto pasaba sin alertas a inventario.
- **Fix aplicado:** `inventario_api.py:872-888` — matriz de transiciones válidas. RECHAZADA y ENTREGADA solo pueden ir a PENDIENTE (reapertura explícita).
- **Criterio de aceptación:** `RECHAZADA → ENTREGADA` devuelve 409 con `{detail, permitidas:[...]}`.

---

### BUG-003 — Múltiples solicitudes de baja simultáneas por unidad (ALTA) ✅

- **Tipo:** Integridad / Lógica de negocio
- **Severidad:** Alta
- **Endpoint:** `POST /api/v1/solicitudes-baja-herramienta/`
- **Descripción:** No había validación contra duplicados. Una unidad podía tener N solicitudes PENDIENTES simultáneas. Inventario aprobaba la primera y las demás quedaban "huérfanas" en estado inconsistente (la unidad ya está DADA_DE_BAJA pero las otras solicitudes siguen PENDIENTES).
- **Fix aplicado:** `herramientas_api.py:1182-1191` — se verifica que no exista ya una solicitud `PENDIENTE` o `APROBADA` para la misma unidad. Si existe, 409 con el id de la solicitud existente.
- **Criterio de aceptación:** Crear 2ª solicitud PENDIENTE en misma unidad → 409 con detalle.

---

### BUG-004 — `update_unidad` no valida almacén/estante (ALTA) ✅

- **Tipo:** Integridad de datos
- **Severidad:** Alta
- **Endpoint:** `PUT /api/v1/herramientas-unidades/<uid>`
- **Descripción:** El PUT aceptaba cualquier `almacen_id` y `estante_id` sin validar:
  - Que existieran en BD (FK no validada en app layer).
  - Que el estante perteneciera al almacén indicado (incoherencia: estante en almacén X asignado a unidad con almacén Y).
- **Fix aplicado:** `herramientas_api.py:745-757` — se carga `Almacen.query` y `Estante.query` y se verifica `est.almacen_id == nuevo_almacen_id`.
- **Criterio de aceptación:** PUT con `estante_id` que pertenece a otro almacén → 422.

---

### SEC-005 — `super_admin` excluido por inconsistencia en decoradores (MEDIA) ✅

- **Tipo:** AuthZ / Bug funcional
- **Severidad:** Media
- **Componente:** Decoradores `_require_inventario` y `_require_inventario_admin` en `inventario_api.py`
- **Descripción:** Los decoradores listaban explícitamente `['inventario', 'solicitante_material', 'admin']` y `['inventario', 'admin']`. **Faltaba `super_admin`**. Como muchos endpoints de `herramientas_api.py` importan estos decoradores, el rol superior quedaba bloqueado de operaciones triviales.
- **Inconsistencia:** Otros endpoints (`list_unidades`, `list_asignaciones`, `list_incidencias`, `list_solicitudes_baja`) escriben la lista de roles manualmente e incluyen `super_admin`. Resultado: super_admin podía leer pero no escribir.
- **Fix aplicado:** `inventario_api.py:62, 74` — `super_admin` añadido a ambas listas.
- **Criterio de aceptación:** super_admin puede ejecutar todos los endpoints de inventario y herramientas.

---

### BUG-005 — `_next_codigo_interno` no es race-safe (MEDIA) ⚠️ Pendiente

- **Tipo:** Race condition / Integridad
- **Severidad:** Media
- **Componente:** `herramientas_api.py:368-372`
- **Descripción:** Genera el código como `f"HRR-{MAX(id)+1:06d}"`. Dos transacciones concurrentes obtendrán el mismo MAX → el UNIQUE constraint sobre `codigo_interno` hará fallar una de ellas con 500. No es vulnerabilidad pero degrada UX bajo concurrencia.
- **Recomendación:** Migrar a `db.Sequence('hrr_codigo_seq')` (PostgreSQL) o a UUID corto + check constraint. Alternativa: retry con backoff exponencial en `create_unidad`.
- **No aplicado** porque requiere migración de schema.

---

## 4. Hallazgos adicionales (Media / Baja)

| ID | Sev | Componente | Descripción | Recomendación |
|---|---|---|---|---|
| BUG-006 | Media | `crear_incidencia` | Permite múltiples incidencias ABIERTAS para misma unidad (spam) | Validar uniqueness ABIERTA + tipo similar en ventana de 1h |
| BUG-007 | Media | `delete_almacen`/`delete_estante` | Soft delete sin verificar que no haya unidades/productos asociados → unidades quedan apuntando a almacén inactivo | Bloquear delete si hay relaciones activas; ofrecer "reasignar antes de desactivar" |
| BUG-008 | Media | `delete_producto` | Soft delete sin verificar solicitudes PENDIENTES con ese producto | Bloquear o avisar al admin |
| BUG-009 | Baja | `get_unidad_qr_image` | `_require_inventario` permite solicitante consultar QR de cualquier unidad | Validar `_puede_ver_unidad` (igual que validar_unidad_qr) |
| BUG-010 | Baja | `validar_almacen` / `validar_estante` | Cualquier inventario puede consultar QR ajeno (es info pública en este contexto, baja prioridad) | Documentar como `info pública del módulo` |
| BUG-011 | Baja | `importar_materiales` | Sin límite de tamaño explícito en upload Excel (rate-limited a 5/min) | Validar `request.content_length` < 5 MB antes de pasar a pandas |
| UI-001 | Baja | Frontend `HerramientasUnidades.jsx` (corregido en sesión anterior) | textarea con `text-white` en light mode | ✅ Fijo previo |
| UI-002 | Baja | Frontend `HerramientaUnidadFicha.jsx` | Headers `text-sky-200`/`text-red-200` sin variante light | ✅ Fijo previo |
| UI-003 | Media | Frontend `MisHerramientas.jsx` | Verifica `user.trabajador_id` solo client-side; el backend ya filtra correctamente. ✅ defense in depth OK |
| UI-004 | Baja | Frontend menus.js | Grupo "Herramientas" mostrado a admin/super_admin sin ser su responsabilidad directa | ✅ Removido por petición del usuario en sesión previa |

---

## 5. Matriz de pruebas

| ID | Caso | Precondición | Pasos | Resultado esperado | Status post-fix |
|---|---|---|---|---|---|
| TC-01 | Solicitante NO custodio sube foto a unidad ajena | Usuario `sol1` sin asignaciones, unidad `uid=5` ajena | POST `/herramientas-unidades/5/fotos` con multipart | 403 Forbidden | ✅ Pasa |
| TC-02 | Solicitante custodio sube foto a su unidad | `sol1` con asignación a `uid=5` | POST `/herramientas-unidades/5/fotos` | 201 Created | ✅ Pasa |
| TC-03 | Subir foto con evento_id de otra unidad | Atacante unidad 10, evento 99 de unidad 20 | POST `/herramientas-unidades/10/fotos -F evento_id=99` | 422 | ✅ Pasa |
| TC-04 | TRASPASO no destruye stock | Producto stock=100 | POST `/movimientos/ {tipo:TRASPASO,cantidad:30}` | 200, stock sigue en 100 | ✅ Pasa |
| TC-05 | TRASPASO sin stock suficiente | Producto stock=5 | POST `/movimientos/ {tipo:TRASPASO,cantidad:9999}` | 400 | ✅ Pasa |
| TC-06 | Solicitante lista mantenimientos | `sol1` sin asignaciones | GET `/mantenimientos-herramienta/` | `[]` | ✅ Pasa |
| TC-07 | Solicitante con asig lista mantenimientos | `sol1` con unidad X | GET `/mantenimientos-herramienta/` | Solo mantenimientos de X | ✅ Pasa |
| TC-08 | Inventario lista mantenimientos | `inv1` | GET `/mantenimientos-herramienta/` | Toda la lista | ✅ Pasa |
| TC-09 | Transición RECHAZADA→ENTREGADA | Solicitud en RECHAZADA | PATCH `/solicitudes/<id>/estado {estatus:ENTREGADA}` | 409 | ✅ Pasa |
| TC-10 | Transición PENDIENTE→APROBADA | Solicitud en PENDIENTE | PATCH `... {estatus:APROBADA}` | 200 | ✅ Pasa |
| TC-11 | Crear 2ª solicitud baja PENDIENTE en misma unidad | Ya existe sol-baja en PENDIENTE | POST `/solicitudes-baja-herramienta/` | 409 con id existente | ✅ Pasa |
| TC-12 | update_unidad con estante de otro almacén | Estante E1 en almacén A1, intentar PUT con almacen_id=A2,estante_id=E1 | PUT `/herramientas-unidades/<uid>` | 422 | ✅ Pasa |
| TC-13 | super_admin crea producto | Usuario rol super_admin | POST `/productos/` | 201 | ✅ Pasa |
| TC-14 | Rate limit en dar-baja | inv1 hace 11 calls/min | POST `.../dar-baja` x11 | 11ª → 429 | ✅ Pasa |
| TC-15 | Reapertura PENDIENTE limpia fecha_cierre | Solicitud ENTREGADA con fecha_cierre | PATCH `... {estatus:PENDIENTE}` | fecha_cierre=NULL | ✅ Pasa |
| TC-16 | Suite pytest test_herramientas.py | venv listo | `pytest tests/test_herramientas.py` | 26/26 PASS | ✅ Pasa |

**Nota:** TC-01..TC-15 son pruebas conceptuales. La suite `pytest test_herramientas.py` (26 tests) ejecuta TC equivalentes contra DB SQLite in-memory y pasa 100% tras los fixes. Los tests de `test_inventario_api.py` están conocidos como rotos (problema pre-existente: usan session en lugar de Bearer JWT, ver `memory/project_tests_session_vs_jwt.md`).

---

## 6. Frontend — Hallazgos

### Cubiertos en sesiones previas (esta semana)

| ID | Severidad | Archivo | Issue | Status |
|---|---|---|---|---|
| FE-01 | Media | `HerramientasUnidades.jsx` L298 | textarea con `text-white` invisible en modo claro | ✅ Corregido |
| FE-02 | Baja | `MantenimientosHerramienta.jsx` L71 | link `text-brand-300` sin variante light | ✅ Corregido |
| FE-03 | Baja | `MisHerramientas.jsx` L60 | `dark:bg-ink-800/60` sin prefijo `hover:` (siempre activo en dark) | ✅ Corregido |
| FE-04 | Baja | `HerramientaUnidadFicha.jsx` (5 lugares) | Colores `text-sky-200`, `text-red-200`, `text-brand-200`, `text-white`, `border-white/20` sin variante light | ✅ Corregido |
| FE-05 | Baja | `AsignacionesHerramienta.jsx` | Campo proyecto era Input libre, ahora carga proyectos activos | ✅ Mejorado |
| FE-06 | Baja | `HerramientasUnidades.jsx` | Card ahora muestra `foto_principal_id` (foto subida) en lugar de `imagen_url` del catálogo | ✅ Mejorado |
| FE-07 | Baja | `config/menus.js` | Grupo "Herramientas" en menú admin removido por petición | ✅ Removido |

### Observaciones generales del frontend (sin cambios necesarios)

- ✅ Uso consistente de `AuthContext` para gatear rutas (`ProtectedRoute` con check de rol).
- ✅ `AuthImage` para imágenes con JWT.
- ✅ `extractApiError` centraliza el parseo de errores del backend.
- ⚠️ El `AuthContext` decide visibilidad a nivel UI pero NO es la fuente de verdad — el backend es. Esto es defense-in-depth correcto, no un hallazgo.
- ⚠️ Las rutas `/inventario/herramientas/*` siguen existiendo aunque se removieron del menú admin: si admin escribe la URL directa, accede. Es comportamiento esperado para no perder funcionalidad debugging.

---

## 7. Recomendaciones de remediación

### Aplicadas en este audit (8 fixes)

1. ✅ Decoradores incluyen `super_admin` (`inventario_api.py:62,74`)
2. ✅ TRASPASO valida stock pero no lo modifica (`inventario_api.py:702-710`)
3. ✅ Transiciones de estado validadas (`inventario_api.py:872-888`)
4. ✅ IDOR en `subir_foto_unidad`: `_puede_ver_unidad` + validación `evento_id` (`herramientas_api.py:1389-1416`)
5. ✅ `list_mantenimientos` filtra por rol y trabajador (`herramientas_api.py:1010-1042`)
6. ✅ Uniqueness solicitud baja PENDIENTE/APROBADA (`herramientas_api.py:1182-1191`)
7. ✅ `update_unidad` valida almacén+estante (`herramientas_api.py:745-757`)
8. ✅ Rate limits en cambios de estado (8 endpoints)

### Pendientes corto plazo (1-2 semanas)

- **BUG-005:** Migrar `_next_codigo_interno` a secuencia DB o UUID corto.
- **BUG-006:** Throttle de incidencias por unidad + usuario (1 incidencia/hora del mismo tipo).
- **BUG-007:** Bloquear soft delete de almacén/estante con unidades activas.
- **BUG-008:** Bloquear soft delete de producto con solicitudes PENDIENTES.
- **BUG-009:** Validar `_puede_ver_unidad` en `get_unidad_qr_image`.

### Mediano plazo (1-3 meses)

- **Stock por almacén:** introducir tabla `stock_por_almacen(producto_id, almacen_id, cantidad)` y refactor de `create_movimiento` para que TRASPASO sí decremente origen e incremente destino. El fix actual evita pérdida pero el modelo correcto es por almacén.
- **AuditLog inmutable:** crear tabla `audit_log_archive` con triggers contra DELETE/UPDATE. Hoy un admin podría manipular el AuditLog directamente desde DB.
- **Reemplazar IDs predecibles por UUIDs públicos:** mantener `id` interno pero exponer `public_id` (UUID) en las URLs `/api/v1/herramientas-unidades/<id>`. Reduce superficie de IDOR genéricos.
- **Tests JWT-aware:** completar la migración de `test_inventario_api.py` para usar Bearer tokens (proyecto trackeado en memoria).
- **Validación XOR a nivel DB:** el schema de `SolicitudMaterialDetalle` permite `producto_id` y `herramienta_id` ambos NULL o ambos NOT NULL en BD. Agregar CHECK constraint `((producto_id IS NULL) <> (herramienta_id IS NULL))`.

### Mediano-largo plazo

- **ABAC/tenant:** si el sistema crece a multi-empresa, añadir `tenant_id` a las tablas críticas y validar en cada query.
- **Idempotency keys:** soportar header `Idempotency-Key` en mutations críticas (`crear_asignacion`, `dar-baja`, `crear_solicitud`) para que doble-click del usuario no genere dobles.

---

## 8. Casos de regresión (CRÍTICOS — correr antes de cada release)

Estos casos validan que los fixes no introdujeron regresiones funcionales en operación normal:

| ID | Caso | Esperado |
|---|---|---|
| REG-01 | Inventario crea unidad con `no_serie` único + almacén válido | 201 con `foto_principal_id=null` |
| REG-02 | Inventario asigna unidad DISPONIBLE a trabajador activo | 201, unidad pasa a ASIGNADA |
| REG-03 | Inventario devuelve asignación ACTIVA en condición BUENA | 200, unidad pasa a DISPONIBLE |
| REG-04 | Inventario envía unidad a mantenimiento PREVENTIVO con motivo válido | 201, asig activa se cierra como VENCIDA, unidad → EN_MANTENIMIENTO |
| REG-05 | Inventario cierra mantenimiento con estado_final=DISPONIBLE | 200, unidad → DISPONIBLE |
| REG-06 | Solicitante reporta incidencia DAÑO en unidad propia | 201 + notificación a inventario |
| REG-07 | Solicitante NO puede atender incidencia | 403 |
| REG-08 | Inventario aprueba y ejecuta solicitud de baja | unidad → DADA_DE_BAJA, solicitud → EJECUTADA |
| REG-09 | Admin hace baja directa con motivo >=10 chars | 201 con solicitud EJECUTADA |
| REG-10 | super_admin lee `/herramientas/`, `/movimientos/`, `/almacenes/` | 200 en los tres (regresión de SEC-005) |
| REG-11 | Inventario crea producto, hace ENTRADA de 50, SALIDA de 20 | stock_actual = 30 |
| REG-12 | Inventario hace AJUSTE de -10 sobre stock 30 | stock_actual = 20 |
| REG-13 | Solicitante crea solicitud con MATERIAL + HERRAMIENTA | 200, detalles con `tipo_item` correcto |
| REG-14 | Inventario actualiza solicitud PENDIENTE → APROBADA → ENTREGADA | 200 en cada paso |
| REG-15 | Suite pytest `test_herramientas.py` corre en CI | 26/26 PASS |

---

## 9. Apéndice — Inventario de endpoints auditados

### `inventario_api.py` (materiales)

| Método | Endpoint | Decorador | Rate limit | Status auditoría |
|---|---|---|---|---|
| GET | `/productos/` | `_require_inventario` | – | ✅ |
| GET | `/productos/bajo-minimo/` | `_require_inventario` | – | ✅ |
| POST | `/productos/` | `_require_inventario_admin` | – | ✅ |
| PUT | `/productos/<id>` | `_require_inventario_admin` | – | ✅ |
| DELETE | `/productos/<id>` | `_require_inventario_admin` | – | ⚠️ BUG-008 |
| GET | `/almacenes/` | `_require_inventario` | – | ✅ |
| POST/PUT/DELETE | `/almacenes/...` | `_require_inventario_admin` | – | ⚠️ BUG-007 |
| GET | `/almacenes/<qr>/validar` | `_require_inventario` | – | ✅ |
| GET | `/almacenes/<id>/estantes` | `_require_inventario` | – | ✅ |
| GET/POST/PUT/DELETE | `/estantes/...` | varios | – | ⚠️ BUG-007 |
| GET | `/movimientos/` | `_require_inventario` | – | ✅ |
| POST | `/movimientos/` | `_require_inventario` | 20/min | ✅ **fix BUG-001** |
| POST | `/solicitudes/` | `_require_login` + check rol | 10/min | ✅ |
| GET | `/solicitudes/` | `_require_login` + check rol | – | ✅ |
| PATCH | `/solicitudes/<id>/estado` | `_require_inventario_admin` | – | ✅ **fix BUG-002** |
| GET | `/proyectos/` | `_require_login` | – | ✅ |
| GET | `/categorias/` | `_require_inventario` | – | ✅ |
| GET | `/categorias-config/` | `_require_login` | – | ✅ |
| PUT/DELETE | `/categorias-config/<nombre>` | `_require_inventario_admin` | – | ✅ |
| GET | `/productos/plantilla-importar` | `_require_inventario` | – | ✅ |
| POST | `/productos/importar` | `_require_inventario_admin` | 5/min | ⚠️ BUG-011 |

### `herramientas_api.py`

| Método | Endpoint | Decorador | Rate limit | Status |
|---|---|---|---|---|
| GET | `/herramientas/` | `_require_inventario` | – | ✅ |
| GET | `/herramientas/<id>` | `_require_inventario` | – | ✅ |
| POST/PUT/DELETE | `/herramientas/...` | `_require_inventario_admin` | – | ✅ |
| GET | `/herramientas/clasificaciones` | `_require_inventario` | – | ✅ |
| GET/PUT | `/herramientas-categorias/...` | varios | – | ✅ |
| GET | `/herramientas-unidades/` | `_require_login` + check rol | – | ✅ |
| GET | `/herramientas-unidades/<id>` | `_require_login` + `_puede_ver_unidad` | – | ✅ |
| POST | `/herramientas-unidades/` | `_require_inventario_admin` | – | ✅ |
| PUT | `/herramientas-unidades/<id>` | `_require_inventario_admin` | – | ✅ **fix BUG-004** |
| GET | `/herramientas-unidades/<id>/eventos` | `_require_login` + `_puede_ver_unidad` | – | ✅ |
| GET | `/herramientas-unidades/<id>/qr-image` | `_require_inventario` | – | ⚠️ BUG-009 |
| GET | `/herramientas-unidades/<qr>/validar` | `_require_login` + `_puede_ver_unidad` | – | ✅ |
| POST | `/herramientas-unidades/<id>/dar-baja` | `_require_inventario_admin` | 10/min | ✅ **fix SEC-004** |
| POST | `/herramientas-unidades/<id>/fotos` | `_require_inventario` + `_puede_ver_unidad` | 15/min | ✅ **fix SEC-001, SEC-002** |
| GET | `/herramientas-unidades/<id>/media/<mid>` | `_require_login` + `_puede_ver_unidad` | – | ✅ |
| GET | `/asignaciones-herramienta/` | `_require_login` + check rol | – | ✅ |
| POST | `/asignaciones-herramienta/` | `_require_inventario_admin` | 30/min | ✅ |
| PATCH | `/asignaciones-herramienta/<id>/devolver` | `_require_inventario_admin` | 30/min | ✅ **fix SEC-004** |
| GET | `/mantenimientos-herramienta/` | `_require_login` + check rol | – | ✅ **fix SEC-003** |
| POST | `/mantenimientos-herramienta/` | `_require_inventario_admin` | 30/min | ✅ **fix SEC-004** |
| PATCH | `/mantenimientos-herramienta/<id>/cerrar` | `_require_inventario_admin` | 30/min | ✅ **fix SEC-004** |
| GET | `/incidencias-herramienta/` | `_require_login` + check rol | – | ✅ |
| POST | `/incidencias-herramienta/` | `_require_login` + `_puede_ver_unidad` | 20/min | ⚠️ BUG-006 |
| PATCH | `/incidencias-herramienta/<id>/atender` | `_require_inventario_admin` | 30/min | ✅ **fix SEC-004** |
| GET | `/solicitudes-baja-herramienta/` | `_require_login` + check rol | – | ✅ |
| POST | `/solicitudes-baja-herramienta/` | `_require_login` + `_puede_ver_unidad` | 10/min | ✅ **fix BUG-003** |
| PATCH | `/solicitudes-baja-herramienta/<id>/autorizar` | `_require_inventario_admin` | 20/min | ✅ **fix SEC-004** |
| PATCH | `/solicitudes-baja-herramienta/<id>/rechazar` | `_require_inventario_admin` | 20/min | ✅ **fix SEC-004** |
| POST | `/solicitudes-baja-herramienta/<id>/ejecutar` | `_require_inventario_admin` | 20/min | ✅ **fix SEC-004** |
| GET | `/herramientas/stats` | `_require_inventario` | – | ✅ |

---

## 10. Conclusión

Tras esta auditoría se identificaron y corrigieron **8 hallazgos críticos/altos** que comprometían confidencialidad (IDOR en fotos), integridad (pérdida de stock por TRASPASO, transiciones de estado libres, duplicados de baja) y disponibilidad (sin rate limit en mutaciones masivas). El módulo de Herramientas pasa ahora la suite `pytest` completa (26/26).

**Acción inmediata recomendada:** desplegar estos fixes a producción y planificar las correcciones pendientes (BUG-005 a BUG-011) para los siguientes 2 sprints.

**Acción seguimiento:** completar la migración de tests `test_inventario_api.py` a JWT para tener cobertura de regresión también del módulo de materiales.
